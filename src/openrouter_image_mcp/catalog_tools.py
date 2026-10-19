"""The free model-discovery tools, `list_image_models` and `get_image_model`.

Markdown tables and per-model details over the live OpenRouter catalog. No inference
and no cost; all text passes through `redact`.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal

from .catalog import Catalog, ModelCapabilities, price_key
from .logs import redact
from .tool_support import tool_errors

TABLE_COLUMNS = (
    "id", "name", "inputs", "aspect ratios", "resolutions", "quality", "max n", "seed",
    "moderated", "price",
)
_PRICE_UNITS = (("image_output", "img-unit"), ("image", "image"), ("completion", "tok"))
_DASH = "—"


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


def catalog_tools(catalog: Catalog) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """The free model-discovery tools, registered by the hosted image tools."""

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
        with tool_errors():
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
        with tool_errors():
            m = await catalog.get(model_id)
            endpoints = await catalog.endpoints(model_id) if m.route == "images" else None
        return redact(_model_details(m, endpoints))

    return list_image_models, get_image_model
