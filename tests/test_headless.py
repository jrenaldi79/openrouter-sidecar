"""Headless advisor: ask_advisor / wait_advisor_job behave like a stateful subagent."""

import asyncio
import json
import time

import httpx
import pytest
from mcp import Client

from openrouter_image_mcp import headless
from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import COHORT_WORKSPACES
from tests.tool_harness import build_tool_server

MODEL = "openai/test-model"


def decoded(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
async def rig(tmp_path, keys):
    workspace = COHORT_WORKSPACES["2028"]
    keys.set("synthetic-key-A", workspace_id=workspace)
    state = {"delay": 0.0, "bodies": []}

    async def handle(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{
                "id": MODEL, "created": int(time.time()) - 86400,
                "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools"]}]})
        if request.url.path.endswith("/responses"):
            state["bodies"].append(json.loads(request.content))
            await asyncio.sleep(state["delay"])
            body = {"status": "completed", "usage": {"cost": 0.01}, "output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Here is my advice."}]}]}
            events = [{"type": "response.completed", "response": body}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        pytest.fail("Unexpected HTTP operation " + request.url.path)

    transport = httpx.MockTransport(handle)
    image_client = OpenRouterClient(30, transport=transport, credentials=keys.credentials(workspace))
    advisor_client = AdvisorClient(workspace, 30, transport=transport)
    service = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), advisor_client,
                             credentials=keys.credentials(workspace),
                             sandbox_default=False, sandbox_allowed=False)
    server = build_tool_server(image_client, service)
    yield server, state, workspace
    await image_client.aclose()
    await advisor_client.aclose()


async def test_ask_creates_session_and_returns_enveloped_answer(rig):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        result = decoded(await client.call_tool("ask_advisor", {"prompt": "Review my plan", "model": MODEL}))
        assert result["status"] == "completed"
        assert result["chat_id"].startswith("chat_") and result["job_id"].startswith("job_")
        assert result["answer"].startswith("<advisor_output>\nHere is my advice.\n</advisor_output>")
        assert result["model"] == MODEL and result["cost_usd"] == 0.01
        assert "tools" not in state["bodies"][-1]


async def test_follow_up_restores_transcript_and_files(rig):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        files = [{"path": "src/app.py", "content": "print('hello')"}]
        first = decoded(await client.call_tool("ask_advisor", {
            "prompt": "Find bugs", "model": MODEL, "files": files}))
        second = decoded(await client.call_tool("ask_advisor", {
            "prompt": "What did you say before?", "chat_id": first["chat_id"]}))
        assert second["status"] == "completed" and second["chat_id"] == first["chat_id"]
        body = state["bodies"][-1]
        assert "print('hello')" in body["input"][0]["content"]
        contents = [item["content"] for item in body["input"][1:]]
        assert any("Find bugs" in c for c in contents) and "Here is my advice." in contents


async def test_short_wait_falls_back_to_running_then_wait_completes(rig):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        state["delay"] = 0.3
        started = decoded(await client.call_tool("ask_advisor", {"prompt": "Slow", "model": MODEL,
                                                                 "wait_s": 0}))
        assert started["status"] == "running" and "answer" not in started
        done = decoded(await client.call_tool("wait_advisor_job", {"job_id": started["job_id"], "wait_s": 5}))
        assert done["status"] == "completed" and "Here is my advice." in done["answer"]
        assert done["chat_id"] == started["chat_id"]


async def test_busy_chat_reports_running_job(rig):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        state["delay"] = 0.5
        first = decoded(await client.call_tool("ask_advisor", {"prompt": "One", "model": MODEL, "wait_s": 0}))
        busy = decoded(await client.call_tool("ask_advisor", {"prompt": "Two", "chat_id": first["chat_id"],
                                                              "wait_s": 0}))
        assert busy == {"status": "busy", "chat_id": first["chat_id"], "running_job_id": first["job_id"]}
        await client.call_tool("wait_advisor_job", {"job_id": first["job_id"], "wait_s": 5})


async def test_progress_notifications_while_waiting(rig, monkeypatch):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        monkeypatch.setattr(headless, "PROGRESS_S", 0.05)
        state["delay"] = 0.3
        updates = []

        async def on_progress(progress, total, message):
            updates.append((progress, total, message))

        result = decoded(await client.call_tool("ask_advisor", {"prompt": "Slow", "model": MODEL, "wait_s": 5},
                                                progress_callback=on_progress))
        assert result["status"] == "completed"
        assert updates and all(total == 5 for _, total, _ in updates)


async def test_other_account_cannot_wait_on_job(keys, rig):
    server, _, workspace = rig
    async with Client(server, mode="legacy") as client:
        done = decoded(await client.call_tool("ask_advisor", {"prompt": "Mine", "model": MODEL}))
        keys.set("synthetic-key-B", workspace_id=workspace)
        denied = await client.call_tool("wait_advisor_job", {"job_id": done["job_id"]})
        assert denied.is_error and "unavailable" in denied.content[0].text


@pytest.mark.parametrize("arguments,match", [
    ({"prompt": "x", "model": MODEL, "sandbox": True}, "turned off"),
    ({"prompt": "x", "model": MODEL, "wait_s": 46}, "wait_s"),
    ({"prompt": "x", "model": MODEL, "wait_s": True}, "wait_s"),
    ({"prompt": "x", "model": MODEL, "container_id": "nu_other"}, "Unsupported"),
])
async def test_invalid_requests_are_refused(rig, arguments, match):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool("ask_advisor", arguments)
        assert result.is_error and match in result.content[0].text
        assert state["bodies"] == []
        assert decoded(await client.call_tool("list_advisor_chats", {})) == []


async def test_session_options_only_apply_when_creating(rig):
    server, _, _ = rig
    async with Client(server, mode="legacy") as client:
        first = decoded(await client.call_tool("ask_advisor", {"prompt": "x", "model": MODEL}))
        result = await client.call_tool("ask_advisor", {"prompt": "y", "chat_id": first["chat_id"],
                                                        "sandbox": False})
        assert result.is_error and "creating" in result.content[0].text


async def test_failed_first_request_does_not_leave_an_empty_session(rig):
    server, _, _ = rig
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool("ask_advisor", {"prompt": " ", "model": MODEL})
        assert result.is_error
        assert decoded(await client.call_tool("list_advisor_chats", {})) == []


async def test_viewer_tool_accepts_files_and_start_accepts_sandbox(rig):
    server, state, _ = rig
    async with Client(server, mode="legacy") as client:
        chat = decoded(await client.call_tool("start_advisor_chat", {"model": MODEL, "sandbox": False}))
        sent = decoded(await client.call_tool("send_advisor_message", {
            "chat_id": chat["chat_id"], "prompt": "Look", "files": [{"path": "a.md", "content": "A"}]}))
        assert "answer" not in sent
        await client.call_tool("wait_advisor_job", {"job_id": sent["job_id"], "wait_s": 5})
        assert 'path="a.md"' in state["bodies"][-1]["input"][0]["content"]
