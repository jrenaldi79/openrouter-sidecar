"""OpenRouter error bodies: message extraction and moderation detection (shared by both clients)."""

import httpx

from openrouter_image_mcp.error_bodies import (
    MAX_BODY_CHARS,
    body_text,
    error_info,
    moderation_error,
)
from openrouter_image_mcp.errors import ModerationError


def test_error_info_prefers_the_error_message():
    resp = httpx.Response(400, json={"error": {"code": 400, "message": "bad size"}})
    assert error_info(resp) == ({"code": 400, "message": "bad size"}, "bad size")


def test_error_info_string_error_and_raw_body():
    assert error_info(httpx.Response(400, json={"error": "nope"})) == ({}, "nope")
    assert error_info(httpx.Response(502, text="<html>gateway</html>")) == ({}, "<html>gateway</html>")


def test_body_text_is_cut_short():
    text = body_text(httpx.Response(500, text="x" * (MAX_BODY_CHARS + 50)))
    assert len(text) == MAX_BODY_CHARS + 1 and text.endswith("…")


def test_moderation_from_reasons_or_wording():
    flagged = moderation_error({"metadata": {"reasons": ["violence"], "provider_name": "P"}}, "blocked")
    assert isinstance(flagged, ModerationError) and flagged.reasons == ["violence"] and flagged.provider == "P"
    worded = moderation_error({"code": "content_policy"}, "Request blocked")
    assert isinstance(worded, ModerationError)
    assert moderation_error({"code": 403}, "Key limit exceeded") is None
