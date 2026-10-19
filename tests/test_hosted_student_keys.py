"""Provisioned student keys at rest: one row per principal and workspace, sealed, rotatable."""

import base64
import os

import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h

ALICE = Principal(h.ISSUER, "email|alice", "ws-2027", "2027", "alice@u.edu")
DAY = 86400


def _vault_spec():
    return "v1:" + base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def keys(db_target):
    return StudentKeyStore(db_target, Vault(_vault_spec()))


def test_insert_once_then_read_back(keys):
    assert keys.insert(ALICE, "sk-or-v1-alice", "h1", "Sidecar · alice@u.edu · 2027") is True
    assert keys.insert(ALICE, "sk-or-v1-other", "h2", "dup") is False
    assert keys.key_for(ALICE) == "sk-or-v1-alice"
    row = keys.row(ALICE.key, ALICE.workspace_id)
    assert (row.key_hash, row.key_name, row.rejected_at) == ("h1", "Sidecar · alice@u.edu · 2027", None)


def test_key_is_sealed_and_bound_to_the_principal(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    with storage.connect(keys.target) as db:
        sealed = db.execute("SELECT sealed_key FROM student_keys").fetchone()["sealed_key"]
    assert "sk-or-v1-alice" not in sealed
    with pytest.raises(ValueError):
        keys.vault.open(sealed, principal_key="iss|mallory")


def test_missing_and_rejected_keys_are_not_served(keys):
    assert keys.key_for(ALICE) is None
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    assert keys.is_active(ALICE.key, ALICE.workspace_id)
    keys.mark_rejected(ALICE.key, ALICE.workspace_id)
    assert keys.key_for(ALICE) is None
    assert not keys.is_active(ALICE.key, ALICE.workspace_id)
    keys.clear_rejected(ALICE.key, ALICE.workspace_id)
    assert keys.key_for(ALICE) == "sk-or-v1-alice"


def test_other_workspace_is_a_separate_row(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    other = Principal(ALICE.issuer, ALICE.subject, "ws-2028", "2028")
    assert keys.key_for(other) is None
    assert not keys.is_active(ALICE.key, "ws-2028")


def test_remove_only_deletes_the_matching_hash(keys):
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    keys.remove(ALICE.key, ALICE.workspace_id, "stale")
    assert keys.key_for(ALICE) == "sk-or-v1-alice"
    keys.remove(ALICE.key, ALICE.workspace_id, "h1")
    assert keys.row(ALICE.key, ALICE.workspace_id) is None


def test_rotation_reseals_on_read(db_target):
    old = _vault_spec()
    StudentKeyStore(db_target, Vault(old)).insert(ALICE, "sk-or-v1-alice", "h1", "n")
    rotated = StudentKeyStore(db_target, Vault(_vault_spec().replace("v1:", "v2:") + "," + old))
    assert rotated.key_for(ALICE) == "sk-or-v1-alice"
    with storage.connect(db_target) as db:
        assert db.execute("SELECT sealed_key FROM student_keys").fetchone()["sealed_key"].startswith("v2:")


def test_use_is_stamped_at_most_hourly(keys):
    keys.clock = lambda: 1000.0
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    keys.clock = lambda: 1000.0 + 600
    keys.key_for(ALICE)
    assert keys.row(ALICE.key, ALICE.workspace_id).last_used_at == 1000.0
    keys.clock = lambda: 1000.0 + 7200
    keys.key_for(ALICE)
    assert keys.row(ALICE.key, ALICE.workspace_id).last_used_at == 8200.0


def test_idle_lists_unused_keys_without_chats(keys):
    keys.clock = lambda: 0.0
    keys.insert(ALICE, "sk-or-v1-alice", "h1", "n")
    bob = Principal(h.ISSUER, "email|bob", "ws-2027", "2027")
    keys.insert(bob, "sk-or-v1-bob", "h2", "n")
    AdvisorStore(keys.target).create(Scope(bob.key, bob.workspace_id), "m", "kept")
    assert [row.key_hash for row in keys.idle(cutoff=DAY)] == ["h1"]
    assert keys.idle(cutoff=-1) == []
