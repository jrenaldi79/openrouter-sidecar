"""How a Slack job ends when a stop lands during its last request, or when it runs past its deadline."""

import asyncio

import pytest
from slack_runner_rig import CHANNEL, THREAD, Rig, answer_item, body, call

from openrouter_image_mcp.advisor_store import OPERATION_LEASE_S
from openrouter_image_mcp.hosted.slack.runner import JOB_DEADLINE_S
from openrouter_image_mcp.hosted.slack.runner_turn import DEADLINE_ERROR, DEADLINE_TEXT


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


async def test_stop_during_the_final_request_discards_the_answer(slack_rig):
    rig = slack_rig
    stopped = []

    def respond(request):
        stopped.append(rig.runner.stop(rig.scope.owner, CHANNEL, THREAD))  # lands while this request runs
        return body(answer_item("Too late."), cost=0.04)

    rig.model.respond = respond
    chat = rig.chat()
    await rig.run(rig.job(chat))
    assert stopped == [1] and len(rig.model.requests) == 1
    assert rig.slack.updates[-1]["text"] == "Stopped."
    assert not any("Too late." in post["text"] for post in rig.slack.posts)
    (row,) = rig.job_rows()
    assert (row["status"], row["error"], row["answer"]) == ("interrupted", "Stopped from Slack", "")
    assert row["cost_usd"] == pytest.approx(0.04) and row["slack_posted_at"] is None
    assert rig.history(chat) == []


class Timer:
    """Injected `SlackRunner.call_later`: records the deadline and lets the test fire it."""

    def __init__(self):
        self.delays, self.fire, self.cancelled = [], None, False

    def __call__(self, delay, callback):
        self.delays.append(delay)
        self.fire = callback
        return self

    def cancel(self):
        self.cancelled = True


async def test_deadline_fails_the_job_and_keeps_its_spend(slack_rig):
    rig = slack_rig
    timer = rig.runner.call_later = Timer()
    never = asyncio.Event()

    async def respond(request):
        if len(rig.model.requests) == 1:
            return body(call(), cost=0.30)
        timer.fire()  # the deadline passes while the second request is still out
        await never.wait()

    rig.model.respond = respond
    chat = rig.chat()
    await rig.run(rig.job(chat))
    assert timer.delays == [JOB_DEADLINE_S] and timer.cancelled
    (row,) = rig.job_rows()
    assert (row["status"], row["error"]) == ("failed", DEADLINE_ERROR)
    assert row["cost_usd"] == pytest.approx(0.30)
    assert rig.slack.updates[-1]["text"] == DEADLINE_TEXT.format(label="Gemini")
    assert DEADLINE_TEXT.format(label="Gemini") == (
        "Sidecar · Gemini ran for 30 minutes and stopped. Try a smaller question.")
    assert len(rig.slack.posts) == 1 and rig.history(chat) == []  # only the working message


async def test_a_job_that_finishes_cancels_its_deadline(slack_rig):
    rig = slack_rig
    timer = rig.runner.call_later = Timer()
    await rig.run(rig.job(rig.chat()))
    assert timer.cancelled and rig.job_rows()[0]["status"] == "completed"


async def test_a_response_without_a_cost_keeps_the_known_spend(slack_rig):
    rig = slack_rig
    unpriced = body(call())
    del unpriced["usage"]
    replies = iter([unpriced, body(answer_item("Done."), cost=0.25)])
    rig.model.respond = lambda request: next(replies)
    await rig.run(rig.job(rig.chat()))
    assert rig.job_rows()[0]["cost_usd"] == pytest.approx(0.25)  # a lower bound the daily cap still counts
    assert rig.texts()[-1].endswith("Gemini · g/model · $0.2500")


def test_deadline_is_below_the_chat_lease():
    assert JOB_DEADLINE_S == 30 * 60 < OPERATION_LEASE_S
