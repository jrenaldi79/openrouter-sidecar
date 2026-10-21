"""How Sidecar's text reaches Slack safely: escaping, redaction, splitting, plain-text copy, progress edits."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Collection

from ...logs import redact
from ..limits import RateLimitedError
from .api import SlackApi, SlackCallError

log = logging.getLogger(__name__)

SPLIT_AT = 3500
MAX_PARTS = 3
_CUT_WINDOW = 500  # a hard cut looks for a newline or space this far back from the limit
_PARAGRAPH = re.compile(r"(?<=\n\n)")


def escape(text: str) -> str:
    """Slack's three control characters, so model output can't form mentions or disguised links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean(text: str, secrets: Collection[str]) -> str:
    """Redact known token shapes, mask the caller's own secrets, then escape for Slack."""
    text = redact(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return escape(text)


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
        self._text = f"Sidecar · {self._label} is working…"
        self._ts = await self._api.post(self._channel, self._thread_ts, self._text,
                                        username=f"Sidecar · {self._label}")
        self._last_edit = self._clock()
        return self._ts

    async def step(self, text: str) -> None:
        """Edit when the text changed and the interval has passed; intermediate steps are dropped."""
        now = self._clock()
        if text == self._text or now - self._last_edit < self._every_s:
            return
        self._last_edit = now  # a failed edit also waits out the interval, so a 429 isn't hammered
        await self._edit(text)

    async def finish(self, text: str) -> None:
        """Always edit; never raises."""
        await self._edit(text)

    async def _edit(self, text: str) -> None:
        try:
            await self._api.update(self._channel, self._ts, text)
        except SlackCallError as exc:
            log.warning("working message edit failed: %s", redact(str(exc)))
            return
        self._text = text
