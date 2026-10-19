"""Provision on first use: one key per principal and workspace, orphans removed, clear failures."""

import asyncio
import base64
import hashlib
import os

import pytest

from openrouter_image_mcp.errors import AuthRequiredError, OpenRouterError
from openrouter_image_mcp.hosted.openrouter_keys import CreatedKey, KeyManagementError
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.provisioning import (
    DISABLED,
    NOT_SERVED,
    NOT_SET_UP,
    KeyProvisioner,
    key_name,
    pending_reason,
)
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h

WS = "ws-2027"
ALICE = Principal(h.ISSUER, "email|alice", WS, "2027", "alice@u.edu")


class FakeManager:
    """Stands in for KeyManager: records creations and deletions."""

    def __init__(self):
        self.created, self.deleted, self.keys = [], [], {}
        self.delay, self.fail_create = 0.0, None

    async def create(self, name, workspace_id):
        await asyncio.sleep(self.delay)
        if self.fail_create:
            raise self.fail_create
        n = len(self.created) + 1
        self.created.append((name, workspace_id))
        self.keys[f"h{n}"] = {"hash": f"h{n}", "name": name, "disabled": False}
        return CreatedKey(f"sk-or-v1-new{n}", f"h{n}", self.keys[f"h{n}"])

    async def get(self, key_hash):
        return self.keys.get(key_hash)

    async def delete(self, key_hash):
        self.deleted.append(key_hash)
        return self.keys.pop(key_hash, None) is not None


@pytest.fixture
def store(db_target):
    return StudentKeyStore(db_target, Vault("v1:" + base64.b64encode(os.urandom(32)).decode()))


@pytest.fixture
def manager():
    return FakeManager()


@pytest.fixture
def provisioner(store, manager):
    return KeyProvisioner(store, manager, {WS})


def test_key_name_uses_email_or_a_stable_hash():
    assert key_name(ALICE) == "Sidecar · alice@u.edu · 2027"
    anonymous = Principal(h.ISSUER, "email|zed", WS, None, None)
    digest = hashlib.sha256(anonymous.key.encode()).hexdigest()[:10]
    assert key_name(anonymous, cohort="2027") == f"Sidecar · user-{digest} · 2027"


async def test_first_use_creates_and_stores_a_key(provisioner, manager, store):
    row = await provisioner.ensure(ALICE)
    assert manager.created == [("Sidecar · alice@u.edu · 2027", WS)]
    assert row.key_hash == "h1" and store.key_for(ALICE) == "sk-or-v1-new1"
    assert (await provisioner.ensure(ALICE)).key_hash == "h1"
    assert len(manager.created) == 1


async def test_concurrent_first_calls_create_one_key(provisioner, manager):
    manager.delay = 0.05
    rows = await asyncio.gather(*(provisioner.ensure(ALICE) for _ in range(5)))
    assert {row.key_hash for row in rows} == {"h1"}
    assert len(manager.created) == 1 and manager.deleted == []


async def test_a_key_that_loses_the_insert_race_is_deleted(provisioner, manager, store):
    real_insert = store.insert

    def racing_insert(principal, key, key_hash, name):
        real_insert(principal, "sk-or-v1-other-replica", "h-other", "n")  # another replica wins
        return real_insert(principal, key, key_hash, name)

    store.insert = racing_insert
    row = await provisioner.ensure(ALICE)
    assert row.key_hash == "h-other"
    assert manager.deleted == ["h1"]


async def test_a_key_that_cannot_be_stored_is_deleted(provisioner, manager, store):
    def broken(*_args):
        raise RuntimeError("database down")

    store.insert = broken
    with pytest.raises(RuntimeError):
        await provisioner.ensure(ALICE)
    assert manager.deleted == ["h1"]


async def test_management_failure_gives_a_retry_message(provisioner, manager, store):
    manager.fail_create = KeyManagementError("OpenRouter key management failed (HTTP 500).", 500)
    with pytest.raises(OpenRouterError, match="try again"):
        await provisioner.ensure(ALICE)
    assert store.row(ALICE.key, WS) is None


async def test_without_a_management_key_only_existing_keys_work(store):
    provisioner = KeyProvisioner(store, None, {WS})
    with pytest.raises(AuthRequiredError) as exc:
        await provisioner.ensure(ALICE)
    assert str(exc.value) == NOT_SET_UP and "OPENROUTER" not in NOT_SET_UP
    store.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    assert (await provisioner.ensure(ALICE)).key_hash == "h1"


async def test_unserved_workspace_is_refused(provisioner, manager):
    with pytest.raises(AuthRequiredError, match=NOT_SERVED):
        await provisioner.ensure(Principal(h.ISSUER, "email|x", "ws-unknown"))
    assert manager.created == []


async def test_rejected_key_that_was_deleted_is_replaced(provisioner, manager, store):
    await provisioner.ensure(ALICE)
    store.mark_rejected(ALICE.key, WS)
    manager.keys.pop("h1")  # an admin deleted it
    row = await provisioner.ensure(ALICE)
    assert row.key_hash == "h2" and store.key_for(ALICE) == "sk-or-v1-new2"


async def test_rejected_key_that_is_disabled_is_reported_not_replaced(provisioner, manager, store):
    await provisioner.ensure(ALICE)
    store.mark_rejected(ALICE.key, WS)
    manager.keys["h1"]["disabled"] = True
    with pytest.raises(AuthRequiredError, match=DISABLED):
        await provisioner.ensure(ALICE)
    assert len(manager.created) == 1
    manager.keys["h1"]["disabled"] = False  # re-enabled by the instructor
    assert (await provisioner.ensure(ALICE)).rejected_at is None
    assert store.key_for(ALICE) == "sk-or-v1-new1"


async def test_prepare_records_why_no_key_is_available(store):
    provisioner = KeyProvisioner(store, None, {WS})
    await provisioner.prepare(ALICE)
    assert pending_reason() == NOT_SET_UP
    store.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    await provisioner.prepare(ALICE)
    assert pending_reason() is None
