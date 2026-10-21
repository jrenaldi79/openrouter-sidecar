"""A Slack failure around a paid job never decides the job's fate: no lost answer, spend or stuck row."""

import pytest
from slack_runner_rig import Rig, answer_item, body, call

from openrouter_image_mcp.hosted.slack import runner_turn
from openrouter_image_mcp.hosted.slack.runner import START_FAILED
from openrouter_image_mcp.hosted.slack.runner_turn import LOCAL_FAILURE


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


def _search_then_answer(rig):
    replies = iter([body({"type": "web_search_call", "status": "completed"}, call(), cost=0.50),
                    body(answer_item("Done."), cost=0.25)])
    rig.model.respond = lambda request: next(replies)


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("socket gone")])
async def test_failed_progress_edit_does_not_fail_the_job(slack_rig, error):
    rig = slack_rig
    _search_then_answer(rig)
    update = rig.slack.update

    async def flaky(channel, ts, text):
        if "Searched" in text:
            raise error
        await update(channel, ts, text)

    rig.slack.update = flaky
    await rig.run(rig.job(rig.chat()))
    (row,) = rig.job_rows()
    assert row["status"] == "completed" and row["cost_usd"] == pytest.approx(0.75)
    assert any(post["text"].startswith("Done.") for post in rig.slack.posts)
    assert rig.slack.updates[-1]["text"] == "Sidecar · Gemini answered below."


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("socket gone")])
async def test_failed_working_message_leaves_no_queued_row(slack_rig, error):
    rig = slack_rig

    async def post(*args, **kwargs):
        raise error

    rig.slack.post = post
    await rig.run(rig.job(rig.chat()))
    (row,) = rig.job_rows()
    assert (row["status"], row["error"]) == ("failed", START_FAILED)
    assert rig.model.requests == []


async def test_unexpected_error_mid_loop_keeps_the_spend(slack_rig, monkeypatch):
    rig = slack_rig
    _search_then_answer(rig)
    labels = iter(["Searched the web", None])  # the first response's two items; the answer's item raises

    def label(item):
        try:
            return next(labels)
        except StopIteration:
            raise RuntimeError("unexpected") from None

    monkeypatch.setattr(runner_turn, "step_label", label)
    await rig.run(rig.job(rig.chat()))
    (row,) = rig.job_rows()
    assert (row["status"], row["error"]) == ("failed", LOCAL_FAILURE)
    assert row["cost_usd"] == pytest.approx(0.75)
