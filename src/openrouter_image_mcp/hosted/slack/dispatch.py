"""The request flow for one `@sidecar` mention: check the event, gate the channel, welcome once, parse, the
per-user limit, the team key, then a command reply or one background job per model.

Identity comes only from the signed event's team and channel ids and the registry row, never from the text.
Every reply goes in the event's thread (or under the mention at the top level) and is post-ready: the copy here
is escaped where it is composed. Job tasks are held until they finish, and their failures are logged by type.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial

from ...errors import KEY_REJECTED, OpenRouterError
from ..limits import Limits, RateLimitedError
from ..provisioning import DISABLED, NOT_READY, UNAVAILABLE, TeamKey
from .api import SlackApi, SlackCallError, SlackMessage
from .asks import Say, prepare_jobs
from .commands import Turn, run_command, static_text
from .context import ContextBuilder
from .guests import GuestGuard
from .jobs import SlackJobs
from .memory import ChannelMemory
from .parser import (
    Ask,
    Compare,
    Empty,
    Help,
    Intent,
    ThreadCommand,
    command_text,
    parse,
)
from .principal import acting_as, team_principal
from .registry import ChannelRegistry, ChannelRow
from .replies import escape, key_text, limit_text
from .runner import SlackJob, SlackRunner, SlackServices
from .runner_queue import Shared
from .settings import SlackSettings
from .threads import ThreadMap
from .welcome import NOT_SET_UP, welcome_text

log = logging.getLogger(__name__)

USERNAME = "Sidecar"
USER_SPAN_S = 600
ONLY_SOME = "Only some members of this channel can use Sidecar."
KEY_PROBLEM = "Sidecar can't reach this team's key right now. Ask your instructor."
FAILED = "Something went wrong with that request. Try again."
_KEY_MESSAGES = frozenset({NOT_READY, DISABLED, UNAVAILABLE, KEY_REJECTED})  # key_text has team copy for these
_IGNORED_SUBTYPES = frozenset({"message_changed", "message_deleted"})
_UNLIMITED = (ThreadCommand, Help, Empty)  # free and needed most when busy: outside the per-user window


@dataclass
class Deps:
    registry: ChannelRegistry
    guard: GuestGuard
    provisioner: object  # KeyProvisioner: `await ensure(principal, team=TeamKey(...))`
    runner: SlackRunner
    builder: ContextBuilder
    threads: ThreadMap
    memory: ChannelMemory
    jobs: SlackJobs
    api: SlackApi
    limits: Limits
    services: Mapping[str, SlackServices]  # by cohort
    settings: SlackSettings
    sandbox_enabled: bool
    # (owner, workspace, channel, thread_ts, alias, mode) -> lock around find-or-start of the thread's chat
    chat_locks: Shared = field(default_factory=lambda: Shared(asyncio.Lock))


def _text_id(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _ignored(event: dict) -> bool:
    """Edits, deletes and other bots' posts never start work (only new mentions by people count)."""
    return bool(event.get("edited") or event.get("subtype") in _IGNORED_SUBTYPES or event.get("bot_id"))


def _key_reply(error: Exception) -> str:
    if isinstance(error, OpenRouterError) and error.message in _KEY_MESSAGES:
        return key_text(error)
    return KEY_PROBLEM


class Dispatcher:
    def __init__(self, deps: Deps, *, bot_user_id: str):
        self.deps, self.bot_user_id = deps, bot_user_id
        self._tasks: set[asyncio.Task] = set()

    async def on_mention(self, team_id: str, event: dict) -> None:
        """Never raises: a malformed event or a storage error is logged by type, so the gateway keeps going."""
        try:
            await self._on_mention(team_id, event)
        except Exception as error:  # noqa: BLE001 - one bad event must not stop the gateway loop
            log.warning("slack mention failed: %s", type(error).__name__)

    async def _on_mention(self, team_id: str, event: dict) -> None:
        if not isinstance(event, dict) or _ignored(event):
            log.info("slack mention ignored: an edit, a delete or a bot post")
            return
        ids = [_text_id(team_id), *(_text_id(event.get(name)) for name in ("channel", "user", "ts"))]
        if None in ids or ids[2] == self.bot_user_id:
            log.info("slack mention ignored: missing ids or our own message")
            return
        team, channel, user, ts = ids
        reply_ts = _text_id(event.get("thread_ts")) or ts
        say = partial(self._say, channel, reply_ts)
        row = await self._gate(team, channel, user, say)
        if row is None:
            return
        if row.welcomed_at is None and self.deps.registry.mark_welcomed(team, channel):
            await say(escape(welcome_text(row, None)))
        text = event.get("text") if isinstance(event.get("text"), str) else ""
        intent = parse(command_text(text, self.bot_user_id), row.alias_map.keys())
        await self._handle(intent, row, event, user, reply_ts, say)

    async def drain(self) -> None:
        """Wait for every job started so far (tests, and a clean shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self, timeout_s: float) -> None:
        """At server shutdown: give running jobs `timeout_s`, then cancel the rest (the runner interrupts their
        rows, so restart recovery has nothing half-done to guess about)."""
        pending = list(self._tasks)
        if not pending:
            return
        _, still = await asyncio.wait(pending, timeout=timeout_s)
        for task in still:
            task.cancel()
        await asyncio.gather(*still, return_exceptions=True)

    async def _gate(self, team: str, channel: str, user: str, say: Say) -> ChannelRow | None:
        row = self.deps.registry.get(team, channel)
        if row is None:
            await say(escape(NOT_SET_UP))
            return None
        if not row.active:
            return None
        if row.invoker_allowlist and user not in row.invoker_allowlist:
            await say(escape(ONLY_SOME))
            return None
        if not await self.deps.guard.allowed(row):
            return None  # the guard posted the off notice when it turned the channel off
        return row

    def _over_limit(self, row: ChannelRow, user: str) -> str | None:
        settings = self.deps.settings
        try:
            self.deps.limits.window(f"slack-user|{row.slack_team_id}|{user}", "Sidecar requests",
                                    limit=settings.user_limit, span_s=USER_SPAN_S)
        except RateLimitedError as error:
            return escape(limit_text(error, daily_cap_usd=row.daily_cap_usd, max_concurrent=row.max_concurrent,
                                     user_limit=settings.user_limit))
        return None

    async def _handle(self, intent: Intent, row: ChannelRow, event: dict, user: str, reply_ts: str,
                      say: Say) -> None:
        if not isinstance(intent, _UNLIMITED) and (limited := self._over_limit(row, user)) is not None:
            await say(limited)
            return
        if (text := static_text(intent, row)) is not None:
            await say(text)
            return
        principal = team_principal(row.slack_team_id, row.channel_id, row)
        services = self.deps.services.get(row.cohort)
        if services is None:
            log.error("slack: no services for cohort %s", row.cohort)
            await say(escape(FAILED))
            return
        if not await self._provisioned(principal, row, say):
            return
        with acting_as(principal):
            try:
                _, scope = services.advisors._credentials()
            except OpenRouterError as error:
                await say(escape(_key_reply(error)))
                return
            turn = Turn(row, principal, scope, row.channel_id, reply_ts, user, SlackMessage.from_raw(event),
                        services)
            await self._respond(intent, turn, say)

    async def _provisioned(self, principal, row: ChannelRow, say: Say) -> bool:
        try:
            await self.deps.provisioner.ensure(principal, team=TeamKey(row.team_label))
        except Exception as error:  # noqa: BLE001 - every key problem gets team copy, never the error text
            log.warning("slack team key unavailable: %s", type(error).__name__)
            await say(escape(_key_reply(error)))
            return False
        return True

    async def _respond(self, intent: Intent, turn: Turn, say: Say) -> None:
        try:
            if isinstance(intent, Ask | Compare):
                for job in await prepare_jobs(intent, turn, self.deps, say):
                    self._spawn(turn.services, job)
            else:
                await say(await run_command(intent, turn, self.deps))
        except Exception as error:  # noqa: BLE001 - the thread gets a plain line, the log the type
            log.warning("slack request failed: %s", type(error).__name__)
            await say(escape(FAILED))

    def _spawn(self, services: SlackServices, job: SlackJob) -> None:
        # The copied context carries the team principal set by `acting_as`; the runner sets it again.
        task = asyncio.create_task(self._job(services, job), context=contextvars.copy_context())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _job(self, services: SlackServices, job: SlackJob) -> None:
        try:
            await self.deps.runner.run(services, job)
        except Exception as error:  # noqa: BLE001 - a background task must not die silently
            log.warning("slack job failed: %s", type(error).__name__)

    async def _say(self, channel: str, thread_ts: str, text: str) -> None:
        try:
            await self.deps.api.post(channel, thread_ts, text, username=USERNAME)
        except SlackCallError as error:
            log.warning("slack reply failed: %s", error.code)
