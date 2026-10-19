"""MCP tools: thin wrappers over ImageService, Catalog, LoginManager and keystore.

Built on the mcp 2.x SDK (`MCPServer`, formerly FastMCP). Every tool maps the
package's expected errors to a `ToolError`, so the client gets `isError=True`
with a readable message; anything unexpected propagates and the SDK reports it
without details. All text output passes through `redact`.
"""

import asyncio
import base64
import inspect
import webbrowser
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import keyring.errors
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from . import __version__, keystore
from .advisor import AdvisorService
from .advisor_client import AdvisorClient
from .advisor_jobs import AdvisorJobs
from .advisor_store import AdvisorStore, default_store_path
from .advisor_tools import enforce_advisor_arguments, register_advisor_tools
from .apps import (
    IMAGE_TOOLS,
    UI_URI,
    ImageApps,
    app_icons,
    negotiate_apps,
    preview_image,
    preview_meta,
    register_advisor_viewer,
    register_gallery,
    register_settings,
)
from .auth import LoginManager, LoginState
from .catalog import Catalog, ModelCapabilities, price_key
from .client import OpenRouterClient
from .config import Settings, load_settings
from .errors import AuthRequiredError, CatalogUnavailableError, OpenRouterError
from .image_onboarding import image_runner
from .imaging import InputError
from .logs import configure_logging, redact
from .onboarding import OnboardingDemos
from .preference_tools import register_preference_tools
from .preferences import PreferenceService
from .remask import remask
from .service import ImageService, ProgressFn, ServiceResult
from .tool_annotations import annotate

SERVER_NAME = "openrouter-sidecar"
CATALOG_PREFIX = "Couldn't reach the OpenRouter catalog"
KEYRING_ERROR_PREFIX = "Couldn't access the OS credential store"
LOGOUT_MSG = (
    "Signed out of OpenRouter on this computer. To revoke the key itself, "
    "delete it at https://openrouter.ai/settings/keys"
)
TABLE_COLUMNS = (
    "id", "name", "inputs", "aspect ratios", "resolutions", "quality", "max n", "seed",
    "moderated", "price",
)
_PRICE_UNITS = (("image_output", "img-unit"), ("image", "image"), ("completion", "tok"))
_DASH = "—"

ImageResult = CallToolResult


# ------------------------------------------------------------------ helpers


@contextmanager
def _tool_errors(target: str = "") -> Iterator[None]:
    """Turn the package's expected failures into tool errors (isError=True)."""
    def failure(message: str) -> ToolError:
        return ToolError(redact(f"{target}\n{message}" if target else message))

    try:
        yield
    except CatalogUnavailableError as exc:
        message = exc.message
        if not message.startswith(CATALOG_PREFIX):
            message = f"{CATALOG_PREFIX}: {message}"
        raise failure(message) from None
    except OpenRouterError as exc:
        raise failure(exc.message) from None
    except (InputError, keystore.InsecureKeyringError) as exc:
        raise failure(str(exc)) from None
    except keyring.errors.KeyringError as exc:
        raise failure(f"{KEYRING_ERROR_PREFIX}: {exc}") from None
    except OSError as exc:  # unwritable output folder, sign-in listener bind, disk full
        raise failure(str(exc)) from None


def _progress(ctx: Context) -> ProgressFn:
    """Forward service progress (value, total = number of calls) to the client.

    MCP requires progress to increase with every notification, so a value that
    doesn't is nudged just above the last one sent. `Context.report_progress` is a
    no-op when the request carried no progress token.
    """
    last: float | None = None

    async def report(value: float, total: float, message: str) -> None:
        nonlocal last
        if last is not None:
            value = max(last + 1e-6, value)
        last = value
        await ctx.report_progress(value, total, message)

    return report


def _sig(value: float) -> str:
    """Up to 6 significant digits, never in scientific notation."""
    text = format(Decimal(f"{value:.6g}"), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    number = _number(value)
    return f"${number:.2f}" if number is not None else "n/a"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _join(values: list[str] | None) -> str:
    return ", ".join(values) if values else _DASH


def _yes_no(value: bool | None) -> str:
    return "?" if value is None else ("yes" if value else "no")


def _inputs(m: ModelCapabilities) -> str:
    return f"text+image (max {m.max_input_images})" if m.accepts_images else "text"


def _price(m: ModelCapabilities) -> str:
    """First usable price, in `price_key` order; negative means variable pricing."""
    for field, unit in _PRICE_UNITS:
        value = _number(m.pricing.get(field))
        if value is not None and value >= 0:
            return f"${_sig(value)}/{unit}"
    return _DASH


def _row(m: ModelCapabilities) -> str:
    cells = (
        m.id, m.name, _inputs(m), _join(m.aspect_ratios), _join(m.resolutions),
        _join(m.qualities), m.max_n, "yes" if m.seed else "no", _yes_no(m.is_moderated),
        _price(m),
    )
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _table(models: list[ModelCapabilities]) -> str:
    lines = [
        "| " + " | ".join(TABLE_COLUMNS) + " |",
        "|" + "---|" * len(TABLE_COLUMNS),
        *(_row(m) for m in models),
        "",
        (
            f"{len(models)} model{'s' if len(models) != 1 else ''}. Call `get_image_model` "
            "with an id for per-provider parameters, passthrough options and pricing."
        ),
    ]
    return "\n".join(lines)


def _param_line(name: str, spec: Any) -> str:
    if isinstance(spec, dict):
        values = spec.get("values")
        if isinstance(values, list):
            return f"- {name}: {', '.join(str(v) for v in values)}"
        if "min" in spec or "max" in spec:
            return f"- {name}: {spec.get('min', '?')}–{spec.get('max', '?')}"
        if spec.get("type"):
            return f"- {name}: {spec['type']}"
    return f"- {name}"


def _pricing_lines(pricing: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(pricing, list):
        for item in pricing:
            if not isinstance(item, dict):
                continue
            cost = _number(item.get("cost_usd"))
            amount = f"${_sig(cost)}" if cost is not None else str(item.get("cost_usd"))
            unit = f"/{item['unit']}" if item.get("unit") else ""
            lines.append(f"- {item.get('billable', '?')}: {amount}{unit}")
    elif isinstance(pricing, dict):
        lines += [f"- {k}: {v}" for k, v in pricing.items()]
    return lines or [f"- {_DASH}"]


def _endpoint_section(endpoint: dict) -> list[str]:
    name = endpoint.get("provider_name") or endpoint.get("provider_slug") or "unknown provider"
    lines = ["", f"## Provider: {name}", "Supported parameters:"]
    params = endpoint.get("supported_parameters")
    if isinstance(params, dict) and params:
        lines += [_param_line(k, v) for k, v in params.items()]
    elif isinstance(params, list) and params:
        lines += [f"- {p}" for p in params]
    else:
        lines.append(f"- {_DASH}")
    passthrough = endpoint.get("allowed_passthrough_parameters")
    lines.append(
        "Passthrough parameters (for `provider_options`): "
        + (", ".join(str(p) for p in passthrough) if passthrough else "none")
    )
    lines.append("Pricing:")
    lines += _pricing_lines(endpoint.get("pricing"))
    return lines


def _model_details(m: ModelCapabilities, endpoints: list[dict] | None) -> str:
    lines = [f"# {m.name} (`{m.id}`)"]
    if m.description:
        lines += ["", m.description]
    lines += [
        "",
        "## Capabilities",
        f"- Route: {'images API' if m.route == 'images' else 'chat completions'}",
        f"- Inputs: {_inputs(m)}",
        f"- Aspect ratios: {_join(m.aspect_ratios)}",
        f"- Resolutions: {_join(m.resolutions)}",
        f"- Quality: {_join(m.qualities)}",
        f"- Background: {_join(m.backgrounds)}",
        f"- Output formats: {_join(m.output_formats)}",
        f"- Max n per call: {m.max_n}",
        f"- Seed: {'yes' if m.seed else 'no'}",
        f"- Exact size: {'yes' if m.size else 'no'}",
        f"- Moderated: {_yes_no(m.is_moderated)}",
        f"- Price: {_price(m)}",
    ]
    if endpoints is None:
        lines += [
            "",
            (
                "This model is served through chat completions: only `aspect_ratio` can be "
                "set, and `provider_options` is not supported."
            ),
        ]
    elif not endpoints:
        lines += ["", "No provider endpoints are listed for this model right now."]
    for endpoint in endpoints or []:
        if isinstance(endpoint, dict):
            lines += _endpoint_section(endpoint)
    return "\n".join(lines)


def _key_status(info: dict) -> str:
    label = info.get("label")
    lines = [f"Signed in to OpenRouter. Key: {label}" if label else "Signed in to OpenRouter."]
    lines.append(
        f"Usage: today {_money(info.get('usage_daily'))} · "
        f"this week {_money(info.get('usage_weekly'))} · "
        f"this month {_money(info.get('usage_monthly'))} · "
        f"total {_money(info.get('usage'))}"
    )
    if info.get("limit") is None:
        lines.append("Limit: none set on this key")
    else:
        line = f"Limit: {_money(info.get('limit'))}"
        if info.get("limit_remaining") is not None:
            line += f" · remaining {_money(info.get('limit_remaining'))}"
        lines.append(line)
    return "\n".join(lines)


def _image_result(result: ServiceResult) -> ImageResult:
    lines = []
    for img in result.images:
        line = f"Saved: {img.path}  (sidecar: {img.sidecar})"
        if img.note:
            line += f" ({img.note})"
        lines.append(line)
    if result.notes:
        lines += ["Notes:", *(f"- {n}" for n in result.notes)]
    if result.failures:
        lines += ["Failures:", *(f"- {f}" for f in result.failures)]
    lines.append(
        f"Model: {result.model} · Provider: {result.provider or 'unknown'} · "
        f"{result.elapsed_s:.0f}s"
    )
    lines.append(result.usage_line)
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=redact("\n".join(lines)))]
    content += [
        ImageContent(
            type="image", data=base64.b64encode(img.preview_jpeg).decode("ascii"),
            mime_type="image/jpeg",
        )
        for img in result.images if img.preview_jpeg is not None
    ]
    return CallToolResult(content=content, structured_content={
        "images": [preview_image(img.path) for img in result.images],
        "model": redact(result.model),
        "provider": redact(result.provider) if result.provider else None,
        "callCostUsd": result.call_cost_usd,
        "usageLine": redact(result.usage_line),
        "notes": [redact(note) for note in result.notes],
        "failures": [redact(failure) for failure in result.failures],
    }, meta=preview_meta(img.preview_jpeg for img in result.images))


def _add(server: MCPServer, fn: Callable[..., Any]) -> None:
    """Register `fn` with its cleaned docstring as the description and unstructured output."""
    meta = {"ui": {"resourceUri": UI_URI}} if fn.__name__ in IMAGE_TOOLS else None
    server.add_tool(
        fn, description=inspect.cleandoc(fn.__doc__ or ""), structured_output=False,
        meta=meta, icons=app_icons() if fn.__name__ in IMAGE_TOOLS else None,
    )


def catalog_tools(catalog: Catalog) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """The free model-discovery tools, shared by the local and hosted servers."""

    async def list_image_models(
        query: str | None = None,
        accepts_images: bool | None = None,
        author: str | None = None,
        sort: Literal["newest", "price"] = "newest",
    ) -> str:
        """List the image models available on OpenRouter right now, as a markdown table.

        Use this before `generate_image` or `edit_image` to choose a model and see which
        options it takes. The catalog is live (cached for 10 minutes), so don't rely on
        remembered model ids. Columns: id, name, inputs ("text", or "text+image (max N)"
        input images), aspect ratios, resolutions, quality values, max n per call, seed
        support, whether the provider moderates prompts (yes/no/?), and a price summary
        ($/img-unit per image-output unit, $/image, or $/tok per completion token).
        Call `get_image_model` for one model's per-provider details.

        Args:
            query: Case-insensitive text to find in the model id or name.
            accepts_images: true for models that take input images (needed by
                `edit_image`); false for text-only models.
            author: Only this author's models: the part of the id before "/".
            sort: "newest" (default, newest first) or "price" (cheapest first).
        """
        with _tool_errors():
            models = await catalog.all()
        if query:
            needle = query.lower()
            models = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
        if accepts_images is not None:
            models = [m for m in models if m.accepts_images == accepts_images]
        if author:
            models = [m for m in models if m.id.split("/", 1)[0].lower() == author.lower()]
        if sort == "price":
            models = sorted(models, key=price_key)
        if not models:
            return "No image models match those filters. Call `list_image_models` with fewer filters."
        return redact(_table(models))

    async def get_image_model(model_id: str) -> str:
        """Show full details for one image model.

        Returns the description, the capabilities, and one section per provider endpoint
        with its supported parameters and allowed values, the passthrough parameters usable
        in `provider_options`, and its pricing line items.

        Args:
            model_id: Exact model id from `list_image_models`.
        """
        with _tool_errors():
            m = await catalog.get(model_id)
            endpoints = await catalog.endpoints(model_id) if m.route == "images" else None
        return redact(_model_details(m, endpoints))

    return list_image_models, get_image_model


# ------------------------------------------------------------------ server


def build_server(
    settings: Settings, client: OpenRouterClient | None = None, *,
    advisor: AdvisorService | None = None, advisor_store_path: Path | None = None,
) -> MCPServer:
    """Create image/account tools and credential-scoped local advisor tools."""
    owns_client = client is None
    http = client if client is not None else OpenRouterClient(timeout_s=settings.timeout_s, workspace_id=settings.workspace_id)
    if http.workspace_id != settings.workspace_id:
        raise ValueError("Client workspace must match the configured cohort workspace.")
    catalog = Catalog(http)
    service = ImageService(http, catalog, settings)
    # Late-bound so the browser opener can be replaced (tests) after import.
    login = LoginManager(http, settings, open_browser=lambda url: webbrowser.open(url))
    owns_advisor = advisor is None
    advisors = advisor or AdvisorService(
        settings.workspace_id, AdvisorStore(advisor_store_path or default_store_path()),
        AdvisorClient(settings.workspace_id, settings.advisor_timeout_s),
    )
    if advisors.workspace_id != settings.workspace_id:
        raise ValueError("Advisor workspace must match the configured cohort workspace.")
    advisor_jobs = AdvisorJobs(advisors)
    preferences = PreferenceService(advisors)
    demos = OnboardingDemos(advisors, advisor_jobs, preferences,
                            image_runner=image_runner(settings, advisors))

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict]:
        try:
            yield {}
        finally:
            login.cancel()  # close a pending sign-in listener
            await demos.close()
            await advisor_jobs.close()
            if owns_client:
                await http.aclose()
            if owns_advisor:
                await advisors.client.aclose()

    server = MCPServer(
        SERVER_NAME, version=__version__, lifespan=lifespan, icons=app_icons(),
        extensions=[ImageApps()], middleware=[enforce_advisor_arguments, negotiate_apps],
    )
    register_gallery(server)
    register_advisor_viewer(server)
    register_settings(server)
    register_advisor_tools(server, advisors, advisor_jobs, _tool_errors, preferences=preferences)
    register_preference_tools(server, preferences, demos, catalog, settings, _tool_errors)

    # ---------------------------------------------------------- account

    async def account_status() -> str:
        """Show whether this computer is signed in to OpenRouter, with the key's spending.

        Reports the key label, usage today / this week / this month / in total, and the
        key's spending limit and remaining credit. If a sign-in is in progress, shows its
        status and link. Use it after `auth_login` to confirm sign-in finished, or when the
        user asks about cost, usage or credits. Takes no parameters.
        """
        with _tool_errors(settings.target):
            if keystore.get_key(workspace_id=settings.workspace_id) is None:
                # Not signed in, or the status of a pending/failed/expired sign-in.
                return redact(settings.target + "\n" + login.status()["message"])
            try:
                text = _key_status(await http.key_info())
            except AuthRequiredError:
                raise
            except OpenRouterError as exc:
                text = f"Signed in to OpenRouter (couldn't fetch usage: {exc.message})"
            status = login.status()
            if status["state"] == LoginState.PENDING.value:
                text += "\n" + status["message"]
            return redact(settings.target + "\n" + text)

    async def auth_login(switch_account: bool = False) -> str:
        """Sign in to OpenRouter in the user's browser.

        Opens the OpenRouter sign-in page and returns at once with a link. Show the link to
        the user in case the browser didn't open (it contains no secret). The user finishes
        in the browser; then call `account_status` to confirm. The new key is stored in the
        OS credential store and is never shown. Does nothing if already signed in, unless
        switch_account is true.

        Args:
            switch_account: true to sign in as a different account or workspace. The current
                key is kept until the new one is stored.
        """
        with _tool_errors(settings.target):
            started = await login.start(switch_account=switch_account)
            return redact(settings.target + "\n" + started["message"])

    async def auth_logout() -> str:
        """Sign out: delete the stored OpenRouter key from this computer.

        The key stays valid on OpenRouter until it is deleted on the keys dashboard; the
        result links that page. Takes no parameters.
        """
        with _tool_errors(settings.target):
            login.reset()  # a pending sign-in must not finish after sign-out
            keystore.delete_key(workspace_id=settings.workspace_id)
            return settings.target + "\n" + LOGOUT_MSG

    list_image_models, get_image_model = catalog_tools(catalog)

    # ----------------------------------------------------------- images

    async def generate_image(
        prompt: str,
        model: str,
        ctx: Context,
        n: int = 1,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        seed: int | None = None,
        background: str | None = None,
        output_format: str | None = None,
        output_dir: str | None = None,
        filename_prefix: str | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ImageResult:
        """Generate new images from a text prompt with an OpenRouter image model.

        Call `list_image_models` first to choose a model, and `get_image_model` for its exact
        allowed values. Options are checked against the model before anything is sent: an
        invalid value fails with the allowed values and costs nothing.

        Each call costs money on the user's OpenRouter account; the result reports the cost.
        Prompts go to a third-party model provider through OpenRouter: get the user's OK
        before sending client or project images or details to third-party providers.

        Images are saved (never overwriting) to the configured output folder with a JSON
        sidecar of the settings used. The result lists the saved paths, any notes and
        failures, the model, provider, time and cost, plus JPEG previews.

        Args:
            prompt: What to create.
            model: Model id from `list_image_models`.
            n: Number of images, 1 to 10. Above the model's max n, the server splits them
                into several calls (each billed) and says so in the notes.
            aspect_ratio: One of the model's aspect ratios, e.g. "16:9".
            resolution: One of the model's resolution tiers.
            size: Exact pixel size such as "1024x1024", for models that support it.
            quality: One of the model's quality values.
            seed: Integer for repeatable results, for models that support it.
            background: One of the model's background values, e.g. "transparent".
            output_format: One of the model's output formats, e.g. "png" or "svg".
            output_dir: Folder to save into instead of the default. Must be an absolute
                path or start with "~".
            filename_prefix: File name stem; defaults to a slug of the prompt.
            provider_options: Provider passthrough options as a flat dict, e.g.
                {"moderation": "low"}. Keys must be in the model's passthrough parameters
                (see `get_image_model`); the server sends each key to every provider that
                allows it. Any other key is rejected before anything is spent.
        """
        with _tool_errors():
            result = await service.generate(
                prompt, model, n=n, aspect_ratio=aspect_ratio, resolution=resolution,
                size=size, quality=quality, seed=seed, background=background,
                output_format=output_format, output_dir=output_dir,
                filename_prefix=filename_prefix, provider_options=provider_options,
                progress=_progress(ctx),
            )
        return _image_result(result)

    async def edit_image(
        prompt: str,
        model: str,
        images: list[str],
        ctx: Context,
        mask_path: str | None = None,
        mask_feather_px: int | None = None,
        fit: Literal["preserve", "model"] = "preserve",
        n: int = 1,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        seed: int | None = None,
        background: str | None = None,
        output_format: str | None = None,
        output_dir: str | None = None,
        filename_prefix: str | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ImageResult:
        """Edit an image (or create one from reference images) with an OpenRouter image model.

        Call `list_image_models` first to choose a model, picking one whose inputs column
        says "text+image". Options are checked against the model before anything is sent.

        The input images are uploaded to a third-party model provider through OpenRouter:
        get the user's OK before sending client or project images to third-party providers.
        Each call costs money on the user's OpenRouter account; the result reports the cost.

        All paths must be absolute (or start with "~"); relative paths are rejected.
        Results are saved next to the first input image (never overwriting it) with a JSON
        sidecar, unless output_dir is given. The result lists the saved paths, notes,
        failures, model, provider, time and cost, plus JPEG previews.

        Args:
            prompt: The change to make, e.g. "make it dusk with warm window light".
            model: Model id from `list_image_models`.
            images: Absolute paths of the input images. The first is the image being
                edited; any others are references.
            mask_path: Optional absolute path of a mask for the first image: white = change,
                black = keep. The edit is composited locally, so only the white area
                changes, but there may be a visible lighting seam at the mask edge where
                new and old pixels meet; a soft mask edge or mask_feather_px helps.
                Requires fit="preserve". A masked edit also saves the model's image
                before the blend as `<result>.unmasked.png`, so `remask_image` can redo
                the blend with a different mask for free.
            mask_feather_px: Blur radius in pixels for the mask edge; by default it is
                chosen from the image size.
            fit: "preserve" (default) returns each result at exactly the first input's
                pixel size: the server picks the model's closest aspect ratio, then scales
                and crops back (padding the input first if no ratio is close). With
                fit="preserve" don't pass aspect_ratio or size. "model" keeps the size the
                model returns and lets you choose aspect_ratio.
            n: Number of images, 1 to 10. Above the model's max n, the server splits them
                into several calls (each billed) and says so in the notes.
            aspect_ratio: One of the model's aspect ratios (only with fit="model").
            resolution: One of the model's resolution tiers.
            size: Exact pixel size such as "1024x1024" (only with fit="model").
            quality: One of the model's quality values.
            seed: Integer for repeatable results, for models that support it.
            background: One of the model's background values, e.g. "transparent".
            output_format: One of the model's output formats, e.g. "png".
            output_dir: Folder to save into instead of next to the first input. Must be
                an absolute path or start with "~".
            filename_prefix: File name stem; defaults to the first input's name.
            provider_options: Provider passthrough options as a flat dict, e.g.
                {"moderation": "low"}. Keys must be in the model's passthrough parameters
                (see `get_image_model`); the server sends each key to every provider that
                allows it. Any other key is rejected before anything is spent.
        """
        with _tool_errors():
            result = await service.edit(
                prompt, model, images, mask_path=mask_path, mask_feather_px=mask_feather_px,
                fit=fit, n=n, aspect_ratio=aspect_ratio, resolution=resolution, size=size,
                quality=quality, seed=seed, background=background,
                output_format=output_format, output_dir=output_dir,
                filename_prefix=filename_prefix, provider_options=provider_options,
                progress=_progress(ctx),
            )
        return _image_result(result)

    async def remask_image(
        image: str,
        mask_path: str,
        mask_feather_px: int | None = None,
        output_dir: str | None = None,
    ) -> ImageResult:
        """Redo the mask blend of a masked `edit_image` result with a new mask: free and offline.

        Use it when a masked edit came back with a good change that blends badly, such as
        a seam or a ghosted edge where the model drew past the mask (a limb or shadow cut
        off at the mask edge), or when the mask should have been bigger or smaller. Draw a
        new mask and call this instead of paying for another `edit_image`: it re-blends the
        model's image that the edit already returned, on this computer. Nothing is
        uploaded, no model is called and it costs nothing.

        It needs a result made by `edit_image` with `mask_path` by openrouter-image-mcp
        0.2.0 or later: those keep the model's image before the blend as
        `<result>.unmasked.png`, next to the result and its .json sidecar. The original
        input image must still be in place and unchanged. A re-masked result can itself
        be re-masked.

        All paths must be absolute (or start with "~"); relative paths are rejected. The
        new image is saved as a PNG next to `image` (never overwriting) with a JSON
        sidecar, unless output_dir is given. The result lists the saved path, plus a JPEG
        preview.

        Args:
            image: Absolute path of a masked `edit_image` result (or of an earlier
                `remask_image` result).
            mask_path: Absolute path of the new mask for the original input: white = take
                the model's image, black = keep the original. Any size; it is stretched
                to the input's size.
            mask_feather_px: Blur radius in pixels for the mask edge; by default it is
                chosen from the image size.
            output_dir: Folder to save into instead of next to `image`. Must be an
                absolute path or start with "~".
        """
        with _tool_errors():
            done = await asyncio.to_thread(
                remask, image, mask_path, mask_feather_px, output_dir, settings,
                datetime.now().astimezone(),
            )
        lines = [
            f"Saved: {done.path}  (sidecar: {done.sidecar})",
            "Cost: $0.00 (local re-blend, nothing uploaded)",
        ]
        if done.notes:
            lines += ["Notes:", *(f"- {n}" for n in done.notes)]
        return CallToolResult(content=[
            TextContent(type="text", text=redact("\n".join(lines))),
            ImageContent(
                type="image", data=base64.b64encode(done.preview_jpeg).decode("ascii"),
                mime_type="image/jpeg",
            ),
        ], structured_content={
            "images": [preview_image(done.path)],
            "model": "Local re-blend",
            "provider": None,
            "callCostUsd": 0,
            "usageLine": lines[1],
            "notes": [redact(note) for note in done.notes],
            "failures": [],
        }, meta=preview_meta([done.preview_jpeg]))

    for fn in (account_status, auth_login, auth_logout, list_image_models, get_image_model,
               generate_image, edit_image, remask_image):
        _add(server, fn)
    return annotate(server)


def run() -> None:
    """Run the MCP server over stdio. stdout carries only the protocol."""
    configure_logging()
    build_server(load_settings()).run(transport="stdio")
