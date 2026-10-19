"""wait_for_advisor_share: Claude stays on the call until the student shares, then reads it."""

import asyncio

import pytest
from mcp import Client

from openrouter_image_mcp import advisor_shares
from tests import test_advisor_mcp
from tests.test_advisor_mcp import APP_EXTENSION
from tests.test_advisor_shares import ANSWER, _result, _text

rig = test_advisor_mcp.rig  # the same in-memory advisor server and fake OpenRouter
WAIT = "wait_for_advisor_share"


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(advisor_shares, "POLL_S", 0.02)


async def test_returns_the_share_as_soon_as_the_student_shares(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)

        async def share_later():
            await asyncio.sleep(0.2)
            await client.call_tool("share_advisor_result", {"job_id": job_id})

        waited, _ = await asyncio.gather(client.call_tool(WAIT, {"job_id": job_id, "wait_s": 5}), share_later())
        assert not waited.is_error and f"<advisor_output>\n{ANSWER}" in _text(waited)
        # Delivered once: it is now read, so neither the read tool nor the reminder repeats it.
        assert ANSWER not in _text(await client.call_tool("read_shared_advisor_result", {}))
        assert "read_shared_advisor_result()" not in _text(await client.call_tool("list_advisor_chats", {}))


async def test_times_out_with_instructions_and_no_answer(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        waited = await client.call_tool(WAIT, {"job_id": job_id, "wait_s": 0.2})
        assert not waited.is_error
        text = _text(waited)
        assert ANSWER not in text and "hasn't shared" in text and WAIT in text


async def test_a_share_already_read_does_not_end_a_new_wait(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        await client.call_tool("share_advisor_result", {"job_id": job_id})
        await client.call_tool("read_shared_advisor_result", {})
        assert "hasn't shared" in _text(await client.call_tool(WAIT, {"job_id": job_id, "wait_s": 0.1}))


async def test_rejects_long_waits_and_other_students_results(keys, rig):
    server, workspace, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        assert (await client.call_tool(WAIT, {"job_id": job_id, "wait_s": advisor_shares.MAX_WAIT_S + 1})).is_error
        keys.set("synthetic-key-B", workspace_id=workspace)
        assert (await client.call_tool(WAIT, {"job_id": job_id, "wait_s": 0.1})).is_error
