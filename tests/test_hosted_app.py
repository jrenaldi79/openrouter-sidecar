"""Hosted server over real HTTP: auth, discovery, isolation and advisor round trips."""

import asyncio
import json

import httpx
import pytest

from openrouter_image_mcp.hosted.principal import Principal
from tests import jwt_helpers as h
from tests.hosted_harness import MODEL, WS_2027, WS_2028, running_server


def _text(result):
    return "\n".join(getattr(item, "text", "") for item in result.content)


def _alice(running):
    return Principal(h.ISSUER, "email|alice", WS_2027, "2027")


@pytest.fixture
async def running(db_target):
    async with running_server(db_target) as server:
        yield server


async def test_unauthenticated_request_gets_401_with_resource_metadata(running):
    async with httpx.AsyncClient() as http:
        response = await http.post(running.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
        meta = (await http.get(f"http://127.0.0.1:{running.port}/.well-known/oauth-protected-resource/mcp")).json()
    assert meta["resource"] == running.settings.resource
    assert meta["authorization_servers"] == [h.ISSUER]


async def test_token_without_permission_is_forbidden(running):
    async with httpx.AsyncClient() as http:
        response = await http.post(running.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream",
                                            "Authorization": "Bearer " + running.token(permissions=[])})
    assert response.status_code == 403


async def test_token_without_workspace_is_forbidden_at_the_door(running):
    token = running.token(**{h.PROFILE: {"email": "x@example.edu", "cohort": "2027"}})
    async with httpx.AsyncClient() as http:
        response = await http.post(running.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream",
                                            "Authorization": "Bearer " + token})
    assert response.status_code == 403


async def test_wrong_audience_is_rejected(running):
    async with httpx.AsyncClient() as http:
        response = await http.post(running.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream",
                                            "Authorization": "Bearer " + h.token(workspace=WS_2027)})
    assert response.status_code == 401


async def test_tool_surface_is_hosted(running):
    async with running.client(running.token()) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert {"account_status", "start_advisor_chat", "send_advisor_message",
            "ask_advisor", "wait_advisor_job"} <= names
    assert not names & {"auth_login", "auth_logout", "generate_image", "export_advisor_result",
                        "link_openrouter", "unlink_openrouter"}


async def test_first_tool_call_provisions_a_key_in_the_cohort_workspace(running):
    async with running.client(running.token()) as client:
        assert not (await client.call_tool("list_chat_models", {})).is_error
        assert not (await client.call_tool("list_advisor_chats", {})).is_error
    assert running.fake.created_bodies() == [
        {"name": "Sidecar · alice@u.northwestern.edu · 2027", "workspace_id": WS_2027}]
    models = [r for r in running.fake.requests if r.url.path.endswith("/models")]
    assert models[-1].headers["Authorization"] == "Bearer sk-or-v1-fake1"
    assert running.keys.row(_alice(running).key, WS_2027).key_hash == "hash1"


async def test_concurrent_first_calls_provision_one_key(running):
    async with running.client(running.token()) as client:
        results = await asyncio.gather(*(client.call_tool("list_chat_models", {}) for _ in range(4)))
    assert not any(result.is_error for result in results)
    assert len(running.fake.created_bodies()) == 1


async def test_free_tools_without_keys_still_work_when_provisioning_is_off(db_target, tmp_path):
    env = {"OPENROUTER_MANAGEMENT_KEY": "", "ASSET_DIR": str(tmp_path / "assets")}
    async with running_server(db_target, env=env) as server, server.client(server.token()) as client:
        paid = await client.call_tool("list_chat_models", {})
        status = _text(await client.call_tool("account_status", {}))
        upload = await client.call_tool("create_image_upload",
                                        {"filename": "a.png", "mime": "image/png", "size_bytes": 10})
    assert paid.is_error and "isn't set up to issue OpenRouter keys" in _text(paid)
    assert "isn't set up to issue OpenRouter keys" in status
    assert "MANAGEMENT" not in status and "MANAGEMENT" not in _text(paid)
    assert not upload.is_error
    assert server.fake.created_bodies() == []


async def test_account_status_shows_key_usage_and_budget(running):
    async with running.client(running.token()) as client:
        status = _text(await client.call_tool("account_status", {}))
    assert "Sidecar · alice@u.northwestern.edu · 2027" in status and "2027" in status
    assert "$1.50" in status and "$25.00" in status and "monthly" in status
    assert "sk-or-v1" not in status


async def test_provisioned_user_runs_an_advisor_job(running):
    running.keys.insert(_alice(running), "sk-or-v1-alice", "hash-alice", "Alice key")
    async with running.client(running.token()) as client:
        status = _text(await client.call_tool("account_status", {}))
        assert "Alice key" in status and "2027" in status
        chat = json.loads(_text(await client.call_tool("start_advisor_chat", {"model": MODEL})))
        job = (await client.call_tool("send_advisor_message",
                                      {"chat_id": chat["chat_id"], "prompt": "Review this"})).structured_content
        for _ in range(100):
            state = (await client.call_tool("get_advisor_job", {"job_id": job["job_id"]})).structured_content
            if state["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.05)
    assert state["status"] == "completed"
    auth = [r.headers["Authorization"] for r in running.fake.requests if r.url.path.endswith("/responses")]
    assert auth == ["Bearer sk-or-v1-alice"]


async def test_headless_session_resumes_from_another_connection(running):
    running.keys.insert(_alice(running), "sk-or-v1-alice", "hash-alice", "Alice key")
    files = [{"path": "src/app.py", "content": "def handler():\n    return 42\n"}]
    async with running.client(running.token()) as first:
        asked = (await first.call_tool("ask_advisor", {"prompt": "Review handler", "model": MODEL,
                                                       "files": files})).structured_content
    assert asked["status"] == "completed", asked
    assert asked["answer"].startswith("<advisor_output>\nFinal advice")
    # A different connection (e.g. claude.ai after Claude Code) continues the session.
    async with running.client(running.token()) as second:
        follow = (await second.call_tool("ask_advisor", {"prompt": "And tests?",
                                                         "chat_id": asked["chat_id"]})).structured_content
    assert follow["status"] == "completed" and follow["chat_id"] == asked["chat_id"]
    body = running.fake.bodies()[-1]
    assert "tools" not in body
    assert "return 42" in body["input"][0]["content"]
    assert any("Review handler" in str(item["content"]) for item in body["input"][1:])


async def test_hosted_sandbox_is_disabled_and_chats_are_private(running):
    running.keys.insert(_alice(running), "sk-or-v1-alice", "hash-alice", "Alice key")
    bob = Principal(h.ISSUER, "email|bob", WS_2027, "2027")
    running.keys.insert(bob, "sk-or-v1-bob", "hash-bob", "Bob key")
    async with running.client(running.token()) as alice:
        refused = await alice.call_tool("ask_advisor", {"prompt": "x", "model": MODEL, "sandbox": True})
        assert refused.is_error and "turned off" in _text(refused)
        asked = (await alice.call_tool("ask_advisor", {"prompt": "Mine", "model": MODEL})).structured_content
    async with running.client(running.token(sub="email|bob")) as client:
        foreign = await client.call_tool("ask_advisor", {"prompt": "Show me", "chat_id": asked["chat_id"]})
        waited = await client.call_tool("wait_advisor_job", {"job_id": asked["job_id"]})
    assert foreign.is_error and "unavailable" in _text(foreign)
    assert waited.is_error and "unavailable" in _text(waited)


async def test_users_are_isolated(running):
    running.keys.insert(_alice(running), "sk-or-v1-alice", "hash-alice", "Alice key")
    bob = Principal(h.ISSUER, "email|bob", WS_2027, "2027")
    running.keys.insert(bob, "sk-or-v1-bob", "hash-bob", "Bob key")
    async with running.client(running.token()) as alice:
        chat = json.loads(_text(await alice.call_tool("start_advisor_chat", {"model": MODEL})))
    async with running.client(running.token(sub="email|bob")) as client:
        assert json.loads(_text(await client.call_tool("list_advisor_chats", {}))) == []
        foreign = await client.call_tool("get_advisor_chat", {"chat_id": chat["chat_id"]})
    assert foreign.is_error and "unavailable" in _text(foreign)


async def test_cohorts_route_to_their_own_workspace(running):
    carol = Principal(h.ISSUER, "email|carol", WS_2028, "2028")
    running.keys.insert(carol, "sk-or-v1-carol", "hash-carol", "Carol key")
    async with running.client(running.token(sub="email|carol", workspace=WS_2028)) as client:
        assert not (await client.call_tool("list_chat_models", {})).is_error
    workspaces = {r.url.params.get("workspace_id") for r in running.fake.requests}
    assert WS_2027 not in workspaces


async def test_unknown_workspace_is_refused(running):
    async with running.client(running.token(workspace="ws-unknown")) as client:
        result = await client.call_tool("list_advisor_chats", {})
    assert result.is_error and "not served" in _text(result)


async def test_health_and_ready(running):
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"http://127.0.0.1:{running.port}/health")).json() == {"ok": True}
        ready = await http.get(f"http://127.0.0.1:{running.port}/ready")
    assert ready.status_code == 200 and ready.json()["ready"] is True


async def test_link_pages_are_gone(running):
    async with httpx.AsyncClient() as http:
        response = await http.get(f"http://127.0.0.1:{running.port}/link/openrouter/start?t=x")
    assert response.status_code == 404


async def test_rejected_key_deleted_by_an_admin_is_replaced(running):
    running.keys.insert(_alice(running), "sk-or-v1-alice", "hash-alice", "Alice key")
    running.keys.mark_rejected(_alice(running).key, WS_2027)  # inference got a 401; key gone
    async with running.client(running.token()) as client:
        assert not (await client.call_tool("list_chat_models", {})).is_error
    assert running.keys.row(_alice(running).key, WS_2027).key_hash == "hash1"


async def test_shutdown_closes_the_database_pool(db_target, monkeypatch):
    from openrouter_image_mcp import storage

    closed = []
    monkeypatch.setattr(storage, "close_pool", closed.append)
    async with running_server(db_target):
        assert closed == []
    assert [str(target) for target in closed] == [str(db_target)]
