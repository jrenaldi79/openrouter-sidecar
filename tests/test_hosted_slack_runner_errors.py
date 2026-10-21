"""Slack job runner: limits, budget, provider and key errors reach Slack as team-facing copy."""

import asyncio

import httpx
import pytest
from slack_runner_rig import THREAD, Rig, answer_item, body

from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.hosted.slack.replies import budget_text, provider_error_text
from openrouter_image_mcp.hosted.slack.runner_steps import MODERATED


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


def _spent(rig, cost):
    chat = rig.store.create(rig.scope, "m", "earlier")
    job = rig.results.create(rig.scope, chat, "p", "proc-old")
    rig.results.update(rig.scope, job["id"], "proc-old", status="completed", cost_usd=cost)


async def test_daily_cap_text(slack_rig):
    rig = slack_rig
    _spent(rig, 4.5)
    await rig.run(rig.job(rig.chat()))
    (post,) = rig.slack.posts
    assert post["text"] == ("Your team's daily Sidecar cap ($4) is reached. It resets at midnight UTC. "
                            "Ask your instructor if you need more.")
    assert post["thread_ts"] == THREAD and post["username"] == "Sidecar · Gemini"
    assert rig.model.requests == [] and len(rig.job_rows()) == 1


async def test_budget_hit_text(slack_rig):
    rig = slack_rig
    rig.model.respond = lambda request: httpx.Response(402, json={"error": {"message": "limit"}})
    await rig.run(rig.job(rig.chat()))
    assert rig.slack.updates[-1]["text"] == budget_text()
    (row,) = rig.job_rows()
    assert row["status"] == "failed" and row["slack_posted_at"] is None


async def test_forbidden_is_budget_text(slack_rig):
    rig = slack_rig
    rig.model.respond = lambda request: httpx.Response(403, json={"error": {"message": "key limit"}})
    await rig.run(rig.job(rig.chat()))
    assert rig.slack.updates[-1]["text"] == budget_text()


async def test_provider_error_names_model_others_continue(slack_rig):
    rig = slack_rig

    def respond(request):
        if request["model"] == "x/bad":
            return httpx.Response(500)
        return body(answer_item("Good answer."))

    rig.model.respond = respond
    good = rig.job(rig.chat(model="g/good"))
    bad = rig.job(rig.chat(model="x/bad", alias="grok"), alias="grok", label="Grok")
    await asyncio.gather(rig.run(good), rig.run(bad))
    assert provider_error_text("Grok", ["gemini", "gpt", "open"]) in [u["text"] for u in rig.slack.updates]
    assert any(text.startswith("Good answer.") for text in rig.texts())
    statuses = {row["model"]: row["status"] for row in rig.job_rows()}
    assert statuses == {"g/good": "completed", "x/bad": "failed"}


async def test_rejected_key_uses_team_wording(slack_rig):
    """Ruling 6: key problems reach Slack in team words, never the student-facing MCP copy."""
    rig = slack_rig
    rig.model.respond = lambda request: httpx.Response(401)
    await rig.run(rig.job(rig.chat()))
    text = rig.slack.updates[-1]["text"]
    assert "this channel's Sidecar key" in text
    for student_word in ("student", "Your Sidecar", "account_status"):
        assert student_word not in text
    assert rig.job_rows()[0]["status"] == "failed"


async def test_missing_key_posts_team_wording(slack_rig, keys):
    rig = slack_rig
    keys.delete(workspace_id=SLACK_WORKSPACE)
    await rig.run(rig.job(rig.chat()))
    (post,) = rig.slack.posts
    assert "this channel's Sidecar key" in post["text"] and "student" not in post["text"]
    assert rig.job_rows() == []


async def test_unexpected_error_fails_job_without_details(slack_rig, monkeypatch):
    rig = slack_rig

    def boom(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(rig.memory, "instructions", boom)
    await rig.run(rig.job(rig.chat()))
    text = rig.slack.updates[-1]["text"]
    assert "secret detail" not in text and "Gemini" in text
    (row,) = rig.job_rows()
    assert row["status"] == "failed" and "secret detail" not in (row["error"] or "")


async def test_moderation_block_has_its_own_copy(slack_rig):
    rig = slack_rig
    flagged = {"error": {"code": 403, "message": "flagged", "metadata": {
        "reasons": ["violence"], "flagged_input": "the private brief text"}}}
    rig.model.respond = lambda request: httpx.Response(403, json=flagged)
    await rig.run(rig.job(rig.chat()))
    assert rig.slack.updates[-1]["text"] == MODERATED != budget_text()
    (row,) = rig.job_rows()
    assert row["status"] == "failed" and row["error"] == "Blocked by provider moderation."


async def test_delivery_marks_posted_before_outputs_and_survives_bookkeeping(slack_rig, monkeypatch):
    rig = slack_rig
    seen = []

    class Outputs:
        async def post(self, client, key, channel, thread_ts, label, container_id, refs):
            seen.append(rig.job_rows()[0]["slack_posted_at"])

    rig.runner.outputs = Outputs()

    def broken(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(rig.threads, "answered", broken)
    await rig.run(rig.job(rig.chat(sandbox=True, mode="sandbox"), mode="sandbox"))
    assert len(seen) == 1 and seen[0] is not None
    assert rig.slack.updates[-1]["text"] == "Sidecar · Gemini answered below."


async def test_mark_posted_failure_still_says_answered(slack_rig, monkeypatch, caplog):
    rig = slack_rig

    def broken(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(rig.jobs, "mark_posted", broken)
    with caplog.at_level("WARNING"):
        await rig.run(rig.job(rig.chat()))
    assert rig.slack.updates[-1]["text"] == "Sidecar · Gemini answered below."
    assert "mark posted failed: RuntimeError" in caplog.text
    assert rig.threads.last_answered(rig.scope, "C1", THREAD) is not None


async def test_bad_concurrency_value_posts_limit_instead_of_raising(slack_rig):
    rig = slack_rig
    rig.set_row(max_concurrent=0)
    await rig.run(rig.job(rig.chat()))
    assert rig.texts()[-1] == "Your team already has 0 answers running. Try again in a minute."
    assert rig.model.requests == [] and len(rig.runner._slots) == 0
