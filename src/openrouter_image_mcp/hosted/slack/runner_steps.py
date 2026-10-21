"""Pure helpers of the Slack runner: progress labels, Fusion panel failure, session file room, error copy.

No I/O. The server tools' item type names are matched loosely (`openrouter:web_search`,
`web_search_call`, ...) until the live spike records the exact ones (Task 0, decision record).
"""

from __future__ import annotations

from ...errors import (
    AuthRequiredError,
    ForbiddenError,
    InsufficientCreditsError,
    ModerationError,
    OpenRouterError,
)
from ...session_files import MAX_SESSION_CHARS, FilePacket
from ..limits import RateLimitedError
from ..provisioning import UNAVAILABLE
from .context import CONTEXT_PATH
from .registry import ChannelRow
from .replies import budget_text, key_text, limit_text, provider_error_text

HISTORY_CHARS = 300_000  # the advisor's own cap on stored history plus the new brief
MODERATED = "The provider flagged this request and didn't answer."
_PANEL_LISTS = ("responses", "results", "analyses", "panel")
_FAILED = frozenset({"failed", "error", "incomplete", "cancelled"})
_MODEL_CHARS = 60

# Checked in order: `web_search` before `subagent`, since a subagent item can name its tools.
_STEPS = (
    ("web_fetch", "Read a web page"),
    ("web_search", "Searched the web"),
    ("shell", "Ran code in the sandbox"),
    ("fusion", "Compared the panel's answers"),
    ("subagent", "Ran a helper"),
)


class ChatFullError(OpenRouterError):
    """The chat is full: its history plus this brief is over HISTORY_CHARS, or its session files can't
    take the new context. `message` is Slack copy."""

    def __init__(self, label: str):
        super().__init__(f"This thread's {label} chat is full. Start over with @Sidecar !restart.")


def step_label(item: dict) -> str | None:
    """The progress step for one server tool output item, or None (messages, function calls, datetime)."""
    kind = item.get("type")
    if not isinstance(kind, str) or kind.startswith("function_call"):
        return None
    return next((label for marker, label in _STEPS if marker in kind), None)


def _panelists(item: dict) -> list[dict]:
    for name in _PANEL_LISTS:
        value = item.get(name)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


def _failed(entry: dict) -> bool:
    return bool(entry.get("error")) or str(entry.get("status", "")).lower() in _FAILED


def _model(entry: dict) -> str:
    name = entry.get("model")
    return " ".join(str(name).split())[:_MODEL_CHARS] if name else "a model"


def panel_failure(items: list[dict]) -> str | None:
    """"Every model in the panel failed: …" when a Fusion item ran and none of its panelists answered."""
    for item in items:
        kind = item.get("type")
        if not isinstance(kind, str) or "fusion" not in kind:
            continue
        panel = _panelists(item)
        if panel and all(_failed(entry) for entry in panel):
            return "Every model in the panel failed: " + ", ".join(_model(e) for e in panel) + "."
    return None


def trim_files(files: list[FilePacket], stored: list[dict], context_chars: int
               ) -> tuple[list[FilePacket], list[str]]:
    """The trigger files that fit the chat's session room, in order, and the names of those that don't.

    Room is MAX_SESSION_CHARS minus the new context file and the stored files that stay: the old
    `slack/context.md` and any stored path an incoming file replaces don't count.
    """
    incoming = {packet.path for packet in files}
    staying = sum(len(row["content"]) for row in stored
                  if row["path"] != CONTEXT_PATH and row["path"] not in incoming)
    room = MAX_SESSION_CHARS - context_chars - staying
    kept, listed = [], []
    for packet in files:
        if len(packet.content) <= room:
            kept.append(packet)
            room -= len(packet.content)
        else:
            listed.append(packet.path.rsplit("/", 1)[-1])
    return kept, listed


def failure_text(error: BaseException | None, label: str, row: ChannelRow, alias: str) -> str:
    """Team-facing copy for a job that could not answer; `None` is a local failure. Never the error text."""
    if isinstance(error, RateLimitedError):
        return limit_text(error, daily_cap_usd=row.daily_cap_usd, max_concurrent=row.max_concurrent)
    if isinstance(error, (InsufficientCreditsError, ForbiddenError)):
        return budget_text()
    if isinstance(error, ModerationError):
        return MODERATED
    if isinstance(error, AuthRequiredError) or (isinstance(error, OpenRouterError)
                                                and error.message == UNAVAILABLE):
        return key_text(error)
    if isinstance(error, ChatFullError):
        return error.message
    if isinstance(error, OpenRouterError):
        return provider_error_text(label, sorted(a for a in row.alias_map if a != alias))
    return f"Sidecar · {label} hit an error and stopped. Try again."
