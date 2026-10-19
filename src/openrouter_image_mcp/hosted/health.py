"""Liveness, readiness and the single-owner lock for the hosted server.

Background advisor jobs live in this process, so exactly one instance may serve.
On Postgres the owner holds a session-level advisory lock on a dedicated
connection; a second instance stays unready and refuses tool calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import psycopg
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import storage
from .workspaces import Gate

log = logging.getLogger(__name__)
OWNER_LOCK = 0x51DECA4
MAINTENANCE = "The Sidecar is under maintenance; retry in a few minutes."


class Health:
    def __init__(self, target, gate: Gate, *, maintenance: bool = False,
                 asset_dir: str | None = None):
        self.target, self.gate, self.maintenance = target, gate, maintenance
        self.asset_dir = asset_dir
        self._owner: psycopg.Connection | None = None

    async def start(self) -> None:
        if storage.is_postgres(self.target):
            self._owner = await asyncio.to_thread(psycopg.connect, self.target, autocommit=True)
            row = await asyncio.to_thread(
                lambda: self._owner.execute("SELECT pg_try_advisory_lock(%s)", (OWNER_LOCK,)).fetchone())
            if not row[0]:
                self.gate.reason = "Another Sidecar instance is active; this one is not serving."
                log.error("single-owner lock is held by another instance")
                return
        self.gate.reason = MAINTENANCE if self.maintenance else None

    async def stop(self) -> None:
        if self._owner is not None:
            await asyncio.to_thread(self._owner.close)  # releases the session lock
            self._owner = None

    def _database_ok(self) -> bool:
        try:
            with storage.connect(self.target) as db:
                db.execute("SELECT 1")
            return True
        except storage.DB_ERRORS:
            return False

    def _assets_ok(self) -> bool:
        """A mounted volume can be root-owned; uploads would fail with a 500 on every call."""
        if not self.asset_dir:
            return True
        try:
            Path(self.asset_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return os.access(self.asset_dir, os.W_OK | os.X_OK)

    async def ready(self) -> tuple[bool, str]:
        if not await asyncio.to_thread(self._database_ok):
            return False, "database unavailable"
        if not await asyncio.to_thread(self._assets_ok):
            return False, "asset storage is not writable"
        if self.gate.reason:
            return False, self.gate.reason
        return True, "ready"

    def register(self, server) -> None:
        @server.custom_route("/health", methods=["GET"])
        async def health(_request: Request):
            return JSONResponse({"ok": True})

        @server.custom_route("/ready", methods=["GET"])
        async def ready(_request: Request):
            ok, detail = await self.ready()
            return JSONResponse({"ready": ok, "detail": detail}, status_code=200 if ok else 503)
