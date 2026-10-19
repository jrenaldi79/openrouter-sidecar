"""Image input preparation and previews.

Pure module: Pillow plus the standard library only, no network, and no imports
from other package modules.
"""

from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PureWindowsPath
from typing import Literal

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "TIFF", "BMP"})
JPEG_QUALITY = 95
_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})


class InputError(ValueError):
    """A user-supplied image path or file cannot be used."""


@dataclass
class PreparedImage:
    """A source image ready to send to a model.

    `original` is the full-resolution, EXIF-rotated, mode-converted image (RGB
    or RGBA, 8-bit). `sha256` is the hex digest of the file bytes on disk.
    `encoded`/`mime` are the (possibly downscaled) bytes sent to the model, and
    `sent_size` is the size of that sent copy.
    """

    path: Path
    sha256: str
    original: Image.Image
    original_size: tuple[int, int]
    encoded: bytes
    mime: str
    sent_size: tuple[int, int]

    @property
    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.encoded).decode('ascii')}"


def is_network_path(text: str) -> bool:
    r"""True for UNC / network / device paths, on every platform.

    Windows treats '/' and '\' alike, so `\\host\share`, `//host/share`, `\/host/share`,
    `/\host\share` and `\\?\UNC\host\share` are all one thing: a path whose stat opens
    SMB and can leak the user's NTLM credentials. Never stat a path this returns True for.
    `\\?\C:\...` and `\\.\...` device paths are refused too.
    """
    return (
        text.replace("/", "\\").startswith("\\\\")
        or PureWindowsPath(text).drive.startswith("\\\\")
    )


def reject_network_path(p: str, what: str = "paths") -> None:
    """Raise InputError for a network path, before anything touches the filesystem."""
    if is_network_path(p):
        raise InputError(f"Network (UNC) paths aren't supported for {what}: {p!r}")


def resolve_user_path(p: str) -> Path:
    """Expand `~` and return an existing absolute local file path, else raise InputError.

    Network (UNC) paths are rejected without being touched.
    """
    reject_network_path(p)
    path = Path(p).expanduser()
    if not path.is_absolute():
        raise InputError(f"Path must be absolute or start with '~': {p!r}")
    if not path.exists():
        raise InputError(f"File not found: {path}")
    if not path.is_file():
        raise InputError(f"Not a file: {path}")
    return path


def check_mask(mask_path: str) -> Path:
    """Resolve the mask and prove it decodes, so a bad mask fails before any work or spend."""
    path = resolve_user_path(mask_path)
    try:
        with Image.open(path) as opened:
            opened.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise InputError(f"Cannot read mask {path} as an image: {e}") from e
    return path


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in _ALPHA_MODES or (img.mode in ("P", "L", "RGB", "1") and "transparency" in img.info)


def _to_8bit(img: Image.Image) -> Image.Image:
    """Reduce 16/32-bit integer and float modes to 8-bit grayscale ('L')."""
    if img.mode.startswith("I;16") or img.mode == "I":
        # Scale 0..65535 down to 0..255 rather than letting convert() clip.
        return img.convert("I").point(lambda v: v / 257).convert("L")
    if img.mode == "F":
        return img.convert("L")
    return img


def _normalize_mode(img: Image.Image) -> Image.Image:
    """Convert to 8-bit RGB, or RGBA if the image carries alpha."""
    has_alpha = _has_alpha(img)
    img = _to_8bit(img)
    target = "RGBA" if has_alpha else "RGB"
    return img if img.mode == target else img.convert(target)


def _resize_to_fit(img: Image.Image, max_edge: int) -> Image.Image:
    """Downscale (never upscale) so the longest edge is at most `max_edge`."""
    w, h = img.size
    longest = max(w, h)
    if longest <= max_edge:
        return img
    size = (max(1, round(w * max_edge / longest)), max(1, round(h * max_edge / longest)))
    return img.resize(size, Image.Resampling.LANCZOS)


def prepare_input(path: str, max_edge: int) -> PreparedImage:
    """Load, orient, normalise and encode a user-supplied image for sending."""
    if max_edge < 1:
        raise InputError(f"max_edge must be at least 1, got {max_edge}")
    resolved = resolve_user_path(path)
    try:
        raw = resolved.read_bytes()
    except OSError as e:
        raise InputError(f"Cannot read {resolved}: {e}") from e
    sha256 = hashlib.sha256(raw).hexdigest()

    try:
        with Image.open(BytesIO(raw)) as opened:
            source_format = opened.format
            if source_format not in SUPPORTED_FORMATS:
                raise InputError(
                    f"Unsupported image format {source_format!r}; "
                    f"supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
                )
            opened.load()
            rotated = ImageOps.exif_transpose(opened)
    except InputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise InputError(f"Cannot read {resolved} as an image: {e}") from e

    original = _normalize_mode(rotated)
    sent = _resize_to_fit(original, max_edge)

    buf = BytesIO()
    if original.mode == "RGBA" or source_format == "PNG":
        sent.save(buf, format="PNG")
        mime = "image/png"
    else:
        sent.save(buf, format="JPEG", quality=JPEG_QUALITY)
        mime = "image/jpeg"

    return PreparedImage(
        path=resolved,
        sha256=sha256,
        original=original,
        original_size=original.size,
        encoded=buf.getvalue(),
        mime=mime,
        sent_size=sent.size,
    )


def make_preview(img: Image.Image, max_edge: int = 1024, quality: int = 80) -> bytes:
    """JPEG preview of `img` with the longest edge at most `max_edge`.

    Alpha is flattened onto white; the image is never upscaled.
    """
    img = _normalize_mode(img)
    if img.mode == "RGBA":
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
    img = _resize_to_fit(img, max_edge)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# --- Aspect-ratio fitting, padding and mask compositing ---------------------

RESOLUTION_TIERS = {"512": 512, "768": 768, "1K": 1024, "1.5K": 1536, "2K": 2048, "4K": 4096}

Box = tuple[float, float, float, float]


@dataclass
class FitPlan:
    """How to make a model's output match the input's exact pixel size.

    `content_box` is only set for "pad": the original's position inside the
    padded frame, as fractions (left, top, right, bottom) of that frame.
    """

    strategy: Literal["crop_back", "pad", "none_supported", "model"]
    aspect_ratio: str | None
    resolution: str | None
    target_size: tuple[int, int]
    content_box: Box | None


@dataclass
class FitReport:
    """What `apply_fit` did. `crop_box` is in the coordinates of the image that was cropped."""

    strategy: str
    requested_ratio: str | None
    raw_size: tuple[int, int]
    final_size: tuple[int, int]
    crop_box: tuple[int, int, int, int] | None
    padded: bool
    upscaled: bool


def _parse_ratio(ratio: str) -> tuple[float, float] | None:
    """Parse "W:H" into positive floats; None for "auto" or anything malformed."""
    parts = ratio.split(":")
    if len(parts) != 2:
        return None
    try:
        w, h = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
        return None
    return w, h


def _padded_frame(size: tuple[int, int], ratio: tuple[float, float]) -> tuple[tuple[int, int], tuple[int, int]]:
    """Frame size and content offset for padding `size` out to `ratio` (never shrinks).

    The dimension that already fits is kept, the other grows, and the content is
    centred. Shared by `plan_fit` and `pad_to_ratio` so their boxes always agree.
    """
    w, h = size
    rw, rh = ratio
    if w * rh >= h * rw:  # image is wider than the ratio: add height
        frame = (w, max(h, round(w * rh / rw)))
    else:  # image is narrower than the ratio: add width
        frame = (max(w, round(h * rw / rh)), h)
    return frame, ((frame[0] - w) // 2, (frame[1] - h) // 2)


def _content_box(size: tuple[int, int], frame: tuple[int, int], offset: tuple[int, int]) -> Box:
    return (
        offset[0] / frame[0],
        offset[1] / frame[1],
        (offset[0] + size[0]) / frame[0],
        (offset[1] + size[1]) / frame[1],
    )


def _pick_resolution(target_size: tuple[int, int], resolutions: list[str] | None) -> str | None:
    tiers = sorted((RESOLUTION_TIERS[r], r) for r in set(resolutions or []) if r in RESOLUTION_TIERS)
    if not tiers:
        return None
    longest = max(target_size)
    for px, name in tiers:
        if px >= longest:
            return name
    return tiers[-1][1]


def plan_fit(
    target_size: tuple[int, int],
    aspect_ratios: list[str] | None,
    resolutions: list[str] | None,
    tolerance: float,
) -> FitPlan:
    """Choose the supported aspect ratio nearest the target and how to reconcile the rest."""
    w, h = target_size
    if w < 1 or h < 1:
        raise ValueError(f"target_size must be positive, got {target_size}")
    resolution = _pick_resolution(target_size, resolutions)

    candidates = [(s, p) for s in (aspect_ratios or []) if (p := _parse_ratio(s)) is not None]
    if not candidates:
        return FitPlan("none_supported", None, resolution, target_size, None)

    actual = w / h
    name, parsed = min(candidates, key=lambda c: abs(math.log((c[1][0] / c[1][1]) / actual)))
    wanted = parsed[0] / parsed[1]
    if abs(actual - wanted) / wanted <= tolerance:
        return FitPlan("crop_back", name, resolution, target_size, None)
    frame, offset = _padded_frame(target_size, parsed)
    return FitPlan("pad", name, resolution, target_size, _content_box(target_size, frame, offset))


def _reflect_pad(img: Image.Image, frame: tuple[int, int], offset: tuple[int, int]) -> Image.Image:
    """Place `img` on a `frame` canvas and fill the surround with mirrored copies."""
    canvas = Image.new(img.mode, frame)
    canvas.paste(img, offset)
    w, h = img.size
    x0, y0 = offset
    flipped = ImageOps.flip(img)
    # Vertical bands: alternate flipped/straight copies until the band is covered.
    for k in range(1, math.ceil(y0 / h) + 1):
        canvas.paste(flipped if k % 2 else img, (x0, y0 - k * h))
    for k in range(1, math.ceil((frame[1] - y0 - h) / h) + 1):
        canvas.paste(flipped if k % 2 else img, (x0, y0 + k * h))
    # Horizontal bands: mirror full-height columns of the (now vertically complete) canvas.
    column = canvas.crop((x0, 0, x0 + w, frame[1]))
    mirrored = ImageOps.mirror(column)
    for k in range(1, math.ceil(x0 / w) + 1):
        canvas.paste(mirrored if k % 2 else column, (x0 - k * w, 0))
    for k in range(1, math.ceil((frame[0] - x0 - w) / w) + 1):
        canvas.paste(mirrored if k % 2 else column, (x0 + k * w, 0))
    return canvas


def pad_to_ratio(img: Image.Image, ratio: str) -> tuple[Image.Image, Box]:
    """Pad `img` out to `ratio` with a mirrored, blurred surround.

    Returns the padded frame and the original's box in it (fractions), equal to
    `plan_fit(...).content_box` for the same size and ratio.
    """
    parsed = _parse_ratio(ratio)
    if parsed is None:
        raise ValueError(f"Not a usable W:H aspect ratio: {ratio!r}")
    frame, offset = _padded_frame(img.size, parsed)
    box = _content_box(img.size, frame, offset)
    if frame == img.size:
        return img.copy(), box

    band = max(offset)
    padded = _reflect_pad(img, frame, offset)
    padded = padded.filter(ImageFilter.GaussianBlur(radius=max(8, band / 4)))
    padded.paste(img, offset)
    return padded, box


def apply_fit(output: Image.Image, plan: FitPlan) -> tuple[Image.Image, FitReport]:
    """Bring a model output back to `plan.target_size`."""
    raw = output.size
    target = plan.target_size
    if plan.strategy == "model":
        return output, FitReport(plan.strategy, plan.aspect_ratio, raw, raw, None, False, False)

    if plan.strategy == "pad":
        if plan.content_box is None:
            raise ValueError("A 'pad' plan needs a content_box")
        left, top, right, bottom = plan.content_box
        crop = (round(left * raw[0]), round(top * raw[1]), round(right * raw[0]), round(bottom * raw[1]))
        cropped = output.crop(crop)
        upscaled = cropped.width < target[0] or cropped.height < target[1]
        final = cropped if cropped.size == target else cropped.resize(target, Image.Resampling.LANCZOS)
        return final, FitReport(plan.strategy, plan.aspect_ratio, raw, final.size, crop, True, upscaled)

    # crop_back and none_supported: scale to cover the target, then centre-crop.
    scale = max(target[0] / raw[0], target[1] / raw[1])
    scaled_size = (max(target[0], round(raw[0] * scale)), max(target[1], round(raw[1] * scale)))
    scaled = output if scaled_size == raw else output.resize(scaled_size, Image.Resampling.LANCZOS)
    left, top = (scaled_size[0] - target[0]) // 2, (scaled_size[1] - target[1]) // 2
    crop = (left, top, left + target[0], top + target[1])
    final = scaled.crop(crop)
    upscaled = scaled_size[0] > raw[0] or scaled_size[1] > raw[1]
    return final, FitReport(plan.strategy, plan.aspect_ratio, raw, final.size, crop, False, upscaled)


def composite_mask(
    output: Image.Image, original: Image.Image, mask_path: str, feather_px: int | None
) -> tuple[Image.Image, int]:
    """Blend `output` over `original` through a mask (white = take output, black = keep original).

    The mask is resized to the output's size and feathered. `feather_px=None`
    uses 0.5% of the short edge (at least 1). `original` is resized to the output's
    size if it differs. Returns the image and the feather used.
    """
    resolved = resolve_user_path(mask_path)
    try:
        with Image.open(resolved) as opened:
            opened.load()
            mask = ImageOps.exif_transpose(opened).convert("L")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise InputError(f"Cannot read mask {resolved} as an image: {e}") from e

    size = output.size
    feather = max(1, round(0.005 * min(size))) if feather_px is None else max(0, feather_px)

    mask = mask.resize(size, Image.Resampling.BILINEAR)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))

    output, original = _normalize_mode(output), _normalize_mode(original)
    if original.size != size:
        original = original.resize(size, Image.Resampling.LANCZOS)
    if output.mode != original.mode:
        output, original = output.convert("RGBA"), original.convert("RGBA")
    return Image.composite(output, original, mask), feather
