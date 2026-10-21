"""Command parser for @sidecar mentions (pure, no I/O)."""

import pytest

from openrouter_image_mcp.hosted.slack.parser import (
    Ask,
    Compare,
    Empty,
    Forget,
    Help,
    Models,
    Recall,
    Remember,
    SetDefault,
    ThreadCommand,
    Usage,
    command_text,
    parse,
    wants_sandbox,
)

ALIASES = ("gemini", "gpt", "claude")


def p(text: str):
    return parse(text, ALIASES)


# --- one test per row of the spec's Commands table ---------------------------------


def test_question_uses_default_model():
    assert p("how do I price this?") == Ask((), "how do I price this?")


def test_ask_one_model():
    assert p("ask gemini is this right?") == Ask(("gemini",), "is this right?")


def test_ask_two_models():
    assert p("ask gemini and gpt is this right?") == Ask(("gemini", "gpt"), "is this right?")


def test_compare():
    assert p("compare which logo is better?") == Compare("which logo is better?")


def test_models():
    assert p("models") == Models()


def test_set_default():
    assert p("set default gpt") == SetDefault("gpt")


def test_usage():
    assert p("usage") == Usage()


def test_remember_note():
    assert p("remember for this channel: client prefers blue") == Remember("client prefers blue")


def test_recall():
    assert p("what do you remember?") == Recall()


def test_forget():
    assert p("forget 2") == Forget(2)


def test_help():
    assert p("help") == Help()


@pytest.mark.parametrize("name", ["restart", "status", "stop"])
def test_bang_commands(name):
    assert p(f"!{name}") == ThreadCommand(name)


# --- ask details -------------------------------------------------------------------


def test_and_not_alias_stays_in_question():
    assert p("ask gemini and explain") == Ask(("gemini",), "and explain")


def test_exact_id():
    assert p("ask openai/gpt-5 hi") == Ask(("openai/gpt-5",), "hi")


def test_and_exact_id_is_second_alias():
    assert p("ask gemini and openai/gpt-5 hi") == Ask(("gemini", "openai/gpt-5"), "hi")


def test_three_models_and_duplicates_collapsed():
    assert p("ask gemini and gpt and gemini hi") == Ask(("gemini", "gpt"), "hi")


def test_unknown_alias_is_left_for_dispatch():
    assert p("ask nope what now") == Ask(("nope",), "what now")


def test_ask_alone_and_alias_without_question():
    assert p("ask") == Ask((), "")
    assert p("ask gemini") == Ask(("gemini",), "")


def test_command_words_are_case_insensitive():
    assert p("ASK Gemini hi") == Ask(("gemini",), "hi")
    assert p("Compare it") == Compare("it")
    assert p("HELP") == Help()
    assert p("Set Default GPT") == SetDefault("gpt")


# --- rulings and fallbacks to question ---------------------------------------------


def test_plain_question():
    assert p("what's wrong here?") == Ask((), "what's wrong here?")


def test_what_prefix_alone_is_not_recall():
    assert p("what do you remember about Bob?") == Ask((), "what do you remember about Bob?")
    assert p("what do you remember") == Recall()
    assert p("What Do You Remember ?") == Recall()


def test_empty():
    assert p("") == Empty()
    assert p("   ") == Empty()


def test_compare_empty():
    assert p("compare") == Compare("")


@pytest.mark.parametrize("text", ["set", "set default", "set default a b", "set color red"])
def test_set_needs_exact_form(text):
    assert p(text) == Ask((), text)


def test_remember_needs_prefix_and_note():
    assert p("remember to buy milk") == Ask((), "remember to buy milk")
    assert p("remember for this channel:") == Ask((), "remember for this channel:")
    assert p("Remember For This Channel:  x ") == Remember("x")


@pytest.mark.parametrize("text", ["forget", "forget 0", "forget -1", "forget x", "forget 1 2", "forget ²"])
def test_forget_needs_positive_integer(text):
    assert p(text) == Ask((), text)


def test_unknown_bang_is_question():
    assert p("!foo") == Ask((), "!foo")


@pytest.mark.parametrize("text", ["models are great", "help me write this", "usage of the word"])
def test_bare_commands_with_extra_words_are_questions(text):
    assert p(text) == Ask((), text)


# --- command_text ------------------------------------------------------------------


def test_mention_mid_text():
    got = command_text("hey <@B> ask gemini is &lt;x&gt; ok", "B")
    assert got == "ask gemini is <x> ok"
    assert p(got) == Ask(("gemini",), "is <x> ok")


def test_mention_at_end_is_question():
    assert command_text("is this right <@B>?", "B") == "is this right ?"


def test_leading_mention_question_and_command():
    assert command_text("<@B> hello there", "B") == "hello there"
    assert command_text("<@B> models", "B") == "models"
    assert command_text("<@B>   !stop", "B") == "!stop"
    assert command_text("<@B> what do you remember?", "B") == "what do you remember?"


def test_text_before_a_non_command_mention_is_kept():
    assert command_text("hey <@B> what's up", "B") == "hey what's up"


def test_only_mention_is_empty():
    assert command_text("<@B>", "B") == ""
    assert p(command_text("<@B>", "B")) == Empty()


def test_other_mentions_are_kept():
    assert command_text("<@B> ask gemini ping <@U123>", "B") == "ask gemini ping <@U123>"


def test_only_first_bot_mention_is_removed():
    assert command_text("<@B> compare <@B> x", "B") == "compare <@B> x"


def test_link_kept_as_url():
    assert command_text("<@B> read <https://x.com/a?b=1&amp;c=2|the page>", "B") == "read https://x.com/a?b=1&c=2"
    assert command_text("<@B> read <https://x.com>", "B") == "read https://x.com"


def test_escaped_link_text_is_not_a_link():
    assert command_text("<@B> say &lt;https://x|y&gt;", "B") == "say <https://x|y>"


def test_entities_decoded_once():
    assert command_text("<@B> a &amp;lt; b", "B") == "a &lt; b"


def test_no_mention_returns_decoded_text():
    assert command_text("just text &amp; more", "B") == "just text & more"


def test_other_bot_mention_is_not_ours():
    assert command_text("<@OTHER> ask gemini hi", "B") == "<@OTHER> ask gemini hi"


# --- wants_sandbox -----------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "use sandbox for this",
        "please USE SANDBOX",
        "run this python script",
        "execute the code",
        "chart this data by month",
        "PLOT the data",
        "Analyze the numbers",
        "analyse the dataset",
        "calculate totals from the spreadsheet",
        "plot the numbers in the csv",
    ],
)
def test_wants_sandbox_positive_without_file(question):
    assert wants_sandbox(question, False) is True


@pytest.mark.parametrize(
    "question",
    [
        "Give feedback on this graphic design poster",
        "Rewrite this paragraph",
        "Is this photograph well composed?",
        "What is the plot of this story",
        "Why do users abandon the flowchart step",
        "analyze this logo",
        "how do I calculate a fair price",
        "we need to execute the launch plan",
        "I run a small studio, advice?",
        "make a chart of sales",
        "what do you think?",
    ],
)
def test_wants_sandbox_negative_without_file(question):
    assert wants_sandbox(question, False) is False


def test_wants_sandbox_file_with_action():
    assert wants_sandbox("chart this by month", True) is True
    assert wants_sandbox("chart this by month", False) is False
    assert wants_sandbox("analyze the attached", True) is True


@pytest.mark.parametrize("question", ["summarize data.csv", "read the attached", "look at notes.TXT"])
def test_wants_sandbox_text_file_hints(question):
    assert wants_sandbox(question, True) is True
    assert wants_sandbox(question, False) is False


@pytest.mark.parametrize("question", ["what do you think?", "Rewrite this paragraph", "graphic design feedback"])
def test_wants_sandbox_file_without_intent(question):
    assert wants_sandbox(question, True) is False
