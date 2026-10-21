"""Pure helpers of the Slack runner: progress labels, Fusion panel failure, file room, error copy."""

import pytest

from openrouter_image_mcp.errors import (
    AuthRequiredError,
    BadRequestError,
    ForbiddenError,
    InsufficientCreditsError,
    ModerationError,
    ProviderError,
)
from openrouter_image_mcp.hosted.limits import RateLimitedError
from openrouter_image_mcp.hosted.provisioning import DISABLED, NOT_READY, UNAVAILABLE
from openrouter_image_mcp.hosted.slack.context import CONTEXT_PATH
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.slack.replies import (
    budget_text,
    key_text,
    limit_text,
    provider_error_text,
)
from openrouter_image_mcp.hosted.slack.runner_steps import (
    MODERATED,
    ChatFullError,
    failure_text,
    panel_failure,
    step_label,
    trim_files,
)
from openrouter_image_mcp.session_files import FilePacket

ROW = new_row("T1", "C1", "team-a", "2027", fusion_panel=("a/one", "b/two"), fusion_analyst="d/a",
              subagent_model="e/s", daily_cap_usd=2.5, max_concurrent=2)


@pytest.mark.parametrize(("kind", "label"), [
    ("openrouter:web_search", "Searched the web"),
    ("web_search_call", "Searched the web"),
    ("openrouter:shell", "Ran code in the sandbox"),
    ("openrouter:web_fetch", "Read a web page"),
    ("openrouter:fusion", "Compared the panel's answers"),
    ("openrouter:subagent", "Ran a helper"),
    ("openrouter:datetime", None),
    ("message", None),
    ("function_call", None),
])
def test_step_label(kind, label):
    assert step_label({"type": kind}) == label


def test_step_label_ignores_odd_items():
    assert step_label({"type": 3}) is None and step_label({}) is None


def _fusion(*panel, key="responses"):
    return {"type": "openrouter:fusion", key: list(panel)}


def test_panel_failure_only_when_every_panelist_failed():
    failed = _fusion({"model": "a/one", "error": "timeout"}, {"model": "b/two", "status": "failed"})
    assert panel_failure([failed]) == "Every model in the panel failed: a/one, b/two."
    assert panel_failure([_fusion({"model": "a/one", "error": "x"}, {"model": "b/two", "text": "ok"})]) is None
    assert panel_failure([_fusion()]) is None
    assert panel_failure([{"type": "message"}]) is None


def test_panel_failure_reads_other_list_names_and_clips_models():
    item = _fusion({"model": "<@U1>" * 50, "status": "error"}, key="results")
    text = panel_failure([item])
    assert text.startswith("Every model in the panel failed: ") and len(text) < 200


def _stored(path, chars):
    return {"path": path, "content": "s" * chars, "version": 1}


def _packet(name, chars):
    return FilePacket(path="slack/files/" + name, content="p" * chars)


def test_trim_files_keeps_what_fits_in_order():
    stored = [_stored(CONTEXT_PATH, 90_000), _stored("slack/files/old.txt", 150_000)]
    kept, listed = trim_files([_packet("big.csv", 60_000), _packet("small.txt", 10)], stored, 1_000)
    assert [p.path for p in kept] == ["slack/files/small.txt"] and listed == ["big.csv"]


def test_trim_files_ignores_paths_being_replaced():
    stored = [_stored("slack/files/data.csv", 150_000)]
    kept, listed = trim_files([_packet("data.csv", 90_000)], stored, 70_000)
    assert len(kept) == 1 and listed == []


def test_failure_text_maps_each_error():
    def text(error):
        return failure_text(error, "Gemini", ROW, "gemini")

    daily = RateLimitedError("x", 3600, "daily")
    assert text(daily) == limit_text(daily, daily_cap_usd=2.5, max_concurrent=2)
    busy = RateLimitedError("x", 30, "concurrency")
    assert text(busy) == limit_text(busy, daily_cap_usd=2.5, max_concurrent=2)
    assert text(InsufficientCreditsError("x")) == budget_text() == text(ForbiddenError("x"))
    for message in (DISABLED, NOT_READY, "anything else"):
        assert text(AuthRequiredError(message)) == key_text(AuthRequiredError(message))
    assert text(ProviderError(UNAVAILABLE)) == key_text(ProviderError(UNAVAILABLE))
    others = ["gpt", "grok", "open"]
    assert text(ProviderError("HTTP 500")) == provider_error_text("Gemini", others)
    assert text(BadRequestError("Advisor brief must contain")) == provider_error_text("Gemini", others)
    assert text(ChatFullError("Gemini")) == ChatFullError("Gemini").message
    assert text(ModerationError(["violence"])) == MODERATED
    local = text(None)
    assert "Gemini" in local and "error" in local


def test_chat_full_names_restart():
    assert "!restart" in ChatFullError("Gemini").message
