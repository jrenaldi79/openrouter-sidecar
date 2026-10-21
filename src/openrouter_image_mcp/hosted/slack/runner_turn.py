"""One admitted Slack job: session files, payload and tool loop, the answer, how the job ends, and its
job-row bookkeeping.

`SlackRunner` makes a `Turn` once the job row exists (the row holds the chat's lease token), runs
`work()` in its own task under a heartbeat, then hands the outcome to `finish()`. Every job-row write
goes through the methods here, so the process id and scope are always the job's own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ...advisor_loop import FunctionRunner, LoopResult, LoopStopped, run_tool_loop
from ...advisor_results import HEARTBEAT_S as ROW_HEARTBEAT_S
from ...errors import BadRequestError, ModerationError, OpenRouterError, ProviderError
from ...prompt_cache import configure_cache
from ...session_files import FilePacket
from .api import SlackCallError
from .context import CONTEXT_PATH
from .outputs import output_refs
from .payload import build_slack_payload
from .replies import footer, post_answer
from .runner_steps import (
    HISTORY_CHARS,
    ChatFullError,
    failure_text,
    panel_failure,
    step_label,
    trim_files,
)
from .slack_tools import FUNCTION_NAMES, TOOL_ERROR, ChannelTools
from .usage import answer_warning

if TYPE_CHECKING:
    from ...advisor_store import Chat, Scope
    from .replies import WorkingMessage

log = logging.getLogger(__name__)

CLEANUP_FAILED = "Skill upload cleanup failed; no new inference was requested."
WITHHELD = "[credential withheld]"
STOPPED = "Stopped."
STOPPED_ERROR = "Stopped from Slack"
INTERRUPTED = "Interrupted. Ask again."
INTERRUPTED_ERROR = "Slack job was interrupted; no automatic retry was made."
LOCAL_FAILURE = "Slack job failed locally; no automatic retry was made."
MODERATION_ERROR = "Blocked by provider moderation."


class LeaseLost(Exception):
    """The heartbeat lost the job's lease or key and cancelled the work."""


class Turn:
    def __init__(self, services, job, key: str, scope: Scope, row: dict, working: WorkingMessage, entry,
                 runner: Any):
        self.services, self.job, self.key, self.scope, self.row = services, job, key, scope, row
        self.working, self.entry, self.runner = working, entry, runner
        self.model: str = row["model"]
        self.sandbox = False  # set once the chat is loaded: a sandbox chat that isn't a compare
        self.items: list[dict] = []  # every output item, for the sandbox outputs
        self.container_id = ""  # the chat's container, set once the chat is loaded
        self.spent: float | None = None  # what the tool loop's requests cost, even when the job fails

    # ---- job row ----

    def update(self, **fields) -> None:
        self.services.results.update(self.scope, self.row["id"], self.runner.process_id, **fields)

    def guard(self) -> None:
        self.services.advisors._current(self.scope)
        self.services.results.guard(self.scope, self.row["id"], self.runner.process_id)

    def beat(self) -> None:
        self.services.advisors._current(self.scope)
        self.update(heartbeat_until=time.time() + ROW_HEARTBEAT_S)

    def _pending(self) -> bool:
        try:
            return bool(self.services.store.pending_uploads(self.scope, self.job.chat_id))
        except OpenRouterError:
            return False

    def fail(self, message: str) -> None:
        """Failed, keeping any spend already made (the daily cap sums `cost_usd`)."""
        spent = {} if self.spent is None else {"cost_usd": self.spent}
        try:
            self.update(status="failed", error=message, cleanup_pending=self._pending(), **spent)
        except OpenRouterError:  # the row is no longer ours to update: record what we can
            self.interrupt(message)

    def interrupt(self, message: str) -> None:
        """Interrupted with no answer kept (stop, shutdown, lost lease), keeping spend already made."""
        try:
            self.services.results.interrupt(self.scope, self.row["id"], self.runner.process_id, error=message,
                                            suppress=True, cleanup_pending=self._pending(),
                                            cost_usd=self.spent)
        except OpenRouterError as error:
            log.warning("slack job interrupt not recorded: %s", type(error).__name__)

    # ---- the answer ----

    def _packets(self) -> tuple[list[FilePacket], list[str]]:
        """Trigger files that fit the session room plus the context file; overflow names join `listed`."""
        context = self.job.context
        stored = self.services.files.current(self.scope, self.job.chat_id)
        kept, overflow = trim_files(list(context.files), stored, len(context.text))
        return [*kept, FilePacket(path=CONTEXT_PATH, content=context.text)], [*context.listed, *overflow]

    async def work(self, ts: str) -> tuple[str, float | None]:
        self.update(status="running", slack_working_ts=ts)
        return await self.answer()

    async def answer(self) -> tuple[str, float | None]:
        s, job, scope, key = self.services, self.job, self.scope, self.key
        token = self.row["lease_token"]
        packets, listed = self._packets()
        chat, _, bundle, brief = s.advisors._prepare(job.chat_id, job.brief, None, key, scope, packets)
        if sum(len(message["content"]) for message in chat.history) + len(brief) > HISTORY_CHARS:
            raise ChatFullError(job.label)
        try:
            note = s.files.upsert(scope, chat.id, packets).note()  # slack/context.md replaced, never added
        except BadRequestError:  # validated above, so this is the session size limit
            raise ChatFullError(job.label) from None
        self.sandbox, self.container_id = chat.sandbox and not job.compare, chat.container_id
        if not await s.advisors._cleanup(key, scope, chat.id, token):
            raise ProviderError(CLEANUP_FAILED)
        try:
            result = await self._loop(chat, bundle, brief + ("\n\n" + note if note else ""), listed, token)
        except (OpenRouterError, asyncio.CancelledError) as error:  # the loop attaches the spend so far
            self.spent = getattr(error, "cost_usd", None)
            raise
        finally:
            cleanup_ok = await self._cleanup_after(chat.id, token)
        self.spent = result.cost_usd
        answer = self._final(chat, result)
        self.guard()
        s.store.append_turn(scope, chat.id, brief, answer, token)  # the plain brief, not the notes
        self.update(status="completed", answer=answer, cost_usd=result.cost_usd, cleanup_pending=not cleanup_ok)
        return answer, result.cost_usd

    async def _cleanup_after(self, chat_id: str, token: str) -> bool:
        """Upload cleanup after the loop; while the work is being cancelled a cleanup error is logged,
        not raised, so it can't replace the cancellation (and read as a provider failure)."""
        try:
            return await self.services.advisors._cleanup(self.key, self.scope, chat_id, token)
        except Exception as error:
            if not asyncio.current_task().cancelling():
                raise
            log.warning("slack upload cleanup failed while cancelling: %s", type(error).__name__)
            return False

    async def _loop(self, chat: Chat, bundle: str, asked: str, listed: list[str], token: str) -> LoopResult:
        s, job, scope = self.services, self.job, self.scope
        current = s.files.current(scope, chat.id)
        uploads = (await s.advisors._sandbox_uploads(self.key, scope, chat, token, [], bundle, current,
                                                     self.guard) if self.sandbox else [])
        self.guard()
        payload = build_slack_payload(chat, asked, current, uploads, row=job.row, domains=job.domains,
                                      memory_text=self.runner.memory.instructions(scope),
                                      compare=job.compare, listed=listed)
        configure_cache(payload, chat.id, allow_explicit=not uploads)
        return await run_tool_loop(s.client, self.key, payload, self._functions(), on_items=self._on_items,
                                   should_stop=lambda: self.entry.stopped)

    def _final(self, chat: Chat, result: LoopResult) -> str:
        advisors = self.services.advisors
        if self.job.compare:
            failed = panel_failure(result.items)
            if failed:
                return failed
        if self.sandbox:  # every round's items, not just the last response's
            advisors._check_container({"output": result.items}, chat.container_id)
        return advisors._answer(result.body, chat.container_id).replace(self.key, WITHHELD)

    # ---- tools and progress ----

    def _functions(self) -> dict[str, FunctionRunner]:
        if self.job.compare:
            return {}
        tools = ChannelTools(self.runner.api, self.runner.names, self.job.channel_id, self.job.thread_ts)

        async def run(name: str, arguments: str) -> str:
            text, label = await tools.call(name, arguments)
            if label != TOOL_ERROR:
                await self.working.step(label)
            return text

        return dict.fromkeys(FUNCTION_NAMES, run)

    async def _on_items(self, items: list[dict]) -> None:
        self.items.extend(items)
        for item in items:
            label = step_label(item)
            if label:
                await self.working.step(label)

    # ---- how the job ends (the heartbeat is already stopped) ----

    async def finish(self, answer: tuple[str, float | None] | None, problem: BaseException | None) -> None:
        if problem is None and answer is not None:
            await self._deliver(*answer)
        elif isinstance(problem, LoopStopped):
            await self._stopped(problem)
        elif isinstance(problem, LeaseLost):
            self.interrupt(INTERRUPTED_ERROR)
            await self.working.finish(INTERRUPTED)
        else:
            await self._failed(problem)

    async def _stopped(self, stop: LoopStopped) -> None:
        """Spend already made is recorded; the answer is discarded and no turn is stored."""
        self.spent = stop.cost_usd
        self.interrupt(STOPPED_ERROR)
        await self.working.finish(STOPPED)

    async def _failed(self, error: BaseException | None) -> None:
        job = self.job
        if isinstance(error, ModerationError):
            self.fail(MODERATION_ERROR)  # its message quotes the flagged input
        elif isinstance(error, OpenRouterError):
            self.fail(error.message)
        else:
            # Never stringify an unexpected error: it could carry a key or a request body.
            log.warning("slack job failed locally: %s", type(error).__name__)
            self.fail(LOCAL_FAILURE)
            error = None
        await self.working.finish(failure_text(error, job.label, job.row, job.alias))

    async def _deliver(self, answer: str, cost: float | None) -> None:
        job, runner = self.job, self.runner
        warning = await answer_warning(runner.key_info, self.key, job.row, runner.registry)
        try:
            await post_answer(runner.api, job.channel_id, job.thread_ts, job.label, answer,
                              footer(f"{job.label} · {self.model}", cost, warning), secrets=(self.key,))
        except SlackCallError as error:  # retried once already: leave it for restart recovery
            log.warning("slack answer post failed twice: %s", error.code)
            await self.working.finish(f"Sidecar · {job.label} couldn't post its answer to Slack.")
            return
        # The answer is posted: each step after it is logged on failure, and the job always says so.
        self._logged("mark posted", runner.jobs.mark_posted, self.scope, self.row["id"])  # first: no repost
        if runner.outputs is not None and self.sandbox:
            await self._post_outputs()
        self._logged("thread answered", runner.threads.answered, self.scope, job.chat_id)
        self._logged("thread seen", runner.threads.seen, self.scope, job.chat_id, job.context.newest_ts)
        await self.working.finish(f"Sidecar · {job.label} answered below.")

    async def _post_outputs(self) -> None:
        """Sandbox files for the thread; never fails the answer."""
        job = self.job
        try:
            refs = output_refs(self.items, self.container_id)
            await self.runner.outputs.post(self.services.client, self.key, job.channel_id, job.thread_ts,
                                           job.label, self.container_id, refs)
        except Exception as error:  # noqa: BLE001
            log.warning("slack outputs failed: %s", type(error).__name__)

    @staticmethod
    def _logged(what: str, call, *args) -> None:
        try:
            call(*args)
        except Exception as error:  # noqa: BLE001 - bookkeeping after the post must not undo the answer
            log.warning("slack %s failed: %s", what, type(error).__name__)
