"""Output naming and encoding for image results.

Pure module: standard library plus Pillow (only for `encode_image`), no network.
Results are stored as hosted assets (`hosted/images.py`); SCHEMA_VERSION versions
their metadata.
"""

from __future__ import annotations

import re
from io import BytesIO

from PIL import Image

SCHEMA_VERSION = 1
QUALITY = 95
_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})


def model_short(model_id: str) -> str:
    """The part of a model id after the last '/'."""
    return model_id.rsplit("/", 1)[-1]


def prompt_slug(prompt: str) -> str:
    """Filesystem-safe slug from the first six words of a prompt ('image' if empty)."""
    words = prompt.lower().split()[:6]
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(words)).strip("-")
    slug = slug[:60].strip("-")
    return slug or "image"


def encode_image(img: Image.Image, output_format: str | None) -> tuple[bytes, str]:
    """Encode `img`; returns (bytes, extension including the dot). Default is PNG."""
    fmt = (output_format or "png").lower()
    buf = BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG")
        ext = ".png"
    elif fmt in ("jpeg", "jpg"):
        if img.mode in _ALPHA_MODES or "transparency" in img.info:
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
        else:
            flat = img.convert("RGB")
        flat.save(buf, format="JPEG", quality=QUALITY)
        ext = ".jpg"
    elif fmt == "webp":
        img.save(buf, format="WEBP", quality=QUALITY)
        ext = ".webp"
    else:
        raise ValueError(f"Unsupported output format: {output_format!r}")
    return buf.getvalue(), ext
