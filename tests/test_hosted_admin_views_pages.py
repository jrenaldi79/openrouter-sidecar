import re
from dataclasses import replace

from admin_views_rig import (
    Entry,
    Sess,
    body,
    channels_html,
    form_ctx,
    overview,
    posts,
    row,
    values,
)

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.views import Views
from openrouter_image_mcp.hosted.slack.registry import DEFAULT_ALIASES


def test_status_plain_text():
    on = row()
    off = replace(row(label="burke", base={}), active=False, inactive_reason="guest joined")
    html = channels_html(overview=overview([(on, "abbott-team"), (off, "burke-team")]))
    assert re.search(r"<td>On</td>", html)
    assert re.search(r"<td>Off: guest joined</td>", html)
    lowered = html.lower()
    assert "<mark" not in lowered and "pill" not in lowered
    bare = replace(on, active=False, inactive_reason=None)
    assert "<td>Off</td>" in channels_html(overview=overview([(bare, "x")]))


def test_channels_table_columns_and_links():
    r = replace(row(), daily_cap_usd=4.5, max_concurrent=2)
    html = channels_html(overview=overview([(r, "abbott-team"), (replace(r, channel_id="C2", team_label="lost"), "C2")],
                                          [{"id": "C9", "name": "new-room"}]))
    assert "<td>abbott</td>" in html and "<td>#abbott-team</td>" in html and "<td>2027</td>" in html
    assert "<td>gemini</td>" in html and "$4.50" in html and "<td>2</td>" in html
    assert 'href="/admin/channels/T1/C1"' in html
    assert "<td>C2</td>" in html  # the id stands in when Slack didn't give a name
    assert 'href="/admin/channels/new?channel=C9"' in html and "#new-room" in html
    assert copy.NO_CHANNELS not in html


def test_channels_empty_and_slack_notes():
    assert copy.NO_CHANNELS in channels_html()
    off = channels_html(overview=overview([(row(), "abbott-team")], slack_note=copy.SLACK_OFF))
    assert "Slack isn&#39;t turned on for this server." in off
    assert "/admin/channels/T1/C1" not in off and copy.NO_CHANNELS not in off
    down = channels_html(overview=overview([(row(), "abbott-team")], slack_note=copy.SLACK_DOWN))
    assert "Slack didn&#39;t answer." in down and "/admin/channels/T1/C1" in down


def test_form_register_vs_edit():
    register = body(Views().page("channel_form", session=Sess(), **form_ctx("register")))
    assert 'action="/admin/channels/new?channel=C1"' in register
    assert '<input name="label"' in register and '<select name="cohort"' in register
    assert "#abbott-team" in register
    edit = body(Views().page("channel_form", session=Sess(), **form_ctx("edit")))
    assert 'action="/admin/channels/T1/C1"' in edit
    assert '<input name="label"' not in edit and '<select name="cohort"' not in edit
    assert "abbott" in edit and "2027" in edit
    assert 'name="orig_default_model"' in edit and 'name="orig_label"' not in register
    for html in (register, edit):
        assert posts(html)
        for form in posts(html):
            assert form.count('<input type="hidden" name="_csrf"') == 1
    assert "Turn off" in edit and "Turn off" not in register and "Finish setup" not in edit


def test_form_fields_and_prefill():
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(
        values=values(fetch_allowlist=["a.edu", "b.org"], daily_cap_usd="6.5", max_concurrent="4",
                      invoker_allowlist=["U1"]))))
    assert 'name="daily_cap_usd"' in html and 'value="6.5"' in html and 'value="4"' in html
    assert re.search(r'<textarea name="fetch_allowlist"[^>]*>\na\.edu\nb\.org</textarea>', html)
    assert len(re.findall(r'name="alias_name"', html)) >= len(DEFAULT_ALIASES)
    assert '<select name="default_model"' in html
    for family in ("gpt", "gemini"):
        assert f'<select name="model_{family}"' in html
    assert html.count('name="fusion_panel"') == 8
    assert 'name="fusion_analyst"' in html and 'name="subagent_model"' in html
    # Models are grouped by family and the saved choice is selected.
    assert '<optgroup label="gemini">' in html and '<optgroup label="gpt">' in html
    assert re.search(r'<option value="openai/gpt-x" selected>', html)
    assert 'name="invoker_allowlist" value="U1" checked' in html
    assert 'name="invoker_allowlist" value="U2"' in html and 'value="U2" checked' not in html
    assert "Grace Hopper" in html and 'name="invoker_allowlist_text"' not in html


def test_form_keeps_a_saved_model_missing_from_the_catalog():
    r = row(base={"fusion_analyst": "old/retired-model"})
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(r=r)))
    assert '<option value="old/retired-model" selected>' in html


def test_form_members_as_id_list_when_names_do_not_load():
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(
        members=None, values=values(invoker_allowlist=["U1", "U2"]))))
    assert re.search(r'<textarea name="invoker_allowlist_text"[^>]*>\nU1\nU2</textarea>', html)
    assert 'type="checkbox"' not in html


def test_form_shows_error_and_keeps_input():
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(
        "register", error=copy.CATALOG_DOWN, values=values(label="my-team"))))
    assert 'role="alert"' in html and "The model list didn&#39;t load." in html
    assert 'value="my-team"' in html


def test_form_turn_off_and_on_forms():
    active = body(Views().page("channel_form", session=Sess(), **form_ctx()))
    off_form = next(f for f in posts(active) if "/off" in f)
    assert 'action="/admin/channels/T1/C1/off"' in off_form and 'name="reason"' in off_form
    assert "required" in off_form and "hx-confirm=" in off_form
    assert '/on"' not in active
    off = replace(row(), active=False, inactive_reason="guest joined")
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(r=off)))
    on_form = next(f for f in posts(html) if 'action="/admin/channels/T1/C1/on"' in f)
    assert "Turn on" in on_form and "hx-confirm=" in on_form
    assert "Turn off" not in html and "Off: guest joined" in html


def test_finish_setup_shown():
    unfinished = replace(row(), active=False, inactive_reason=copy.SETUP_UNFINISHED)
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(
        r=unfinished, setup_unfinished=True,
        error=copy.SETUP_UNFINISHED_TEXT.format(reason="the model list didn't load"))))
    assert "Setup didn&#39;t finish" in html
    assert 'formaction="/admin/channels/T1/C1/finish"' in html and "Finish setup" in html
    assert "Setup didn&#39;t finish: the model list didn&#39;t load." in html
    assert "Turn on" not in html
    finish_button = re.search(r"<button[^>]*formaction=[^>]*>", html).group(0)
    assert 'type="submit"' in finish_button
    setting_up = replace(row(), active=False, inactive_reason=copy.SETTING_UP)
    other = body(Views().page("channel_form", session=Sess(), **form_ctx(r=setting_up)))
    assert "Finish setup" not in other and "Off: Setting up" in other


def test_log_page():
    entries = [
        Entry(1_700_000_000.0, "ada@northwestern.edu", "changed", "T1", "C1", "abbott",
              {"daily_cap_usd": [4.0, 6.0], "fetch_allowlist": [[], ["a.edu", "b.org"]]}),
        Entry(1_700_000_100.0, "grace@northwestern.edu", "turned off", "T1", "C1", "abbott", {}),
    ]
    html = body(Views().page("log", session=Sess(), entries=entries))
    assert "ada@northwestern.edu" in html and "turned off" in html and "abbott" in html
    assert "daily_cap_usd" in html and "4.0" in html and "6.0" in html and "→" in html
    assert "a.edu, b.org" in html
    assert "2023-11-14 16:13" in html  # America/Chicago, not UTC
    assert "No changes yet." in body(Views().page("log", session=Sess(), entries=[]))
