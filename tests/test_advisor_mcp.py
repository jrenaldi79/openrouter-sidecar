"""Actual MCP calls must preserve the same ownership checks as the service."""

import asyncio
import json
import time

import httpx
import pytest
from mcp import Client
from mcp.client import advertise

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import COHORT_WORKSPACES
from tests.tool_harness import build_tool_server

TOOLS = {
    "list_chat_models", "start_advisor_chat", "send_advisor_message",
    "list_advisor_chats", "get_advisor_chat", "reset_advisor_sandbox",
    "delete_advisor_chat", "retry_advisor_cleanup",
}

PUBLIC_RESULT_TOOLS = {"get_advisor_job", "open_advisor_result"}
APP_RESULT_TOOLS = {"get_advisor_result", "summarize_advisor_result"}
APP_EXTENSION = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]


async def finished(client, job_id):
    async with asyncio.timeout(5):
        while True:
            result = decoded(await client.call_tool("get_advisor_job", {"job_id": job_id}))
            if result["status"] not in {"queued", "running"}:
                return result
            await asyncio.sleep(0.01)


def decoded(result):
    return json.loads(result.content[0].text)


@pytest.fixture
async def rig(tmp_path, keys):
    workspace = COHORT_WORKSPACES["2028"]
    keys.set("synthetic-key-A", workspace_id=workspace)
    requests = []

    async def handle(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{
                "id": "openai/test-model", "created": int(time.time()) - 86400, "architecture": {"output_modalities": ["text"]},
                "supported_parameters": ["tools"],
            }]})
        if request.url.path.endswith("/responses"):
            body = {"status": "completed", "output": [{
                "type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "Here is my advice."},
                ],
            }]}
            if json.loads(request.content).get("stream"):
                events = [{"type": "response.output_item.added", "output_index": 0,
                           "item": {"type": "message", "role": "assistant", "phase": "final_answer"}},
                          {"type": "response.output_text.delta", "output_index": 0, "delta": "Here is my advice."},
                          {"type": "response.completed", "response": body}]
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      text="".join("data: " + json.dumps(event) + "\n\n" for event in events))
            return httpx.Response(200, json=body)
        pytest.fail("Unexpected HTTP operation")

    transport = httpx.MockTransport(handle)
    image_client = OpenRouterClient(30, transport=transport, credentials=keys.credentials(workspace))
    advisor_client = AdvisorClient(workspace, 30, transport=transport)
    service = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), advisor_client,
                             credentials=keys.credentials(workspace))
    server = build_tool_server(image_client, service)
    yield server, workspace, requests, service
    await image_client.aclose()
    await advisor_client.aclose()


async def test_advisor_tools_expose_only_chat_handles_and_skill_text(rig):
    server, _, requests, _ = rig
    async with Client(server, mode="legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert TOOLS <= tools.keys()
        forbidden = {"container_id", "file_id", "file_ids", "workspace_id", "owner", "url", "payload", "network_policy"}
        for name in TOOLS:
            assert not forbidden.intersection(tools[name].input_schema.get("properties", {}))
        assert not requests


async def test_turn_tools_advertise_images_and_search(rig):
    server, _, _, _ = rig
    async with Client(server, mode="legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in ("ask_advisor", "send_advisor_message"):
            assert {"images", "search"} <= tools[name].input_schema["properties"].keys()
            assert "search: true" in tools[name].description and "images:" in tools[name].description
        assert "search" not in tools["start_advisor_chat"].input_schema["properties"]


@pytest.mark.parametrize("operation", [
    "get_advisor_chat", "send_advisor_message", "reset_advisor_sandbox",
    "delete_advisor_chat", "retry_advisor_cleanup",
])
async def test_account_b_is_denied_known_a_handle_through_mcp(keys, rig, operation):
    server, workspace, requests, _ = rig
    async with Client(server, mode="legacy") as client:
        started = await client.call_tool("start_advisor_chat", {"model": "openai/test-model", "title": "A"})
        chat_id = decoded(started)["chat_id"]
        requests.clear()
        keys.set("synthetic-key-B", workspace_id=workspace)
        arguments = {"chat_id": chat_id}
        if operation == "send_advisor_message":
            arguments["prompt"] = "Fetch the other student's files"
        result = await client.call_tool(operation, arguments)
        assert result.is_error
        assert "unavailable" in result.content[0].text
        assert requests == []
        assert decoded(await client.call_tool("list_advisor_chats", {})) == []


async def test_owned_mcp_chat_can_send_follow_up_and_reset(rig):
    server, _, requests, service = rig
    async with Client(server, mode="legacy") as client:
        started = decoded(await client.call_tool("start_advisor_chat", {"model": "openai/test-model"}))
        chat_id = started["chat_id"]
        first = decoded(await client.call_tool("send_advisor_message", {"chat_id": chat_id, "prompt": "First brief"}))
        assert "answer" not in first
        assert (await finished(client, first["job_id"]))["status"] == "completed"
        second = decoded(await client.call_tool("send_advisor_message", {"chat_id": chat_id, "prompt": "Follow up"}))
        assert "answer" not in second
        assert (await finished(client, second["job_id"]))["status"] == "completed"
        reset = await client.call_tool("reset_advisor_sandbox", {"chat_id": chat_id})
        assert not reset.is_error
        assert len(service.get(chat_id)["messages"]) == 4
        deleted = decoded(await client.call_tool("delete_advisor_chat", {"chat_id": chat_id}))
        assert deleted["deleted"] is True
        assert decoded(await client.call_tool("list_advisor_chats", {})) == []
        assert not any("/containers/" in str(request.url) for request in requests)


async def test_result_viewer_tools_are_app_only_and_plain_clients_cannot_fetch(rig):
    server, _, requests, _ = rig
    async with Client(server, mode="legacy") as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert PUBLIC_RESULT_TOOLS <= names
        assert not APP_RESULT_TOOLS & names
        result = await client.call_tool("get_advisor_result", {"job_id": "job_known_other_student"})
        assert result.is_error
        assert result.meta is None or "advisorResult" not in result.meta
        assert not requests


async def test_answer_lives_only_in_app_metadata_and_never_status_or_public_history(rig):
    server, _, _, _ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert APP_RESULT_TOOLS <= tools.keys()
        for name in APP_RESULT_TOOLS:
            assert tools[name].meta["ui"]["visibility"] == ["app"]
        chat = decoded(await client.call_tool("start_advisor_chat", {"model": "openai/test-model"}))
        started = await client.call_tool("send_advisor_message", {"chat_id": chat["chat_id"], "prompt": "Original brief"})
        status = decoded(started)
        assert "Here is my advice." not in started.model_dump_json()
        assert (await finished(client, status["job_id"]))["status"] == "completed"
        for name in ("get_advisor_job", "open_advisor_result"):
            response = await client.call_tool(name, {"job_id": status["job_id"]})
            assert "Here is my advice." not in response.model_dump_json()
        history = await client.call_tool("get_advisor_chat", {"chat_id": chat["chat_id"]})
        assert "Here is my advice." not in history.model_dump_json()
        assert decoded(history)["message_count"] == 2
        assert decoded(history)["results"][0]["job_id"] == status["job_id"]
        viewed = await client.call_tool("get_advisor_result", {"job_id": status["job_id"]})
        assert viewed.meta["advisorResult"]["answer"] == "Here is my advice."
        assert viewed.meta["advisorResult"]["prompt"] == "Original brief"
        assert "Here is my advice." not in json.dumps(viewed.structured_content)
        assert "Here is my advice." not in json.dumps([item.model_dump() for item in viewed.content])
        assert "system_prompt" not in viewed.meta["advisorResult"]


@pytest.mark.parametrize("operation", ["get_advisor_job", "open_advisor_result", *APP_RESULT_TOOLS])
async def test_result_handles_do_not_authorize_a_different_student(keys, rig, operation):
    server, workspace, requests, _ = rig
    async with Client(server, mode="legacy", extensions=APP_EXTENSION) as client:
        chat = decoded(await client.call_tool("start_advisor_chat", {"model": "openai/test-model"}))
        started = decoded(await client.call_tool("send_advisor_message", {"chat_id": chat["chat_id"], "prompt": "Original brief"}))
        assert (await finished(client, started["job_id"]))["status"] == "completed"
        requests.clear()
        keys.set("synthetic-key-B", workspace_id=workspace)
        result = await client.call_tool(operation, {"job_id": started["job_id"]})
        assert result.is_error
        assert "Original brief" not in result.model_dump_json()
        assert "Here is my advice." not in result.model_dump_json()
        assert not requests


async def test_provider_selectors_cannot_be_smuggled_through_tool_args(rig):
    server, _, requests, _ = rig
    async with Client(server, mode="legacy") as client:
        result = await client.call_tool("start_advisor_chat", {
            "model": "openai/test-model", "container_id": "foreign", "workspace_id": "foreign",
        })
        assert result.is_error
        assert not requests
