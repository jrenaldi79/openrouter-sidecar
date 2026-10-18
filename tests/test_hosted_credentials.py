import asyncio
import base64
import os

import httpx
import pytest
import respx
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from openrouter_image_mcp.advisor_store import AdvisorAccessError, Scope
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.credentials import HostedCredentials
from openrouter_image_mcp.hosted.links import LinkRequiredError, LinkStore
from openrouter_image_mcp.hosted.openrouter_keys import exchange_code, verify_key
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.verifier import Auth0Verifier
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h

ALICE = Principal(h.ISSUER, "email|alice", h.WORKSPACE, "2027")


@pytest.fixture
def links(tmp_path):
    vault = Vault("v1:" + base64.b64encode(os.urandom(32)).decode())
    return LinkStore(tmp_path / "db.sqlite3", vault, "k" * 32, "https://sidecar.example")


async def _as(sub, workspace=h.WORKSPACE):
    verifier = Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, key_resolver=h.resolver)
    access = await verifier.verify_token(h.token(sub=sub, workspace=workspace))
    return auth_context_var.set(AuthenticatedUser(access))


async def test_resolve_uses_request_principal(links):
    links.save(ALICE, "sk-alice", "a")
    source = HostedCredentials(h.WORKSPACE, links, h.PROFILE)
    token = await _as("email|alice")
    try:
        key, scope = source.resolve()
    finally:
        auth_context_var.reset(token)
    assert key == "sk-alice"
    assert scope == Scope(ALICE.key, h.WORKSPACE)


async def test_unlinked_principal_gets_link_url(links):
    source = HostedCredentials(h.WORKSPACE, links, h.PROFILE)
    token = await _as("email|bob")
    try:
        with pytest.raises(LinkRequiredError):
            source.resolve()
    finally:
        auth_context_var.reset(token)


async def test_principal_from_other_workspace_is_refused(links):
    source = HostedCredentials("ws-2028", links, h.PROFILE)
    token = await _as("email|alice")
    try:
        with pytest.raises(AdvisorAccessError):
            source.resolve()
    finally:
        auth_context_var.reset(token)


def test_resolve_outside_request_requires_sign_in(links):
    with pytest.raises(AuthRequiredError):
        HostedCredentials(h.WORKSPACE, links, h.PROFILE).resolve()


async def test_check_survives_context_loss_and_detects_revocation(links):
    links.save(ALICE, "sk-alice", "a")
    source = HostedCredentials(h.WORKSPACE, links, h.PROFILE)
    token = await _as("email|alice")
    _, scope = source.resolve()
    auth_context_var.reset(token)
    source.check(scope)  # background heartbeats have no request context
    links.revoke(ALICE.key, h.WORKSPACE)
    with pytest.raises(AdvisorAccessError):
        source.check(scope)
    with pytest.raises(AdvisorAccessError):
        source.check(Scope(ALICE.key, "ws-2028"))


async def test_background_task_inherits_principal(links):
    links.save(ALICE, "sk-alice", "a")
    source = HostedCredentials(h.WORKSPACE, links, h.PROFILE)
    token = await _as("email|alice")
    try:
        task = asyncio.create_task(asyncio.to_thread(lambda: None))
        await task
        key = await asyncio.create_task(asyncio.sleep(0, result=source.resolve()[0]))
    finally:
        auth_context_var.reset(token)
    assert key == "sk-alice"


async def test_forget_revokes_current_link(links):
    links.save(ALICE, "sk-alice", "a")
    source = HostedCredentials(h.WORKSPACE, links, h.PROFILE)
    token = await _as("email|alice")
    try:
        source.forget()
    finally:
        auth_context_var.reset(token)
    assert not links.is_active(ALICE.key, h.WORKSPACE)


@respx.mock
async def test_exchange_code_posts_pkce():
    route = respx.post("https://openrouter.ai/api/v1/auth/keys").mock(
        return_value=httpx.Response(200, json={"key": "sk-new"}))
    assert await exchange_code("code1", "verifier1") == "sk-new"
    assert b"verifier1" in route.calls[0].request.content


@respx.mock
async def test_verify_key_returns_label_and_checks_workspace():
    respx.get("https://openrouter.ai/api/v1/key").mock(return_value=httpx.Response(
        200, json={"data": {"label": "Sidecar key", "workspace_id": h.WORKSPACE}}))
    assert await verify_key("sk-new", h.WORKSPACE) == "Sidecar key"
    with pytest.raises(AuthRequiredError):
        await verify_key("sk-new", "ws-2028")


@respx.mock
async def test_verify_key_rejects_invalid_key():
    respx.get("https://openrouter.ai/api/v1/key").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(AuthRequiredError):
        await verify_key("sk-bad", h.WORKSPACE)
