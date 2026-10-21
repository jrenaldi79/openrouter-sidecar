"""How Sidecar's text reaches Slack safely: escaping, redaction, splitting, plain-text copy, progress edits."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Collection

from ...errors import OpenRouterError
from ...logs import redact
from ..limits import RateLimitedError
from ..provisioning import DISABLED, NOT_READY, UNAVAILABLE
from .api import SlackApi, SlackCallError

log = logging.getLogger(__name__)

SPLIT_AT = 3500
MAX_PARTS = 3
ANSWER_FILE = "answer.md"
RETRY_WAIT_CAP_S = 5.0  # a 429's Retry-After is honoured up to this long before the one retry
_CUT_WINDOW = 500  # a hard cut looks for a newline or space this far back from the limit
_PARAGRAPH = re.compile(r"(?<=\n\n)")


def escape(text: str) -> str:
    """Slack's three control characters, so model output can't form mentions or disguised links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mask(text: str, secrets: Collection[str]) -> str:
    text = redact(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def clean(text: str, secrets: Collection[str]) -> str:
    """Redact known token shapes, mask the caller's own secrets, then escape for Slack."""
    return escape(_mask(text, secrets))


def _hard_cut(chunk: str) -> int:
    """Index to cut an oversized paragraph at: after the last newline or space near the limit, else the limit."""
    window = chunk[:SPLIT_AT]
    best = max(window.rfind("\n"), window.rfind(" "))
    return best + 1 if best >= SPLIT_AT - _CUT_WINDOW else SPLIT_AT


def split_answer(text: str) -> list[str]:
    """Parts of at most SPLIT_AT characters on paragraph boundaries; "".join(parts) == text.

    Empty or whitespace-only text returns []. A single paragraph over the limit is hard-cut.
    """
    if not text.strip():
        return []
    parts: list[str] = []
    current = ""
    for chunk in _PARAGRAPH.split(text):
        if len(current) + len(chunk) <= SPLIT_AT:
            current += chunk
            continue
        if current:
            parts.append(current)
        while len(chunk) > SPLIT_AT:
            cut = _hard_cut(chunk)
            parts.append(chunk[:cut])
            chunk = chunk[cut:]
        current = chunk
    if current:
        parts.append(current)
    return parts


def footer(model_label: str, cost_usd: float | None, warning: str | None = None) -> str:
    cost = "cost unknown" if cost_usd is None else f"${cost_usd:.4f}"
    line = f"{model_label} · {cost}"
    return f"{line}\n{warning}" if warning else line


def limit_text(error: RateLimitedError, *, daily_cap_usd: float = 4.0, max_concurrent: int = 3,
               user_limit: int = 10) -> str:
    if error.kind == "daily":
        return (f"Your team's daily Sidecar cap (${daily_cap_usd:g}) is reached. It resets at midnight UTC. "
                "Ask your instructor if you need more.")
    if error.kind == "concurrency":
        return f"Your team already has {max_concurrent} answers running. Try again in a minute."
    minutes = max(1, math.ceil(error.retry_after_s / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return f"You've sent {user_limit} requests in 10 minutes. Try again in about {minutes} {unit}."


def budget_text() -> str:
    return ("Your team's monthly Sidecar budget is used up. It resets at the start of next month. "
            "Ask your instructor.")


def provider_error_text(label: str, others: list[str]) -> str:
    text = f"{label} didn't answer this time."
    if others:
        return f"{text} Try another model, for example {others[0]}."
    return f"{text} Try another model."


# Team-facing copy for key problems: the provisioning messages are written for one student ("Your key").
_KEY_TEXT = {
    NOT_READY: "This channel's Sidecar key isn't ready yet. Try again in a moment.",
    DISABLED: "This channel's Sidecar key was disabled by your instructor. Ask them to re-enable it.",
    UNAVAILABLE: ("Couldn't create this channel's Sidecar key right now. Try again in a minute, and tell "
                  "your instructor if it keeps happening."),
}
_KEY_OTHER = "OpenRouter didn't accept this channel's Sidecar key. Ask your instructor."


def key_text(error: OpenRouterError) -> str:
    return _KEY_TEXT.get(error.message, _KEY_OTHER)


async def _twice(call: Callable[[], Awaitable[object]]) -> None:
    """One Slack call, retried once; a second failure propagates."""
    try:
        await call()
    except SlackCallError as error:
        log.warning("slack post failed, retrying once: %s", error.code)
        await asyncio.sleep(min(error.retry_after_s or 0.0, RETRY_WAIT_CAP_S))
        await call()


async def post_answer(api: SlackApi, channel: str, thread_ts: str, label: str, text: str, footer: str, *,
                      secrets: Collection[str] = ()) -> None:
    """Post an answer as `Sidecar · <label>` (the adapter turns unfurling off), the footer on the last part.

    Up to MAX_PARTS parts go up as messages; more go up as one `answer.md` (redacted and masked, not
    escaped: Slack doesn't parse a file for mentions) followed by the footer. Each Slack call is retried
    once, so a part never posts twice; a second `SlackCallError` propagates and the caller leaves the
    job unposted for restart recovery.
    """
    username, tail = f"Sidecar · {label}", escape(footer)
    parts = split_answer(clean(text, secrets))
    if len(parts) > MAX_PARTS:
        content = _mask(text, secrets).encode()
        await _twice(lambda: api.upload(channel, thread_ts, ANSWER_FILE, content,
                                        title=f"{username} answer", username=username))
        parts = []
    parts = parts or [""]
    parts[-1] = parts[-1] + "\n\n" + tail if parts[-1] else tail
    for part in parts:
        await _twice(lambda part=part: api.post(channel, thread_ts, part, username=username))


class WorkingMessage:
    """One progress message per model, edited at most every `every_s` seconds and only when the text changes."""

    def __init__(self, api: SlackApi, channel: str, thread_ts: str, label: str, *, every_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self._api, self._channel, self._thread_ts, self._label = api, channel, thread_ts, label
        self._every_s, self._clock = every_s, clock
        self._ts: str | None = None
        self._text = ""
        self._last_edit = 0.0

    @property
    def ts(self) -> str | None:
        return self._ts

    async def start(self) -> str:
        """Post the message; a failure propagates so the runner can handle a failed start."""
        self._text = escape(f"Sidecar · {self._label} is working…")
        self._ts = await self._api.post(self._channel, self._thread_ts, self._text,
                                        username=f"Sidecar · {self._label}")
        self._last_edit = self._clock()
        return self._ts

    async def step(self, text: str) -> None:
        """Edit when the text changed and the interval has passed; intermediate steps are dropped."""
        text, now = escape(text), self._clock()  # step text can carry model-chosen words, so no caller may forget
        if text == self._text or now - self._last_edit < self._every_s:
            return
        self._last_edit = now  # a failed edit also waits out the interval, so a 429 isn't hammered
        await self._edit(text)

    async def finish(self, text: str) -> None:
        """Always edit; never raises."""
        await self._edit(escape(text))

    async def _edit(self, text: str) -> None:
        try:
            await self._api.update(self._channel, self._ts, text)
        except SlackCallError as exc:
            log.warning("working message edit failed: %s", redact(str(exc)))
            return
        self._text = text
