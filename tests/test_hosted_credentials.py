import asyncio
import base64
import os

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from openrouter_image_mcp.advisor_store import AdvisorAccessError, Scope
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.credentials import HostedCredentials
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.provisioning import (
    NOT_READY,
    NOT_SET_UP,
    KeyProvisioner,
)
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from openrouter_image_mcp.hosted.verifier import Auth0Verifier
from tests import jwt_helpers as h

ALICE = Principal(h.ISSUER, "email|alice", h.WORKSPACE, "2027")


@pytest.fixture
def keys(tmp_path):
    vault = Vault("v1:" + base64.b64encode(os.urandom(32)).decode())
    return StudentKeyStore(tmp_path / "db.sqlite3", vault)


async def _as(sub, workspace=h.WORKSPACE):
    verifier = Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, key_resolver=h.resolver)
    access = await verifier.verify_token(h.token(sub=sub, workspace=workspace))
    return auth_context_var.set(AuthenticatedUser(access))


async def test_resolve_uses_request_principal(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|alice")
    try:
        key, scope = source.resolve()
    finally:
        auth_context_var.reset(token)
    assert key == "sk-or-v1-alice"
    assert scope == Scope(ALICE.key, h.WORKSPACE)


async def test_principal_without_a_key_is_told_it_is_not_ready(keys):
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|bob")
    try:
        with pytest.raises(AuthRequiredError, match=NOT_READY):
            source.resolve()
    finally:
        auth_context_var.reset(token)


async def test_missing_key_reports_why_provisioning_failed(keys):
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|bob")
    try:
        await KeyProvisioner(keys, None, {h.WORKSPACE}).prepare(
            Principal(h.ISSUER, "email|bob", h.WORKSPACE))
        with pytest.raises(AuthRequiredError, match=NOT_SET_UP):
            source.resolve()
    finally:
        auth_context_var.reset(token)


async def test_principal_from_other_workspace_is_refused(keys):
    source = HostedCredentials("ws-2028", keys, h.PROFILE)
    token = await _as("email|alice")
    try:
        with pytest.raises(AdvisorAccessError):
            source.resolve()
    finally:
        auth_context_var.reset(token)


def test_resolve_outside_request_requires_sign_in(keys):
    with pytest.raises(AuthRequiredError):
        HostedCredentials(h.WORKSPACE, keys, h.PROFILE).resolve()


async def test_check_survives_context_loss_and_detects_rejection(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|alice")
    _, scope = source.resolve()
    auth_context_var.reset(token)
    source.check(scope)  # background heartbeats have no request context
    keys.mark_rejected(ALICE.key, h.WORKSPACE)
    with pytest.raises(AdvisorAccessError):
        source.check(scope)
    with pytest.raises(AdvisorAccessError):
        source.check(Scope(ALICE.key, "ws-2028"))


async def test_check_detects_a_removed_key(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    keys.remove(ALICE.key, h.WORKSPACE, "h1")
    with pytest.raises(AdvisorAccessError):
        source.check(Scope(ALICE.key, h.WORKSPACE))


async def test_background_task_inherits_principal(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|alice")
    try:
        await asyncio.create_task(asyncio.to_thread(lambda: None))
        key = await asyncio.create_task(asyncio.sleep(0, result=source.resolve()[0]))
    finally:
        auth_context_var.reset(token)
    assert key == "sk-or-v1-alice"


async def test_forget_marks_the_key_rejected_without_deleting_it(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    source = HostedCredentials(h.WORKSPACE, keys, h.PROFILE)
    token = await _as("email|alice")
    try:
        source.forget()
    finally:
        auth_context_var.reset(token)
    assert not keys.is_active(ALICE.key, h.WORKSPACE)
    assert keys.row(ALICE.key, h.WORKSPACE).rejected_at is not None
