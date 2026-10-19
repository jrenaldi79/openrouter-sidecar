"""Exercise real service authorization before the mocked HTTP boundary."""

import importlib
import importlib.util
import json
import time
from contextlib import contextmanager

import httpx
import pytest

from openrouter_image_mcp.advisor_store import AdvisorAccessError, AdvisorStore
from openrouter_image_mcp.errors import OpenRouterError

WORKSPACE = "test-workspace"
KEY_A = "sk-or-v1-synthetic-account-a"
KEY_B = "sk-or-v1-synthetic-account-b"
MODEL = "test/advisor"


async def test_advisor_defaults_exclude_image_outputs_but_keep_vision_chat(tmp_path, keys):
    from openrouter_image_mcp.advisor import AdvisorService
    from openrouter_image_mcp.advisor_client import AdvisorClient
    from openrouter_image_mcp.preferences import PreferenceService

    keys.set(KEY_A, workspace_id=WORKSPACE)
    rows = [
        {"id": "google/chat", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "google/vision-chat", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": "google/image-only", "architecture": {"output_modalities": ["image"]}},
        {"id": "google/image-and-text", "architecture": {"output_modalities": ["text", "image"]}},
        {"id": "google/audio-and-text", "architecture": {"output_modalities": ["text", "audio"]}},
    ]
    rows = [row | {"created": int(time.time()) - 86400, "supported_parameters": ["tools"]} for row in rows]

    def handle(request):
        assert request.url.path.endswith("/models"), "Filtering must not invoke inference"
        return httpx.Response(200, json={"data": rows})

    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(handle))
    try:
        service = AdvisorService(WORKSPACE, AdvisorStore(tmp_path / "advisor.sqlite3"), client,
                                 credentials=keys.credentials(WORKSPACE))
        preferences = PreferenceService(service)
        expected = {"google/chat", "google/vision-chat"}
        assert {row["id"] for row in await service.models()} == expected
        assert {row["id"] for row in (await preferences.view())["models"]} == expected
        initial = preferences.get()
        for model in ("google/image-only", "google/image-and-text"):
            with pytest.raises(OpenRouterError, match="available exact advisor"):
                await preferences.update(initial["settings_id"], initial["revision"], general_default=model)
    finally:
        await client.aclose()


def answer(container_id=None, text="Advice"):
    output = [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": text},
    ]}]
    if container_id:
        output.insert(0, {"type": "openrouter:shell", "container_id": container_id})
    return {"status": "completed", "output": output, "usage": {"cost": 0.001}, "id": "gen-test"}


@pytest.fixture
def modules():
    names = ["openrouter_image_mcp.advisor", "openrouter_image_mcp.advisor_client"]
    assert all(importlib.util.find_spec(name) for name in names), "Guarded advisor service missing"
    return [importlib.import_module(name) for name in names]


@pytest.fixture
async def rig(modules, tmp_path, keys):
    service_mod, client_mod = modules
    keys.set(KEY_A, workspace_id=WORKSPACE)
    seen = []
    state = {"cleanup_fail": False, "inference_fail": False}

    async def handle(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{
                "id": MODEL, "name": "Advisor", "architecture": {"output_modalities": ["text"]},
                "supported_parameters": ["tools"], "pricing": {"prompt": "0.001"},
            }]})
        if path.endswith("/files") and request.method == "POST":
            return httpx.Response(200, json={"id": "or_file_12345678"})
        if request.method == "DELETE":
            return httpx.Response(503 if state["cleanup_fail"] else 204)
        if path.endswith("/responses"):
            if state["inference_fail"]:
                return httpx.Response(503, json={"error": {"message": KEY_A}})
            payload = json.loads(request.content)
            container = payload["tools"][0]["parameters"]["environment"]["container_id"]
            if state.get("foreign_reply"):
                container = "another_students_container"
            if state.get("switch_account"):
                keys.set(KEY_B, workspace_id=WORKSPACE)
            return httpx.Response(200, json=answer(container))
        pytest.fail(f"Unexpected provider operation: {request.method} {path}")

    client = client_mod.AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(handle))
    store = AdvisorStore(tmp_path / "advisor.sqlite3")
    service = service_mod.AdvisorService(WORKSPACE, store, client, credentials=keys.credentials(WORKSPACE))
    yield service, store, seen, state, service_mod
    await client.aclose()


@pytest.mark.parametrize("operation", ["get", "reset", "delete", "send"])
async def test_account_b_known_a_handle_denied_before_http(keys, rig, operation):
    service, _, seen, _, _ = rig
    chat = await service.start(MODEL, "A")
    seen.clear()
    keys.set(KEY_B, workspace_id=WORKSPACE)
    with pytest.raises(AdvisorAccessError, match="unavailable"):
        if operation == "send":
            await service.send(chat["chat_id"], "Read the other student's files")
        else:
            getattr(service, operation)(chat["chat_id"])
    assert seen == []
    assert service.list() == []


async def test_request_pins_container_disables_network_and_keeps_key_out_of_payload(rig):
    service, store, seen, _, mod = rig
    chat = await service.start(MODEL, "Critic")
    packet = mod.SkillPacket(name="critic", content="# Critic\nReview this design.", version="0.5.1")
    reply = await service.send(chat["chat_id"], "Review a fictional project", [packet])
    request = next(r for r in seen if r.url.path.endswith("/responses"))
    payload = json.loads(request.content)
    env = payload["tools"][0]["parameters"]["environment"]
    assert env["type"] == "container_reference"
    assert env["network_policy"] == {"type": "disabled"}
    assert payload["tools"][0]["parameters"]["engine"] == "openrouter"
    assert env["file_ids"] == ["or_file_12345678"]
    assert payload["store"] is False
    assert payload["session_id"] == "sidecar-" + chat["chat_id"]
    assert KEY_A not in request.content.decode()
    assert request.headers["authorization"] == "Bearer " + KEY_A
    assert reply["answer"] == "Advice"
    assert "container_id" not in reply
    assert any(r.method == "DELETE" and r.url.path.endswith("/files/or_file_12345678") for r in seen)
    assert service.get(chat["chat_id"])["messages"][-1]["content"] == "Advice"
    assert KEY_A not in store.path.read_bytes().decode(errors="ignore")


async def test_provider_foreign_container_reference_is_rejected_and_upload_cleaned(rig):
    service, _, seen, state, mod = rig
    chat = await service.start(MODEL, "A")
    state["foreign_reply"] = True
    with pytest.raises(OpenRouterError, match="container"):
        await service.send(chat["chat_id"], "Brief", [mod.SkillPacket(name="critic", content="Review")])
    assert service.get(chat["chat_id"])["messages"] == []
    assert any(r.method == "DELETE" for r in seen)


async def test_ambiguous_inference_is_not_retried_and_error_does_not_echo_key(rig):
    service, _, seen, state, mod = rig
    chat = await service.start(MODEL, "A")
    state["inference_fail"] = True
    with pytest.raises(OpenRouterError) as failure:
        await service.send(chat["chat_id"], "Brief", [mod.SkillPacket(name="critic", content="Review")])
    assert KEY_A not in str(failure.value)
    assert "billed" in str(failure.value)
    assert sum(r.url.path.endswith("/responses") for r in seen) == 1
    assert any(r.method == "DELETE" for r in seen)
    assert service.get(chat["chat_id"])["messages"] == []


async def test_cleanup_failure_blocks_inference_until_receipt_is_deleted(rig):
    service, _, seen, state, mod = rig
    chat = await service.start(MODEL, "A")
    state["cleanup_fail"] = True
    result = await service.send(chat["chat_id"], "Brief", [mod.SkillPacket(name="critic", content="Review")])
    assert result["cleanup_pending"] is True
    seen.clear()
    with pytest.raises(OpenRouterError, match="cleanup"):
        await service.send(chat["chat_id"], "Follow up")
    assert all(r.method == "DELETE" for r in seen)
    state["cleanup_fail"] = False
    seen.clear()
    await service.send(chat["chat_id"], "Follow up")
    assert seen[0].method == "DELETE"
    assert any(r.url.path.endswith("/responses") for r in seen)


async def test_mid_request_account_switch_does_not_reveal_or_save_answer(keys, rig):
    service, _, seen, state, mod = rig
    chat = await service.start(MODEL, "A")
    state["switch_account"] = True
    with pytest.raises(AdvisorAccessError, match="sign-in changed"):
        await service.send(chat["chat_id"], "Brief", [mod.SkillPacket(name="critic", content="Review")])
    assert service.list() == []
    deletion = next(r for r in seen if r.method == "DELETE")
    assert deletion.headers["authorization"] == "Bearer " + KEY_A
    keys.set(KEY_A, workspace_id=WORKSPACE)
    assert service.get(chat["chat_id"])["messages"] == []


@pytest.mark.parametrize("handle", ["nu_some_container", "../containers/foreign", "https://evil.invalid", "chat_%2f"])
async def test_malformed_handles_never_reach_http(rig, handle):
    service, _, seen, _, _ = rig
    with pytest.raises(AdvisorAccessError):
        await service.send(handle, "Brief")
    assert not seen


async def test_no_key_means_no_catalog_or_inference(keys, rig):
    service, _, seen, _, _ = rig
    keys.delete(workspace_id=WORKSPACE)
    with pytest.raises(OpenRouterError, match="not linked"):
        await service.start(MODEL, "A")
    assert not seen


async def test_advisor_follows_up_using_only_briefs_and_final_answers(rig):
    service, _, seen, _, _ = rig
    chat = await service.start(MODEL, "A")
    await service.send(chat["chat_id"], "First brief")
    await service.send(chat["chat_id"], "Follow up")
    calls = [json.loads(r.content) for r in seen if r.url.path.endswith("/responses")]
    assert calls[-1]["input"] == [
        {"role": "user", "content": "First brief"},
        {"role": "assistant", "content": "Advice"},
        {"role": "user", "content": "Follow up"},
    ]
    assert calls[0]["session_id"] == calls[-1]["session_id"]
    assert calls[0]["tools"][0]["parameters"]["environment"]["container_id"] == calls[-1]["tools"][0]["parameters"]["environment"]["container_id"]


async def test_extra_skill_fields_are_rejected(modules):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        modules[0].SkillPacket(name="critic", content="Review", file_ids=["foreign"])


async def test_delete_adapter_rejects_paths_before_http(rig):
    service, _, seen, _, _ = rig
    with pytest.raises(OpenRouterError):
        await service.client.delete_upload(KEY_A, "../containers/foreign")
    assert not seen


async def test_unknown_model_is_not_silently_substituted(rig):
    service, _, seen, _, _ = rig
    with pytest.raises(OpenRouterError, match="model"):
        await service.start("unknown/model", "A")
    assert all(r.url.path.endswith("/models") for r in seen)


async def test_active_credential_cannot_be_saved_in_title(rig):
    service, store, seen, _, _ = rig
    with pytest.raises(OpenRouterError, match="credential"):
        await service.start(MODEL, "My key: " + KEY_A)
    assert not seen
    assert not store.path.exists()


async def test_reloaded_history_bound_is_checked_under_lease(rig, monkeypatch):
    service, store, seen, _, _ = rig
    chat = await service.start(MODEL, "A")
    real_lease = store.lease

    @contextmanager
    def competing_turn(owner, chat_id):
        # Another service instance commits after the initial read but before this
        # request obtains its lease. Exercise the actual repository, not a fake row.
        with real_lease(owner, chat_id) as token:
            store.append_turn(owner, chat_id, "x" * 299_900, "Previous answer", token)
        with real_lease(owner, chat_id) as token:
            yield token

    monkeypatch.setattr(store, "lease", competing_turn)
    seen.clear()
    with pytest.raises(OpenRouterError, match="history"):
        await service.send(chat["chat_id"], "y" * 200)
    assert not seen


async def test_only_last_completed_final_message_is_an_advisor_answer(modules):
    body = answer(text="Final answer")
    body["output"] = [
        {"type": "reasoning", "summary": [{"text": "INTERNAL_TRACE"}]},
        {"type": "message", "role": "assistant", "status": "completed", "phase": "commentary",
         "content": [{"type": "output_text", "text": "INTERMEDIATE_COMMENTARY"}]},
        {"type": "message", "role": "assistant", "status": "in_progress",
         "content": [{"type": "output_text", "text": "UNFINISHED_TEXT"}]},
        body["output"][0] | {"status": "completed", "phase": "final_answer"},
    ]
    assert modules[0].AdvisorService._answer(body, "owned_container") == "Final answer"


async def test_provider_io_has_total_deadline_not_only_read_timeout(modules, monkeypatch):
    import asyncio

    entered = []

    @contextmanager
    def deadline_marker(seconds):
        entered.append(seconds)
        raise TimeoutError("Synthetic total deadline")
        yield  # Make it a context manager; __enter__ raises before network.

    class AsyncDeadline:
        async def __aenter__(self):
            with deadline_marker(30):
                pass

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(asyncio, "timeout", lambda seconds: AsyncDeadline())
    client = modules[1].AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(
        lambda request: pytest.fail("Deadline was bypassed")))
    try:
        with pytest.raises(OpenRouterError, match="confirmed"):
            await client.respond(KEY_A, {})
    finally:
        await client.aclose()
    assert entered == [30]


@pytest.mark.parametrize("configured", [1200, 1800, 3600])
async def test_long_response_deadline_and_short_control_deadline(modules, monkeypatch, configured):
    import asyncio

    deadlines = []
    real_timeout = asyncio.timeout

    def track_timeout(seconds):
        deadlines.append(seconds)
        return real_timeout(seconds)

    monkeypatch.setattr(asyncio, "timeout", track_timeout)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    client = modules[1].AdvisorClient(WORKSPACE, configured, transport=httpx.MockTransport(handle))
    try:
        await client.respond(KEY_A, {})
        await client.models(KEY_A)
    finally:
        await client.aclose()
    expected = min(configured, 1800)
    assert deadlines == [expected, 30]
    assert requests[0].extensions["timeout"]["read"] == expected
    assert requests[1].extensions["timeout"]["read"] == 30


async def test_response_total_timeout_cancels_work_without_retry(modules):
    import asyncio

    attempts = []
    cancelled = []

    async def slow_provider(request):
        attempts.append(request)
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)
        return httpx.Response(200, json={})

    client = modules[1].AdvisorClient(WORKSPACE, 0.01, transport=httpx.MockTransport(slow_provider))
    try:
        with pytest.raises(OpenRouterError, match="may have been billed.*no automatic retry"):
            await client.respond(KEY_A, {})
    finally:
        await client.aclose()
    assert len(attempts) == 1
    assert cancelled == [True]


async def test_confirmed_upload_deleted_when_local_receipt_cannot_be_saved(rig, monkeypatch):
    import sqlite3

    service, store, seen, _, mod = rig
    chat = await service.start(MODEL, "A")

    def disk_full(*args):
        raise sqlite3.OperationalError("Synthetic disk full")

    monkeypatch.setattr(store, "record_upload", disk_full)
    seen.clear()
    with pytest.raises(OpenRouterError, match="receipt"):
        await service.send(chat["chat_id"], "Brief", [mod.SkillPacket(name="critic", content="Review")])
    assert not any(r.url.path.endswith("/responses") for r in seen)
    assert any(r.method == "DELETE" and r.url.path.endswith("/files/or_file_12345678") for r in seen)


async def test_stale_cleanup_cannot_delete_new_operations_receipts(rig, monkeypatch):
    service, store, seen, _, _ = rig
    chat = await service.start(MODEL, "A")
    key, scope = service._credentials()
    clock = [1000]
    monkeypatch.setattr("openrouter_image_mcp.advisor_store.time.time", lambda: clock[0])
    with store.lease(scope, chat["chat_id"]) as old_token:
        clock[0] = 4000  # Expire the old lease, as after a clock jump or suspended process.
        with store.lease(scope, chat["chat_id"]) as new_token:
            store.record_upload(scope, chat["chat_id"], "or_file_NEW", new_token)
            seen.clear()
            with pytest.raises(AdvisorAccessError):
                await service._cleanup(key, scope, chat["chat_id"], old_token)
            assert seen == []
            assert store.pending_uploads(scope, chat["chat_id"]) == ["or_file_NEW"]
