"""The single-owner lock: one replica runs background work, and hooks follow it.

Background advisor jobs (and later the Slack gateway) live in this process, so exactly one
instance may own them. On Postgres the owner holds a session-level advisory lock on one
dedicated connection; every `every_s` it checks the connection (owner) or retries the lock
(waiting replica). On SQLite there is only one process, so it always owns.

`on_gain` hooks run on every gain, including a regain after a loss; `on_lose` hooks run once
per loss and at `stop()`. Hooks are awaited in order; one that raises is logged by exception
type and never stops the others or the loop.
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
_DRAIN_S = 2.0  # how long a cancelled call may finish its thread before we move on


async def _off_loop(fn, *args):
    """Run a blocking call in a thread; if cancelled, let the thread finish so its connection
    is not closed under it."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        await asyncio.wait({fut}, timeout=_DRAIN_S)
        raise


def _close_result(fut: asyncio.Future) -> None:
    if not fut.cancelled() and fut.exception() is None:
        fut.result().close()


class OwnerLock:
    def __init__(self, target, *, every_s: float = 15.0, on_gain: list[Hook] | None = None,
                 on_lose: list[Hook] | None = None):
        self.target, self.every_s = target, every_s
        self.on_gain: list[Hook] = [] if on_gain is None else on_gain
        self.on_lose: list[Hook] = [] if on_lose is None else on_lose
        self.held = False
        self._conn: psycopg.Connection | None = None
        self._task: asyncio.Task | None = None

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
        try:
            if self._conn is None:
                self._conn = await self._connect()
            if self.held:
                await _off_loop(self._conn.execute, "SELECT 1")
            elif await _off_loop(self._try_lock, self._conn):
                await self._gain()
        except (psycopg.Error, OSError) as exc:
            # Type only: the message can carry the DSN.
            log.warning("owner lock check failed (%s); reconnecting", type(exc).__name__)
            await self._lose()
            await self._close()

    @staticmethod
    def _try_lock(conn: psycopg.Connection) -> bool:
        return conn.execute("SELECT pg_try_advisory_lock(%s)", (OWNER_LOCK,)).fetchone()[0]

    async def _connect(self) -> psycopg.Connection:
        fut = asyncio.ensure_future(asyncio.to_thread(psycopg.connect, self.target, autocommit=True))
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            fut.add_done_callback(_close_result)  # nobody will own this connection
            raise

    async def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await asyncio.to_thread(conn.close)  # releases the session lock
            except psycopg.Error as exc:
                log.warning("owner connection close failed (%s)", type(exc).__name__)

    async def _gain(self) -> None:
        if not self.held:
            self.held = True
            await self._run(self.on_gain, "gain")

    async def _lose(self) -> None:
        if self.held:
            self.held = False
            await self._run(self.on_lose, "lose")

    @staticmethod
    async def _run(hooks: list[Hook], name: str) -> None:
        for hook in list(hooks):
            try:
                await hook()
            except Exception as exc:  # noqa: BLE001 - a hook must never break the lock
                log.error("owner on_%s hook failed (%s)", name, type(exc).__name__)
