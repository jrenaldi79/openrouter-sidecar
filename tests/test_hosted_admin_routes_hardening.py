"""Route guard details: cross-site sign-out, every method carries the headers, id shapes, and the edit page while
OpenRouter's model list is down."""

import pytest
from admin_routes_rig import (
    EDIT,
    LOGOUT,
    NEW,
    ORIGIN,
    AdminRig,
    edited,
    form_pairs,
    register_form,
)
from markupsafe import escape

from openrouter_image_mcp.errors import ProviderError
from openrouter_image_mcp.hosted.admin import copy


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    yield rig
    await rig.aclose()


@pytest.fixture
async def signed_in(rig):
    rig.sign_in()
    return rig


# ---- sign-out with no session ----

@pytest.mark.parametrize("headers", [
    {}, {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}, {"origin": "https://evil.example"},
    {"origin": "null"}, {"origin": ORIGIN + ".evil.example"}])
async def test_a_cross_site_sign_out_with_no_session_does_nothing(rig, headers):
    response = await rig.client.post("/admin/logout", data={}, headers=headers)
    assert (response.status_code, response.headers["location"]) == (303, "/admin")
    assert "set-cookie" not in response.headers  # nothing cleared, and no trip to Auth0


@pytest.mark.parametrize("headers", [{"sec-fetch-site": "same-origin"}, {"origin": ORIGIN}])
async def test_a_same_origin_sign_out_with_no_session_still_ends_the_auth0_session(rig, headers):
    response = await rig.client.post("/admin/logout", data={}, headers=headers)
    assert (response.status_code, response.headers["location"]) == (302, LOGOUT)
    assert "max-age=0" in response.headers["set-cookie"].lower()


async def test_a_signed_in_sign_out_needs_the_token_not_the_origin(signed_in):
    response = await signed_in.post("/admin/logout", headers={"origin": "https://evil.example"})
    assert (response.status_code, response.headers["location"]) == (302, LOGOUT)  # the CSRF token proves intent


# ---- methods the route table doesn't list ----

PATHS = ["/admin", "/admin/login", "/admin/log", "/admin/public/tokens.css", "/admin/channels/x/y", EDIT, "/admin/nope"]


@pytest.mark.parametrize("method", ["TRACE", "PROPFIND", "PURGE"])
@pytest.mark.parametrize("configured", [True, False])
async def test_every_method_gets_the_security_headers(db_target, method, configured):
    rig = AdminRig(db_target, login=True if configured else None)
    try:
        for path in PATHS:
            response = await rig.client.request(method, path)
            assert response.status_code in {404, 503}, (path, response.status_code)
            assert "default-src 'none'" in response.headers["content-security-policy"], path
            assert response.headers["cache-control"] == "no-store", path
    finally:
        await rig.aclose()


# ---- Slack ids ----

async def test_a_long_slack_id_is_still_an_id_and_a_longer_one_is_not(signed_in):
    twenty_one = "W" + "0" * 20
    page = await signed_in.client.get(f"/admin/channels/T0123ABCD/{twenty_one}")
    assert page.status_code == 404 and str(escape(copy.NOT_REGISTERED)) in page.text  # got past the id check
    too_long = await signed_in.client.get(f"/admin/channels/T0123ABCD/{twenty_one}0")
    assert too_long.status_code == 404 and str(escape(copy.NOT_FOUND)) in too_long.text
    lower = await signed_in.client.get(f"/admin/channels/T0123ABCD/{twenty_one.lower()}")
    assert str(escape(copy.NOT_FOUND)) in lower.text


# ---- the edit page while the model list is down ----

async def test_the_edit_page_opens_while_the_model_list_is_down_and_keeps_the_current_values(signed_in):
    await signed_in.register()
    signed_in.slack_rig.client.error = ProviderError("down")
    page = await signed_in.client.get(EDIT)
    assert page.status_code == 200 and str(escape(copy.CATALOG_DOWN)) in page.text
    fields = dict(form_pairs(page.text, EDIT))
    assert fields["fusion_analyst"] == "openai/gpt-x" and fields["subagent_model"] == "google/gemini-flash"
    assert fields["model_google"] == "google/gemini-x"  # the saved models stay selected, nothing is blanked
    assert signed_in.row().active


async def test_turn_off_turn_on_and_other_edits_work_while_the_model_list_is_down(signed_in):
    await signed_in.register()
    signed_in.slack_rig.client.error = ProviderError("down")
    page = await signed_in.client.get(EDIT)
    saved = await signed_in.post(EDIT, edited(form_pairs(page.text, EDIT), daily_cap_usd="9"))
    assert saved.status_code == 303 and signed_in.row().daily_cap_usd == 9.0
    models = list(signed_in.slack_rig.prefs.calls)
    assert models == [{"google": "google/gemini-x", "openai": "openai/gpt-x"}]  # none rewritten by that save
    assert (await signed_in.post(EDIT + "/off", {"reason": "pause"})).status_code == 303
    assert not signed_in.row().active
    assert (await signed_in.post(EDIT + "/on", {})).status_code == 303
    assert signed_in.row().active


# ---- registering needs the analyst and the helper ----

@pytest.mark.parametrize(("field", "sentence"), [("fusion_analyst", copy.CHOOSE_ANALYST),
                                                 ("subagent_model", copy.CHOOSE_HELPER)])
async def test_register_refuses_a_blank_analyst_or_helper_and_keeps_the_input(signed_in, field, sentence):
    response = await signed_in.post(NEW, register_form(**{field: "", "label": "my-team"}))
    assert response.status_code == 422 and str(escape(sentence)) in response.text
    assert dict(form_pairs(response.text, NEW))["label"] == "my-team"
    assert signed_in.slack_rig.registry.all() == []


async def test_a_refused_register_still_offers_none_for_the_families_that_have_no_model(signed_in):
    response = await signed_in.post(NEW, register_form(label="Bad Label"))
    assert response.status_code == 422
    select = response.text.split('<select name="model_google"')[1].split("</select>")[0]
    assert '<option value="">None</option>' in select and '<option value="google/gemini-x" selected>' in select
