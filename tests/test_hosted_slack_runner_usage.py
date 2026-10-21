"""Slack job runner: the one-time budget warning on the answer footer."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from slack_runner_rig import KEY, Rig

from openrouter_image_mcp.hosted.slack import usage
from openrouter_image_mcp.hosted.slack.usage import month_key

NEAR = {"usage_monthly": 70.0, "effective_budget": {"limit_usd": 75.0}}
WARNING = "This team has used 93% of this month's Sidecar budget."


@pytest.fixture
def slack_rig(db_target, keys):
    rig = Rig(db_target, keys)
    rig.registry.add(rig.row)
    return rig


def answers(rig):
    return [text for text in rig.texts() if text.startswith("The answer.")]


async def test_warning_follows_the_footer_once(slack_rig):
    rig = slack_rig
    rig.key_info = NEAR
    chat = rig.chat()
    await rig.run(rig.job(chat))
    await rig.run(rig.job(chat, brief="and again?"))
    first, second = answers(rig)
    assert first == f"The answer.\n\nGemini · g/model · $0.0100\n{WARNING}"
    assert second == "The answer.\n\nGemini · g/model · $0.0100"
    assert rig.registry.get("T1", "C1").warned_month == month_key(datetime.now(UTC))
    assert rig.key_info_keys == [KEY, KEY]


async def test_below_the_threshold_does_not_claim_the_warning(slack_rig):
    rig = slack_rig
    rig.key_info = {"usage_monthly": 10.0, "effective_budget": {"limit_usd": 75.0}}
    await rig.run(rig.job(rig.chat()))
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]
    assert rig.registry.get("T1", "C1").warned_month is None


async def test_no_monthly_limit_gives_no_warning(slack_rig):
    rig = slack_rig
    rig.key_info = {"usage_monthly": 500.0, "effective_budget": None}
    await rig.run(rig.job(rig.chat()))
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]


async def test_key_info_failure_still_posts_the_answer(slack_rig, caplog):
    rig = slack_rig
    rig.key_info = RuntimeError(f"boom {KEY}")
    with caplog.at_level(logging.WARNING):
        await rig.run(rig.job(rig.chat()))
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]
    assert rig.job_rows()[0]["slack_posted_at"] is not None
    assert "RuntimeError" in caplog.text and KEY not in caplog.text


async def test_failed_job_does_not_fetch_key_info(slack_rig):
    rig = slack_rig
    rig.key_info = NEAR
    rig.model.respond = lambda request: httpx.Response(402, json={"error": {"message": "limit"}})
    await rig.run(rig.job(rig.chat()))
    assert rig.key_info_keys == [] and rig.registry.get("T1", "C1").warned_month is None


async def test_unregistered_channel_has_no_warning(db_target, keys):
    rig = Rig(db_target, keys)  # the row is not in the registry
    rig.key_info = NEAR
    await rig.run(rig.job(rig.chat()))
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]


async def test_slow_key_fetch_is_bounded_and_the_answer_posts(slack_rig, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(usage, "WARN_FETCH_S", 0.01)
    assert usage.WARN_FETCH_S != 4.0  # patched; the real bound is 4 seconds
    cancelled = []

    async def slow(key):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    rig.key_info = slow
    await asyncio.wait_for(rig.run(rig.job(rig.chat())), timeout=10)
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]
    assert cancelled and rig.registry.get("T1", "C1").warned_month is None


def test_fetch_bound_is_four_seconds():
    assert usage.WARN_FETCH_S == 4.0


async def test_already_warned_this_month_skips_the_fetch(slack_rig):
    rig = slack_rig
    rig.key_info = NEAR
    warned = replace(rig.row, warned_month=month_key(datetime.now(UTC)))
    await rig.run(rig.job(rig.chat(), row=warned))
    assert answers(rig) == ["The answer.\n\nGemini · g/model · $0.0100"]
    assert rig.key_info_keys == []
