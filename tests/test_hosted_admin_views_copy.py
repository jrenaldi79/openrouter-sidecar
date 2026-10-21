"""The templates' text and markup contracts: sentences come from copy.py, htmx refusals show the banner, and the
form's model choices, labels and attributes."""

import json
import re
from html.parser import HTMLParser

from admin_views_rig import (
    Sess,
    body,
    channels_html,
    form_ctx,
    overview,
    row,
    values,
)
from markupsafe import escape

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.views import TEMPLATES_DIR, Views

PLAIN = ["SETUP_NOTICE", "LABEL_HELP", "MODELS_HELP", "ALIASES_HELP", "FUSION_HELP", "FETCH_HELP",
         "MEMBERS_HELP", "LABEL_FIXED", "IDS_HELP", "NONE_REGISTERED", "NO_CHANGES", "LOG_INTRO", "CHOOSE_MODEL"]


def form(mode="edit", **over):
    return body(Views().page("channel_form", session=Sess(), **form_ctx(mode, **over)))


def test_htmx_counts_a_refused_request_as_an_error_so_the_banner_shows_it():
    html = channels_html()
    config = json.loads(re.search(r"""<meta name="htmx-config" content='([^']+)'""", html).group(1))
    codes = [entry["code"] for entry in config["responseHandling"]]
    assert "422" not in codes and "409" not in codes  # a swapped 422 would replace the whole page body
    assert config["responseHandling"][-1] == {"code": "[45]..", "swap": False, "error": True}
    assert {"code": "[23]..", "swap": True} in config["responseHandling"]


def test_template_sentences_come_from_copy():
    pages = [channels_html(), channels_html(overview=overview([(row(), "abbott")], [])),
             form("register", values=values(fetch_allowlist=[]), members=None, setup_unfinished=False),
             form(setup_unfinished=True),
             body(Views().page("log", session=Sess(), entries=[]))]
    html = "".join(pages)
    for name in PLAIN:
        assert str(escape(getattr(copy, name))) in html, name
    sources = "".join(path.read_text(encoding="utf-8") for path in TEMPLATES_DIR.glob("*.html"))
    for text in [getattr(copy, name) for name in PLAIN] + ["Sidecar stops answering there",
                                                          "Sidecar starts answering there", copy.SIGNED_IN_AS]:
        assert text not in sources, text  # the sentence lives in copy.py, not in a template


def test_the_edit_page_shows_the_fixed_label_note_and_the_register_page_the_label_help():
    assert str(escape(copy.LABEL_FIXED)) in form("edit")
    assert str(escape(copy.LABEL_HELP)) in form("register") and copy.LABEL_FIXED not in form("register")


def test_the_models_help_is_true_in_both_modes():
    for mode in ("register", "edit"):
        html = form(mode)
        assert str(escape(copy.MODELS_HELP)) in html and "leaves a family as it is" not in html


def test_the_channels_page_needs_no_context_variable_for_the_empty_text():
    assert str(escape(copy.NO_CHANNELS)) in channels_html()
    assert str(escape(copy.NONE_REGISTERED)) in channels_html()


def test_confirm_texts_name_the_channel_and_stay_escaped():
    active = form(channel={"id": "C1", "name": "abbott-team"})
    assert f'hx-confirm="{escape(copy.CONFIRM_OFF.format(name="abbott-team"))}"' in active
    off = row()
    off = type(off)(**{**off.__dict__, "active": False, "inactive_reason": "paused"})
    inactive = form(r=off, channel={"id": "C1", "name": "abbott-team"})
    assert f'hx-confirm="{escape(copy.CONFIRM_ON.format(name="abbott-team"))}"' in inactive


def test_register_asks_for_the_analyst_and_helper_instead_of_preselecting_a_model():
    html = form("register", values=values(fusion_analyst="", subagent_model=""))
    for name in ("fusion_analyst", "subagent_model"):
        select = re.search(rf'<select name="{name}".*?</select>', html, re.DOTALL).group()
        assert " required" in select.split(">")[0]
        first = re.search(r"<option[^>]*>[^<]*</option>", select).group()
        assert first == f'<option value="" selected>{copy.CHOOSE_MODEL}</option>'
    typed = form("register", values=values(fusion_analyst="openai/gpt-x"))
    analyst = re.search(r'<select name="fusion_analyst".*?</select>', typed, re.DOTALL).group()
    assert '<option value="openai/gpt-x" selected>' in analyst and 'value="" selected' not in analyst


def test_edit_keeps_the_analyst_and_helper_selects_as_they_were():
    html = form("edit")
    for name in ("fusion_analyst", "subagent_model"):
        select = re.search(rf'<select name="{name}".*?</select>', html, re.DOTALL).group()
        assert " required" not in select.split(">")[0] and copy.CHOOSE_MODEL not in select


def test_the_none_choice_follows_what_was_saved_not_what_was_typed():
    typed = values(family_models={"gpt": "openai/gpt-x", "gemini": "google/gemini-x"})
    saved = values(family_models={"gpt": "openai/gpt-x", "gemini": ""})
    html = form(values=typed, orig=saved)
    gemini = re.search(r'<select name="model_gemini".*?</select>', html, re.DOTALL).group()
    gpt = re.search(r'<select name="model_gpt".*?</select>', html, re.DOTALL).group()
    assert '<option value="">None</option>' in gemini  # still there after a refusal, so the typed model can be undone
    assert '<option value="google/gemini-x" selected>' in gemini
    assert 'value=""' not in gpt


def test_accessible_names_for_the_members_fieldset_and_the_action_columns():
    html = form()
    assert re.search(r"<fieldset>\s*<legend[^>]*>[^<]+</legend>", html)
    page = channels_html(overview=overview([(row(), "abbott-team")], [{"id": "C9", "name": "new-room"}]))
    assert page.count('<th><span class="sr">Actions</span></th>') == 2
    assert re.search(r'<a href="/admin/channels/T1/C1">Edit <span class="sr">#abbott-team</span></a>', page)
    assert re.search(r'>Register <span class="sr">#new-room</span></a>', page)
    assert "<th></th>" not in page


class _Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names, self.values = set(), []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            self.names.add(name)
            self.values.append((name, value))


def test_quotes_in_slack_text_cannot_break_out_of_an_attribute():
    quote = '" onfocus="alert(1)\' autofocus x="'
    r = row(label="abbott")
    ctx = form_ctx(channel={"id": "C1", "name": quote}, members=[("U1", quote)],
                   values=values(r, fetch_allowlist=[quote], alias_map={quote: quote}, invoker_allowlist=[quote]))
    ctx["orig"] = values(r, alias_map={quote: quote}, fusion_panel=[quote], fusion_analyst=quote)
    off = type(r)(**{**r.__dict__, "active": False, "inactive_reason": quote})
    pages = [body(Views().page("channel_form", session=Sess(), **ctx)),
             form(r=off, channel={"id": "C1", "name": quote}),
             form("register", values=values(r, label=quote)),
             channels_html(overview=overview([(r, quote)], [{"id": quote, "name": quote}]))]
    for html in pages:
        found = _Attributes()
        found.feed(html)
        assert not {n for n in found.names if n.startswith("on")} and "autofocus" not in found.names, html[:80]
        assert quote in {v for _, v in found.values} or quote not in html  # it only ever appears as a whole value
    page = pages[0]
    assert 'name="orig_alias_map"' in page and "hx-confirm=" in page and f'value="{escape(quote)}"' in page
