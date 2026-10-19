"""Offline preference tests against the real advisor credential boundary."""

import importlib
import importlib.util
import json
from datetime import UTC, datetime

import httpx
import pytest

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.errors import OpenRouterError

KEY_A = "sk-or-v1-synthetic-preference-a"
KEY_B = "sk-or-v1-synthetic-preference-b"
WORKSPACE = COHORT_WORKSPACES["2027"]
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
RECENT = int(NOW.timestamp())
MODELS = [
    {"id": "google/gemini-test", "name": "Gemini test", "pricing": {"prompt": "0.001", "completion": "0.002"}, "context_length": 128000},
    {"id": "openai/gpt-test", "name": "GPT test", "pricing": {"prompt": "0.003"}, "context_length": 64000},
    {"id": "qwen/qwen-test", "name": "Qwen test", "pricing": {}, "context_length": 200000},
    {"id": "google/other-advisor", "name": "Other", "pricing": {}, "context_length": 4000},
]
MODELS = [row | {"created": RECENT} for row in MODELS]


async def test_batch_variants_are_excluded_from_preferences(rig):
    preferences, _, state, _, _ = rig
    state["models"] += [MODELS[1] | {"id": "openai/gpt-test:batch"},
                        MODELS[1] | {"id": "openai/special", "name": "GPT (Batch)"}]
    assert {row["id"] for row in (await preferences.view())["models"]} == {row["id"] for row in MODELS}


async def test_grok_default_resolves_and_retains_catalog_filters(rig):
    preferences, _, state, _, _ = rig
    state["models"] += [MODELS[0] | {"id": "x-ai/grok-test", "name": "Grok"},
                        MODELS[0] | {"id": "x-ai/grok-old", "created": 1},
                        MODELS[0] | {"id": "x-ai/grok-test:batch"}]
    models = (await preferences.view())["models"]
    assert [m["id"] for m in models if m["family"] == "x-ai"] == ["x-ai/grok-test"]
    initial = preferences.get()
    await preferences.update(initial["settings_id"], 0, family_defaults={"x-ai": "x-ai/grok-test"})
    assert (await preferences.resolve("Grok"))["model"] == "x-ai/grok-test"
    assert (await preferences.resolve("xAI"))["model"] == "x-ai/grok-test"



def _sqlite_only(store):
    if store.path is None:
        pytest.skip("inspects the local SQLite file or local export")


@pytest.fixture
async def rig(tmp_path, keys, db_target):
    spec = importlib.util.find_spec("openrouter_image_mcp.preferences")
    assert spec is not None, "PreferenceService has not been implemented"
    module = importlib.import_module("openrouter_image_mcp.preferences")
    keys.set(KEY_A, workspace_id=WORKSPACE)
    state = {"models": json.loads(json.dumps(MODELS)), "switch": False}
    seen = []

    async def handle(request):
        seen.append(request)
        assert request.url.path.endswith("/models"), "Preferences must never invoke inference"
        if state["switch"]:
            keys.set(KEY_B, workspace_id=WORKSPACE)
        rows = [row | {"architecture": {"output_modalities": ["text"]},
                       "supported_parameters": ["tools"]} for row in state["models"]]
        return httpx.Response(200, json={"data": rows})

    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(handle))
    advisors = AdvisorService(WORKSPACE, AdvisorStore(db_target), client, credentials=keys.credentials(WORKSPACE))
    yield module.PreferenceService(advisors, utcnow=lambda: NOW), advisors, state, seen, module
    await client.aclose()


def test_initial_preferences_are_stable_and_have_no_identity(rig):
    _sqlite_only(rig[1].store)
    preferences, advisors, _, seen, module = rig
    initial = preferences.get()
    assert initial == module.PreferenceService(advisors).get()
    assert initial == {"settings_id": initial["settings_id"], "revision": 0,
                       "general_default": None, "family_defaults": {"google": None, "openai": None, "open_weight": None, "x-ai": None},
                       "onboarding_completed": False}
    assert len(initial["settings_id"]) >= 32
    assert KEY_A not in json.dumps(initial)
    assert advisors._credentials()[1].owner not in json.dumps(initial)
    assert KEY_A.encode() not in advisors.store.path.read_bytes()
    assert not seen


async def test_saves_persist_after_service_restart(rig):
    preferences, advisors, _, _, module = rig
    initial = preferences.get()
    saved = await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor",
                                     family_defaults={"google": "google/gemini-test"}, onboarding_completed=True)
    assert saved["revision"] == 1
    assert saved["general_default"] == "google/other-advisor"
    assert saved["family_defaults"] == {"google": "google/gemini-test", "openai": None, "open_weight": None, "x-ai": None}
    assert saved["onboarding_completed"] is True
    assert module.PreferenceService(advisors).get() == saved


@pytest.mark.parametrize("alias,family", [("Gemini", "google"), ("Google", "google"), ("ChatGPT", "openai"), ("Chat GPT", "openai"), ("GPT", "openai"), ("OpenAI", "openai"), ("open weight", "open_weight"), ("openweight", "open_weight"), ("open-weight", "open_weight"), ("open source", "open_weight")])
async def test_family_alias_uses_selected_default(rig, alias, family):
    preferences, _, _, _, _ = rig
    model = next(row for row in MODELS if row["id"].startswith(("qwen" if family == "open_weight" else family) + "/"))
    initial = preferences.get()
    await preferences.update(initial["settings_id"], 0, family_defaults={family: model["id"]})
    assert await preferences.resolve(alias) == {"model": model["id"], "name": model["name"], "source": "family_default"}


async def test_general_default_and_explicit_override(rig):
    preferences, _, _, seen, _ = rig
    initial = preferences.get()
    await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")
    assert await preferences.resolve() == {"model": "google/other-advisor", "name": "Other", "source": "general_default"}
    assert await preferences.resolve("openai/gpt-test") == {"model": "openai/gpt-test", "name": "GPT test", "source": "explicit"}
    assert all(request.url.path.endswith("/models") for request in seen)


async def test_partial_update_preserves_defaults_and_empty_clears(rig):
    preferences, _, _, _, _ = rig
    initial = preferences.get()
    await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor",
                             family_defaults={"google": "google/gemini-test", "openai": "openai/gpt-test"})
    saved = await preferences.update(initial["settings_id"], 1, family_defaults={"google": ""}, onboarding_completed=True)
    assert saved["general_default"] == "google/other-advisor"
    assert saved["family_defaults"] == {"google": None, "openai": "openai/gpt-test", "open_weight": None, "x-ai": None}
    saved = await preferences.update(initial["settings_id"], 2, general_default="", family_defaults={"openai": None})
    assert saved["general_default"] is None
    assert saved["family_defaults"]["openai"] == "openai/gpt-test"
    assert saved["onboarding_completed"] is True


@pytest.mark.parametrize("values", [
    {"family_defaults": {"unknown": "openai/gpt-test"}},
    {"family_defaults": {"google": "openai/gpt-test"}},
    {"family_defaults": {"google": "missing/model"}},
    {"family_defaults": []},
    {"general_default": "Gemini"},
    {"general_default": "missing/model"},
    {"general_default": ["google/other-advisor"]},
    {"general_default": "x" * 201},
    {"onboarding_completed": 1},
    {"family_defaults": {"openai": True}},
])
async def test_invalid_update_is_atomic(rig, values):
    preferences, _, _, _, _ = rig
    initial = preferences.get()
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, **values)
    assert preferences.get() == initial


async def test_invalid_later_family_does_not_save_earlier_changes(rig):
    preferences, _, _, _, _ = rig
    initial = preferences.get()
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor",
                                 family_defaults={"google": "google/gemini-test", "openai": "missing/model"},
                                 onboarding_completed=True)
    assert preferences.get() == initial


@pytest.mark.parametrize("revision", [True, -1, "0", None, 2])
async def test_malformed_or_stale_revision_does_not_write(rig, revision):
    preferences, _, _, _, _ = rig
    initial = preferences.get()
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], revision, general_default="google/other-advisor")
    assert preferences.get() == initial


async def test_stale_revision_and_wrong_id_rejected_before_catalog(rig):
    preferences, _, _, seen, _ = rig
    initial = preferences.get()
    saved = await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")
    seen.clear()
    with pytest.raises(OpenRouterError, match="Refresh|refresh|changed"):
        await preferences.update(initial["settings_id"], 0, general_default="openai/gpt-test")
    with pytest.raises(OpenRouterError):
        await preferences.update("settings_unknown", 1, general_default="openai/gpt-test")
    with pytest.raises(OpenRouterError):
        preferences.assert_context("settings_unknown")
    assert preferences.get() == saved
    assert not seen


async def test_revision_is_rechecked_after_catalog_for_competing_instance(rig, monkeypatch):
    preferences, advisors, _, _, module = rig
    initial = preferences.get()
    other = module.PreferenceService(advisors, utcnow=lambda: NOW)
    real_models = advisors.models

    async def competing_models():
        monkeypatch.setattr(advisors, "models", real_models)
        await other.update(initial["settings_id"], 0, general_default="openai/gpt-test")
        return await real_models()

    monkeypatch.setattr(advisors, "models", competing_models)
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")
    assert preferences.get()["general_default"] == "openai/gpt-test"
    assert preferences.get()["revision"] == 1


async def test_two_owners_and_two_workspaces_are_independent(keys, rig):
    preferences, advisors, _, seen, module = rig
    initial = preferences.get()
    saved = await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")
    keys.set(KEY_B, workspace_id=WORKSPACE)
    account_b = preferences.get()
    assert account_b["settings_id"] != initial["settings_id"]
    assert account_b["general_default"] is None
    seen.clear()
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, general_default="openai/gpt-test")
    assert not seen
    other_workspace = COHORT_WORKSPACES["2028"]
    keys.set(KEY_A, workspace_id=other_workspace)
    other_client = AdvisorClient(other_workspace, 30, transport=advisors.client._http._transport)
    try:
        scoped = module.PreferenceService(AdvisorService(other_workspace, advisors.store, other_client,
                                                         credentials=keys.credentials(other_workspace)))
        assert scoped.get()["settings_id"] not in {initial["settings_id"], account_b["settings_id"]}
        assert scoped.get()["general_default"] is None
        with pytest.raises(OpenRouterError):
            scoped.assert_context(initial["settings_id"])
        keys.set(KEY_B, workspace_id=other_workspace)
        assert scoped.get()["settings_id"] not in {initial["settings_id"], account_b["settings_id"]}
    finally:
        await other_client.aclose()
    keys.set(KEY_A, workspace_id=WORKSPACE)
    assert preferences.get() == saved


@pytest.mark.parametrize("operation", ["view", "resolve", "update"])
async def test_switch_during_catalog_withholds_result_and_write(keys, rig, operation):
    preferences, _, state, _, _ = rig
    initial = preferences.get()
    state["switch"] = True
    with pytest.raises(OpenRouterError, match="sign-in changed"):
        if operation == "update":
            await preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")
        elif operation == "resolve":
            await preferences.resolve("google/other-advisor")
        else:
            await preferences.view()
    keys.set(KEY_A, workspace_id=WORKSPACE)
    assert preferences.get() == initial


@pytest.mark.parametrize("model", [None, "Gemini", "google/gemini-test"])
async def test_retired_model_fails_without_substitution(rig, model):
    preferences, _, state, _, _ = rig
    initial = preferences.get()
    await preferences.update(initial["settings_id"], 0, general_default="google/gemini-test",
                             family_defaults={"google": "google/gemini-test"})
    state["models"] = MODELS[1:]
    with pytest.raises(OpenRouterError, match="settings"):
        await preferences.resolve(model)
    assert preferences.get()["general_default"] == "google/gemini-test"


@pytest.mark.parametrize("model", [None, "Gemini", "unknown", 1, [], "x" * 201])
async def test_absent_or_invalid_choice_has_no_inference(rig, model):
    preferences, _, _, seen, _ = rig
    with pytest.raises(OpenRouterError):
        await preferences.resolve(model)
    assert all(request.url.path.endswith("/models") for request in seen)


async def test_signed_out_cannot_read_or_mutate_preferences(keys, rig):
    preferences, _, _, seen, _ = rig
    initial = preferences.get()
    keys.delete(workspace_id=WORKSPACE)
    for action in (preferences.get, lambda: preferences.assert_context(initial["settings_id"])):
        with pytest.raises(OpenRouterError):
            action()
    for action in (preferences.view, preferences.resolve,
                   lambda: preferences.update(initial["settings_id"], 0, general_default="google/other-advisor")):
        with pytest.raises(OpenRouterError):
            await action()
    assert not seen


async def test_view_is_bounded_and_sanitizes_malformed_catalog(rig, monkeypatch):
    preferences, advisors, _, _, _ = rig
    owner = advisors._credentials()[1].owner
    rows = [None, [], {"id": []}, {"id": "x" * 201}, {"id": "google/" + KEY_A},
            {"id": "other/" + owner},
            {"id": "google/safe", "name": KEY_A + owner + "x" * 1000,
             "pricing": {"prompt": KEY_A, "completion": "0.001", "hostile": {"key": KEY_A}},
             "context_length": [KEY_A], "secret": KEY_A, "created": RECENT}]
    rows += [{"id": f"google/model-{number}", "name": "x" * 1000,
              "pricing": {"prompt": "0.0001"}, "context_length": 1000, "created": RECENT} for number in range(1200)]

    async def malformed_models():
        return rows

    monkeypatch.setattr(advisors, "models", malformed_models)
    view = await preferences.view()
    encoded = json.dumps(view)
    assert KEY_A not in encoded and owner not in encoded
    assert view["workspace_id"] == WORKSPACE and view["cohort"] == "2027"
    assert 1 <= len(view["models"]) <= 1000
    assert all(set(model) == {"id", "name", "pricing", "context_length", "created", "family"} for model in view["models"])
    assert all(len(model["name"]) <= 200 for model in view["models"])
    assert view["models"][0]["id"] == "google/safe"
    assert view["models"][0]["family"] == "google"
    assert view["models"][0]["pricing"] == {"completion": "0.001"}
    assert view["models"][0]["context_length"] is None


async def test_active_credential_cannot_be_stored_as_catalog_model(rig, monkeypatch):
    _sqlite_only(rig[1].store)
    preferences, advisors, _, _, _ = rig
    initial = preferences.get()

    async def poisoned_catalog():
        return [{"id": "other/" + KEY_A, "name": KEY_A, "pricing": {}}]

    monkeypatch.setattr(advisors, "models", poisoned_catalog)
    with pytest.raises(OpenRouterError) as failure:
        await preferences.update(initial["settings_id"], 0, general_default="other/" + KEY_A)
    assert KEY_A not in str(failure.value)
    assert KEY_A.encode() not in advisors.store.path.read_bytes()


async def test_numeric_credential_is_not_exposed_in_valid_pricing(keys, rig, monkeypatch):
    preferences, advisors, _, _, _ = rig
    numeric_key = "12345678901234567890"
    keys.set(numeric_key, workspace_id=WORKSPACE)

    async def credential_price():
        return [{"id": "google/other-advisor", "name": "Other", "pricing": {"prompt": numeric_key,
                 "completion": "0.001"}, "context_length": 1000, "created": RECENT}]

    monkeypatch.setattr(advisors, "models", credential_price)
    view = await preferences.view()
    assert numeric_key not in json.dumps(view)
    assert view["models"][0]["pricing"] == {"completion": "0.001"}


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 10, 31, 12, tzinfo=UTC), datetime(2026, 4, 30, 12, tzinfo=UTC)),
    (datetime(2024, 8, 31, 12, tzinfo=UTC), datetime(2024, 2, 29, 12, tzinfo=UTC)),
    (datetime(2025, 8, 31, 12, tzinfo=UTC), datetime(2025, 2, 28, 12, tzinfo=UTC)),
])
def test_six_month_cutoff_clamps_calendar_day(now, expected):
    from openrouter_image_mcp.preferences import six_month_cutoff
    assert six_month_cutoff(now) == expected


async def test_catalog_enforces_inclusive_calendar_window(rig):
    preferences, _, state, _, module = rig
    cutoff = int(module.six_month_cutoff(NOW).timestamp())
    state["models"] = [{"id": f"google/model-{index}", "created": created}
                       for index, created in enumerate([cutoff - 1, cutoff, RECENT, RECENT + 1])]
    assert [row["id"] for row in (await preferences.view())["models"]] == [
        "google/model-1", "google/model-2"]


@pytest.mark.parametrize("created", [None, True, False, "1780272000", 1780272000.0,
                                     float("nan"), float("inf"), -1, 10**100])
async def test_catalog_omits_malformed_created(rig, created, monkeypatch):
    preferences, advisors, _, _, _ = rig

    async def malformed_models():
        return [{"id": "google/test", "created": created}]

    monkeypatch.setattr(advisors, "models", malformed_models)
    assert (await preferences.view())["models"] == []


async def test_catalog_omits_missing_created(rig):
    preferences, _, state, _, _ = rig
    state["models"] = [{"id": "google/test"}]
    assert (await preferences.view())["models"] == []


@pytest.mark.parametrize("model_id,family", [
    ("google/gemini-test", "google"), ("openai/gpt-test", "openai"),
    ("z-ai/glm-5", "open_weight"), ("thudm/glm-4", "open_weight"),
    ("qwen/qwen3-test", "open_weight"), ("moonshotai/kimi-test", "open_weight"),
    ("minimax/minimax-test", "open_weight"), ("deepseek/deepseek-test", "open_weight"),
    ("anthropic/claude-test", None), ("meta-llama/llama-test", None),
    ("mistralai/mistral-test", None), ("qwen/closed-other", None),
    ("moonshotai/moonlight-test", None), ("other/qwen3-test", None),
    ("z-ai/other-test", None), ("deepseek/other-test", None),
])
async def test_catalog_approved_vendor_and_model_family(rig, model_id, family):
    preferences, _, state, _, _ = rig
    state["models"] = [{"id": model_id, "created": RECENT}]
    models = (await preferences.view())["models"]
    assert len(models) == (family is not None)
    if family is not None:
        assert models[0]["family"] == family


async def test_legacy_preferences_hide_old_family_and_migrate_on_update(rig):
    preferences, advisors, _, _, _ = rig
    initial = preferences.get()
    with preferences._connection() as db:
        db.execute("UPDATE sidecar_preferences SET general_default=?, family_defaults=? WHERE settings_id=?",
                   ("anthropic/claude-test", json.dumps({"google": "google/gemini-test",
                    "openai": None, "anthropic": "anthropic/claude-test", "unknown": "other/model"}),
                    initial["settings_id"]))
    legacy = preferences.get()
    assert legacy["family_defaults"] == {"google": "google/gemini-test", "openai": None, "open_weight": None, "x-ai": None}
    with pytest.raises(OpenRouterError):
        await preferences.resolve()
    saved = await preferences.update(initial["settings_id"], 0, general_default="qwen/qwen-test",
                                     family_defaults={"open_weight": "qwen/qwen-test"})
    assert saved["family_defaults"]["open_weight"] == "qwen/qwen-test"
    with preferences._connection() as db:
        raw = preferences._row(db, advisors._credentials()[1])
        assert set(json.loads(raw["family_defaults"])) == {"google", "openai", "x-ai", "open_weight"}


@pytest.mark.parametrize("model", ["Claude", "Anthropic", "anthropic/claude-test", "other/advisor"])
async def test_disallowed_choices_cannot_resolve_or_save(rig, model):
    preferences, _, state, _, _ = rig
    state["models"].append({"id": "anthropic/claude-test", "created": RECENT})
    initial = preferences.get()
    with pytest.raises(OpenRouterError):
        await preferences.resolve(model)
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, general_default=model)
    assert preferences.get() == initial


async def test_legacy_family_update_rejection_is_atomic(rig):
    preferences, _, _, _, _ = rig
    initial = preferences.get()
    with pytest.raises(OpenRouterError):
        await preferences.update(initial["settings_id"], 0, general_default="google/gemini-test",
                                 family_defaults={"anthropic": "anthropic/claude-test"})
    assert preferences.get() == initial
