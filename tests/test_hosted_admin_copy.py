"""The admin page's sentences: plain text, and the templated ones take exactly their fields."""

from openrouter_image_mcp.hosted.admin import copy


def test_templated_sentences_format():
    assert copy.NO_GUARDRAIL.format(cohort="2027").startswith("The 2027 workspace has no")
    assert copy.GUEST_BLOCKED.format(name="abbott", reason="has a guest member") == (
        "Sidecar can't be used in #abbott: it has a guest member.")
    assert copy.SETUP_UNFINISHED_TEXT.format(reason="x") == "Setup didn't finish: x."
    assert copy.MODELS_NOT_SAVED.format(reason="x").endswith("weren't changed: x.")


def test_sentences_are_plain_text():
    texts = [value for name, value in vars(copy).items() if name.isupper()]
    assert texts and not any(mark in text for text in texts for mark in "<>&")
