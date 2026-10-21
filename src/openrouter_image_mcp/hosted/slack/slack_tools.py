"""Function tools a Slack side-advisor model calls to read more of its own channel.

Server-side, bound to the job's channel: no argument names a channel, so a forged `channel` key is
ignored, never used. Output is data for the model (`DATA_PREFACE`); every failure becomes the tool's
output text (`Error: ...`, only a Slack error code or an exception type name), never an exception.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

from .api import SlackApi, SlackCallError, SlackMessage
from .context import (
    DATA_PREFACE,
    DROPPED_SUBTYPES,
    NameCache,
    format_message,
    is_channel_message,
    newest_within,
    ts_key,
)
from .parser import decode_entities

log = logging.getLogger(__name__)

READ_LIMIT = 200  # most messages one call returns
CHARS_CAP = 30_000  # most message characters one call returns (the newest are kept)
SEARCH_PAGES = 5
SEARCH_MESSAGES = 1000
READ_DEFAULT = 50
SEARCH_DEFAULT = 20
HISTORY_PAGE_MIN = 50
HISTORY_PAGES = 5
PAGE = 200
THREAD_PAGES = 10
LABEL_QUERY_CHARS = 40
TOOL_ERROR = "Tool error"
_TS = re.compile(r"[0-9]{1,12}\.[0-9]{1,8}")  # not \d: that accepts non-ASCII digits

_TS_PROP = {"type": "string", "description": "A Slack message timestamp such as 1700000000.000100."}
_LIMIT_PROP = {"type": "integer", "description": f"How many messages to return (1 to {READ_LIMIT})."}


def _spec(name: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    parameters = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        parameters["required"] = list(required)
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


TOOL_SPECS: list[dict] = [
    _spec("read_thread",
          "Read messages from a thread in this Slack channel (default: the current thread). Returns the newest "
          "`limit` messages; pass `before` (a ts) to page into earlier ones.",
          {"thread_ts": _TS_PROP, "before": _TS_PROP, "limit": _LIMIT_PROP}),
    _spec("read_channel_history",
          "Read top-level messages from this Slack channel, newest first by default. Pass `before` (a ts) to "
          "page into earlier ones.",
          {"before": _TS_PROP, "limit": _LIMIT_PROP}),
    _spec("search_channel",
          "Search this Slack channel's recent messages for text. Every word of `query` must appear "
          "(any case). Searches the newest 1,000 messages only.",
          {"query": {"type": "string", "description": "Words to look for."}, "limit": _LIMIT_PROP}, ("query",)),
]
FUNCTION_NAMES = frozenset(spec["name"] for spec in TOOL_SPECS)


class _BadArguments(Exception):
    """A short, model-readable reason (never exception text from elsewhere)."""


def _parse(arguments: str | None) -> dict:
    if arguments is None or (isinstance(arguments, str) and not arguments.strip()):
        return {}
    try:
        data = json.loads(arguments)
    except (ValueError, TypeError, RecursionError):
        raise _BadArguments("bad arguments") from None
    if not isinstance(data, dict):
        raise _BadArguments("bad arguments")
    return data


def _ts_arg(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _TS.fullmatch(value):  # fullmatch: `$` would pass a trailing newline
        raise _BadArguments("bad timestamp")
    return value


def _limit_arg(args: dict, default: int) -> int:
    value = args.get("limit")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(1, min(READ_LIMIT, value))
    return default


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _before(args: dict, key: str = "before") -> Callable[[SlackMessage], bool]:
    cutoff = _ts_arg(args, key)
    return lambda m: cutoff is None or ts_key(m.ts) < ts_key(cutoff)


Footer = Callable[[list[SlackMessage], int], list[str]]  # (messages shown, messages cut by the char cap) -> lines


def _more(tool: str, left_out: int, exact: bool, extra: str = "", notes: tuple[str, ...] = ()) -> Footer:
    """Tells the model how many earlier messages were left out (limit, cap or unread pages) and the `before` to use."""

    def footer(shown: list[SlackMessage], cut: int) -> list[str]:
        missing = left_out + cut
        if not shown or (exact and not missing):
            return list(notes)
        how = f'To continue, call {tool} with {extra}before="{shown[0].ts}".'
        if missing:
            count = _count(missing, "earlier message", "earlier messages")
            return [f"[{'' if exact else 'At least '}{count} not shown. {how}]", *notes]
        return [f"[Older messages may exist. {how}]", *notes]

    return footer


class ChannelTools:
    def __init__(self, api: SlackApi, names: NameCache, channel_id: str, thread_ts: str):
        self._api, self._names, self._channel, self._thread_ts = api, names, channel_id, thread_ts
        self._handlers: dict[str, Callable[[dict], Awaitable[tuple[str, str]]]] = {
            "read_thread": self._read_thread,
            "read_channel_history": self._read_history,
            "search_channel": self._search,
        }

    async def call(self, name: str, arguments: str) -> tuple[str, str]:
        """Run one tool. Returns (output text, step label); never raises."""
        try:
            handler = self._handlers.get(name)
            if handler is None:
                raise _BadArguments("unknown tool")
            return await handler(_parse(arguments))
        except _BadArguments as error:
            return f"Error: {error}", TOOL_ERROR
        except SlackCallError as error:
            log.warning("slack tool %s failed: %s", name, error.code)
            return f"Error: Slack said {str(error.code)[:60]}", TOOL_ERROR
        except Exception as error:  # noqa: BLE001  the model gets the type name only; the redacting log keeps the rest
            log.warning("slack tool %s failed: %s: %s", name, type(error).__name__, error)
            return f"Error: {type(error).__name__}", TOOL_ERROR

    # --- tools ------------------------------------------------------------------------------

    async def _read_thread(self, args: dict) -> tuple[str, str]:
        thread_ts = _ts_arg(args, "thread_ts") or self._thread_ts
        older, limit = _before(args), _limit_arg(args, READ_DEFAULT)
        found, exhausted = await self._thread(thread_ts)
        eligible = sorted((m for m in found if m.subtype not in DROPPED_SUBTYPES and older(m)),
                          key=lambda m: ts_key(m.ts))
        shown = eligible[-limit:]
        extra = f'thread_ts="{thread_ts}", ' if thread_ts != self._thread_ts else ""
        notes = () if exhausted else (f"[Only the first {THREAD_PAGES * PAGE} thread messages were read.]",)
        footer = _more("read_thread", len(eligible) - len(shown), exhausted, extra, notes)
        return await self._output(shown, footer), "Read the thread"

    async def _read_history(self, args: dict) -> tuple[str, str]:
        before, limit = _ts_arg(args, "before"), _limit_arg(args, READ_DEFAULT)
        found, exhausted = await self._scan(before, max(limit + 1, HISTORY_PAGE_MIN), HISTORY_PAGES, limit + 1)
        shown = sorted(found, key=lambda m: ts_key(m.ts))[-limit:]
        out = await self._output(shown, _more("read_channel_history", len(found) - len(shown), exhausted))
        return out, f"Read {_count(len(shown), 'earlier message', 'earlier messages')}" if shown else "Read the channel history"

    async def _search(self, args: dict) -> tuple[str, str]:
        query = args.get("query")
        terms = query.lower().split() if isinstance(query, str) else []
        if not terms:
            raise _BadArguments("query is required")
        limit = _limit_arg(args, SEARCH_DEFAULT)
        found, exhausted = await self._scan(None, PAGE, SEARCH_PAGES, SEARCH_MESSAGES)
        searched = found[:SEARCH_MESSAGES]
        exhausted = exhausted and len(searched) == len(found)
        hits = [m for m in searched if all(t in decode_entities(m.text).lower() for t in terms)]
        shown = sorted(hits, key=lambda m: ts_key(m.ts))[-limit:]
        unshown = len(hits) - len(shown)
        out = await self._output(shown, lambda _kept, cut: _search_notes(unshown, cut, searched, exhausted))
        return out, f"Searched the channel for '{_clip(query)}'"

    # --- reading ----------------------------------------------------------------------------

    async def _thread(self, thread_ts: str) -> tuple[list[SlackMessage], bool]:
        by_ts: dict[str, SlackMessage] = {}
        cursor = None
        for _ in range(THREAD_PAGES):
            page = await self._api.replies(self._channel, thread_ts, cursor=cursor, limit=PAGE)
            for message in page.messages:
                by_ts.setdefault(message.ts, message)
            cursor = page.next_cursor
            if not cursor:
                break
        return list(by_ts.values()), cursor is None

    async def _scan(self, before: str | None, page_size: int, max_pages: int, want: int
                    ) -> tuple[list[SlackMessage], bool]:
        """Top-level channel messages older than `before`, newest first, until `want` are found or the pages run
        out. The flag is True only when the channel's history was read to its start."""
        found: dict[str, SlackMessage] = {}  # by ts, like `_thread`: a repeated page must not count twice
        older = _before({"before": before})
        cursor = None
        for _ in range(max_pages):
            page = await self._api.history(self._channel, latest=before, cursor=cursor, limit=page_size)
            for m in page.messages:
                if is_channel_message(m) and older(m):
                    found.setdefault(m.ts, m)
            cursor = page.next_cursor
            if not cursor or len(found) >= want:
                break
        return sorted(found.values(), key=lambda m: ts_key(m.ts), reverse=True), cursor is None

    async def _output(self, shown: list[SlackMessage], footer: Footer) -> str:
        """The preface, `shown` (oldest first) trimmed to CHARS_CAP keeping the newest, then the footer lines."""
        lines = [await self._line(m) for m in shown]
        kept = newest_within(lines, CHARS_CAP)
        cut = len(lines) - len(kept)
        return "\n".join([DATA_PREFACE, *(kept or ["No messages found."]), *footer(shown[cut:], cut)])

    async def _line(self, message: SlackMessage) -> str:
        name = "" if message.label or not message.user else await self._names.name(message.user)
        return format_message(message, name)


def _search_notes(over_limit: int, over_cap: int, searched: list[SlackMessage], exhausted: bool) -> list[str]:
    notes = []
    if over_limit:
        notes.append(f"[{_count(over_limit, 'more match', 'more matches')} not shown; raise limit or narrow the query.]")
    if over_cap:  # raising `limit` cannot help when the size cap did the cutting
        notes.append(f"[{_count(over_cap, 'match', 'matches')} left out to fit the size cap; narrow the query.]")
    summary = f"Searched {_count(len(searched), 'message', 'messages')} in this channel"
    if not searched:
        return [*notes, summary + "."]
    oldest = min(searched, key=lambda m: ts_key(m.ts)).ts
    if exhausted:
        return [*notes, f"[{summary}, back to the start.]"]
    older = f'Older messages were not searched; call read_channel_history with before="{oldest}" to read further.'
    return [*notes, f"[{summary}, back to ts {oldest}. {older}]"]


def _clip(query: str) -> str:
    one_line = " ".join(query.split())
    return one_line if len(one_line) <= LABEL_QUERY_CHARS else one_line[:LABEL_QUERY_CHARS - 1] + "…"
