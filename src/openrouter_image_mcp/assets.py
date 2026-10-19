"""Hosted image assets: metadata in the database, bytes in a blob store.

Every asset belongs to one owner and workspace (the same Scope as advisor chats),
expires after 30 days, and counts against a per-owner storage cap. Blobs are kept
on a local directory (a Railway volume in production; single replica).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import storage
from .advisor_store import Scope
from .errors import OpenRouterError

TTL_S = 30 * 86400
CAP_BYTES = 2 * 1024**3
KINDS = {"input", "output", "unmasked", "mask"}
_ID = re.compile(r"^img_[0-9a-f]{32}$")


class AssetError(OpenRouterError):
    """Uniform denial for absent, foreign, expired or over-quota assets."""


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str
    mime: str
    size: int
    width: int | None
    height: int | None
    filename: str
    sha256: str
    created_at: float
    expires_at: float
    parent_ids: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class LocalDirBlobs:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, blob_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_]+", blob_id):
            raise ValueError("Invalid blob id.")
        return self.root / blob_id[-2:] / blob_id

    def write(self, blob_id: str, data: bytes) -> None:
        path = self._path(blob_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)

    def read(self, blob_id: str) -> bytes:
        return self._path(blob_id).read_bytes()

    def delete(self, blob_id: str) -> None:
        self._path(blob_id).unlink(missing_ok=True)


class AssetStore:
    def __init__(self, target, blobs: LocalDirBlobs, *, ttl_s: int = TTL_S, cap_bytes: int = CAP_BYTES):
        self.target, self.blobs, self.ttl_s, self.cap_bytes = target, blobs, ttl_s, cap_bytes
        self.clock = time.time

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS image_assets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                kind TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                width INTEGER, height INTEGER, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                parent_ids TEXT NOT NULL DEFAULT '[]', meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, expires_at REAL NOT NULL)""")
            db.commit()
            yield db

    @staticmethod
    def _asset(row) -> Asset:
        return Asset(row["id"], row["kind"], row["mime"], row["size"], row["width"], row["height"],
                     row["filename"], row["sha256"], row["created_at"], row["expires_at"],
                     json.loads(row["parent_ids"]), json.loads(row["meta"]))

    def _row(self, db, scope: Scope, asset_id: str):
        row = None
        if isinstance(asset_id, str) and _ID.fullmatch(asset_id):
            row = db.execute("SELECT * FROM image_assets WHERE id=? AND owner=? AND workspace=? "
                             "AND expires_at>?", (asset_id, scope.owner, scope.workspace,
                                                  self.clock())).fetchone()
        if row is None:
            raise AssetError("Image asset is unavailable for this sign-in (unknown, expired or not yours).")
        return row

    def info(self, scope: Scope, asset_id: str) -> Asset:
        with self._db() as db:
            return self._asset(self._row(db, scope, asset_id))

    def locate(self, asset_id: str) -> Scope | None:
        """Owner of a live asset, for verifying a signed download link (no request scope)."""
        if not (isinstance(asset_id, str) and _ID.fullmatch(asset_id)):
            return None
        with self._db() as db:
            row = db.execute("SELECT owner, workspace FROM image_assets WHERE id=? AND expires_at>?",
                             (asset_id, self.clock())).fetchone()
        return Scope(row["owner"], row["workspace"]) if row else None

    def get(self, scope: Scope, asset_id: str) -> tuple[bytes, Asset]:
        asset = self.info(scope, asset_id)
        try:
            return self.blobs.read(asset.id), asset
        except OSError:
            raise AssetError("Image asset data is missing; upload or generate it again.") from None

    def put(self, scope: Scope, data: bytes, *, kind: str, mime: str, filename: str,
            width: int | None = None, height: int | None = None, parents=(),
            meta: dict | None = None) -> Asset:
        if kind not in KINDS:
            raise ValueError(f"Unknown asset kind {kind!r}.")
        now = self.clock()
        asset = Asset("img_" + secrets.token_hex(16), kind, mime, len(data), width, height,
                      filename[:200] or "image", hashlib.sha256(data).hexdigest(), now,
                      now + self.ttl_s, list(parents), meta or {})
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for parent in asset.parent_ids:
                self._row(db, scope, parent)
            used = db.execute("SELECT COALESCE(SUM(size), 0) AS n FROM image_assets WHERE owner=? "
                              "AND workspace=? AND expires_at>?",
                              (scope.owner, scope.workspace, now)).fetchone()["n"]
            if used + asset.size > self.cap_bytes:
                raise AssetError("Image storage is full for this account; old images expire after "
                                 "30 days.")
            self.blobs.write(asset.id, data)
            db.execute("INSERT INTO image_assets (id,owner,workspace,kind,mime,size,width,height,"
                       "filename,sha256,parent_ids,meta,created_at,expires_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (asset.id, scope.owner, scope.workspace, kind, mime, asset.size, width,
                        height, asset.filename, asset.sha256, json.dumps(asset.parent_ids),
                        json.dumps(asset.meta), now, asset.expires_at))
        return asset

    def expired_count(self, *, now: float | None = None) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) AS n FROM image_assets WHERE expires_at<=?",
                              (self.clock() if now is None else now,)).fetchone()["n"]

    def purge_expired(self, *, now: float | None = None, batch: int = 500) -> int:
        """Delete expired assets: blob first, then its row, so no row points at nothing."""
        now = self.clock() if now is None else now
        removed = 0
        while True:
            with self._db() as db:
                ids = [row["id"] for row in db.execute(
                    "SELECT id FROM image_assets WHERE expires_at<=? LIMIT ?", (now, batch)).fetchall()]
                for asset_id in ids:
                    self.blobs.delete(asset_id)
                    db.execute("DELETE FROM image_assets WHERE id=?", (asset_id,))
            removed += len(ids)
            if len(ids) < batch:
                return removed
