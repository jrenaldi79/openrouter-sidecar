"""Known identifiers must not authorize a foreign credential or workspace."""

import importlib
import importlib.util

import pytest


@pytest.fixture
def module():
    name = "openrouter_image_mcp.advisor_store"
    assert importlib.util.find_spec(name), "The scoped advisor repository is missing"
    return importlib.import_module(name)


@pytest.fixture
def store(module, db_target):
    return module.AdvisorStore(db_target)


@pytest.fixture
def a(module):
    return module.Scope("owner-a", "workspace-a")


@pytest.fixture
def b(module):
    return module.Scope("owner-b", "workspace-a")


def test_persists_owned_history_without_credentials(store, module, a):
    chat = store.create(a, "test/model", "Critic")
    with store.lease(a, chat.id) as token:
        store.append_turn(a, chat.id, "Brief", "Answer", token)
    reopened = module.AdvisorStore(store.target)
    assert reopened.get(a, chat.id).history == [
        {"role": "user", "content": "Brief"},
        {"role": "assistant", "content": "Answer"},
    ]
    assert reopened.list(a)[0].id == chat.id
    assert chat.container_id != chat.id
    assert len(chat.container_id) <= 40


@pytest.mark.parametrize("operation", ["get", "reset", "delete", "lease"])
def test_known_foreign_id_is_rejected(store, module, a, b, operation):
    chat = store.create(a, "test/model", "A")
    with pytest.raises(module.AdvisorAccessError, match="unavailable"):
        if operation == "lease":
            with store.lease(b, chat.id):
                pytest.fail("foreign lease granted")
        else:
            getattr(store, operation)(b, chat.id)
    assert store.get(a, chat.id).title == "A"
    assert store.list(b) == []


def test_workspace_is_part_of_owner_scope(store, module, a):
    chat = store.create(a, "test/model", "A")
    with pytest.raises(module.AdvisorAccessError):
        store.get(module.Scope(a.owner, "workspace-b"), chat.id)


def test_reset_rotates_container_and_preserves_history(store, a):
    chat = store.create(a, "test/model", "A")
    with store.lease(a, chat.id) as token:
        store.append_turn(a, chat.id, "Brief", "Answer", token)
    reset = store.reset(a, chat.id)
    assert reset.container_id != chat.container_id
    assert reset.history == store.get(a, chat.id).history
    assert reset.history


def test_cross_instance_lease_blocks_mutations_and_releases_on_error(store, module, a):
    chat = store.create(a, "test/model", "A")
    another = module.AdvisorStore(store.target)
    with pytest.raises(RuntimeError, match="synthetic"), store.lease(a, chat.id):
        for operation in (another.reset, another.delete):
            with pytest.raises(module.AdvisorAccessError, match="busy"):
                operation(a, chat.id)
        with pytest.raises(module.AdvisorAccessError, match="busy"), another.lease(a, chat.id):
            pytest.fail("duplicate lease")
        raise RuntimeError("synthetic")
    with another.lease(a, chat.id):
        pass


def test_turn_cannot_be_appended_without_current_lease(store, module, a, b):
    chat = store.create(a, "test/model", "A")
    for scope in (a, b):
        with pytest.raises(module.AdvisorAccessError):
            store.append_turn(scope, chat.id, "Bad", "Bad", "forged")
    assert store.get(a, chat.id).history == []


def test_long_call_keeps_exclusive_lease_and_can_save_answer(store, module, a, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    chat = store.create(a, "test/model", "Long task")
    another = module.AdvisorStore(store.target)
    with store.lease(a, chat.id) as token:
        clock[0] += 31 * 60
        with pytest.raises(module.AdvisorAccessError, match="busy"):
            another.reset(a, chat.id)
        with pytest.raises(module.AdvisorAccessError, match="busy"), another.lease(a, chat.id):
            pytest.fail("Long-running advisor lost its exclusive lease")
        store.append_turn(a, chat.id, "Long brief", "Late answer", token)
    assert another.get(a, chat.id).history[-1]["content"] == "Late answer"
    with another.lease(a, chat.id):
        pass


def test_pending_cleanup_owned_and_prevents_delete_or_reset(store, module, a, b):
    chat = store.create(a, "test/model", "A")
    with store.lease(a, chat.id) as token:
        store.record_upload(a, chat.id, "or_file_123", token)
    assert store.pending_uploads(a, chat.id) == ["or_file_123"]
    for scope in (a, b):
        with pytest.raises(module.AdvisorAccessError):
            store.reset(scope, chat.id)
        with pytest.raises(module.AdvisorAccessError):
            store.delete(scope, chat.id)
    with pytest.raises(module.AdvisorAccessError):
        store.pending_uploads(b, chat.id)
    with store.lease(a, chat.id) as token:
        store.forget_upload(a, chat.id, "or_file_123", token)
    store.delete(a, chat.id)
    assert store.list(a) == []


@pytest.mark.parametrize("chat_id", ["missing", "../containers/other", "https://evil.invalid", "chat_%2f"])
def test_unknown_and_malformed_handles_have_same_denial(store, module, a, chat_id):
    with pytest.raises(module.AdvisorAccessError, match="unavailable"):
        store.get(a, chat_id)
