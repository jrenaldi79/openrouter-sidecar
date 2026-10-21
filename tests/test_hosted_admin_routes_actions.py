"""The register, save, finish, turn off and turn on POSTs through the routes, against the real channel admin service."""

import pytest
from admin_routes_rig import (
    CHAN,
    EDIT,
    NEW,
    OTHER,
    AdminRig,
    edited,
    form_pairs,
    register_form,
)
from markupsafe import escape
from slack_admin_rig import GUEST_TEXT

from openrouter_image_mcp.errors import ProviderError
from openrouter_image_mcp.hosted.admin import audit as audit_log
from openrouter_image_mcp.hosted.admin import copy


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    rig.sign_in()
    yield rig
    await rig.aclose()


async def edit_form(rig, **changes):
    """The edit page's form as a browser would post it, with `changes` typed in."""
    page = await rig.client.get(EDIT)
    assert page.status_code == 200
    return edited(form_pairs(page.text, EDIT), **changes)


async def test_register_one_form(rig):
    response = await rig.post(NEW, register_form(invoker_allowlist=["U1"], fetch_allowlist="example.com\ndocs.org"))
    assert (response.status_code, response.headers["location"]) == (303, EDIT)
    row = rig.row()
    assert row.active and row.team_label == "abbott" and row.cohort == "2027"
    assert row.invoker_allowlist == ("U1",) and row.fetch_allowlist == ("example.com", "docs.org")
    assert (row.daily_cap_usd, row.max_concurrent) == (4.0, 3)
    assert rig.slack_rig.provisioner.creations == 1
    assert rig.slack_rig.prefs.saved["google"] == "google/gemini-x"
    assert rig.slack_rig.actions() == [audit_log.REGISTERED, audit_log.MODELS_CHANGED, audit_log.TURNED_ON]
    assert (await rig.client.get(EDIT)).status_code == 200


async def test_register_twice_refuses_the_second(rig):
    assert (await rig.post(NEW, register_form())).status_code == 303
    again = await rig.post(NEW, register_form())
    assert again.status_code == 422 and "already" in again.text
    assert len(rig.slack_rig.registry.all()) == 1 and rig.slack_rig.provisioner.creations == 1


async def test_refusal_keeps_input(rig):
    data = register_form(label="Bad Label", daily_cap_usd="7", fetch_allowlist="example.com",
                         alias_name=["gpt", "gemini", "quick"], alias_target=["ChatGPT", "Gemini", "Grok"],
                         default_model="quick", model_google="google/gemini-flash")
    response = await rig.post(NEW, data)
    assert response.status_code == 422 and "team_label must match" in response.text
    shown = dict(form_pairs(response.text, NEW))
    assert shown["label"] == "Bad Label" and shown["daily_cap_usd"] == "7"
    assert shown["default_model"] == "quick" and shown["model_google"] == "google/gemini-flash"
    assert shown["fetch_allowlist"].strip() == "example.com"
    assert 'value="quick"' in response.text  # the typed alias row survives
    assert rig.slack_rig.registry.all() == []


async def test_register_with_an_unlisted_model_is_refused_with_the_input(rig):
    response = await rig.post(NEW, register_form(fusion_analyst="nope/unlisted"))
    assert response.status_code == 422 and "nope/unlisted isn&#39;t in the model list" in response.text
    assert dict(form_pairs(response.text, NEW))["fusion_analyst"] == "nope/unlisted"
    assert rig.slack_rig.registry.all() == []


async def test_register_setup_failure_then_finish_setup_route(rig):
    from openrouter_image_mcp.errors import NoGuardrailError
    rig.slack_rig.provisioner.error = NoGuardrailError("no guardrail")
    response = await rig.post(NEW, register_form())
    row = rig.row()
    assert response.status_code == 422 and not row.active and row.inactive_reason == copy.SETUP_UNFINISHED
    assert "no &#39;Sidecar team budget&#39; guardrail" in response.text  # the plain reason, on the edit page
    assert "Finish setup" in response.text and f'formaction="{EDIT}/finish"' in response.text
    assert "Turn on" not in response.text  # finishing, not switching on
    rig.slack_rig.provisioner.error = None
    fields = form_pairs(response.text, EDIT)
    done = await rig.post(EDIT + "/finish", fields)
    assert (done.status_code, done.headers["location"]) == (303, EDIT)
    assert rig.row().active and rig.slack_rig.provisioner.creations == 1
    assert rig.slack_rig.prefs.saved["google"] == "google/gemini-x"


async def test_finish_setup_failure_shows_the_reason(rig):
    from openrouter_image_mcp.errors import NoGuardrailError
    rig.slack_rig.provisioner.error = NoGuardrailError("no guardrail")
    await rig.post(NEW, register_form())
    response = await rig.post(EDIT + "/finish", form_pairs((await rig.client.get(EDIT)).text, EDIT))
    assert response.status_code == 422 and "Setup didn&#39;t finish: " in response.text


async def test_finish_setup_on_a_channel_turned_off_by_an_admin_is_refused(rig):
    await rig.register()
    await rig.post(EDIT + "/off", {"reason": "class ended"})
    response = await rig.post(EDIT + "/finish", form_pairs((await rig.client.get(EDIT)).text, EDIT))
    assert response.status_code == 422 and "isn&#39;t waiting for setup" in response.text


async def test_edit_sends_only_changed_fields(rig):
    await rig.register()
    first, second = await edit_form(rig), await edit_form(rig)  # two admins open the same page
    assert (await rig.post(EDIT, edited(first, daily_cap_usd="9"))).status_code == 303
    assert (await rig.post(EDIT, edited(second, max_concurrent="5"))).status_code == 303
    row = rig.row()
    assert (row.daily_cap_usd, row.max_concurrent) == (9.0, 5)  # the second save didn't undo the first
    entries = [e for e in rig.slack_rig.audit.recent() if e.action == audit_log.CHANGED]
    assert sorted(list(e.changes) for e in entries) == [["daily_cap_usd"], ["max_concurrent"]]


async def test_edit_saves_changed_models(rig):
    await rig.register()
    response = await rig.post(EDIT, await edit_form(rig, model_openai="openai/gpt-y"))
    assert response.status_code == 303
    assert rig.slack_rig.prefs.saved["openai"] == "openai/gpt-y" and rig.slack_rig.prefs.saved["google"] == "google/gemini-x"
    assert rig.slack_rig.actions()[-1] == audit_log.MODELS_CHANGED


async def test_edit_nothing_changed(rig):
    await rig.register()
    response = await rig.post(EDIT, await edit_form(rig))
    assert response.status_code == 422 and "Nothing changed." in response.text
    assert dict(form_pairs(response.text, EDIT))["daily_cap_usd"] == "4.0"


async def test_edit_refusal_keeps_input_and_the_rendered_originals(rig):
    await rig.register()
    bad = edited(await edit_form(rig), daily_cap_usd="9", fusion_analyst="nope/unlisted")
    response = await rig.post(EDIT, bad)
    assert response.status_code == 422 and "nope/unlisted isn&#39;t in the model list" in response.text
    shown = dict(form_pairs(response.text, EDIT))
    assert shown["daily_cap_usd"] == "9" and shown["fusion_analyst"] == "nope/unlisted"  # the input is kept
    assert shown["orig_daily_cap_usd"] == '"4.0"'  # but the snapshot is still what the page first showed
    fixed = edited(form_pairs(response.text, EDIT), fusion_analyst="openai/gpt-y")
    assert (await rig.post(EDIT, fixed)).status_code == 303
    row = rig.row()
    assert row.daily_cap_usd == 9.0 and row.fusion_analyst == "openai/gpt-y"  # the earlier edit wasn't dropped


async def test_edit_invalid_number_is_a_sentence(rig):
    await rig.register()
    response = await rig.post(EDIT, await edit_form(rig, daily_cap_usd="lots"))
    assert response.status_code == 422 and str(escape(copy.CAP_INVALID)) in response.text
    assert rig.row().daily_cap_usd == 4.0


async def test_edit_model_save_failure_keeps_the_registry_change(rig):
    await rig.register()
    rig.slack_rig.prefs.error = ProviderError("down")
    response = await rig.post(EDIT, await edit_form(rig, daily_cap_usd="9", model_openai="openai/gpt-y"))
    assert response.status_code == 422 and "Saved the channel settings. The team&#39;s models weren&#39;t changed" in response.text
    assert rig.row().daily_cap_usd == 9.0


async def test_turn_off_requires_reason(rig):
    await rig.register()
    blank = await rig.post(EDIT + "/off", {"reason": "   "})
    assert blank.status_code == 422 and copy.REASON_REQUIRED in blank.text
    assert rig.row().active
    htmx = await rig.post(EDIT + "/off", {"reason": ""}, headers={"hx-request": "true"})
    assert htmx.status_code == 422 and htmx.headers["content-type"].startswith("text/plain")
    assert htmx.text == copy.REASON_REQUIRED
    done = await rig.post(EDIT + "/off", {"reason": "class ended"})
    assert (done.status_code, done.headers["location"]) == (303, EDIT)
    row = rig.row()
    assert not row.active and row.inactive_reason == "class ended"
    assert "Status: Off: class ended" in (await rig.client.get(EDIT)).text.replace("\n", " ")


async def test_turn_on_route(rig):
    await rig.register()
    assert (await rig.post(EDIT + "/on", {})).status_code == 422  # already on
    await rig.post(EDIT + "/off", {"reason": "pause"})
    rig.slack_rig.slack.users["U2"] = {"is_restricted": True}
    blocked = await rig.post(EDIT + "/on", {}, headers={"hx-request": "true"})
    assert blocked.status_code == 422 and blocked.text == GUEST_TEXT  # the guest check runs again
    rig.slack_rig.slack.users["U2"] = {"profile": {"display_name": "Ana"}}
    assert (await rig.post(EDIT + "/on", {})).status_code == 303
    assert rig.row().active


async def test_log_names_the_signed_in_admin(rig):
    rig.sign_in("ada@northwestern.edu")
    await rig.register()
    await rig.post(EDIT + "/off", {"reason": "class ended"})
    page = (await rig.client.get("/admin/log")).text
    assert page.count("ada@northwestern.edu") >= 2 and "turned off" in page  # the rail shows it too


async def test_saving_an_unrelated_field_writes_no_team_models_for_an_unset_family(rig):
    await rig.register()  # google and openai get models; x-ai is left unset
    assert rig.slack_rig.prefs.saved["x-ai"] is None
    saves, entries = list(rig.slack_rig.prefs.calls), rig.slack_rig.actions()
    form = await edit_form(rig, daily_cap_usd="9")
    assert ("model_x-ai", "") in form  # the browser submits the "None" choice, not the first model
    assert (await rig.post(EDIT, form)).status_code == 303
    assert rig.row().daily_cap_usd == 9.0
    assert rig.slack_rig.prefs.calls == saves and rig.slack_rig.prefs.saved["x-ai"] is None
    assert rig.slack_rig.actions().count(audit_log.MODELS_CHANGED) == entries.count(audit_log.MODELS_CHANGED) == 1


async def test_a_family_can_be_set_later_from_none(rig):
    await rig.register()
    assert (await rig.post(EDIT, await edit_form(rig, **{"model_x-ai": "x-ai/grok-x"}))).status_code == 303
    assert rig.slack_rig.prefs.saved["x-ai"] == "x-ai/grok-x"


async def test_register_can_leave_every_family_unset(rig):
    data = register_form(model_google="", model_openai="")
    assert (await rig.post(NEW, data)).status_code == 303
    assert rig.row().active and set(rig.slack_rig.prefs.saved.values()) == {None}
    assert rig.slack_rig.actions() == [audit_log.REGISTERED, audit_log.TURNED_ON]


async def test_unreadable_saved_models_leave_the_selects_off_and_send_no_model_changes(rig, monkeypatch):
    await rig.register()
    saves = list(rig.slack_rig.prefs.calls)

    def unreadable(row):
        raise RuntimeError("preferences down")

    monkeypatch.setattr(rig.slack_rig.admin._models, "saved", unreadable)
    page = await rig.client.get(EDIT)
    assert page.status_code == 200 and str(escape(copy.MODELS_UNREADABLE)) in page.text
    form = edited(form_pairs(page.text, EDIT), daily_cap_usd="9")
    assert not [name for name, _ in form if name.startswith("model_")]  # disabled selects aren't posted
    assert (await rig.post(EDIT, form)).status_code == 303
    assert rig.row().daily_cap_usd == 9.0 and rig.slack_rig.prefs.calls == saves


async def test_out_of_range_numbers_are_refused_in_plain_sentences_with_the_input(rig):
    await rig.register()
    for field, bad, sentence in (("daily_cap_usd", "5000", copy.CAP_INVALID), ("daily_cap_usd", "abc", copy.CAP_INVALID),
                                 ("max_concurrent", "0", copy.CONCURRENT_INVALID),
                                 ("max_concurrent", "99", copy.CONCURRENT_INVALID)):
        response = await rig.post(EDIT, await edit_form(rig, **{field: bad}))
        assert response.status_code == 422 and str(escape(sentence)) in response.text, (field, bad)
        assert field not in response.text.split('role="alert"')[1].split("</p>")[0]  # no raw field name
        assert dict(form_pairs(response.text, EDIT))[field] == bad
    assert (rig.row().daily_cap_usd, rig.row().max_concurrent) == (4.0, 3)
    registered = await rig.post(NEW.replace(CHAN, OTHER), register_form(label="other", max_concurrent="25"))
    assert registered.status_code == 422 and str(escape(copy.CONCURRENT_INVALID)) in registered.text
    assert dict(form_pairs(registered.text, NEW.replace(CHAN, OTHER)))["label"] == "other"


async def test_a_refusal_keeps_the_input_when_the_model_list_is_down_too(rig):
    await rig.register()
    form = await edit_form(rig, fusion_analyst="openai/gpt-y")
    rig.slack_rig.client.error = ProviderError("down")
    response = await rig.post(EDIT, form)
    assert response.status_code == 422 and str(escape(copy.CATALOG_DOWN)) in response.text
    assert dict(form_pairs(response.text, EDIT))["fusion_analyst"] == "openai/gpt-y"  # not a bare message page
    fresh = await rig.post(NEW.replace(CHAN, OTHER), register_form(label="other"))
    assert fresh.status_code == 422 and str(escape(copy.CATALOG_DOWN)) in fresh.text
    assert dict(form_pairs(fresh.text, NEW.replace(CHAN, OTHER)))["label"] == "other"
