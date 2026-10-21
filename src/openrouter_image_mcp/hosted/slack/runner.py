"""Runs one Slack ask end to end: queue, limits, job row, progress, tool loop, answer, post, stop.

Everything runs as the team (`acting_as(job.principal)`); the key and scope come from the cohort's
credential source. Advisor helpers are reused, not forked: `_prepare`, `_cleanup`,
`_sandbox_uploads`, `_check_container` and `_answer` on the cohort's `AdvisorService`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from ...advisor import AdvisorService
from ...advisor_client import AdvisorClient
from ...advisor_results import AdvisorResults
from ...advisor_store import AdvisorAccessError, AdvisorStore, Scope
from ...errors import OpenRouterError
from ...logs import redact
from ...preferences import PreferenceService
from ...session_files import SessionFiles
from ..limits import Limits, RateLimitedError
from ..principal import Principal
from .api import SlackApi, SlackCallError
from .context import Context, NameCache
from .jobs import SlackJobs
from .memory import ChannelMemory
from .principal import acting_as
from .registry import ChannelRow
from .replies import WorkingMessage, escape
from .runner_queue import ACQUIRED, STOPPED, Entry, Shared, wait_turn
from .runner_steps import failure_text
from .runner_turn import INTERRUPTED, INTERRUPTED_ERROR, LeaseLost, Turn
from .runner_turn import STOPPED as STOPPED_TEXT
from .threads import Mode, ThreadMap

log = logging.getLogger(__name__)

QUEUE_WAIT_S = 120.0
HEARTBEAT_S = 10.0
IDLE_POLL_S = 1.0
CHAT_BUSY = "Sidecar is still answering the previous question in this thread. Ask again in a moment."
START_FAILED = "The working message could not be posted to Slack."


@dataclass
class SlackServices:
    """One cohort's advisor services, as the runner needs them (Task 21 builds the real ones)."""

    advisors: AdvisorService
    preferences: PreferenceService
    store: AdvisorStore
    results: AdvisorResults
    files: SessionFiles
    client: AdvisorClient


@dataclass(frozen=True)
class SlackJob:
    principal: Principal
    row: ChannelRow
    channel_id: str
    thread_ts: str
    user_id: str
    alias: str
    label: str
    mode: Mode
    chat_id: str
    brief: str
    context: Context
    domains: list[str]
    compare: bool


class SlackRunner:
    """`clock` paces progress edits only; queue deadlines use the event loop's clock."""

    def __init__(self, api: SlackApi, limits: Limits, threads: ThreadMap, memory: ChannelMemory,
                 jobs: SlackJobs, outputs=None, *, process_id: str,
                 clock: Callable[[], float] = time.monotonic, names: NameCache | None = None):
        self.api, self.limits, self.threads, self.memory, self.jobs = api, limits, threads, memory, jobs
        self.outputs, self.process_id, self.clock = outputs, process_id, clock
        self.names = names or NameCache(api)
        self._chats = Shared(asyncio.Lock)  # chat id -> FIFO lock: one job per chat at a time, in order
        self._slots = Shared(asyncio.Semaphore)  # owner -> the team's concurrent jobs
        self._entries: dict[str, list[Entry]] = defaultdict(list)  # owner -> queued and running jobs

    # ---- thread commands (in memory: one owner replica runs every Slack job) ----

    def _in(self, owner: str, channel_id: str, thread_ts: str | None) -> list[Entry]:
        return [e for e in self._entries.get(owner, ()) if e.job.channel_id == channel_id
                and (thread_ts is None or e.job.thread_ts == thread_ts)]

    def stop(self, owner: str, channel_id: str, thread_ts: str) -> int:
        """Flag the thread's jobs: a queued one wakes and says "Stopped.", a running one stops before
        its next model request."""
        hits = [e for e in self._in(owner, channel_id, thread_ts) if not e.stopped]
        for entry in hits:
            entry.stop()
        return len(hits)

    def running(self, owner: str, channel_id: str, thread_ts: str | None = None) -> list[str]:
        """Labels of the queued and running jobs in a thread, or in the whole channel."""
        return [e.job.label for e in self._in(owner, channel_id, thread_ts)]

    # ---- queue ----

    async def run(self, services: SlackServices, job: SlackJob) -> None:
        with acting_as(job.principal):
            try:
                key, scope = services.advisors._credentials()
            except OpenRouterError as error:
                await self._say(job, failure_text(error, job.label, job.row, job.alias))
                return
            entry = Entry(job)
            self._entries[scope.owner].append(entry)
            chat_lock = self._chats.take(job.chat_id)
            slots = self._slots.take(scope.owner, job.row.max_concurrent)
            try:
                await self._queued(services, job, key, scope, entry, chat_lock, slots)
            finally:
                self._slots.drop(scope.owner)
                self._chats.drop(job.chat_id)
                self._entries[scope.owner].remove(entry)
                if not self._entries[scope.owner]:
                    del self._entries[scope.owner]

    async def _queued(self, services, job: SlackJob, key: str, scope: Scope, entry: Entry,
                      chat_lock: asyncio.Lock, slots: asyncio.Semaphore) -> None:
        """Chat first, then a team slot, both by one deadline: a waiting follow-up holds no slot."""
        deadline = asyncio.get_running_loop().time() + QUEUE_WAIT_S
        waited = await wait_turn(chat_lock.acquire, chat_lock.release, entry, deadline)
        if waited != ACQUIRED:
            await self._say(job, STOPPED_TEXT if waited == STOPPED else CHAT_BUSY)
            return
        try:
            waited = await wait_turn(slots.acquire, slots.release, entry, deadline)
            if waited != ACQUIRED:
                await self._say(job, STOPPED_TEXT if waited == STOPPED else self._busy(job))
                return
            try:
                row = await self._admitted(services, job, scope, deadline, entry)
                if row is not None:
                    working = WorkingMessage(self.api, job.channel_id, job.thread_ts, job.label, clock=self.clock)
                    await self._execute(Turn(services, job, key, scope, row, working, entry, self))
            finally:
                slots.release()
        finally:
            chat_lock.release()

    def _busy(self, job: SlackJob) -> str:
        return failure_text(RateLimitedError("Queue wait ran out.", 30, "concurrency"),
                            job.label, job.row, job.alias)

    async def _admitted(self, services, job: SlackJob, scope: Scope, deadline: float, entry: Entry):
        """The job row once limits pass and no other process holds the chat (Review Focus 3); None after
        a reply."""
        if entry.stopped:
            await self._say(job, STOPPED_TEXT)
            return None
        try:
            self.limits.admit(scope, max_concurrent=job.row.max_concurrent, daily_usd=job.row.daily_cap_usd)
            chat = services.store.get(scope, job.chat_id)
            while not entry.stopped:
                if services.results.active_for_chat(scope, job.chat_id) is None:
                    with contextlib.suppress(AdvisorAccessError):  # busy elsewhere: keep waiting
                        return services.results.create(
                            scope, chat, redact(job.brief), self.process_id,
                            slack_user_id=job.user_id, slack_channel_id=job.channel_id)
                if asyncio.get_running_loop().time() >= deadline:
                    await self._say(job, CHAT_BUSY)
                    return None
                await asyncio.sleep(IDLE_POLL_S)
        except OpenRouterError as error:
            await self._say(job, failure_text(error, job.label, job.row, job.alias))
            return None
        await self._say(job, STOPPED_TEXT)
        return None

    async def _say(self, job: SlackJob, text: str) -> None:
        """A reply in the thread before any working message exists; a Slack failure is logged."""
        try:
            await self.api.post(job.channel_id, job.thread_ts, escape(text), username=f"Sidecar · {job.label}")
        except SlackCallError as error:
            log.warning("slack reply failed: %s", error.code)

    # ---- one admitted job ----

    async def _execute(self, turn: Turn) -> None:
        try:
            ts = await turn.working.start()
        except SlackCallError as error:
            log.warning("working message failed: %s", error.code)
            turn.fail(START_FAILED)
            return
        except asyncio.CancelledError:
            turn.interrupt(INTERRUPTED_ERROR)
            raise
        work = asyncio.create_task(turn.work(ts))
        try:
            answer, problem = await self._watched(turn, work)
        except asyncio.CancelledError:  # this task was cancelled (shutdown), not the work by the heartbeat
            turn.interrupt(INTERRUPTED_ERROR)
            await turn.working.finish(INTERRUPTED)
            raise
        await turn.finish(answer, problem)

    async def _watched(self, turn: Turn, work: asyncio.Task):
        """The work's outcome as (answer, None) or (None, problem); the heartbeat is stopped before it
        returns, so no tick can race the job's ending."""
        heartbeat = asyncio.create_task(self._heartbeat(turn, work))
        try:
            return await work, None
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return None, LeaseLost()
        except Exception as error:  # noqa: BLE001 - the outcome decides the job's ending
            return None, error
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, turn: Turn, work: asyncio.Task) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_S)
                turn.beat()
        except OpenRouterError as error:  # the job lost its lease or its key: stop the work, not the runner
            log.warning("slack job heartbeat stopped: %s", type(error).__name__)
            work.cancel()
