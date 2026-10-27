"""Image attachments for advisor turns: asset ids or inline base64, sent as input_image parts.

Each turn keeps only asset ids in the transcript; the bytes are read from the asset
store when a later request needs them, so follow-ups in the same chat_id can refer to
earlier images without re-sending them.
"""

from __future__ import annotations

import base64
import binascii
import io
import re

from PIL import Image, UnidentifiedImageError

from .assets import AssetError, AssetStore
from .errors import BadRequestError

MAX_IMAGES = 8
MAX_INLINE_BYTES = 4 * 1024**2
_DATA_URL = re.compile(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})\Z")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def sniff(data: bytes) -> str | None:
    """MIME type from the leading bytes, so a stated type is never trusted on its own."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def vision(row: dict) -> bool:
    return "image" in ((row.get("architecture") or {}).get("input_modalities") or [])


def _store_inline(assets: AssetStore, scope, value: str):
    match = _DATA_URL.fullmatch(value)
    if not match or len(match.group(2)) * 3 // 4 > MAX_INLINE_BYTES:
        raise BadRequestError("Inline images must be base64 PNG, JPEG, WebP or GIF data URLs "
                              "of up to 4 MB.")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except binascii.Error:
        raise BadRequestError("Inline image is not valid base64.") from None
    mime = sniff(data)
    if mime is None or mime != match.group(1):
        raise BadRequestError("Inline data is not an image of the stated type.")
    try:
        width, height = Image.open(io.BytesIO(data)).size
    except (UnidentifiedImageError, OSError):
        raise BadRequestError("Inline image could not be decoded.") from None
    return assets.put(scope, data, kind="input", mime=mime, filename="inline." + _EXTENSIONS[mime],
                      width=width, height=height)


def _check_id(assets: AssetStore, scope, asset_id: str) -> str:
    try:
        asset = assets.info(scope, asset_id)
    except AssetError:
        asset = None
    if asset is None or not asset.mime.startswith("image/"):
        raise BadRequestError(f"Image {asset_id} is not one of your images or has expired.")
    return asset_id


def image_ids(assets: AssetStore | None, scope, images) -> list[str]:
    """Validate a turn's images before any inference; inline data is stored as input assets once."""
    if not images:
        return []
    if assets is None:
        raise BadRequestError("Image attachments are not available on this server.")
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise BadRequestError(f"Attach at most {MAX_IMAGES} images per turn.")
    ids = []
    for value in images:
        if not isinstance(value, str):
            raise BadRequestError("Each image must be an asset id (img_...) or a base64 data URL.")
        ids.append(_store_inline(assets, scope, value).id if value.startswith("data:")
                   else _check_id(assets, scope, value))
    return ids


def require_vision(rows: list[dict], model: str) -> None:
    """Refuse before spending when the chat's model cannot read images."""
    row = next((item for item in rows if item.get("id") == model), None)
    if row is not None and vision(row):
        return
    suggestion = next((item["id"] for item in rows if vision(item)), None)
    hint = f" Try a vision-capable model such as {suggestion}." if suggestion else ""
    raise BadRequestError(f"{model} cannot read images; choose a model with vision: true "
                          f"from list_chat_models.{hint}")


def _image_parts(assets: AssetStore, scope, ids: list[str]) -> list[dict]:
    parts = []
    for asset_id in ids:
        data, asset = assets.get(scope, asset_id)
        parts.append({"type": "input_image",
                      "image_url": f"data:{asset.mime};base64," + base64.b64encode(data).decode()})
    return parts


def _content(assets: AssetStore, scope, text: str, ids: list[str]):
    if not ids:
        return text
    return [{"type": "input_text", "text": text}, *_image_parts(assets, scope, ids)]


def turn_content(assets: AssetStore | None, scope, history: list[dict], brief: str, ids: list[str]):
    """Rebuild earlier image turns from their ids, and the new turn's content with its images."""
    turn = _content(assets, scope, brief, ids)
    if not any(message.get("images") for message in history):
        return history, turn
    rebuilt = [{"role": message["role"],
                "content": _content(assets, scope, message["content"], message["images"])}
               if message.get("images") else {"role": message["role"], "content": message["content"]}
               for message in history]
    return rebuilt, turn
