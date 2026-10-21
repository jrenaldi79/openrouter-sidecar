"""Finishing setup: models typed before the team has a key, and the rule that a row turns on only when its
default alias resolves to a model."""

from dataclasses import replace

import pytest
from slack_admin_rig import ACTOR, FAMILY_DEFAULTS, Rig, registered_row

from openrouter_image_mcp.errors import NoGuardrailError
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.provisioning import UNAVAILABLE
from openrouter_image_mcp.hosted.slack.channel_admin import ChannelRefused

CHOOSE_GOOGLE = copy.CHOOSE_DEFAULT_MODEL.format(family="google")


@pytest.fixture
def rig(db_target):
    return Rig(db_target)


def in_setup(rig, reason=copy.SETUP_UNFINISHED, **optional):
    rig.registry.add(replace(registered_row(**optional), active=False, inactive_reason=reason))


@pytest.mark.parametrize("reason", [copy.SETTING_UP, copy.SETUP_UNFINISHED])
async def test_a_row_in_setup_with_no_key_reads_as_nothing_saved(rig, reason):
    in_setup(rig, reason)
    _, saved = await rig.admin.channel("T1", "C1")
    assert saved == {"google": None, "openai": None, "x-ai": None, "open_weight": None}


async def test_a_row_that_is_not_in_setup_with_no_key_still_reads_as_unreadable(rig):
    rig.registry.add(registered_row())  # on, yet no key was ever made: not a setup state
    assert (await rig.admin.channel("T1", "C1"))[1] is None
    rig.registry.update("T1", "C1", active=False, inactive_reason="class ended")
    assert (await rig.admin.channel("T1", "C1"))[1] is None


async def test_register_with_a_missing_guardrail_then_finish_with_the_typed_models(rig):
    rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
    with pytest.raises(ChannelRefused):
        await rig.register()
    assert rig.provisioner.creations == 0
    _, saved = await rig.admin.channel("T1", "C1")
    assert saved is not None and set(saved.values()) == {None}  # the page can render enabled selects
    rig.provisioner.error = None
    done = await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)
    assert (done.active, done.inactive_reason) == (True, None)
    assert rig.prefs.saved["google"] == "google/gemini-x" and rig.prefs.saved["openai"] == "openai/gpt-x"
    assert rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]


async def test_finish_without_a_model_for_the_default_family_is_refused_and_stays_off(rig):
    in_setup(rig)
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.finish_setup("T1", "C1", {"openai": "openai/gpt-x"}, ACTOR)  # default alias: gemini
    assert str(refused.value) == CHOOSE_GOOGLE
    row = rig.registry.get("T1", "C1")
    assert not row.active and row.inactive_reason in (copy.SETTING_UP, copy.SETUP_UNFINISHED)
    assert rig.prefs.calls == [] and rig.actions() == []  # nothing saved, nothing turned on
    done = await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)
    assert done.active


async def test_finish_accepts_a_default_family_model_saved_earlier(rig):
    in_setup(rig)
    rig.provisioner.keys.add("slack|T1:C1")
    rig.prefs.saved["google"] = "google/gemini-x"
    done = await rig.admin.finish_setup("T1", "C1", {}, ACTOR)
    assert done.active


async def test_a_default_alias_for_an_exact_model_id_needs_no_family_model(rig):
    alias_map = {"fast": "google/gemini-flash", "gpt": "ChatGPT"}
    in_setup(rig, alias_map=alias_map, default_model="fast")
    done = await rig.admin.finish_setup("T1", "C1", {}, ACTOR)
    assert done.active and set(rig.prefs.saved.values()) == {None}


async def test_register_refuses_a_default_family_with_no_model_before_writing_anything(rig):
    with pytest.raises(ChannelRefused) as refused:
        await rig.register(family_defaults={"openai": "openai/gpt-x"})
    assert str(refused.value) == CHOOSE_GOOGLE
    assert rig.registry.all() == [] and rig.provisioner.calls == []
    with pytest.raises(ChannelRefused) as blank:
        await rig.register(family_defaults={})
    assert str(blank.value) == CHOOSE_GOOGLE and rig.registry.all() == []


async def test_the_rule_follows_the_default_alias_not_gemini(rig):
    with pytest.raises(ChannelRefused) as refused:
        await rig.register(default_model="gpt", family_defaults={"google": "google/gemini-x"})
    assert str(refused.value) == copy.CHOOSE_DEFAULT_MODEL.format(family="openai")


async def test_finish_that_fails_on_a_row_that_was_already_on_is_refused_not_reported_as_done(rig):
    await rig.register()
    rig.provisioner.error = NoGuardrailError(UNAVAILABLE)
    with pytest.raises(ChannelRefused) as refused:
        await rig.admin.finish_setup("T1", "C1", FAMILY_DEFAULTS, ACTOR)
    assert str(refused.value).startswith(copy.SETUP_UNFINISHED)
    assert rig.registry.get("T1", "C1").active  # a live channel is never marked unfinished
