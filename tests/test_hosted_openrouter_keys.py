"""The OpenRouter key-management seam: request shapes, pagination and safe errors."""

import json

import httpx
import pytest
import respx

from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.openrouter_keys import (
    BUDGET_GUARDRAIL,
    TEAM_GUARDRAIL,
    GuardrailMissingError,
    KeyManagementError,
    KeyManager,
    current_key,
)

API = "https://openrouter.ai/api/v1"
MANAGEMENT = "sk-or-v1-management"
WS = "ws-2027"


@pytest.fixture
async def manager():
    keys = KeyManager(MANAGEMENT)
    yield keys
    await keys.aclose()


def _key(n, **extra):
    return {"hash": f"h{n}", "name": f"Sidecar · s{n}@u.edu · 2027", "disabled": False,
            "usage_monthly": 0.5, "workspace_id": WS} | extra


def _budget_routes(assign_status=200):
    guardrails = respx.get(f"{API}/guardrails").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "g-other", "name": "Something else"}, {"id": "g-budget", "name": BUDGET_GUARDRAIL}],
        "total_count": 2}))
    assign = respx.post(f"{API}/guardrails/g-budget/assignments/keys").mock(
        return_value=httpx.Response(assign_status, json={"assigned_count": 1}))
    return guardrails, assign


@respx.mock
async def test_create_posts_name_and_workspace_then_assigns_the_class_budget(manager):
    _, assign = _budget_routes()
    route = respx.post(f"{API}/keys").mock(return_value=httpx.Response(
        201, json={"key": "sk-or-v1-alice", "data": _key(1)}))
    created = await manager.create("Sidecar · alice@u.edu · 2027", WS)
    assert created.key == "sk-or-v1-alice" and created.hash == "h1"
    request = route.calls[0].request
    assert json.loads(request.content) == {"name": "Sidecar · alice@u.edu · 2027", "workspace_id": WS}
    assert request.headers["Authorization"] == f"Bearer {MANAGEMENT}"
    assert json.loads(assign.calls[0].request.content) == {"key_hashes": ["h1"]}


@respx.mock
async def test_budget_guardrail_lookup_is_cached_per_workspace(manager):
    guardrails, _ = _budget_routes()
    respx.post(f"{API}/keys").mock(return_value=httpx.Response(
        201, json={"key": "sk-or-v1-alice", "data": _key(1)}))
    await manager.create("a", WS)
    await manager.create("b", WS)
    assert guardrails.call_count == 1


@respx.mock
async def test_no_budget_guardrail_creates_no_key(manager):
    respx.get(f"{API}/guardrails").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "g-other", "name": "Something else"}], "total_count": 1}))
    create = respx.post(f"{API}/keys")
    with pytest.raises(KeyManagementError, match="budget"):
        await manager.create("n", WS)
    assert not create.called


@respx.mock
async def test_failed_assignment_deletes_the_new_key(manager):
    _budget_routes(assign_status=500)
    respx.post(f"{API}/keys").mock(return_value=httpx.Response(
        201, json={"key": "sk-or-v1-alice", "data": _key(1)}))
    delete = respx.delete(f"{API}/keys/h1").mock(return_value=httpx.Response(200, json={"deleted": True}))
    with pytest.raises(KeyManagementError):
        await manager.create("n", WS)
    assert delete.called


@respx.mock
async def test_create_without_key_or_hash_fails(manager):
    _budget_routes()
    respx.post(f"{API}/keys").mock(return_value=httpx.Response(201, json={"data": {}}))
    with pytest.raises(KeyManagementError):
        await manager.create("n", WS)


@respx.mock
async def test_errors_are_typed_and_never_carry_the_management_key(manager):
    _budget_routes()
    respx.post(f"{API}/keys").mock(return_value=httpx.Response(
        403, json={"error": {"message": f"bad key {MANAGEMENT}"}}))
    with pytest.raises(KeyManagementError) as exc:
        await manager.create("n", WS)
    assert exc.value.status == 403
    assert MANAGEMENT not in str(exc.value)


@respx.mock
async def test_transport_failure_is_a_key_management_error(manager):
    _budget_routes()
    respx.post(f"{API}/keys").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(KeyManagementError) as exc:
        await manager.create("n", WS)
    assert exc.value.status is None


@respx.mock
async def test_get_returns_data_or_none_when_gone(manager):
    respx.get(f"{API}/keys/h1").mock(return_value=httpx.Response(200, json={"data": _key(1)}))
    respx.get(f"{API}/keys/h2").mock(return_value=httpx.Response(404, json={}))
    assert (await manager.get("h1"))["hash"] == "h1"
    assert await manager.get("h2") is None


@respx.mock
async def test_delete_reports_whether_the_key_existed(manager):
    respx.delete(f"{API}/keys/h1").mock(return_value=httpx.Response(200, json={"deleted": True}))
    respx.delete(f"{API}/keys/h2").mock(return_value=httpx.Response(404, json={}))
    assert await manager.delete("h1") is True
    assert await manager.delete("h2") is False


@respx.mock
async def test_set_disabled_patches_only_disabled(manager):
    route = respx.patch(f"{API}/keys/h1").mock(return_value=httpx.Response(
        200, json={"data": _key(1, disabled=True)}))
    assert (await manager.set_disabled("h1", True))["disabled"] is True
    assert json.loads(route.calls[0].request.content) == {"disabled": True}


@respx.mock
async def test_list_keys_pages_through_a_workspace_including_disabled(manager):
    first = [_key(n) for n in range(100)]
    route = respx.get(f"{API}/keys").mock(side_effect=[
        httpx.Response(200, json={"data": first}), httpx.Response(200, json={"data": [_key(100)]})])
    keys = await manager.list_keys(WS)
    assert len(keys) == 101
    params = [call.request.url.params for call in route.calls]
    assert [p["offset"] for p in params] == ["0", "100"]
    assert all(p["workspace_id"] == WS and p["include_disabled"] == "true" for p in params)


@respx.mock
async def test_guardrail_reads(manager):
    respx.get(f"{API}/workspaces/{WS}").mock(return_value=httpx.Response(
        200, json={"data": {"id": WS, "default_guardrail_id": "g0"}}))
    guardrails = respx.get(f"{API}/guardrails").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "g0", "limit_usd": 25, "reset_interval": "monthly"}], "total_count": 1}))
    respx.get(f"{API}/guardrails/assignments/keys").mock(return_value=httpx.Response(
        200, json={"data": [{"guardrail_id": "g1", "key_hash": "h1"}], "total_count": 1}))
    assert (await manager.workspace(WS))["default_guardrail_id"] == "g0"
    assert (await manager.guardrails(WS))[0]["limit_usd"] == 25
    assert guardrails.calls[0].request.url.params["workspace_id"] == WS
    assert (await manager.key_assignments())[0]["key_hash"] == "h1"


@respx.mock
async def test_current_key_uses_the_students_own_key():
    route = respx.get(f"{API}/key").mock(return_value=httpx.Response(
        200, json={"data": {"usage_monthly": 1.25}}))
    assert (await current_key("sk-or-v1-alice"))["usage_monthly"] == 1.25
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-or-v1-alice"


@respx.mock
async def test_current_key_rejected_is_auth_required():
    respx.get(f"{API}/key").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(AuthRequiredError):
        await current_key("sk-or-v1-alice")


@respx.mock
async def test_create_with_the_team_guardrail_assigns_it_and_caches_per_name(manager):
    respx.get(f"{API}/guardrails").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "g-budget", "name": BUDGET_GUARDRAIL}, {"id": "g-team", "name": TEAM_GUARDRAIL}],
        "total_count": 2}))
    team = respx.post(f"{API}/guardrails/g-team/assignments/keys").mock(
        return_value=httpx.Response(200, json={"assigned_count": 1}))
    student = respx.post(f"{API}/guardrails/g-budget/assignments/keys").mock(
        return_value=httpx.Response(200, json={"assigned_count": 1}))
    respx.post(f"{API}/keys").mock(return_value=httpx.Response(
        201, json={"key": "sk-or-v1-team", "data": _key(1)}))
    await manager.create("Sidecar · abbott · 2027", WS, guardrail=TEAM_GUARDRAIL)
    await manager.create("Sidecar · alice@u.edu · 2027", WS)
    assert team.call_count == 1 and student.call_count == 1


@respx.mock
async def test_missing_team_guardrail_names_it_and_creates_no_key(manager):
    _budget_routes()  # only the student guardrail exists
    create = respx.post(f"{API}/keys")
    with pytest.raises(GuardrailMissingError, match=TEAM_GUARDRAIL) as missing:
        await manager.create("n", WS, guardrail=TEAM_GUARDRAIL)
    assert isinstance(missing.value, KeyManagementError)
    assert not create.called
