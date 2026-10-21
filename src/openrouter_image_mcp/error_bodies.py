"""Reading OpenRouter error responses: the error object, a short message, and moderation blocks.

Shared by the image client (`client.py`) and the advisor client (`advisor_client.py`), so a
moderation-flagged 403 is a `ModerationError` on both paths.
"""

from __future__ import annotations

import httpx

from .errors import ModerationError

MAX_BODY_CHARS = 300  # raw (non-JSON) bodies quoted in error messages


def body_text(resp: httpx.Response) -> str:
    """The response body for an error message, cut short: it may be a whole HTML page."""
    text = resp.text.strip()
    return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + "…"


def error_info(resp: httpx.Response) -> tuple[dict, str]:
    """Return (error object, human message) from an error response."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return error, message
        return error, body_text(resp)
    if isinstance(error, str) and error:
        return {}, error
    return {}, body_text(resp)


def moderation_error(error: dict, message: str) -> ModerationError | None:
    metadata = error.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    reasons = metadata.get("reasons")
    if isinstance(reasons, list) and reasons:
        return ModerationError(
            [str(r) for r in reasons],
            metadata.get("flagged_input"),
            metadata.get("provider_name"),
        )
    haystack = f"{error.get('code', '')} {message}".lower()
    if "content_policy" in haystack or "moderation" in haystack:
        return ModerationError(
            [message or "content policy"],
            metadata.get("flagged_input"),
            metadata.get("provider_name"),
        )
    return None
