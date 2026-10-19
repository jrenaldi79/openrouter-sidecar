"""Image transfer for hosted assets: single-use upload URLs and signed download links.

Uploads go to our own endpoint (not a presigned bucket URL) so size, pixel count,
real image decoding and principal binding are all checked before anything is
stored. Clients without an MCP App (Claude Code, Claude Tag) upload with
`curl -T file URL`. Download links are stateless HMAC tokens, valid for 24 hours.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import time
from contextlib import contextmanager

from PIL import Image, UnidentifiedImageError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import storage
from ..advisor_store import Scope
from ..assets import Asset, AssetError, AssetStore
from ..imaging import InputError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
UPLOAD_TTL_S = 600
TICKET_GRACE_S = 86400
DOWNLOAD_TTL_S = 86400
FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class TransferError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class TransferTokens:
    def __init__(self, target, signing_key: str, public_origin: str):
        # A purpose-specific key, so upload/download tokens can't double as link tokens.
        self._key = hmac.new(signing_key.encode(), b"sidecar-image-transfer", hashlib.sha256).digest()
        self.target, self.origin, self.clock = target, public_origin, time.time

    def _sign(self, text: str) -> str:
        return _b64(hmac.new(self._key, text.encode(), hashlib.sha256).digest())

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS image_uploads (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                filename TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                expires_at REAL NOT NULL, used_at REAL)""")
            db.commit()
            yield db

    # ----------------------------------------------------------------- uploads

    def issue_upload(self, scope: Scope, filename: str, mime: str, size_bytes: int) -> dict:
        if mime not in FORMATS:
            raise InputError("Upload PNG, JPEG or WebP images (mime image/png, image/jpeg, image/webp).")
        if type(size_bytes) is not int or not 0 < size_bytes <= MAX_UPLOAD_BYTES:
            raise InputError(f"size_bytes must be 1 to {MAX_UPLOAD_BYTES} (20 MB).")
        # The name ends up in a Content-Disposition header: keep it to safe characters.
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename or "image").strip(" .")[:200] or "image"
        upload_id, expires = "upl_" + secrets.token_hex(16), int(self.clock()) + UPLOAD_TTL_S
        with self._db() as db:
            db.execute("INSERT INTO image_uploads (id,owner,workspace,filename,mime,size,expires_at) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (upload_id, scope.owner, scope.workspace, name, mime, size_bytes, expires))
        token = f"{upload_id}.{expires}.{self._sign(f'up|{upload_id}|{expires}')}"
        return {"upload_id": upload_id, "upload_url": f"{self.origin}/uploads/{token}",
                "expires_in": UPLOAD_TTL_S, "max_bytes": size_bytes}

    def claim_upload(self, token: str) -> dict:
        """Single use: the first valid PUT consumes the token, even if its body is rejected."""
        try:
            upload_id, expires, signature = token.split(".")
            fresh = int(expires) > self.clock()
        except ValueError:
            raise TransferError("Invalid upload link.") from None
        if not (hmac.compare_digest(signature, self._sign(f"up|{upload_id}|{expires}")) and fresh):
            raise TransferError("Upload link is invalid or expired; ask for a new one.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM image_uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None or row["used_at"] is not None:
                raise TransferError("Upload link was already used; ask for a new one.")
            db.execute("UPDATE image_uploads SET used_at=? WHERE id=?", (self.clock(), upload_id))
            return dict(row)

    def purge_expired_uploads(self) -> int:
        """Drop upload tickets a day after they expire (used or not)."""
        with self._db() as db:
            cutoff = (self.clock() - TICKET_GRACE_S,)
            count = db.execute("SELECT COUNT(*) AS n FROM image_uploads WHERE expires_at < ?",
                               cutoff).fetchone()["n"]
            db.execute("DELETE FROM image_uploads WHERE expires_at < ?", cutoff)
            return count

    # --------------------------------------------------------------- downloads

    def _download_sig(self, scope: Scope, asset_id: str, expires: int, part: str) -> str:
        return self._sign(f"dl|{asset_id}|{scope.owner}|{scope.workspace}|{expires}|{part}")

    def download_links(self, scope: Scope, asset: Asset) -> dict:
        expires = int(self.clock()) + DOWNLOAD_TTL_S
        urls = {part: f"{self.origin}/assets/{asset.id}.{expires}.{part}."
                      f"{self._download_sig(scope, asset.id, expires, part)}"
                for part in ("image", "json")}
        return {"asset_id": asset.id, "filename": asset.filename, "image_url": urls["image"],
                "metadata_url": urls["json"], "expires_in": DOWNLOAD_TTL_S}

    def check_download(self, token: str, assets: AssetStore) -> tuple[Scope, str, str]:
        try:
            asset_id, expires, part, signature = token.split(".")
            fresh = int(expires) > self.clock()
        except ValueError:
            raise TransferError("Not found.", 404) from None
        owner = assets.locate(asset_id) if fresh and part in ("image", "json") else None
        if owner is None or not hmac.compare_digest(
                signature, self._download_sig(owner, asset_id, int(expires), part)):
            raise TransferError("Not found.", 404)
        return owner, asset_id, part


def check_image(data: bytes, mime: str) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt, (width, height) = probe.format, probe.size
            if width * height > MAX_PIXELS:
                raise TransferError(f"Image is too large ({width}×{height}); the limit is 40 megapixels.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:  # verify() leaves the image unusable
            image.load()
    except TransferError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise TransferError("The upload is not a readable PNG, JPEG or WebP image.") from None
    if FORMATS.get(mime) != fmt:
        raise TransferError(f"The upload is {fmt}, not {mime}.")
    return width, height


async def _read_limited(request: Request, limit: int) -> bytes:
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise TransferError(f"Upload is larger than the declared {limit} bytes.", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def transfer_routes(tokens: TransferTokens, assets: AssetStore) -> list[Route]:
    async def upload(request: Request):
        try:
            row = tokens.claim_upload(request.path_params["token"])
            data = await _read_limited(request, row["size"])
            width, height = check_image(data, row["mime"])
            asset = assets.put(Scope(row["owner"], row["workspace"]), data, kind="input",
                               mime=row["mime"], filename=row["filename"], width=width, height=height)
        except TransferError as exc:
            return JSONResponse({"error": str(exc)}, exc.status, headers=_HEADERS)
        except AssetError as exc:
            return JSONResponse({"error": exc.message}, 400, headers=_HEADERS)
        return JSONResponse({"asset_id": asset.id, "width": width, "height": height,
                             "mime": asset.mime, "filename": asset.filename}, headers=_HEADERS)

    async def download(request: Request):
        try:
            scope, asset_id, part = tokens.check_download(request.path_params["token"], assets)
            data, asset = assets.get(scope, asset_id)
        except (TransferError, AssetError):
            return Response("Not found.", 404, headers=_HEADERS)
        stem = asset.filename.rsplit(".", 1)[0] or "image"
        if part == "json":
            body = json.dumps(asset.meta | {"asset_id": asset.id}, indent=2).encode()
            return Response(body, media_type="application/json", headers=_HEADERS | {
                "Content-Disposition": f'attachment; filename="{stem}.json"'})
        return Response(data, media_type=asset.mime, headers=_HEADERS | {
            "Content-Disposition": f'attachment; filename="{asset.filename}"'})

    return [Route("/uploads/{token}", upload, methods=["PUT"]),
            Route("/assets/{token}", download, methods=["GET"])]
