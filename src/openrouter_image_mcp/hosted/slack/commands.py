"""Every `@sidecar` command that is not a question, and alias resolution.

`run_command` returns post-ready text: each user-, model- or Slack-sourced string in it is entity-escaped
exactly once (`replies.escape`); `help_text` and `usage_text` are already safe and are returned as they are.
Commands run inside `acting_as(turn.principal)`, so the team's key and scope come from its credentials.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ...advisor_store import Scope
from ...errors import BadRequestError, OpenRouterError
from ...preferences import NO_DEFAULT
from ..principal import Principal
from .api import SlackMessage
from .parser import (
    Empty,
    Forget,
    Help,
    Intent,
    Models,
    Recall,
    Remember,
    SetDefault,
    ThreadCommand,
    Usage,
)
from .registry import ChannelRow
from .replies import escape
from .runner import SlackServices
from .usage import usage_report, usage_text
from .welcome import help_text

if TYPE_CHECKING:
    from .dispatch import Deps

log = logging.getLogger(__name__)

USAGE_FETCH_S = 10.0
EMPTY = "Ask me a question after @sidecar, or send @sidecar help to see everything I can do."
RESTARTED = "Started fresh. My next answer here rereads the thread."
NO_NOTES = "I don't have any notes for this channel yet."
USAGE_UNAVAILABLE = "I couldn't read this team's spend right now. Try again in a minute."
NO_MODEL_SET = "This alias has no model set yet. Ask your instructor."


@dataclass(frozen=True)
class Turn:
    """One mention, already gated and provisioned: who is asking, where, and with which cohort services."""

    row: ChannelRow
    principal: Principal
    scope: Scope
    channel_id: str
    thread_ts: str  # where replies go: the event's thread, or the mention itself at the top level
    user_id: str
    trigger: SlackMessage
    services: SlackServices

    @property
    def top_level(self) -> bool:
        return self.trigger.ts == self.thread_ts


class UnknownAlias(Exception):
    def __init__(self, alias: str, valid: list[str]):
        self.alias, self.valid = alias, valid
        super().__init__("Unknown model alias.")


class NoModelSet(Exception):
    """A brand alias whose model family has no default saved for this team (the ops script sets them)."""

    text = escape(NO_MODEL_SET)

    def __init__(self, alias: str):
        self.alias = alias
        super().__init__("No model set for this alias.")


def unknown_alias_text(error: UnknownAlias) -> str:
    return escape(f"I don't know the model {error.alias}. Use one of: {', '.join(error.valid)}. "
                  "An exact OpenRouter model id also works.")


async def resolve_alias(row: ChannelRow, alias: str, preferences) -> tuple[str, str]:
    """(model id, label) for an alias of this channel or an exact OpenRouter id, through the channel's
    `alias_map` and then `PreferenceService.resolve` (the model policy and catalog filters). The label is the
    brand from `alias_map`, else the catalog name. A brand whose family default the team hasn't saved raises
    `NoModelSet`."""
    valid = sorted(row.alias_map)
    brand = row.alias_map.get(alias)
    if brand is None and "/" not in alias:
        raise UnknownAlias(alias, valid)
    try:
        found = await preferences.resolve(brand or alias)
    except BadRequestError as error:
        if brand is None:  # an exact id the catalog doesn't offer
            raise UnknownAlias(alias, valid) from None
        if error.message == NO_DEFAULT:  # the team's family default is unset: our copy, never the error text
            raise NoModelSet(alias) from None
        raise
    label = brand if brand and "/" not in brand else found.get("name") or found["model"]
    return found["model"], label


def static_text(intent: Intent, row: ChannelRow) -> str | None:
    """Replies that need only the registry row (no key): help and an empty mention."""
    if isinstance(intent, Help):
        return help_text(row)
    if isinstance(intent, Empty):
        return escape(EMPTY)
    return None


async def _models(intent: Models, turn: Turn, deps: Deps) -> str:
    row, lines = turn.row, ["Models for this channel"]
    for alias in sorted(row.alias_map):
        try:
            model, label = await resolve_alias(row, alias, turn.services.preferences)
            now = f"{label} ({model})"
        except NoModelSet:
            now = f"{row.alias_map[alias]} (no model set yet)"
        except (UnknownAlias, OpenRouterError):
            now = f"{row.alias_map[alias]} (not available right now)"
        lines.append(f"• {alias} → {now}")
    lines += [f"Default: {row.default_model}", f"Fusion panel: {', '.join(row.fusion_panel)}",
              f"Fusion analyst: {row.fusion_analyst}"]
    return escape("\n".join(lines))


async def _set_default(intent: SetDefault, turn: Turn, deps: Deps) -> str:
    row = turn.row
    if intent.alias not in row.alias_map:
        return unknown_alias_text(UnknownAlias(intent.alias, sorted(row.alias_map)))
    deps.registry.update(row.slack_team_id, row.channel_id, default_model=intent.alias)
    return escape(f"The default model for this channel is now {intent.alias} ({row.alias_map[intent.alias]}).")


async def _usage(intent: Usage, turn: Turn, deps: Deps) -> str:
    key, _ = turn.services.advisors._credentials()
    try:
        info = await asyncio.wait_for(deps.runner.key_info(key), USAGE_FETCH_S)
    except Exception as error:  # noqa: BLE001 - any failure reads as "try again", never the error text
        log.warning("slack usage: key info failed: %s", type(error).__name__)
        return USAGE_UNAVAILABLE
    report = await usage_report(info, deps.jobs, turn.scope, turn.row, deps.runner.names, datetime.now(UTC))
    return usage_text(report)


async def _remember(intent: Remember, turn: Turn, deps: Deps) -> str:
    try:
        n = deps.memory.add(turn.scope, intent.note, turn.user_id)
    except BadRequestError as error:
        return escape(error.message)
    return f"Saved as note {n}."


async def _recall(intent: Recall, turn: Turn, deps: Deps) -> str:
    notes = deps.memory.list(turn.scope)
    if not notes:
        return NO_NOTES
    return escape("Notes for this channel\n" + "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)))


async def _forget(intent: Forget, turn: Turn, deps: Deps) -> str:
    try:
        note = deps.memory.forget(turn.scope, intent.n)
    except BadRequestError as error:
        return escape(error.message)
    return escape(f"Forgot note {intent.n}: {note}")


async def _thread(intent: ThreadCommand, turn: Turn, deps: Deps) -> str:
    owner = turn.scope.owner
    if intent.name == "restart":
        deps.threads.archive(turn.scope, turn.channel_id, turn.thread_ts)
        return RESTARTED
    if intent.name == "stop":
        stopped = deps.runner.stop(owner, turn.channel_id, turn.thread_ts)
        if not stopped:
            return "Nothing is running in this thread."
        return f"Stopping {stopped} answer{'' if stopped == 1 else 's'}."
    where = "this channel" if turn.top_level else "this thread"
    labels = deps.runner.running(owner, turn.channel_id, None if turn.top_level else turn.thread_ts)
    if not labels:
        return f"Nothing is running in {where}."
    return escape(f"Running in {where}: {', '.join(labels)}.")


async def _static(intent: Intent, turn: Turn, deps: Deps) -> str:
    return static_text(intent, turn.row) or ""


_HANDLERS: dict[type, Callable[..., Awaitable[str]]] = {
    Models: _models, SetDefault: _set_default, Usage: _usage, Remember: _remember, Recall: _recall,
    Forget: _forget, ThreadCommand: _thread, Help: _static, Empty: _static,
}


async def run_command(intent: Intent, turn: Turn, deps: Deps) -> str:
    """The reply to a non-ask intent, ready to post as is."""
    handler = _HANDLERS.get(type(intent))
    if handler is None:
        raise TypeError(f"{type(intent).__name__} is not a command.")
    return await handler(intent, turn, deps)
