"""Saved shares: the viewer records a share, Claude reads it with a plain tool and is reminded."""

import json

from mcp import Client

from openrouter_image_mcp import advisor_shares
from tests import test_advisor_mcp
from tests.test_advisor_mcp import APP_EXTENSION, decoded, finished

rig = test_advisor_mcp.rig  # the same in-memory advisor server and fake OpenRouter

ANSWER = "Here is my advice."
NOTE = "read_shared_advisor_result"


async def _result(client, prompt="Original brief"):
    chat = decoded(await client.call_tool("start_advisor_chat", {"model": "openai/test-model"}))
    started = decoded(await client.call_tool("send_advisor_message", {"chat_id": chat["chat_id"], "prompt": prompt}))
    assert (await finished(client, started["job_id"]))["status"] == "completed"
    return started["job_id"]


def _text(result):
    return "\n".join(item.text for item in result.content if item.type == "text")


async def test_share_tool_is_app_only_and_read_tool_is_public(rig):
    server, *_ = rig
    async with Client(server, mode="legacy") as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert NOTE in names and "share_advisor_result" not in names
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert tools["share_advisor_result"].meta["ui"]["visibility"] == ["app"]


async def test_shared_answer_is_read_once_as_untrusted_text(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        assert ANSWER not in _text(await client.call_tool(NOTE, {}))  # nothing shared yet
        shared = await client.call_tool("share_advisor_result", {"job_id": job_id})
        assert not shared.is_error and ANSWER not in shared.model_dump_json()
        read = _text(await client.call_tool(NOTE, {}))
        assert f"<advisor_output>\n{ANSWER}" in read and job_id in read
        assert ANSWER not in _text(await client.call_tool(NOTE, {}))  # already read
        assert ANSWER in _text(await client.call_tool(NOTE, {"job_id": job_id}))  # by id, again


async def test_reminder_appears_until_the_share_is_read(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        assert NOTE not in _text(await client.call_tool("list_advisor_chats", {}))
        await client.call_tool("share_advisor_result", {"job_id": job_id})
        assert NOTE in _text(await client.call_tool("list_advisor_chats", {}))
        assert NOTE not in json.dumps((await client.call_tool("get_advisor_result", {"job_id": job_id})).meta)
        await client.call_tool(NOTE, {})
        assert NOTE not in _text(await client.call_tool("list_advisor_chats", {}))


async def test_old_unread_shares_are_not_noted(rig, monkeypatch):
    server, *_ = rig
    monkeypatch.setattr(advisor_shares, "REMINDER_WINDOW_S", -1)  # every share is "old"
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        await client.call_tool("share_advisor_result", {"job_id": job_id})
        assert NOTE not in _text(await client.call_tool("list_advisor_chats", {}))
        assert ANSWER in _text(await client.call_tool(NOTE, {}))  # still readable


async def test_summary_share_returns_the_summary_and_rejects_other_jobs(rig):
    server, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        other = await _result(client, "Another brief")
        wrong = await client.call_tool("share_advisor_result", {"job_id": job_id, "summary_job_id": other})
        assert wrong.is_error
        summary = decoded(await client.call_tool("summarize_advisor_result", {"job_id": job_id}))
        assert (await finished(client, summary["job_id"]))["status"] == "completed"
        shared = await client.call_tool("share_advisor_result", {"job_id": job_id, "summary_job_id": summary["job_id"]})
        assert not shared.is_error
        read = _text(await client.call_tool(NOTE, {}))
        assert "Advisor summary" in read and summary["job_id"] in read


async def test_another_student_cannot_share_or_read(keys, rig):
    server, workspace, *_ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        job_id = await _result(client)
        await client.call_tool("share_advisor_result", {"job_id": job_id})
        keys.set("synthetic-key-B", workspace_id=workspace)
        assert (await client.call_tool("share_advisor_result", {"job_id": job_id})).is_error
        assert ANSWER not in _text(await client.call_tool(NOTE, {}))
        by_id = await client.call_tool(NOTE, {"job_id": job_id})
        assert by_id.is_error and ANSWER not in by_id.model_dump_json()
        assert NOTE not in _text(await client.call_tool("list_advisor_chats", {}))
