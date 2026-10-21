"""The question path of a mention: which models answer, in which mode and chat, with one shared context.

The thread is gathered and rendered once per mention, always in full (pins, recent channel messages and the
whole thread: `slack/context.md` is replaced each turn, so a delta would lose what was said before), and every
model gets the same `Context`. A `compare` is one Fusion job whose outer chat runs the channel's analyst.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import OpenRouterError
from .api import SlackCallError
from .commands import Turn, UnknownAlias, resolve_alias, unknown_alias_text
from .context import Context
from .context_files import is_text_file
from .parser import Ask, Compare, wants_sandbox
from .payload import fetch_domains
from .replies import escape, provider_error_text
from .runner import SlackJob
from .threads import Mode, ThreadChat

if TYPE_CHECKING:
    from .dispatch import Deps

log = logging.getLogger(__name__)

COMPARE_ALIAS = ":fusion"  # the thread-map key of a compare chat; never a parsed alias
COMPARE_LABEL = "Fusion"
SANDBOX_OFF = "Sandbox is off on this server, so I answered without running code."
START_FAILED = "Something went wrong starting this answer. Ask again."
READ_FAILED = "I couldn't read this thread from Slack. Try again in a moment."
DEFAULT_BRIEF = "Reply to the latest message in this thread."

Say = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Pick:
    alias: str
    model: str
    label: str
    mode: Mode


def _mode(alias: str, wanted: bool, last: ThreadChat | None, enabled: bool) -> Mode:
    """Sandbox when asked for; otherwise the mode of this alias's last answer in the thread; else inline."""
    if not enabled:
        return "inline"
    if wanted:
        return "sandbox"
    if last is not None and last.alias == alias:
        return last.mode
    return "inline"


async def _ask_picks(intent: Ask, turn: Turn, deps: Deps, last: ThreadChat | None, say: Say) -> list[Pick]:
    aliases = intent.aliases or ((last.alias if last else turn.row.default_model),)
    has_text = any(is_text_file(f) for f in turn.trigger.files)
    wanted = wants_sandbox(intent.question, has_text)
    picks = []
    for alias in aliases:
        try:
            model, label = await resolve_alias(turn.row, alias, turn.services.preferences)
        except OpenRouterError as error:  # a mapped brand the catalog can't serve now: the others still answer
            log.warning("slack alias resolution failed: %s", type(error).__name__)
            await _cannot_start(turn, alias, turn.row.alias_map.get(alias, alias), say)
            continue
        picks.append(Pick(alias, model, label, _mode(alias, wanted, last, deps.sandbox_enabled)))
    if picks and wanted and not deps.sandbox_enabled:
        await say(escape(SANDBOX_OFF))
    return picks


async def _cannot_start(turn: Turn, alias: str, label: str, say: Say) -> None:
    others = [other for other in sorted(turn.row.alias_map) if other != alias]
    await say(escape(provider_error_text(label, others)))


async def _picks(intent: Ask | Compare, turn: Turn, deps: Deps, say: Say) -> tuple[list[Pick], bool]:
    """The models to run and whether this is a compare (a follow-up to a compare stays one)."""
    last = deps.threads.last_answered(turn.scope, turn.channel_id, turn.thread_ts)
    follows_compare = isinstance(intent, Ask) and not intent.aliases and last and last.alias == COMPARE_ALIAS
    if isinstance(intent, Compare) or follows_compare:
        return [Pick(COMPARE_ALIAS, turn.row.fusion_analyst, COMPARE_LABEL, "inline")], True
    return await _ask_picks(intent, turn, deps, last, say), False


async def _context(turn: Turn, deps: Deps) -> Context:
    gathered = await deps.builder.gather(turn.channel_id, turn.thread_ts, turn.trigger, first_turn=True)
    return await deps.builder.render(gathered, since_ts=None)


async def _chat_id(pick: Pick, turn: Turn, deps: Deps) -> str:
    """The thread's chat for this alias and mode, or a new one linked to the thread."""
    found = deps.threads.get(turn.scope, turn.channel_id, turn.thread_ts, pick.alias, pick.mode)
    if found is not None:
        return found.chat_id
    chat = await turn.services.advisors.start(pick.model, f"Slack · {turn.row.team_label}",
                                              sandbox=pick.mode == "sandbox")
    deps.threads.put(turn.scope, turn.channel_id, turn.thread_ts, pick.alias, pick.mode, chat["chat_id"])
    return chat["chat_id"]


async def _started(pick: Pick, turn: Turn, deps: Deps, say: Say) -> str | None:
    """The chat id, or None after a plain reply; one model failing to start never stops the others."""
    try:
        return await _chat_id(pick, turn, deps)
    except OpenRouterError as error:
        log.warning("slack chat start failed: %s", type(error).__name__)
        await _cannot_start(turn, pick.alias, pick.label, say)
    except RuntimeError as error:  # ThreadMap.put could not save the link
        log.warning("slack thread link failed: %s", type(error).__name__)
        await say(escape(START_FAILED))
    return None


async def prepare_jobs(intent: Ask | Compare, turn: Turn, deps: Deps, say: Say) -> list[SlackJob]:
    """One `SlackJob` per model that could start; every problem is answered in the thread through `say`."""
    try:
        picks, compare = await _picks(intent, turn, deps, say)
    except UnknownAlias as error:
        await say(unknown_alias_text(error))
        return []
    try:
        context = await _context(turn, deps)
    except SlackCallError as error:
        log.warning("slack thread read failed: %s", error.code)
        await say(escape(READ_FAILED))
        return []
    domains = fetch_domains(context.urls, turn.row.fetch_allowlist)
    brief = intent.question.strip() or DEFAULT_BRIEF
    jobs = []
    for pick in picks:
        chat_id = await _started(pick, turn, deps, say)
        if chat_id is not None:
            jobs.append(SlackJob(turn.principal, turn.row, turn.channel_id, turn.thread_ts, turn.user_id,
                                 pick.alias, pick.label, pick.mode, chat_id, brief, context, domains, compare))
    return jobs
