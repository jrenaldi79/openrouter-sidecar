"""The local export bundle: what leaves the computer, and what an import will accept."""

import json

import pytest

from openrouter_image_mcp import local_bundle as lb
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.session_files import FilePacket, SessionFiles

WS = COHORT_WORKSPACES["2027"]
ME = Scope("a" * 64, WS)
OTHER = Scope("b" * 64, WS)


@pytest.fixture
def store(db_target):
    return AdvisorStore(db_target)


def _chat(store, scope, title, turns=(("Brief", "Answer"),), files=()):
    chat = store.create(scope, "openai/gpt-test", title)
    for prompt, answer in turns:
        with store.lease(scope, chat.id) as token:
            store.append_turn(scope, chat.id, prompt, answer, token)
    if files:
        SessionFiles(store).upsert(scope, chat.id, [FilePacket(path=p, content=c) for p, c in files])
    return chat


def test_export_holds_history_files_and_preferences_but_no_secrets(store):
    chat = _chat(store, ME, "Critic", files=[("notes/brief.md", "# Brief")])
    lb.seed_preferences(store.target, ME, {"general_default": "openai/gpt-test",
                                           "family_defaults": {"google": "google/gemini-test"},
                                           "onboarding_completed": True})
    bundle = lb.export_bundle(store.target, ME, cohort="2027")
    assert (bundle["format"], bundle["version"], bundle["workspace"]) == (lb.FORMAT, 1, WS)
    assert bundle["chats"] == [{
        "title": "Critic", "model": "openai/gpt-test", "sandbox": True,
        "history": [{"role": "user", "content": "Brief"}, {"role": "assistant", "content": "Answer"}],
        "files": [{"path": "notes/brief.md", "content": "# Brief"}]}]
    assert bundle["preferences"]["general_default"] == "openai/gpt-test"
    assert bundle["preferences"]["family_defaults"]["google"] == "google/gemini-test"
    text = json.dumps(bundle)
    for secret in (chat.container_id, chat.id, ME.owner, "settings_", "lease", "uploads"):
        assert secret not in text


def test_export_is_limited_to_the_signed_in_credential(store):
    _chat(store, ME, "Mine")
    _chat(store, OTHER, "Not mine")
    assert [c["title"] for c in lb.export_bundle(store.target, ME, cohort="2027")["chats"]] == ["Mine"]


def test_export_keeps_chat_order_and_redacts_key_shaped_text(store):
    _chat(store, ME, "First", turns=[("my key is sk-or-v1-" + "f" * 64, "ok")])
    _chat(store, ME, "Second")
    chats = lb.export_bundle(store.target, ME, cohort="2027")["chats"]
    assert [c["title"] for c in chats] == ["First", "Second"]
    assert "f" * 64 not in json.dumps(chats)


def test_export_without_preferences(store):
    assert lb.export_bundle(store.target, ME, cohort="2027")["preferences"] is None


def test_round_trip_and_digest_ignores_export_time(store):
    _chat(store, ME, "Critic", files=[("a.txt", "x")])
    bundle = lb.export_bundle(store.target, ME, cohort="2027")
    parsed = lb.parse_bundle(json.dumps(bundle).encode())
    assert parsed["chats"] == bundle["chats"]
    later = parsed | {"exported_at": "2030-01-01T00:00:00+00:00"}
    assert lb.bundle_digest(later) == lb.bundle_digest(bundle)


def _bundle(**changes):
    chat = {"title": "T", "model": "openai/gpt-test", "sandbox": False,
            "history": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
            "files": []}
    base = {"format": lb.FORMAT, "version": 1, "exported_at": "2026-10-19T00:00:00+00:00",
            "cohort": "2027", "workspace": WS, "chats": [chat], "preferences": None}
    return base | changes


def _chat_with(**changes):
    return _bundle()["chats"][0] | changes


@pytest.mark.parametrize("bad", [
    b"not json", b"[]", json.dumps(_bundle(format="other")).encode(),
    json.dumps(_bundle(version=2)).encode(),
    json.dumps(_bundle(workspace=7)).encode(),
    json.dumps(_bundle(chats=[_chat_with(model="no slash")])).encode(),
    json.dumps(_bundle(chats=[_chat_with(title="x" * 201)])).encode(),
    json.dumps(_bundle(chats=[_chat_with(history=[{"role": "system", "content": "x"}])])).encode(),
    json.dumps(_bundle(chats=[_chat_with(history=[{"role": "user", "content": 5}])])).encode(),
    json.dumps(_bundle(chats=[_chat_with(files=[{"path": "../up", "content": "x"}])])).encode(),
    json.dumps(_bundle(chats=[_chat_with(extra=1)])).encode(),
    json.dumps(_bundle(chats=[_chat_with()] * (lb.MAX_CHATS + 1))).encode(),
    json.dumps(_bundle(preferences={"general_default": "bad", "family_defaults": {},
                                    "onboarding_completed": False})).encode(),
    json.dumps(_bundle(preferences={"general_default": None, "family_defaults": {"nope": None},
                                    "onboarding_completed": False})).encode(),
])
def test_parse_rejects_malformed_bundles(bad):
    with pytest.raises(lb.BundleError):
        lb.parse_bundle(bad)


def test_parse_rejects_oversized_bundles():
    with pytest.raises(lb.BundleError, match="MB"):
        lb.parse_bundle(b" " * (lb.MAX_BUNDLE_BYTES + 1))


def test_seed_preferences_only_fills_an_empty_account(store):
    first = {"general_default": "openai/gpt-test", "family_defaults": {}, "onboarding_completed": True}
    assert lb.seed_preferences(store.target, ME, first) is True
    second = first | {"general_default": "google/gemini-test"}
    assert lb.seed_preferences(store.target, ME, second) is False
    assert lb.read_preferences(store.target, ME)["general_default"] == "openai/gpt-test"
    assert lb.read_preferences(store.target, OTHER) is None


def test_seed_fills_a_hosted_row_that_has_no_choices_yet(store):
    empty = {"general_default": None, "family_defaults": {}, "onboarding_completed": False}
    assert lb.seed_preferences(store.target, ME, empty) is True
    chosen = empty | {"general_default": "openai/gpt-test"}
    assert lb.seed_preferences(store.target, ME, chosen) is True
    assert lb.read_preferences(store.target, ME)["general_default"] == "openai/gpt-test"
