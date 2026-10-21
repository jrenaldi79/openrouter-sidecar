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
from ...advisor_loop import LoopStopped
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
from .replies import WorkingMessage, escape, footer, post_answer
from .runner_steps import failure_text
from .runner_turn import Turn
from .threads import Mode, ThreadMap

log = logging.getLogger(__name__)

QUEUE_WAIT_S = 120.0
HEARTBEAT_S = 10.0
IDLE_POLL_S = 1.0
STOPPED = "Stopped."
STOPPED_ERROR = "Stopped from Slack"
INTERRUPTED = "Interrupted. Ask again."
INTERRUPTED_ERROR = "Slack job was interrupted; no automatic retry was made."
LOCAL_FAILURE = "Slack job failed locally; no automatic retry was made."
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


@dataclass(eq=False)
class _Entry:
    """A job this process is queueing or running; `stopped` is read by the tool loop's `should_stop`."""

    job: SlackJob
    stopped: bool = False


class SlackRunner:
    """`clock` paces progress edits only; queue deadlines use the event loop's clock."""

    def __init__(self, api: SlackApi, limits: Limits, threads: ThreadMap, memory: ChannelMemory,
                 jobs: SlackJobs, outputs=None, *, process_id: str,
                 clock: Callable[[], float] = time.monotonic, names: NameCache | None = None):
        self.api, self.limits, self.threads, self.memory, self.jobs = api, limits, threads, memory, jobs
        self.outputs, self.process_id, self.clock = outputs, process_id, clock
        self.names = names or NameCache(api)
        self._queues: dict[str, asyncio.Semaphore] = {}
        self._entries: dict[str, list[_Entry]] = defaultdict(list)  # owner -> queued and running jobs

    # ---- thread commands (in memory: one owner replica runs every Slack job) ----

    def _in(self, owner: str, channel_id: str, thread_ts: str | None) -> list[_Entry]:
        return [e for e in self._entries.get(owner, ()) if e.job.channel_id == channel_id
                and (thread_ts is None or e.job.thread_ts == thread_ts)]

    def stop(self, owner: str, channel_id: str, thread_ts: str) -> int:
        """Flag the thread's queued and running jobs; each stops before its next model request."""
        hits = [e for e in self._in(owner, channel_id, thread_ts) if not e.stopped]
        for entry in hits:
            entry.stopped = True
        return len(hits)

    def running(self, owner: str, channel_id: str, thread_ts: str | None = None) -> list[str]:
        """Labels of the queued and running jobs in a thread, or in the whole channel."""
        return [e.job.label for e in self._in(owner, channel_id, thread_ts)]

    # ---- run ----

    async def run(self, services: SlackServices, job: SlackJob) -> None:
        with acting_as(job.principal):
            try:
                key, scope = services.advisors._credentials()
            except OpenRouterError as error:
                await self._say(job, failure_text(error, job.label, job.row, job.alias))
                return
            entry = _Entry(job)
            self._entries[scope.owner].append(entry)
            try:
                await self._queued(services, job, key, scope, entry)
            finally:
                self._entries[scope.owner].remove(entry)

    def _queue(self, owner: str, size: int) -> asyncio.Semaphore:
        return self._queues.setdefault(owner, asyncio.Semaphore(size))

    async def _queued(self, services, job: SlackJob, key: str, scope: Scope, entry: _Entry) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + QUEUE_WAIT_S
        queue = self._queue(scope.owner, job.row.max_concurrent)
        try:
            await asyncio.wait_for(queue.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            await self._say(job, self._busy(job))
            return
        try:
            row = await self._admitted(services, job, scope, deadline, entry)
            if row is not None:
                working = WorkingMessage(self.api, job.channel_id, job.thread_ts, job.label, clock=self.clock)
                await self._execute(Turn(services, job, key, scope, row, working, entry, self))
        finally:
            queue.release()

    def _busy(self, job: SlackJob) -> str:
        return failure_text(RateLimitedError("Queue wait ran out.", 30, "concurrency"),
                            job.label, job.row, job.alias)

    async def _admitted(self, services, job: SlackJob, scope: Scope, deadline: float, entry: _Entry):
        """The job row once limits pass and the chat is idle (Review Focus 3); None after a reply."""
        try:
            self.limits.admit(scope, max_concurrent=job.row.max_concurrent, daily_usd=job.row.daily_cap_usd)
            chat = services.store.get(scope, job.chat_id)
            while not entry.stopped:
                if services.results.active_for_chat(scope, job.chat_id) is None:
                    # No await since the check, so no other job here can take the chat first.
                    with contextlib.suppress(AdvisorAccessError):  # busy elsewhere: keep waiting
                        return services.results.create(
                            scope, chat, redact(job.brief), self.process_id,
                            slack_user_id=job.user_id, slack_channel_id=job.channel_id)
                if asyncio.get_running_loop().time() >= deadline:
                    await self._say(job, self._busy(job))
                    return None
                await asyncio.sleep(IDLE_POLL_S)
        except OpenRouterError as error:
            await self._say(job, failure_text(error, job.label, job.row, job.alias))
            return None
        await self._say(job, STOPPED)
        return None

    async def _say(self, job: SlackJob, text: str) -> None:
        """A reply in the thread before any working message exists; a Slack failure is logged."""
        try:
            await self.api.post(job.channel_id, job.thread_ts, escape(text), username=f"Sidecar · {job.label}")
        except SlackCallError as error:
            log.warning("slack reply failed: %s", error.code)

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
        heartbeat = asyncio.create_task(self._heartbeat(turn, asyncio.current_task()))
        try:
            turn.update(status="running", slack_working_ts=ts)
            answer, cost = await turn.answer()
        except LoopStopped as stop:
            await self._stopped(turn, stop)
            return
        except asyncio.CancelledError:
            turn.interrupt(INTERRUPTED_ERROR)
            await turn.working.finish(INTERRUPTED)
            raise
        except Exception as error:  # noqa: BLE001 - the job boundary records a safe failure
            await self._failed(turn, error)
            return
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await self._deliver(turn, answer, cost)

    async def _heartbeat(self, turn: Turn, worker: asyncio.Task) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_S)
                turn.beat()
        except OpenRouterError as error:  # the job lost its lease or its key: stop working on it
            log.warning("slack job heartbeat stopped: %s", type(error).__name__)
            worker.cancel()

    async def _stopped(self, turn: Turn, stop: LoopStopped) -> None:
        """Spend already made is recorded; the answer is discarded and no turn is stored."""
        if stop.cost_usd is not None:
            with contextlib.suppress(OpenRouterError):
                turn.update(cost_usd=stop.cost_usd)
        turn.interrupt(STOPPED_ERROR)
        await turn.working.finish(STOPPED)

    async def _failed(self, turn: Turn, error: Exception) -> None:
        job = turn.job
        if isinstance(error, OpenRouterError):
            turn.fail(error.message)
        else:
            # Never stringify an unexpected error: it could carry a key or a request body.
            log.warning("slack job failed locally: %s", type(error).__name__)
            turn.fail(LOCAL_FAILURE)
            error = None
        await turn.working.finish(failure_text(error, job.label, job.row, job.alias))

    async def _deliver(self, turn: Turn, answer: str, cost: float | None) -> None:
        job, scope = turn.job, turn.scope
        foot = footer(f"{job.label} · {turn.model}", cost)
        try:
            await post_answer(self.api, job.channel_id, job.thread_ts, job.label, answer, foot,
                              secrets=(turn.key,))
        except SlackCallError as error:  # retried once already: leave it for restart recovery
            log.warning("slack answer post failed twice: %s", error.code)
            await turn.working.finish(f"Sidecar · {job.label} couldn't post its answer to Slack.")
            return
        if self.outputs is not None and turn.sandbox:
            try:
                await self.outputs.post(turn)  # Task 15: sandbox outputs never fail the answer
            except Exception as error:  # noqa: BLE001
                log.warning("slack outputs failed: %s", type(error).__name__)
        self.jobs.mark_posted(scope, turn.row["id"])
        self.threads.answered(scope, job.chat_id)
        self.threads.seen(scope, job.chat_id, job.context.newest_ts)
        await turn.working.finish(f"Sidecar · {job.label} answered below.")
