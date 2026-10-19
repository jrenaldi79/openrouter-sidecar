"""Shared generate/edit pipeline: validate, prepare, call, fit, store.

The hosted image tools drive `ImageService` through `hosted/images.py`, whose
`AssetSink` stores every result (and a masked edit's unblended layer) as an asset.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image, UnidentifiedImageError

from .catalog import Catalog, ModelCapabilities, batch_sizes, validate_params
from .client import GeneratedImage, GenerationResult, OpenRouterClient
from .config import Settings
from .errors import (
    AuthRequiredError,
    BadRequestError,
    InsufficientCreditsError,
    ModerationError,
    OpenRouterError,
    ProviderError,
)
from .imaging import (
    JPEG_QUALITY,
    FitPlan,
    FitReport,
    InputError,
    PreparedImage,
    apply_fit,
    check_mask,
    composite_mask,
    make_preview,
    pad_to_ratio,
    plan_fit,
    prepare_input,
    reject_network_path,
)
from .outputs import encode_image, prompt_slug

log = logging.getLogger(__name__)

HEARTBEAT_S = 10
KEY_INFO_TTL_S = 60
PREVIEW_LIMIT = 4
SVG_NOTE = "SVG output saved without preview or fitting"

# These end a run of sequential calls: retrying can't help.
_STOP_ERRORS = (AuthRequiredError, InsufficientCreditsError, ModerationError)
_EXT_BY_MEDIA = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
_EXT_BY_FORMAT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_ENCODABLE = frozenset({"png", "jpeg", "jpg", "webp"})
_R11 = (
    "aspect_ratio can't be combined with fit='preserve' (the size is taken from the input "
    "image); use fit='model' to choose the output ratio"
)

_R15 = (
    "size can't be combined with fit='preserve' (the size is taken from the input image); "
    "use fit='model' to choose the output size"
)

# (progress, total, message). `progress` strictly increases within one run; `total`
# is the number of calls, and call k (0-based) reports values in (k, k + 1].
ProgressFn = Callable[[float, float, str], Awaitable[None]]


@dataclass
class SavedImage:
    asset_id: str  # what the output sink stored it as
    preview_jpeg: bytes | None
    cost_usd: float | None
    note: str | None


class OutputSink(Protocol):
    """Stores one result and returns its id (hosted: `hosted.images.AssetSink`)."""

    def save(self, job: Any, *, data: bytes, ext: str, unmasked: bytes | None, meta: dict,
             index: int) -> str: ...


@dataclass
class ServiceResult:
    images: list[SavedImage]
    failures: list[str]
    notes: list[str]
    model: str
    provider: str | None
    call_cost_usd: float | None
    usage_line: str
    elapsed_s: float


@dataclass
class _Job:
    """One validated generate/edit invocation, ready to send."""

    tool: Literal["generate_image", "edit_image"]
    prompt: str
    model: ModelCapabilities
    n: int
    send: dict[str, Any]  # request params as sent (None = omitted)
    provider_options: dict | None  # flat, as the user gave them (recorded in the sidecar)
    data_urls: list[str]
    inputs: list[PreparedImage]
    sink: OutputSink
    stem: str
    notes: list[str]
    plan: FitPlan | None = None  # set only when outputs are post-processed (fit="preserve")
    mask: Path | None = None
    mask_feather_px: int | None = None
    fit_model: bool = False
    previews: int = 0
    provider: dict | None = None  # the request's `provider` object: options keyed by slug


def _error_text(exc: Exception) -> str:
    return exc.message if isinstance(exc, OpenRouterError) else str(exc)


def _padded_data_url(prepared: PreparedImage, ratio: str) -> str:
    """Pad the sent copy of an input to `ratio` and re-encode it like the original send."""
    with Image.open(BytesIO(prepared.encoded)) as sent:
        sent.load()
        padded, _ = pad_to_ratio(sent, ratio)
    buf = BytesIO()
    if prepared.mime == "image/png":
        padded.save(buf, format="PNG")
    else:
        padded.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return f"data:{prepared.mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _is_svg(img: GeneratedImage) -> bool:
    if img.media_type == "image/svg+xml":
        return True
    head = img.data[:256].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _fit_dict(r: FitReport) -> dict:
    return {
        "strategy": r.strategy,
        "requested_ratio": r.requested_ratio,
        "raw_size": list(r.raw_size),
        "final_size": list(r.final_size),
        "crop_box": list(r.crop_box) if r.crop_box is not None else None,
        "padded": r.padded,
        "upscaled": r.upscaled,
    }


def _total_cost(info: Any) -> float | None:
    """`total_cost` from a /generation response, wrapped in `data` or not."""
    if not isinstance(info, dict):
        return None
    for source in (info.get("data"), info):
        if isinstance(source, dict):
            value = source.get("total_cost")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _usage_line(cost: float | None, key: dict | None) -> str:
    line = f"This call: ${cost:.4f}" if cost is not None else "This call: cost unavailable"
    if isinstance(key, dict):
        daily, monthly = key.get("usage_daily"), key.get("usage_monthly")
        if isinstance(daily, (int, float)) and isinstance(monthly, (int, float)):
            line += f" · Key usage today: ${daily:.2f} / month: ${monthly:.2f}"
    return line


async def _notify(progress: ProgressFn | None, value: float, total: float, message: str) -> None:
    """Send a progress update; a failed notification never aborts a paid generation."""
    if progress is None:
        return
    try:
        await progress(value, total, message)
    except Exception:
        log.warning("Progress notification failed", exc_info=True)


async def _heartbeat(
    progress: ProgressFn, done: int, total: int, timeout_s: float, model: str, start: float
) -> None:
    """Report `done + elapsed / (elapsed + timeout_s)`: it grows with every beat but
    stays below `done + 1`, the value sent when the call finishes."""
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        elapsed = time.monotonic() - start
        value = done + elapsed / (elapsed + max(timeout_s, 1e-3))
        await _notify(progress, value, total, f"Waiting for {model}… {elapsed:.0f}s")


class ImageService:
    def __init__(
        self,
        client: OpenRouterClient,
        catalog: Catalog,
        settings: Settings,
        clock: Callable[[], datetime] = datetime.now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._settings = settings
        self._clock = clock
        self._monotonic = monotonic
        self._key_cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------ public API

    async def generate(
        self, prompt: str, model: str, *, n: int = 1, aspect_ratio: str | None = None,
        resolution: str | None = None, size: str | None = None, quality: str | None = None,
        seed: int | None = None, background: str | None = None, output_format: str | None = None,
        filename_prefix: str | None = None, provider_options: dict | None = None,
        progress: ProgressFn | None = None, sink: OutputSink,
    ) -> ServiceResult:
        send = {"aspect_ratio": aspect_ratio, "resolution": resolution, "size": size,
                "quality": quality, "seed": seed, "background": background,
                "output_format": output_format}
        m = await self._catalog.get(model)
        notes = self._validate(m, n, send, provider_options, input_count=0)
        provider = await self._provider_object(m, provider_options)
        job = _Job(
            tool="generate_image", prompt=prompt, model=m, n=n, send=send,
            provider_options=provider_options, data_urls=[], inputs=[], sink=sink,
            stem=filename_prefix or prompt_slug(prompt), notes=notes, provider=provider,
        )
        return await self._run(job, progress)

    async def edit(
        self, prompt: str, model: str, images: list[str], *, mask_path: str | None = None,
        mask_feather_px: int | None = None, fit: str = "preserve", n: int = 1,
        aspect_ratio: str | None = None, resolution: str | None = None, size: str | None = None,
        quality: str | None = None, seed: int | None = None, background: str | None = None,
        output_format: str | None = None, filename_prefix: str | None = None,
        provider_options: dict | None = None, progress: ProgressFn | None = None,
        sink: OutputSink,
    ) -> ServiceResult:
        if fit not in ("preserve", "model"):
            raise BadRequestError(f"fit must be 'preserve' or 'model', got {fit!r}")
        if not images:
            raise InputError("edit_image needs at least one input image")
        if mask_path is not None and fit == "model":
            raise InputError("mask_path requires fit='preserve'")
        if fit == "preserve" and aspect_ratio is not None:
            raise BadRequestError(_R11)
        if fit == "preserve" and size is not None:
            raise BadRequestError(_R15)
        mask = check_mask(mask_path) if mask_path is not None else None
        for p in images:  # a network path is refused before any call, and never touched
            reject_network_path(p, "images")

        send = {"aspect_ratio": aspect_ratio, "resolution": resolution, "size": size,
                "quality": quality, "seed": seed, "background": background,
                "output_format": output_format}
        m = await self._catalog.get(model)
        notes = self._validate(m, n, send, provider_options, input_count=len(images))
        provider = await self._provider_object(m, provider_options)
        prepared = [prepare_input(p, self._settings.max_input_edge) for p in images]
        primary = prepared[0]
        data_urls = [p.data_url for p in prepared]

        plan = None
        if fit == "preserve":
            plan = plan_fit(
                primary.original_size, m.aspect_ratios, m.resolutions,
                self._settings.ratio_tolerance,
            )
            send["aspect_ratio"] = plan.aspect_ratio
            if resolution is None:
                send["resolution"] = plan.resolution
            w, h = primary.original_size
            if plan.strategy == "pad":
                data_urls[0] = _padded_data_url(primary, plan.aspect_ratio)
                notes.append(
                    f"No supported aspect ratio is close to the input's {w}×{h}; padded the "
                    f"input to {plan.aspect_ratio} and cropped the result back to {w}×{h}."
                )
            elif plan.strategy == "none_supported":
                notes.append(f"{m.id} has no aspect_ratio setting; outputs were cropped to {w}×{h}.")

        job = _Job(
            tool="edit_image", prompt=prompt, model=m, n=n, send=send,
            provider_options=provider_options, data_urls=data_urls, inputs=prepared,
            sink=sink, stem=filename_prefix or primary.path.stem, notes=notes,
            plan=plan, mask=mask, mask_feather_px=mask_feather_px, fit_model=fit == "model",
            provider=provider,
        )
        return await self._run(job, progress)

    # ------------------------------------------------------------- pipeline

    @staticmethod
    def _validate(
        m: ModelCapabilities, n: int, send: dict, provider_options: dict | None, input_count: int
    ) -> list[str]:
        notes = validate_params(m, n=n, input_count=input_count, **send)
        if provider_options and m.route == "chat":
            raise BadRequestError(f"provider_options is not supported for {m.id}")
        return notes

    async def _provider_object(
        self, m: ModelCapabilities, provider_options: dict | None
    ) -> dict | None:
        """Route flat passthrough options to every provider endpoint that allows them.

        OpenRouter keys `provider.options` by provider slug. A key that no endpoint of
        the model allows is rejected here, before anything is spent.
        """
        if not provider_options:
            return None
        allowed_by_slug: dict[str, set[str]] = {}
        for ep in await self._catalog.endpoints(m.id):
            slug = ep.get("provider_slug") if isinstance(ep, dict) else None
            allowed = ep.get("allowed_passthrough_parameters") if isinstance(ep, dict) else None
            if isinstance(slug, str) and slug and isinstance(allowed, list):
                allowed_by_slug.setdefault(slug, set()).update(str(a) for a in allowed)
        union = set().union(*allowed_by_slug.values())
        unknown = sorted(k for k in provider_options if k not in union)
        if unknown:
            if not union:
                raise BadRequestError(
                    f"{m.id} doesn't accept any provider_options (its providers allow no "
                    "passthrough parameters)"
                )
            raise BadRequestError(
                f"provider_options {', '.join(unknown)} not allowed for {m.id}. "
                f"Allowed: {', '.join(sorted(union))}"
            )
        options = {
            slug: {k: v for k, v in provider_options.items() if k in allowed}
            for slug, allowed in allowed_by_slug.items()
        }
        return {"options": {slug: opts for slug, opts in options.items() if opts}}

    async def _run(self, job: _Job, progress: ProgressFn | None) -> ServiceResult:
        start = time.monotonic()
        now = self._clock()
        sizes = batch_sizes(job.n, job.model.max_n)
        calls = len(sizes)

        saved: list[SavedImage] = []
        failures: list[str] = []
        costs: list[float | None] = []
        provider: str | None = None
        last_error: Exception | None = None
        for k, per_call_n in enumerate(sizes):
            try:
                result, elapsed = await self._call(job, per_call_n, k, calls, progress)
            except _STOP_ERRORS as exc:
                if not saved:
                    raise
                failures.append(exc.message)
                break
            except OpenRouterError as exc:
                last_error = exc
                failures.append(exc.message)
            else:
                cost = await self._call_cost(result)
                costs.append(cost)
                provider = provider or result.provider
                per_image = cost / len(result.images) if cost is not None else None
                for img in result.images:
                    try:
                        saved.append(
                            self._save(job, img, result, now, len(saved) + 1, per_image, cost,
                                       elapsed, per_call_n)
                        )
                    except (ProviderError, InputError, OSError) as exc:
                        # Failures after a paid call: keep going with the other images.
                        last_error = exc
                        failures.append(_error_text(exc))
            await _notify(progress, k + 1, calls, f"Finished call {k + 1} of {calls}")

        if not saved:
            raise last_error or ProviderError("No image was returned.")
        call_cost = None if not costs or None in costs else sum(costs)
        return ServiceResult(
            images=saved,
            failures=failures,
            notes=job.notes,
            model=job.model.id,
            provider=provider,
            call_cost_usd=call_cost,
            usage_line=_usage_line(call_cost, await self._key_usage()),
            elapsed_s=round(time.monotonic() - start, 2),
        )

    def _payload(self, job: _Job, n: int) -> dict:
        payload = {
            "model": job.model.id,
            "prompt": job.prompt,
            "n": n if n > 1 else None,
            **job.send,
            "input_references": [
                {"type": "image_url", "image_url": {"url": u}} for u in job.data_urls
            ] or None,
            "provider": job.provider,
        }
        return {k: v for k, v in payload.items() if v is not None}

    async def _call(
        self, job: _Job, n: int, done: int, total: int, progress: ProgressFn | None
    ) -> tuple[GenerationResult, float]:
        """One network call, with a progress heartbeat every HEARTBEAT_S while it runs."""
        if job.model.route == "chat":
            coro = self._client.chat_image(
                job.model.id, job.prompt, job.data_urls, job.send["aspect_ratio"]
            )
        else:
            coro = self._client.images(self._payload(job, n))
        start = time.monotonic()
        task = asyncio.create_task(coro)
        beat = (
            asyncio.create_task(_heartbeat(
                progress, done, total, self._settings.timeout_s, job.model.id, start
            ))
            if progress is not None
            else None
        )
        try:
            return await task, time.monotonic() - start
        finally:
            if not task.done():  # we were cancelled: stop the request too
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if beat is not None:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)

    async def _call_cost(self, result: GenerationResult) -> float | None:
        if result.cost_usd is not None:
            return result.cost_usd
        if not result.generation_id:
            return None
        try:
            return _total_cost(await self._client.generation(result.generation_id))
        except OpenRouterError:
            return None

    async def _key_usage(self) -> dict | None:
        now = self._monotonic()
        if self._key_cache is not None and now - self._key_cache[0] < KEY_INFO_TTL_S:
            return self._key_cache[1]
        try:
            info = await self._client.key_info()
        except Exception:  # the usage line is best effort
            log.warning("Key usage lookup failed", exc_info=True)
            return None
        self._key_cache = (now, info)
        return info

    def _save(
        self, job: _Job, img: GeneratedImage, result: GenerationResult, now: datetime,
        index: int, cost: float | None, call_cost: float | None, elapsed: float, call_n: int,
    ) -> SavedImage:
        fit: dict | None = None
        mask_meta: dict | None = None
        unmasked: bytes | None = None  # the fitted, pre-blend image (masked edits only)
        notes: list[str] = []
        preview: bytes | None = None
        if _is_svg(img):
            data, ext = img.data, ".svg"
            if SVG_NOTE not in job.notes:
                job.notes.append(SVG_NOTE)
        else:
            try:
                decoded = Image.open(BytesIO(img.data))
                decoded.load()
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
                    Image.DecompressionBombError) as exc:
                raise ProviderError(
                    f"OpenRouter returned image data that could not be read ({exc})."
                ) from None
            if job.plan is None:  # no post-processing: keep the provider's bytes
                data = img.data
                ext = _EXT_BY_MEDIA.get(img.media_type or "") or _EXT_BY_FORMAT.get(
                    decoded.format or "", f".{(decoded.format or 'png').lower()}"
                )
                final = decoded
                if job.fit_model:
                    fit = _fit_dict(FitReport("model", job.send["aspect_ratio"], decoded.size,
                                              decoded.size, None, False, False))
            else:
                if decoded.mode not in ("RGB", "RGBA"):
                    alpha = "A" in decoded.getbands() or "transparency" in decoded.info
                    decoded = decoded.convert("RGBA" if alpha else "RGB")
                final, report = apply_fit(decoded, job.plan)
                fit = _fit_dict(report)
                if report.upscaled:
                    notes.append(f"upscaled from {report.raw_size[0]}×{report.raw_size[1]}")
                if report.strategy == "none_supported":
                    notes.append(
                        f"model output {report.raw_size[0]}×{report.raw_size[1]} centre-cropped "
                        f"to {report.final_size[0]}×{report.final_size[1]}"
                    )
                if job.mask is not None:
                    # Keep the model's fitted image before the blend, lossless and at the
                    # input's exact size, so `remask_image` can re-blend it for free.
                    unmasked, _ = encode_image(final, None)
                    final, feather = composite_mask(
                        final, job.inputs[0].original, str(job.mask), job.mask_feather_px
                    )
                    mask_meta = {"path": job.mask.as_posix(), "feather_px": feather}
                fmt = job.send["output_format"]
                data, ext = encode_image(final, fmt if (fmt or "").lower() in _ENCODABLE else None)
            if job.previews < PREVIEW_LIMIT:
                preview = make_preview(final)
                job.previews += 1

        stamp = now if now.tzinfo is not None else now.astimezone()
        meta = {
            "tool": job.tool,
            "created_at": stamp.isoformat(timespec="seconds"),
            "model": job.model.id,
            "provider": result.provider,
            "prompt": job.prompt,
            "params": self._sidecar_params(job, call_n),
            "seed": job.send["seed"],
            "generation_id": result.generation_id,
            "elapsed_s": round(elapsed, 1),
            "usage": result.usage,
            "cost_usd": cost,
            "call_cost_usd": call_cost,
            "inputs": [
                {"path": p.path.as_posix(), "sha256": p.sha256, "size": list(p.original_size)}
                for p in job.inputs
            ],
            "mask": mask_meta,
            "fit": fit,
        }
        asset_id = job.sink.save(job, data=data, ext=ext, unmasked=unmasked, meta=meta, index=index)
        return SavedImage(asset_id, preview, cost, "; ".join(notes) or None)

    @staticmethod
    def _sidecar_params(job: _Job, call_n: int) -> dict:
        params = {
            "aspect_ratio": job.send["aspect_ratio"],
            "resolution": job.send["resolution"],
            "quality": job.send["quality"],
            "n": call_n,  # images requested by the call that made this one
            "seed": job.send["seed"],
            "provider_options": job.provider_options or {},
        }
        for key in ("size", "background", "output_format"):
            if job.send[key] is not None:
                params[key] = job.send[key]
        return params
