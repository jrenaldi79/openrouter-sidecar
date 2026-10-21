"""Welcome, help and data notice copy for the Slack bot."""

from dataclasses import replace

from openrouter_image_mcp.hosted.slack import parser
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.slack.welcome import (
    DATA_NOTICE,
    GUEST_OFF,
    NOT_SET_UP,
    help_text,
    welcome_text,
)

WELCOME = (
    "Hi, I'm Sidecar. I answer with models other than Claude, so you can compare views with @Claude in the "
    "same thread. I read this thread and recent messages in this channel, including Claude's replies, and "
    "only when you mention me. Other teams can't see anything from here.\n\n"
    "A few first messages to try:\n"
    "• `@Sidecar ask gemini what's the weakest assumption in Claude's answer above?`\n"
    "• `@Sidecar compare should we price per seat or per site?`\n"
    "• `@Sidecar chart this by month` with a CSV attached\n\n"
    "Your team's budget is $75 a month (`@Sidecar usage`). What I send to model and search providers, and "
    "how long files are kept, is in `@Sidecar help`, along with every command."
)


def make_row(**optional):
    return new_row("T1", "C1", "team-a", "2027", fusion_panel=("a/one", "b/two", "c/three"),
                   fusion_analyst="d/analyst", subagent_model="e/sub", **optional)


def test_fixed_lines():
    assert NOT_SET_UP == "Sidecar isn't set up for this channel. Ask your instructor to add it."
    assert GUEST_OFF == ("Sidecar is off in this channel because it now has a guest or is shared outside "
                         "Northwestern. Ask your instructor.")


def test_welcome_matches_spec():
    assert welcome_text(make_row(), 75.0) == WELCOME


def test_welcome_formats_fractional_budget():
    assert "$37.5 a month" in welcome_text(make_row(), 37.5)


def test_welcome_without_budget():
    text = welcome_text(make_row(), None)
    assert "Your team has a monthly budget (`@Sidecar usage`)." in text
    assert "$" not in text


def test_welcome_example_falls_back_to_default_model():
    row = make_row(alias_map={"gpt": "ChatGPT", "grok": "Grok"}, default_model="grok")
    text = welcome_text(row, 75.0)
    assert "`@Sidecar ask grok what's the weakest" in text
    assert "ask gemini" not in text


def test_help_lists_every_alias_and_command():
    row = make_row()
    text = help_text(row)
    for alias, target in row.alias_map.items():
        assert f"{alias} → {target}" in text
    for word in parser._COMMAND_WORDS:
        assert word in text
    for bang in ("!restart", "!status", "!stop"):
        assert bang in text
    assert "&lt;question&gt;" in text and "<" not in text and ">" not in text


def test_help_aliases_sorted_and_names_default_and_panel():
    row = replace(make_row(), default_model="grok")
    text = help_text(row)
    assert text.index("gemini →") < text.index("gpt →") < text.index("grok →") < text.index("open →")
    assert "grok" in text.split("Default")[1].splitlines()[0]
    assert "a/one, b/two, c/three" in text and "d/analyst" in text


def test_help_has_data_notice():
    text = help_text(make_row())
    assert "What I send and keep" in text
    assert DATA_NOTICE in text
    assert DATA_NOTICE.count("•") == 4
    assert "Exa, Parallel, Perplexity or Firecrawl" in DATA_NOTICE
