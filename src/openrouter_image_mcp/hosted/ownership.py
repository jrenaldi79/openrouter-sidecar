"""The single-owner lock: one replica runs background work, and hooks follow it.

Background advisor jobs (and later the Slack gateway) live in this process, so exactly one
instance may own them. On Postgres the owner holds a session-level advisory lock on one
dedicated connection; every `every_s` it checks the connection (owner) or retries the lock
(waiting replica). On SQLite there is only one process, so it always owns.

`on_gain` hooks run on every gain, including a regain after a loss; `on_lose` hooks run once
per loss and at `stop()`. Hooks are awaited in order; one that raises is logged by exception
type and never stops the others or the loop.

Split-brain window: an owner that loses its connection (network cut, Postgres restart)
notices at its next check, within about `every_s`; Postgres frees the lock when it drops the
session. Every check and connect is bounded by `every_s`, and a failed owner reconnects and
retries the lock in the same tick, so a takeover (this replica or another) follows promptly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import psycopg

from .. import storage

log = logging.getLogger(__name__)
OWNER_LOCK = 0x51DECA4
Hook = Callable[[], Awaitable[None]]
_DRAIN_S = 2.0  # how long close waits for an in-flight call before deferring to its callback


def _quiet_close(conn: psycopg.Connection) -> None:
    try:
        conn.close()
    except (psycopg.Error, OSError):
        pass


class OwnerLock:
    def __init__(self, target, *, every_s: float = 15.0, on_gain: list[Hook] | None = None,
                 on_lose: list[Hook] | None = None):
        self.target, self.every_s = target, every_s
        self.on_gain: list[Hook] = [] if on_gain is None else on_gain
        self.on_lose: list[Hook] = [] if on_lose is None else on_lose
        self.held = False
        self._conn: psycopg.Connection | None = None
        self._task: asyncio.Task | None = None
        self._pending: asyncio.Future | None = None  # the thread call using self._conn
        self._lose_task: asyncio.Future | None = None

    async def start(self) -> None:
        if not storage.is_postgres(self.target):
            await self._gain()
            return
        await self._tick()  # decide ownership before the first request is served
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._lose()
        await self._close()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.every_s)
            await self._tick()

    async def _tick(self) -> None:
        was_held = self.held
        if not await self._attempt() and was_held:
            await self._attempt()  # retake the lock now instead of waiting a full interval

    async def _attempt(self) -> bool:
        """One check or lock try. A failure of any kind drops the connection; it never escapes."""
        try:
            if self._conn is None:
                self._conn = await self._connect()
            if self.held:
                await self._bounded(self._conn.execute, "SELECT 1")
            elif await self._bounded(self._try_lock, self._conn):
                await self._gain()
            return True
        except Exception as exc:  # noqa: BLE001 - psycopg, OS, value and unexpected errors alike
            # Type only: the message can carry the DSN.
            log.warning("owner lock check failed (%s); reconnecting", type(exc).__name__)
            await self._lose()
            await self._close()
            return False

    @staticmethod
    def _try_lock(conn: psycopg.Connection) -> bool:
        return conn.execute("SELECT pg_try_advisory_lock(%s)", (OWNER_LOCK,)).fetchone()[0]

    async def _bounded(self, fn, *args):
        """Run a blocking call on the owner connection in a thread, bounded by `every_s`."""
        fut = self._pending = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        return await asyncio.wait_for(asyncio.shield(fut), self.every_s)

    async def _connect(self) -> psycopg.Connection:
        timeout = max(1, int(self.every_s))
        fut = asyncio.ensure_future(asyncio.to_thread(
            psycopg.connect, self.target, autocommit=True, connect_timeout=timeout))
        try:
            return await asyncio.shield(fut)
        except BaseException:
            # Cancelled or failed: a late connection has no owner, so it closes itself.
            fut.add_done_callback(lambda f: None if f.cancelled() or f.exception()
                                  else _quiet_close(f.result()))
            raise

    async def _close(self) -> None:
        conn, self._conn = self._conn, None
        pending, self._pending = self._pending, None
        if conn is None:
            return
        if pending is not None and not pending.done():
            await asyncio.wait({pending}, timeout=_DRAIN_S)
            if not pending.done():  # still running: closing under it is unsafe, so wait for it
                pending.add_done_callback(lambda _f: _quiet_close(conn))
                return
        try:
            await asyncio.to_thread(conn.close)  # releases the session lock
        except (psycopg.Error, OSError) as exc:
            log.warning("owner connection close failed (%s)", type(exc).__name__)

    async def _gain(self) -> None:
        if not self.held:
            self.held = True
            await self._run(self.on_gain, "gain")

    async def _lose(self) -> None:
        """Run on_lose once per loss; a cancel here cannot cut the hooks short."""
        if self.held:
            self.held = False
            self._lose_task = asyncio.ensure_future(self._run(self.on_lose, "lose"))
        if self._lose_task is not None:
            await asyncio.shield(self._lose_task)

    @staticmethod
    async def _run(hooks: list[Hook], name: str) -> None:
        for hook in list(hooks):
            try:
                await hook()
            except Exception as exc:  # noqa: BLE001 - a hook must never break the lock
                log.error("owner on_%s hook failed (%s)", name, type(exc).__name__)
