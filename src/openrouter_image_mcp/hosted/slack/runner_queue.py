"""In-process queueing for Slack jobs: per-chat FIFO locks, per-team slots and waits a stop can end.

Lock order is always chat first, then team slot, so a follow-up waiting for its chat never holds a
slot another chat could use. Everything here is per process: one owner replica runs Slack jobs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

ACQUIRED, TIMED_OUT, STOPPED = "acquired", "timed out", "stopped"


class Entry:
    """A queued or running job; `stop()` flags it and wakes any wait it is in."""

    def __init__(self, job: Any):
        self.job = job
        self.stopped = False
        self._event = asyncio.Event()

    def stop(self) -> None:
        self.stopped = True
        self._event.set()

    async def wait_stopped(self) -> None:
        await self._event.wait()


class Shared:
    """Per-key asyncio primitives, shared by the jobs that `take` them and dropped when the last one
    `drop`s, so the dict can't grow without bound and the next job builds a fresh one (a team slot
    count changed in the registry applies once the team's jobs have all finished)."""

    def __init__(self, make: Callable[..., Any]):
        self._make = make
        self._items: dict[Any, list] = {}  # key -> [primitive, jobs using it]

    def take(self, key: Any, *args: Any) -> Any:
        item = self._items.get(key)
        if item is None:
            item = self._items[key] = [self._make(*args), 0]
        item[1] += 1
        return item[0]

    def drop(self, key: Any) -> None:
        item = self._items[key]
        item[1] -= 1
        if item[1] == 0:
            del self._items[key]

    def __len__(self) -> int:
        return len(self._items)


def _won(getting: asyncio.Future) -> bool:
    return getting.done() and not getting.cancelled() and getting.exception() is None


async def _settle(*futures: asyncio.Future) -> None:
    for future in futures:
        if not future.done():
            future.cancel()
    await asyncio.gather(*futures, return_exceptions=True)


async def wait_turn(acquire: Callable[[], Awaitable[Any]], release: Callable[[], None], entry: Entry,
                    deadline: float) -> str:
    """Acquire a lock or semaphore by the loop-clock `deadline`, unless the entry is stopped first.

    Returns ACQUIRED (the caller now holds it), TIMED_OUT or STOPPED. Whatever happens, including
    the caller's own cancellation, a won acquisition is released unless ACQUIRED is returned.
    """
    if entry.stopped:
        return STOPPED
    getting = asyncio.ensure_future(acquire())
    stopping = asyncio.ensure_future(entry.wait_stopped())
    timeout = max(0.0, deadline - asyncio.get_running_loop().time())
    try:
        await asyncio.wait({getting, stopping}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _settle(getting, stopping)
        if _won(getting) and (entry.stopped or asyncio.current_task().cancelling()):
            release()
    if entry.stopped:
        return STOPPED
    return ACQUIRED if _won(getting) else TIMED_OUT
