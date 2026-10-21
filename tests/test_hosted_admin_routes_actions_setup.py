"""Finish setup and register through the routes: typed models survive a missing guardrail, and a default family
with no model keeps the row off."""

import pytest
from admin_routes_rig import EDIT, NEW, AdminRig, edited, form_pairs, register_form
from markupsafe import escape

from openrouter_image_mcp.errors import NoGuardrailError
from openrouter_image_mcp.hosted.admin import copy

CHOOSE_GOOGLE = str(escape(copy.CHOOSE_DEFAULT_MODEL.format(family="google")))


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    rig.sign_in()
    yield rig
    await rig.aclose()


async def test_the_edit_page_of_a_row_with_no_key_keeps_its_model_selects_on(rig):
    rig.slack_rig.provisioner.error = NoGuardrailError("no guardrail")
    await rig.post(NEW, register_form())
    page = (await rig.client.get(EDIT)).text
    assert str(escape(copy.MODELS_UNREADABLE)) not in page
    names = [name for name, _ in form_pairs(page, EDIT)]
    assert "model_google" in names and "model_openai" in names  # enabled selects are posted


async def test_finish_setup_with_the_default_family_left_at_none_is_refused(rig):
    rig.slack_rig.provisioner.error = NoGuardrailError("no guardrail")
    await rig.post(NEW, register_form())
    rig.slack_rig.provisioner.error = None
    form = form_pairs((await rig.client.get(EDIT)).text, EDIT)
    refused = await rig.post(EDIT + "/finish", edited(form, model_google="", model_openai="openai/gpt-x"))
    assert refused.status_code == 422 and CHOOSE_GOOGLE in refused.text
    assert not rig.row().active and rig.slack_rig.prefs.calls == []
    typed = dict(form_pairs(refused.text, EDIT))
    assert typed["model_openai"] == "openai/gpt-x"  # what was typed stays
    done = await rig.post(EDIT + "/finish", edited(form_pairs(refused.text, EDIT), model_google="google/gemini-x"))
    assert done.status_code == 303 and rig.row().active
    assert rig.slack_rig.prefs.saved["google"] == "google/gemini-x"


async def test_register_with_no_model_for_the_default_family_shows_the_form_again(rig):
    response = await rig.post(NEW, register_form(model_google=""))
    assert response.status_code == 422 and CHOOSE_GOOGLE in response.text
    assert rig.slack_rig.registry.all() == []
    assert dict(form_pairs(response.text, NEW))["model_openai"] == "openai/gpt-x"
