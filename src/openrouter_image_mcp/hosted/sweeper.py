"""Hourly in-process sweep of expired image assets and upload tickets.

Asset bytes live on the serving instance's volume, so only that instance can
reclaim them (an operator's `railway run purge` can't reach the volume). Expired
assets are already unreadable; the sweep only frees disk and rows.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ..assets import AssetStore
from .uploads import TransferTokens

log = logging.getLogger(__name__)
EVERY_S = 3600.0


class Sweeper:
    def __init__(self, assets: AssetStore, transfers: TransferTokens, *, every_s: float = EVERY_S,
                 active=lambda: True):
        self.assets, self.transfers, self.every_s, self.active = assets, transfers, every_s, active
        self._task: asyncio.Task | None = None

    def sweep(self) -> dict:
        return {"image_assets": self.assets.purge_expired(),
                "image_uploads": self.transfers.purge_expired_uploads()}

    async def _loop(self) -> None:
        while True:
            if self.active():
                try:
                    report = await asyncio.to_thread(self.sweep)
                    if any(report.values()):
                        log.info("image sweep removed %s", report)
                except Exception:  # a failed sweep must never stop serving
                    log.exception("image sweep failed")
            await asyncio.sleep(self.every_s)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
