"""One admitted Slack job (runner_turn.Turn): heartbeat races, lost lease, cost on failure, history cap."""

import asyncio

import httpx
import pytest
from slack_runner_rig import CHANNEL, THREAD, Rig, body, call, wait_until

from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.errors import ProviderError
from openrouter_image_mcp.hosted.slack import runner as runner_module
from openrouter_image_mcp.hosted.slack import runner_turn
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
        keys.delete(workspace_id=SLACK_WORKSPACE)  # the next heartbeat's ownership check fails and cancels the job
        await never.wait()

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))  # returns: only the work was cancelled, not the runner
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


def _slow_finish(rig, texts=None):
    """Hold each final edit long enough for many heartbeat ticks after the row has ended."""
    real = rig.slack.update

    async def update(channel, ts, text):
        if texts is None or text in texts:
            await asyncio.sleep(0.05)
        await real(channel, ts, text)

    rig.slack.update = update


async def test_heartbeat_cannot_cancel_a_stopped_finish(slack_rig, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.005)
    _slow_finish(rig, {"Stopped."})

    def respond(request):
        rig.runner.stop(rig.scope.owner, CHANNEL, THREAD)
        return body(call(), cost=0.02)

    rig.model.respond = respond
    await rig.run(rig.job(rig.chat()))  # returns normally
    assert rig.slack.updates[-1]["text"] == "Stopped."
    assert rig.job_rows()[0]["status"] == "interrupted"


async def test_heartbeat_cannot_cancel_a_failed_finish(slack_rig, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.005)
    _slow_finish(rig)
    rig.model.respond = lambda request: httpx.Response(500)
    await rig.run(rig.job(rig.chat()))
    assert "didn't answer" in rig.slack.updates[-1]["text"]
    assert rig.job_rows()[0]["status"] == "failed"


def _round_then_hang(rig, before_hanging=lambda: None):
    never = asyncio.Event()

    async def respond(request):
        if len(rig.model.requests) == 1:
            return body(call(), cost=0.04)
        before_hanging()
        await never.wait()

    rig.model.respond = respond


async def test_outer_cancel_propagates_and_keeps_spend(slack_rig):
    rig = slack_rig
    _round_then_hang(rig)
    task = asyncio.create_task(rig.run(rig.job(rig.chat())))
    await wait_until(lambda: len(rig.model.requests) == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (row,) = rig.job_rows()
    assert row["status"] == "interrupted" and row["cost_usd"] == pytest.approx(0.04)
    assert rig.slack.updates[-1]["text"] == runner_module.INTERRUPTED


async def test_lost_lease_keeps_spend(slack_rig, keys, monkeypatch):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.001)
    _round_then_hang(rig, lambda: keys.delete(workspace_id=SLACK_WORKSPACE))
    await rig.run(rig.job(rig.chat()))
    (row,) = rig.job_rows()
    assert row["status"] == "interrupted" and row["cost_usd"] == pytest.approx(0.04)


async def test_cleanup_error_cannot_replace_a_cancel(slack_rig, keys, monkeypatch):
    """A failing upload cleanup during the cancel must not turn "Interrupted" into a provider error."""
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.001)
    real, calls = rig.advisors._cleanup, []

    async def cleanup(*args):
        calls.append(1)
        if len(calls) > 1:
            raise ProviderError("cleanup failed")
        return await real(*args)

    monkeypatch.setattr(rig.advisors, "_cleanup", cleanup)
    _round_then_hang(rig, lambda: keys.delete(workspace_id=SLACK_WORKSPACE))
    await rig.run(rig.job(rig.chat()))
    assert len(calls) == 2 and rig.slack.updates[-1]["text"] == runner_module.INTERRUPTED
    assert rig.job_rows()[0]["status"] == "interrupted"


async def test_heartbeat_logs_unexpected_errors(slack_rig, monkeypatch, caplog):
    rig = slack_rig
    monkeypatch.setattr(runner_module, "HEARTBEAT_S", 0.001)
    beats = []

    def boom(self):
        beats.append(1)
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(runner_turn.Turn, "beat", boom)

    async def respond(request):
        await wait_until(lambda: beats)
        return body()

    rig.model.respond = respond
    with caplog.at_level("WARNING"):
        await rig.run(rig.job(rig.chat()))
    assert "heartbeat failed: RuntimeError" in caplog.text and "db hiccup" not in caplog.text
    assert rig.texts()[-1].startswith("The answer.")


async def test_cost_kept_when_the_answer_fails_after_the_loop(slack_rig):
    rig = slack_rig
    shell = {"type": "openrouter:shell", "id": "sh_1", "status": "completed", "container_id": "someone-else"}
    rig.model.respond = lambda request: body(shell, call()) if len(rig.model.requests) == 1 else body()
    await rig.run(rig.job(rig.chat(sandbox=True, mode="sandbox"), mode="sandbox"))
    (row,) = rig.job_rows()
    assert row["status"] == "failed" and row["cost_usd"] == pytest.approx(0.02)


async def test_cost_kept_when_a_later_round_fails(slack_rig):
    rig = slack_rig
    rig.model.respond = (lambda request: body(call(), cost=0.03) if len(rig.model.requests) == 1
                         else httpx.Response(402))
    await rig.run(rig.job(rig.chat()))
    (row,) = rig.job_rows()
    assert row["status"] == "failed" and row["cost_usd"] == pytest.approx(0.03)
