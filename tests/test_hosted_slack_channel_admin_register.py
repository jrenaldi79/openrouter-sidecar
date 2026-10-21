import asyncio
from dataclasses import replace

import pytest
from slack_admin_rig import (
    ACTOR,
    FAMILY_DEFAULTS,
    FIELDS,
    GUEST_TEXT,
    PANEL,
    Rig,
    registered_row,
)

from openrouter_image_mcp.errors import (
    AuthRequiredError,
    BadRequestError,
    NoGuardrailError,
    ProviderError,
)
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.provisioning import NOT_SET_UP, UNAVAILABLE, TeamKey
from openrouter_image_mcp.hosted.slack.channel_admin import ChannelAdmin, ChannelRefused


@pytest.fixture
def rig(db_target):
    return Rig(db_target)


async def test_register_refuses_guest_channel(rig):
    rig.slack.users["U2"] = {"is_restricted": True}
    with pytest.raises(ChannelRefused, match="has a guest member") as refused:
        await rig.register()
    assert str(refused.value) == GUEST_TEXT
    assert await rig.admin.check("C1") == GUEST_TEXT
    assert rig.registry.all() == [] and rig.provisioner.calls == []


async def test_register_twice_refuses_second(rig):
    await rig.register()
    with pytest.raises(ChannelRefused, match="already registered"):
        await rig.register()
    assert len(rig.registry.all()) == 1 and len(rig.provisioner.calls) == 1


async def test_register_saves_row_key_models_and_audit(rig):
    row = await rig.register()
    assert (row.active, row.inactive_reason, row.team_label) == (True, None, "abbott")
    principal, team = rig.provisioner.calls[0]
    assert (principal.key, principal.workspace_id, team) == ("slack|T1:C1", row.workspace_id, TeamKey("abbott"))
    assert rig.prefs.calls == [FAMILY_DEFAULTS] and rig.prefs.actors == [principal]
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]
    registered, models, on = reversed(rig.audit.recent())
    assert registered.changes["active"] == [None, False]
    assert registered.changes["inactive_reason"] == [None, copy.SETTING_UP]
    assert registered.changes["fusion_panel"] == [None, PANEL]
    assert models.changes == {"google model": [None, "google/gemini-x"], "openai model": [None, "openai/gpt-x"]}
    assert on.changes == {"active": [False, True], "inactive_reason": [copy.SETTING_UP, None]}
    assert {e.actor_email for e in rig.audit.recent()} == {ACTOR.email}
    assert (registered.team_id, registered.channel_id, registered.label) == ("T1", "C1", "abbott")


@pytest.mark.parametrize("failure", ["guardrail", "not_set_up", "models"])
async def test_setup_failure_then_finish(rig, failure):
    if failure == "guardrail":
        rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
        reason = copy.NO_GUARDRAIL.format(cohort="2027").rstrip(".")
    elif failure == "not_set_up":  # only a missing guardrail may say "no guardrail"
        rig.provisioner.error = AuthRequiredError(NOT_SET_UP)
        reason = copy.KEY_NOT_SET_UP
    else:
        rig.prefs.error = BadRequestError("Choose an available exact advisor model ID")
        reason = copy.MODEL_NOT_ACCEPTED
    with pytest.raises(ChannelRefused) as refused:
        await rig.register()
    assert str(refused.value) == copy.SETUP_UNFINISHED_TEXT.format(reason=reason)
    row = rig.registry.get("T1", "C1")
    assert (row.active, row.inactive_reason) == (False, copy.SETUP_UNFINISHED)
    assert rig.actions() == [audit_log.REGISTERED]
    assert rig.provisioner.creations == (1 if failure == "models" else 0)
    rig.provisioner.error = rig.prefs.error = None
    done = await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)
    assert (done.active, done.inactive_reason) == (True, None)
    assert rig.provisioner.creations == 1 and rig.prefs.saved["google"] == "google/gemini-x"
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]
    again = await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)  # safe to repeat
    assert again.active and len(rig.actions()) == 3 and len(rig.prefs.calls) == 1
    assert rig.provisioner.creations == 1


async def test_unexpected_setup_failure_shows_a_fixed_sentence_not_the_raw_text(rig):
    rig.prefs.error = RuntimeError("secret detail")
    with pytest.raises(ChannelRefused) as refused:
        await rig.register()
    assert str(refused.value) == copy.SETUP_UNFINISHED_TEXT.format(reason=copy.UNEXPECTED)
    assert rig.registry.get("T1", "C1").inactive_reason == copy.SETUP_UNFINISHED


async def test_finish_setup_refuses_a_row_that_was_turned_off(rig):
    await rig.register()
    await rig.admin.turn_off("T1", "C1", "class ended", ACTOR)
    with pytest.raises(ChannelRefused):
        await rig.admin.finish_setup("T1", "C1", {}, ACTOR)
    assert not rig.registry.get("T1", "C1").active


async def test_register_validates_before_writing(rig):
    for bad in ({"fusion_analyst": "openai/unlisted"}, {"label": "Bad Label"}, {"fusion_panel": []},
                {"alias_map": {"gemini": "google/retired", "gpt": "ChatGPT"}, "default_model": "gemini"}):
        with pytest.raises(ChannelRefused):
            await rig.register(**bad)
    with pytest.raises(ChannelRefused, match="isn't a google model"):
        await rig.register(family_defaults={"google": "openai/gpt-x"})
    assert rig.registry.all() == [] and rig.provisioner.calls == []


async def test_register_needs_slack_and_catalog(rig):
    rig.client.error = ProviderError("down")
    with pytest.raises(ChannelRefused) as refused:
        await rig.register()
    assert str(refused.value) == copy.CATALOG_DOWN and rig.registry.all() == []
    off = ChannelAdmin(rig.registry, None, None, rig.provisioner, rig.audit)
    with pytest.raises(ChannelRefused) as refused:
        await off.register("T1", "C1", FIELDS, {}, ACTOR)
    assert str(refused.value) == copy.SLACK_OFF


async def test_a_model_audit_failure_after_a_good_save_is_a_server_error_not_unfinished(rig):
    rig.registry.add(replace(registered_row(), active=False, inactive_reason=copy.SETTING_UP))

    def broken():
        raise RuntimeError("audit store down")

    rig.audit.clock = broken
    with pytest.raises(RuntimeError):
        await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)
    assert rig.prefs.saved["google"] == "google/gemini-x"  # the save happened
    assert rig.registry.get("T1", "C1").inactive_reason == copy.SETTING_UP  # and wasn't called a failed setup


async def test_two_finish_setups_at_once_make_one_key_one_entry(rig):
    rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
    with pytest.raises(ChannelRefused):
        await rig.register()
    rig.provisioner.error = None
    both = await asyncio.gather(*(rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR) for _ in range(2)))
    assert all(row.active for row in both) and rig.registry.get("T1", "C1").active
    assert rig.provisioner.creations == 1
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]


async def test_a_finish_that_loses_a_race_is_not_reported_as_failed(rig):
    """The second call's model save is stale (the first saved first); the row is on, so it just returns it."""
    rig.prefs.yield_in_update = True
    rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
    with pytest.raises(ChannelRefused):
        await rig.register()
    rig.provisioner.error = None
    both = await asyncio.gather(*(rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR) for _ in range(2)))
    assert [row.active for row in both] == [True, True]
    assert rig.registry.get("T1", "C1").inactive_reason is None
    assert rig.actions().count(audit_log.TURNED_ON) == 1 and rig.provisioner.creations == 1


async def test_two_registers_at_once_make_one_row_one_key(rig):
    results = await asyncio.gather(rig.register(), rig.register(), return_exceptions=True)
    assert [isinstance(r, ChannelRefused) for r in results].count(True) == 1
    assert len(rig.registry.all()) == 1 and rig.registry.get("T1", "C1").active
    assert rig.provisioner.creations == 1 and len(rig.provisioner.calls) == 1
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]
