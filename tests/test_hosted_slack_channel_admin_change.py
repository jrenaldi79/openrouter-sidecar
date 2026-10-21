import pytest
from slack_admin_rig import ACTOR, FAMILY_DEFAULTS, PANEL, Rig

from openrouter_image_mcp.errors import BadRequestError
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.slack.channel_admin import ChannelRefused


@pytest.fixture
def rig(db_target):
    return Rig(db_target)


async def test_audit_failure_rolls_back_registry_change(rig):
    await rig.register()

    def broken():
        raise RuntimeError("audit store down")

    rig.audit.clock = broken
    with pytest.raises(RuntimeError):
        await rig.admin.change("T1", "C1", {"daily_cap_usd": 2.0}, {}, ACTOR)
    with pytest.raises(RuntimeError):
        await rig.admin.turn_off("T1", "C1", "class ended", ACTOR)
    row = rig.registry.get("T1", "C1")
    assert (row.daily_cap_usd, row.active) == (4.0, True)


async def test_change_writes_only_changed_fields(rig):
    await rig.register()
    rig.registry.update("T1", "C1", max_concurrent=5)  # another admin saved a different field meanwhile
    changed = await rig.admin.change("T1", "C1", {"daily_cap_usd": 2.0, "fetch_allowlist": [],
                                                  "fusion_panel": PANEL}, {}, ACTOR)
    assert changed == {"daily_cap_usd": [4.0, 2.0]}
    row = rig.registry.get("T1", "C1")
    assert (row.daily_cap_usd, row.max_concurrent) == (2.0, 5)
    latest = rig.audit.recent()[0]
    assert (latest.action, latest.changes) == (audit_log.CHANGED, {"daily_cap_usd": [4.0, 2.0]})


async def test_change_saves_models_and_audits_them(rig):
    await rig.register()
    changed = await rig.admin.change("T1", "C1", {}, {"google": "google/gemini-flash", "openai": "openai/gpt-x"},
                                     ACTOR)
    assert changed == {"google model": ["google/gemini-x", "google/gemini-flash"]}
    assert rig.prefs.calls[-1] == {"google": "google/gemini-flash"}
    latest = rig.audit.recent()[0]
    assert (latest.action, latest.changes) == (audit_log.MODELS_CHANGED, changed)


async def test_change_refuses_models_the_list_lacks(rig):
    await rig.register()
    with pytest.raises(ChannelRefused, match="isn't in the model list"):
        await rig.admin.change("T1", "C1", {"subagent_model": "google/retired"}, {}, ACTOR)
    with pytest.raises(ChannelRefused):
        await rig.admin.change("T1", "C1", {"daily_cap_usd": -1}, {}, ACTOR)
    assert rig.registry.get("T1", "C1").daily_cap_usd == 4.0
    rig.client.ids.remove("google/gemini-x")  # a saved model that left the list doesn't block other edits
    assert await rig.admin.change("T1", "C1", {"daily_cap_usd": 3.0}, FAMILY_DEFAULTS, ACTOR)


async def test_change_model_failure_keeps_registry_change(rig):
    await rig.register()
    rig.prefs.error = BadRequestError("The team's key was rejected")
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.change("T1", "C1", {"daily_cap_usd": 2.0}, {"google": "google/gemini-flash"}, ACTOR)
    assert str(refused.value) == copy.MODELS_NOT_SAVED.format(reason="The team's key was rejected")
    assert rig.registry.get("T1", "C1").daily_cap_usd == 2.0
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON,
                             audit_log.CHANGED]


async def test_change_nothing(rig):
    await rig.register()
    for changes, models in (({}, {}), ({"daily_cap_usd": 4.0, "fusion_panel": tuple(PANEL)}, FAMILY_DEFAULTS)):
        with pytest.raises(ChannelRefused) as refused:
            await rig.admin.change("T1", "C1", changes, models, ACTOR)
        assert str(refused.value) == copy.NOTHING_CHANGED
    with pytest.raises(ValueError):  # state changes go through turn_off and turn_on
        await rig.admin.change("T1", "C1", {"active": False}, {}, ACTOR)
    assert len(rig.actions()) == 3


async def test_a_model_audit_failure_after_a_good_save_is_a_server_error(rig):
    await rig.register(family_defaults={})

    def broken():
        raise RuntimeError("audit store down")

    rig.audit.clock = broken
    with pytest.raises(RuntimeError):  # not "the models weren't changed": they were
        await rig.admin.change("T1", "C1", {}, {"google": "google/gemini-flash"}, ACTOR)
    assert rig.prefs.saved["google"] == "google/gemini-flash"


async def test_change_reports_what_the_transaction_wrote(rig):
    await rig.register()
    rig.registry.update("T1", "C1", daily_cap_usd=3.0)  # saved after the admin's form was rendered
    changed = await rig.admin.change("T1", "C1", {"daily_cap_usd": 2.0}, {}, ACTOR)
    assert changed == {"daily_cap_usd": [3.0, 2.0]} == rig.audit.recent()[0].changes
