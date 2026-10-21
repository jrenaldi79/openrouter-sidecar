"""One admitted Slack job (runner_turn.Turn): heartbeat, lost lease, history cap, collected items."""

import asyncio

import pytest
from slack_runner_rig import Rig, body, call, wait_until

from openrouter_image_mcp.hosted.slack import runner as runner_module
from openrouter_image_mcp.hosted.slack.runner_steps import HISTORY_CHARS, ChatFullError


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


async def test_heartbeat_refreshes_the_job_row(slack_rig, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.001)
    first = {}

    async def respond(request):
        (row,) = rig.job_rows()
        first["beat"] = row["heartbeat_until"]
        await wait_until(lambda: rig.job_rows()[0]["heartbeat_until"] > first["beat"])
        return body()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))
    assert rig.job_rows()[0]["status"] == "completed"


async def test_lost_key_mid_job_interrupts(slack_rig, keys, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.001)
    never = asyncio.Event()

    async def respond(request):
        keys.delete()  # the next heartbeat's ownership check fails and cancels the job
        await never.wait()

    rig.model.respond = respond
    task = asyncio.create_task(rig.run(rig.job(rig.chat())))
    with pytest.raises(asyncio.CancelledError):
        await task
    (row,) = rig.job_rows()
    assert row["status"] == "interrupted" and row["answer"] == ""
    assert rig.slack.updates[-1]["text"] == runner_module.INTERRUPTED
    assert rig.runner.running(rig.scope.owner, "C1") == []


async def test_full_chat_asks_for_restart(slack_rig):
    rig = slack_rig
    chat = rig.chat()
    with rig.store._connection() as db:
        db.execute("UPDATE advisor_chats SET history=? WHERE id=?",
                   ('[{"role": "user", "content": "' + "h" * HISTORY_CHARS + '"}]', chat.id))
    await rig.run(rig.job(chat))
    assert rig.slack.updates[-1]["text"] == ChatFullError("Gemini").message
    assert rig.model.requests == [] and rig.job_rows()[0]["status"] == "failed"


async def test_tool_error_is_not_a_progress_step(slack_rig):
    rig = slack_rig
    rig.slack.fail_next("replies", "ratelimited")
    rig.model.respond = lambda request: body(call()) if len(rig.model.requests) == 1 else body()
    await rig.run(rig.job(rig.chat()))
    assert [u["text"] for u in rig.slack.updates] == ["Sidecar · Gemini answered below."]
    replayed = rig.model.requests[1]["input"][-1]
    assert replayed["output"] == "Error: Slack said ratelimited"
