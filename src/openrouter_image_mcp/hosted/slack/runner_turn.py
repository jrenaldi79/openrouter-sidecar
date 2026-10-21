"""One admitted Slack job: session files, payload and tool loop, the answer, and its job-row bookkeeping.

`SlackRunner` makes a `Turn` once the job row exists (the row holds the chat's lease token) and
calls `answer()`; every job-row write goes through the methods here so the process id and scope
are always the job's own.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...advisor_loop import FunctionRunner, LoopResult, run_tool_loop
from ...advisor_results import HEARTBEAT_S as ROW_HEARTBEAT_S
from ...errors import OpenRouterError, ProviderError
from ...prompt_cache import configure_cache
from ...session_files import FilePacket
from .context import CONTEXT_PATH
from .payload import build_slack_payload
from .runner_steps import (
    HISTORY_CHARS,
    ChatFullError,
    panel_failure,
    step_label,
    trim_files,
)
from .slack_tools import FUNCTION_NAMES, TOOL_ERROR, ChannelTools

if TYPE_CHECKING:
    from ...advisor_store import Chat, Scope
    from .replies import WorkingMessage

log = logging.getLogger(__name__)

CLEANUP_FAILED = "Skill upload cleanup failed; no new inference was requested."
WITHHELD = "[credential withheld]"


class Turn:
    def __init__(self, services, job, key: str, scope: Scope, row: dict, working: WorkingMessage, entry,
                 runner: Any):
        self.services, self.job, self.key, self.scope, self.row = services, job, key, scope, row
        self.working, self.entry, self.runner = working, entry, runner
        self.model: str = row["model"]
        self.sandbox = False  # set once the chat is loaded: a sandbox chat that isn't a compare
        self.items: list[dict] = []  # every output item, for Task 15's sandbox outputs

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
        try:
            self.update(status="failed", error=message, cleanup_pending=self._pending())
        except OpenRouterError:  # the row is no longer ours to update: record what we can
            self.interrupt(message)

    def interrupt(self, message: str) -> None:
        """Interrupted with no answer kept (stop, shutdown, lost lease)."""
        try:
            self.services.results.interrupt(self.scope, self.row["id"], self.runner.process_id, error=message,
                                            suppress=True, cleanup_pending=self._pending())
        except OpenRouterError as error:
            log.warning("slack job interrupt not recorded: %s", type(error).__name__)

    # ---- the answer ----

    def _packets(self) -> tuple[list[FilePacket], list[str]]:
        """Trigger files that fit the session room plus the context file; overflow names join `listed`."""
        context = self.job.context
        stored = self.services.files.current(self.scope, self.job.chat_id)
        kept, overflow = trim_files(list(context.files), stored, len(context.text))
        return [*kept, FilePacket(path=CONTEXT_PATH, content=context.text)], [*context.listed, *overflow]

    async def answer(self) -> tuple[str, float | None]:
        s, job, scope, key = self.services, self.job, self.scope, self.key
        token = self.row["lease_token"]
        packets, listed = self._packets()
        chat, _, bundle, brief = s.advisors._prepare(job.chat_id, job.brief, None, key, scope, packets)
        if sum(len(message["content"]) for message in chat.history) + len(brief) > HISTORY_CHARS:
            raise ChatFullError(job.label)
        note = s.files.upsert(scope, chat.id, packets).note()  # slack/context.md replaced, never added
        self.sandbox = chat.sandbox and not job.compare
        if not await s.advisors._cleanup(key, scope, chat.id, token):
            raise ProviderError(CLEANUP_FAILED)
        try:
            result = await self._loop(chat, bundle, brief + ("\n\n" + note if note else ""), listed, token)
        finally:
            cleanup_ok = await s.advisors._cleanup(key, scope, chat.id, token)
        answer = self._final(chat, result)
        self.guard()
        s.store.append_turn(scope, chat.id, brief, answer, token)  # the plain brief, not the notes
        self.update(status="completed", answer=answer, cost_usd=result.cost_usd, cleanup_pending=not cleanup_ok)
        return answer, result.cost_usd

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
