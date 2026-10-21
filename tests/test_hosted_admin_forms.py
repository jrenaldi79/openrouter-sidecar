"""Form parsing and the rendered-versus-submitted diff, with no server involved."""

import json

import pytest
from admin_views_rig import row, values
from starlette.datastructures import FormData

from openrouter_image_mcp.hosted.admin import copy, forms
from openrouter_image_mcp.hosted.slack.channel_setup import ChannelRefused


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


@pytest.mark.parametrize("cap", ["lots", "", "0", "-1", "1000.01", "1e2", "nan", "inf", "1_0", "4,5", "\u0663"])
def test_bad_caps_are_refused_in_a_sentence(cap):
    shown = forms.submitted_values(FormData([("daily_cap_usd", cap), ("max_concurrent", "3")]))
    with pytest.raises(ChannelRefused) as refused:
        forms.typed(shown, strict=True)
    assert str(refused.value) == copy.CAP_INVALID
    assert forms.typed(shown)["daily_cap_usd"] == cap  # comparing only: nothing is refused until it is saved


@pytest.mark.parametrize("limit", ["lots", "", "0", "-1", "21", "2.5", "1e1", "1_0", "\u0663"])
def test_bad_concurrent_limits_are_refused_in_a_sentence(limit):
    shown = forms.submitted_values(FormData([("daily_cap_usd", "4"), ("max_concurrent", limit)]))
    with pytest.raises(ChannelRefused) as refused:
        forms.typed(shown, strict=True)
    assert str(refused.value) == copy.CONCURRENT_INVALID


@pytest.mark.parametrize("cap,expected", [("0.01", 0.01), ("4", 4.0), ("1000", 1000.0), (" 12.50 ", 12.5),
                                          (".5", 0.5)])
def test_good_caps_and_limits_parse(cap, expected):
    shown = forms.submitted_values(FormData([("daily_cap_usd", cap), ("max_concurrent", "20")]))
    typed = forms.typed(shown, strict=True)
    assert (typed["daily_cap_usd"], typed["max_concurrent"]) == (expected, 20)


def test_a_changed_bad_number_is_refused_but_an_untouched_odd_one_is_not():
    r = row(daily_cap_usd=5000.0)  # set outside this page, over today's bound
    form = _edit_pairs(r, fusion_analyst="openai/gpt-y")
    assert _diff(form)[0] == {"fusion_analyst": "openai/gpt-y"}
    with pytest.raises(ChannelRefused):
        _diff(_edit_pairs(r, daily_cap_usd="6000"))


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
    form = FormData([("default_model", "gemini"), ("daily_cap_usd", "4"), ("max_concurrent", "3"),
                     ("model_gpt", "openai/gpt-x")])
    assert forms.orig_values(form) is None
    changes, family = forms.changed(forms.submitted_values(form), None)
    assert changes["default_model"] == "gemini" and family == {"gpt": "openai/gpt-x"}


def test_garbled_originals_are_ignored():
    form = FormData([("orig_alias_map", "{not json"), ("orig_daily_cap_usd", '"4.0"')])
    assert forms.orig_values(form) == {"daily_cap_usd": "4.0"}


def test_a_blank_model_is_never_a_change():
    assert _diff(_edit_pairs(model_gpt="", model_gemini=""))[1] == {}  # the "None" choice of a family
    assert _diff(_edit_pairs(model_gpt="openai/gpt-y", model_gemini=""))[1] == {"gpt": "openai/gpt-y"}


def test_selected_models_skip_blanks():
    assert forms.selected_models({"family_models": {"gpt": "openai/gpt-x", "gemini": ""}}) == {
        "gpt": "openai/gpt-x"}


def test_snapshot_prefers_posted_originals_and_fills_the_rest():
    rendered = values(row())
    merged = forms.snapshot(rendered, {"daily_cap_usd": "7.0"})
    assert merged["daily_cap_usd"] == "7.0" and merged["default_model"] == rendered["default_model"]
    assert forms.snapshot(rendered, None) == rendered
