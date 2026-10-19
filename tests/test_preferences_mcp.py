"""Wire checks for scoped defaults, onboarding and UI-only demo answers."""

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

APPS = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
PUBLIC = {"open_sidecar_settings", "get_sidecar_preferences", "set_sidecar_preferences", "resolve_advisor_model"}
PRIVATE = {"get_sidecar_settings", "run_sidecar_demo", "get_sidecar_demo"}


def data(result):
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


@pytest.fixture
async def rig(tmp_path, keys):
    workspace = COHORT_WORKSPACES["2027"]
    keys.set("synthetic-owner-A", workspace_id=workspace)
    seen = []

    async def handle(request):
        seen.append(request)
        if request.url.path.endswith("/images/models"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": vendor + ("/qwen-demo" if vendor == "qwen" else "/demo"), "name": vendor + " Demo", "context_length": 8192, "created": int(time.time()) - 86400,
                 "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                 "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools"]}
                for vendor in ["google", "openai", "qwen"]
            ]})
        if request.url.path.endswith("/responses"):
            answer = "Synthetic answer, visible only in app."
            body = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": answer}]}]}
            events = [{"type": "response.output_item.added", "output_index": 0,
                       "item": {"type": "message", "role": "assistant", "phase": "final_answer"}},
                      {"type": "response.output_text.delta", "output_index": 0, "delta": answer},
                      {"type": "response.completed", "response": body}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(event) + "\n\n" for event in events))
        if request.url.path.endswith("/files") and request.method == "POST":
            return httpx.Response(200, json={"id": "or_file_demo123"})
        if request.method == "DELETE" and "/files/" in request.url.path:
            return httpx.Response(204)
        pytest.fail("Unexpected HTTP operation")

    transport = httpx.MockTransport(handle)
    http = OpenRouterClient(30, transport=transport, credentials=keys.credentials(workspace))
    advisor_client = AdvisorClient(workspace, 30, transport=transport)
    advisors = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), advisor_client,
                              credentials=keys.credentials(workspace))
    server = build_tool_server(http, advisors)
    yield server, workspace, seen, advisors
    await http.aclose()
    await advisor_client.aclose()


async def wait(client, job_id):
    async with asyncio.timeout(5):
        while True:
            status = data(await client.call_tool("get_advisor_job", {"job_id": job_id}))
            if status["status"] not in {"queued", "running"}:
                return status
            await asyncio.sleep(0.01)


async def save(client):
    prefs = data(await client.call_tool("get_sidecar_preferences", {}))
    return data(await client.call_tool("set_sidecar_preferences", {
        "settings_id": prefs["settings_id"], "expected_revision": prefs["revision"],
        "general_default": "google/demo", "family_defaults": {"google": "google/demo"},
    }))


async def test_settings_discovery_and_plain_app_only_denial(rig):
    server, _, seen, _ = rig
    async with Client(server, mode="legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert PUBLIC <= tools.keys()
        assert not PRIVATE & tools.keys()
        assert not tools["open_sidecar_settings"].meta
        for name in PRIVATE:
            result = await client.call_tool(name, {})
            assert result.is_error
        assert not seen
    async with Client(server, mode="legacy", extensions=APPS) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in PRIVATE:
            assert tools[name].meta["ui"]["visibility"] == ["app"]
        assert tools["open_sidecar_settings"].meta["ui"]["resourceUri"] == "ui://openrouter-sidecar/settings.html"
        opened = data(await client.call_tool("open_sidecar_settings", {"mode": "settings"}))
        assert opened["mode"] == "settings"
        result = await client.call_tool("get_sidecar_settings", {})
        assert "models" not in data(result)
        assert len(result.meta["sidecarSettings"]["models"]) == 3


async def test_conversational_alias_general_and_override_do_not_change_existing_chat(rig):
    server, _, _, advisors = rig
    async with Client(server, mode="legacy") as client:
        prefs = await save(client)
        alias = data(await client.call_tool("start_advisor_chat", {"model": "Gemini"}))
        general = data(await client.call_tool("start_advisor_chat", {}))
        override = data(await client.call_tool("start_advisor_chat", {"model": "openai/demo"}))
        assert alias["model"] == general["model"] == "google/demo"
        assert override["model"] == "openai/demo"
        updated = data(await client.call_tool("set_sidecar_preferences", {
            "settings_id": prefs["settings_id"], "expected_revision": prefs["revision"],
            "general_default": "qwen/qwen-demo",
        }))
        assert updated["family_defaults"]["google"] == "google/demo"
        assert advisors.get(general["chat_id"])["model"] == "google/demo"
        assert data(await client.call_tool("resolve_advisor_model", {}))["model"] == "qwen/qwen-demo"


async def test_demo_is_limited_idempotent_and_keeps_answer_out_of_parent(rig):
    server, _, seen, _ = rig
    async with Client(server, mode="legacy", extensions=APPS) as client:
        prefs = await save(client)
        arguments = {"demo": "hello", "settings_id": prefs["settings_id"],
                     "expected_revision": prefs["revision"]}
        first = data(await client.call_tool("run_sidecar_demo", arguments))
        assert "answer" not in first
        assert (await wait(client, first["job_id"]))["status"] == "completed"
        second = data(await client.call_tool("run_sidecar_demo", arguments))
        assert first["job_id"] == second["job_id"]
        paid = [request for request in seen if request.url.path.endswith("/responses")]
        assert len(paid) == 1
        payload = json.loads(paid[0].content)
        assert payload["max_output_tokens"] <= 256
        assert payload["max_tool_calls"] <= 1
        result = await client.call_tool("get_advisor_result", {"job_id": first["job_id"]})
        assert "Synthetic answer" not in json.dumps(data(result))
        assert "Synthetic answer" in result.meta["advisorResult"]["answer"]


async def test_old_settings_context_cannot_mutate_or_spend_under_another_sign_in(keys, rig):
    server, workspace, seen, _ = rig
    async with Client(server, mode="legacy", extensions=APPS) as client:
        prefs = await save(client)
        seen.clear()
        keys.set("synthetic-owner-B", workspace_id=workspace)
        for name, extras in [("set_sidecar_preferences", {"general_default": "google/demo"}),
                             ("run_sidecar_demo", {"demo": "hello"})]:
            result = await client.call_tool(name, {"settings_id": prefs["settings_id"],
                "expected_revision": prefs["revision"], **extras})
            assert result.is_error
        assert not seen
        assert data(await client.call_tool("get_sidecar_preferences", {}))["general_default"] is None


async def test_opening_signed_out_settings_and_walking_steps_does_not_infer(keys, rig):
    server, workspace, seen, _ = rig
    keys.delete(workspace_id=workspace)
    async with Client(server, mode="legacy", extensions=APPS) as client:
        opened = data(await client.call_tool("open_sidecar_settings", {}))
        assert opened["signed_in"] is False
        result = await client.call_tool("get_sidecar_settings", {})
        assert result.meta["sidecarSettings"]["signed_in"] is False
        assert not seen
