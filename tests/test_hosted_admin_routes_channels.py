"""The Channels, register, edit and log pages through the routes, against the real channel admin service."""

import pytest
from admin_routes_rig import CHAN, EDIT, NEW, OTHER, AdminRig, edited, form_pairs
from markupsafe import escape
from slack_admin_rig import FAMILY_DEFAULTS, GUEST_TEXT, registered_row

from openrouter_image_mcp.errors import ProviderError
from openrouter_image_mcp.hosted.admin import copy


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    rig.sign_in()
    yield rig
    await rig.aclose()


async def register(rig):
    """A registered channel whose team has a model for every family the form lists (as a registered team does:
    the page selects the first model of a family with none saved)."""
    await rig.register({**FAMILY_DEFAULTS, "x-ai": "x-ai/grok-x"})


async def edit_form(rig, **changes):
    """The edit page's form as a browser would post it, with `changes` typed in."""
    page = await rig.client.get(EDIT)
    assert page.status_code == 200
    return edited(form_pairs(page.text, EDIT), **changes)


async def test_channels_page_lists_registered_and_unregistered(rig):
    await rig.register()
    page = (await rig.client.get("/admin")).text
    assert "abbott" in page and f'href="{EDIT}"' in page
    assert f'href="/admin/channels/new?channel={OTHER}"' in page and "#other" in page


async def test_channels_page_when_slack_fails(rig):
    await rig.register()
    rig.slack_rig.slack.fail_next("bot_channels", "boom")
    response = await rig.client.get("/admin")
    assert response.status_code == 200
    assert "abbott" in response.text and "Slack didn&#39;t answer" in response.text
    assert "boom" not in response.text and "channels/new" not in response.text  # nothing to register from


async def test_new_form_get(rig):
    response = await rig.client.get(NEW)
    assert response.status_code == 200
    fields = dict(form_pairs(response.text, NEW))
    assert fields["_csrf"] == rig.csrf and fields["label"] == "" and fields["cohort"] == "2027"
    assert (fields["daily_cap_usd"], fields["max_concurrent"], fields["default_model"]) == ("4.0", "3", "gemini")
    assert "<h1>Register #abbott</h1>" in response.text and "model_google" in response.text
    assert "Zed" in response.text and "Ana" in response.text  # the members by name


async def test_new_form_blocked_for_a_guest_channel(rig):
    rig.slack_rig.slack.users["U2"] = {"is_restricted": True}
    response = await rig.client.get(NEW)
    assert response.status_code == 422 and str(escape(GUEST_TEXT)) in response.text
    assert "<form" not in response.text.replace('<form method="post" action="/admin/logout"', "")


async def test_new_form_needs_a_channel_and_the_model_list(rig):
    assert (await rig.client.get("/admin/channels/new")).headers["location"] == "/admin"
    rig.slack_rig.client.error = ProviderError("down")
    response = await rig.client.get(NEW)
    assert response.status_code == 422 and "model list didn&#39;t load" in response.text


async def test_edit_form_get(rig):
    await register(rig)
    response = await rig.client.get(EDIT)
    fields = dict(form_pairs(response.text, EDIT))
    assert "<h1>Edit #abbott</h1>" in response.text and "Status: On" in response.text
    assert fields["model_google"] == "google/gemini-x" and fields["model_openai"] == "openai/gpt-x"
    assert fields["orig_daily_cap_usd"] == '"4.0"' and fields["orig_family_models"].startswith("{")
    assert "Turn off" in response.text


async def test_edit_page_404_for_an_unregistered_channel(rig):
    response = await rig.client.get(EDIT)
    assert response.status_code == 404 and copy.NOT_REGISTERED in response.text


async def test_log_page(rig):
    await register(rig)
    await rig.post(EDIT + "/off", {"reason": "class ended"})
    page = (await rig.client.get("/admin/log")).text
    for text in ("registered", "models changed", "turned on", "turned off", "abbott", "class ended"):
        assert text in page
    assert page.index("turned off") < page.index("registered")  # newest first


async def test_registered_channel_with_scripted_label_renders_escaped(rig):
    rig.slack_rig.registry.add(registered_row(label="x-label"))
    rig.slack_rig.slack.channels[CHAN]["name"] = "<script>alert(1)</script>"
    page = (await rig.client.get("/admin")).text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page
    assert FAMILY_DEFAULTS  # the rig's families are what the forms offer


async def test_register_link_for_a_registered_channel_goes_to_its_edit_page(rig):
    await rig.register()
    response = await rig.client.get(NEW)
    assert (response.status_code, response.headers["location"]) == (303, EDIT)
