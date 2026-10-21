"""Form parsing and the rendered-versus-submitted diff, with no server involved."""

import json

from admin_views_rig import Sess, body, form_ctx, row, values
from starlette.datastructures import FormData

from openrouter_image_mcp.hosted.admin import forms
from openrouter_image_mcp.hosted.admin.views import Views


def _edit_pairs(r=None, **changes):
    """What the browser posts for an untouched edit form, with `changes` (name -> value or list) on top."""
    shown = values(r or row())
    pairs = [(f"orig_{f}", json.dumps(shown[f])) for f in forms.ORIG_FIELDS]
    fields = {
        "default_model": shown["default_model"], "fusion_analyst": shown["fusion_analyst"],
        "subagent_model": shown["subagent_model"], "daily_cap_usd": shown["daily_cap_usd"],
        "max_concurrent": shown["max_concurrent"], "fetch_allowlist": "\n".join(shown["fetch_allowlist"]),
        "fusion_panel": shown["fusion_panel"], "invoker_allowlist": shown["invoker_allowlist"],
        "model_gpt": "openai/gpt-x", "model_gemini": "google/gemini-x",
    }
    fields.update(changes)
    for name, value in fields.items():
        pairs += [(name, v) for v in (value if isinstance(value, list) else [value])]
    for alias, target in shown["alias_map"].items():
        pairs += [("alias_name", alias), ("alias_target", target)]
    return FormData(pairs)


def _diff(form):
    return forms.changed(forms.submitted_values(form), forms.orig_values(form))


def test_submitted_values_parse_every_field():
    form = FormData([
        ("label", " abbott "), ("cohort", "2027"), ("default_model", "gpt"),
        ("alias_name", "gpt"), ("alias_target", "ChatGPT"), ("alias_name", ""), ("alias_target", ""),
        ("alias_name", "fast"), ("alias_target", "google/gemini-flash"),
        ("fusion_panel", "openai/gpt-x"), ("fusion_panel", ""), ("fusion_panel", "google/gemini-x"),
        ("fusion_analyst", "openai/gpt-x"), ("subagent_model", "google/gemini-flash"),
        ("daily_cap_usd", "5.50"), ("max_concurrent", "2"),
        ("fetch_allowlist", "example.com\r\n\r\n docs.example.org \r\nexample.com"),
        ("invoker_allowlist", "U1"), ("invoker_allowlist", "U2"), ("invoker_allowlist", "U1"),
        ("model_gpt", "openai/gpt-x"), ("model_gemini", ""),
    ])
    shown = forms.submitted_values(form)
    assert shown["label"] == "abbott" and shown["cohort"] == "2027"
    assert shown["alias_map"] == {"gpt": "ChatGPT", "fast": "google/gemini-flash"}
    assert shown["fusion_panel"] == ["openai/gpt-x", "google/gemini-x"]
    assert shown["fetch_allowlist"] == ["example.com", "docs.example.org"]
    assert shown["invoker_allowlist"] == ["U1", "U2"]
    assert shown["family_models"] == {"gpt": "openai/gpt-x", "gemini": ""}
    assert forms.typed(shown)["daily_cap_usd"] == 5.5 and forms.typed(shown)["max_concurrent"] == 2


def test_invoker_text_replaces_checkboxes_when_members_are_unknown():
    form = FormData([("invoker_allowlist_text", "U1\n\n U2 \nU1")])
    assert forms.submitted_values(form)["invoker_allowlist"] == ["U1", "U2"]


def test_unparsable_numbers_pass_through_for_the_registry_to_refuse():
    typed = forms.typed(forms.submitted_values(FormData([("daily_cap_usd", "lots"), ("max_concurrent", "2.5")])))
    assert typed["daily_cap_usd"] == "lots" and typed["max_concurrent"] == "2.5"


def test_untouched_edit_form_changes_nothing():
    assert _diff(_edit_pairs()) == ({}, {})


def test_only_the_edited_fields_are_changed():
    form = _edit_pairs(daily_cap_usd="9", fusion_analyst="openai/gpt-y", model_gpt="openai/gpt-y")
    changes, family = _diff(form)
    assert changes == {"daily_cap_usd": 9.0, "fusion_analyst": "openai/gpt-y"}
    assert family == {"gpt": "openai/gpt-y"}


def test_equal_numbers_in_other_spellings_are_unchanged():
    assert _diff(_edit_pairs(daily_cap_usd="4", max_concurrent="3"))[0] == {}


def test_invoker_order_alone_is_no_change():
    r = row(invoker_allowlist=("U2", "U1"))
    assert _diff(_edit_pairs(r, invoker_allowlist=["U1", "U2"]))[0] == {}
    assert _diff(_edit_pairs(r, invoker_allowlist=["U1"]))[0] == {"invoker_allowlist": ["U1"]}


def test_missing_originals_count_as_changed():
    form = FormData([("default_model", "gemini"), ("daily_cap_usd", "4"), ("model_gpt", "openai/gpt-x")])
    assert forms.orig_values(form) is None
    changes, family = forms.changed(forms.submitted_values(form), None)
    assert changes["default_model"] == "gemini" and family == {"gpt": "openai/gpt-x"}


def test_garbled_originals_are_ignored():
    form = FormData([("orig_alias_map", "{not json"), ("orig_daily_cap_usd", '"4.0"')])
    assert forms.orig_values(form) == {"daily_cap_usd": "4.0"}


def test_selected_models_skip_blanks():
    assert forms.selected_models({"family_models": {"gpt": "openai/gpt-x", "gemini": ""}}) == {
        "gpt": "openai/gpt-x"}


def test_form_values_show_input_but_keep_the_rendered_originals():
    r = row()
    shown = values(r, daily_cap_usd="9")
    html = body(Views().page("channel_form", session=Sess(), **form_ctx(
        "edit", r, values=forms.FormValues(shown, values(r)))))
    assert 'name="daily_cap_usd" type="number" min="0.01" step="0.01" value="9"' in html
    assert 'name="orig_daily_cap_usd" value="&#34;4.0&#34;"' in html
