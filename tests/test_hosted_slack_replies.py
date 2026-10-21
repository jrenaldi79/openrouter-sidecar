"""Reply helpers for the Slack bot: escaping, splitting, footer, limit copy and the working message."""

import asyncio

from slack_fakes import FakeSlack

from openrouter_image_mcp.errors import AuthRequiredError, ProviderError
from openrouter_image_mcp.hosted.limits import RateLimitedError
from openrouter_image_mcp.hosted.provisioning import DISABLED, NOT_READY, UNAVAILABLE
from openrouter_image_mcp.hosted.slack.api import SlackCallError
from openrouter_image_mcp.hosted.slack.replies import (
    MAX_PARTS,
    SPLIT_AT,
    WorkingMessage,
    budget_text,
    clean,
    escape,
    footer,
    key_text,
    limit_text,
    post_answer,
    provider_error_text,
    split_answer,
)


def test_escape_blocks_mentions_and_links():
    out = escape("<!channel> <@U1> <https://e|x> a&b")
    assert "<" not in out and ">" not in out
    assert "a&amp;b" in out
    assert out.startswith("&lt;!channel&gt;")


def test_escape_does_not_double_escape_its_own_output():
    assert escape("<") == "&lt;"


def test_clean_redacts_key_and_slack_token():
    key = "sk-or-v1-" + "a" * 40
    token = "xoxb-" + "7" * 20
    out = clean(f"key {key} token {token} <!here>", [])
    assert key not in out and token not in out and "<" not in out


def test_clean_masks_supplied_secrets_before_escaping():
    out = clean("the secret is hunter&2 ok", ["hunter&2", ""])
    assert out == "the secret is *** ok"


def test_split_on_paragraphs():
    text = "\n\n".join(f"{i} " + "x" * 1500 for i in range(6))
    parts = split_answer(text)
    assert len(parts) > 1
    assert all(len(p) <= SPLIT_AT for p in parts)
    assert all(p.endswith("\n\n") for p in parts[:-1])
    assert "".join(parts) == text


def test_split_short_text_is_one_part():
    assert split_answer("hello") == ["hello"]


def test_split_empty_text_returns_no_parts():
    assert split_answer("") == [] and split_answer("  \n\n ") == []


def test_split_long_paragraph_hard_cut():
    text = "y" * 8000
    parts = split_answer(text)
    assert [len(p) for p in parts] == [3500, 3500, 1000]
    assert "".join(parts) == text


def test_split_hard_cut_prefers_a_nearby_break():
    text = "a" * 3300 + " " + "b" * 3000
    parts = split_answer(text)
    assert parts[0] == "a" * 3300 + " "
    assert "".join(parts) == text


def test_more_than_three_parts_detected():
    parts = split_answer("z" * 20000)
    assert len(parts) > MAX_PARTS


def test_footer_plain_text():
    assert footer("Gemini 3 Pro", 0.0123) == "Gemini 3 Pro · $0.0123"
    assert footer("Gemini 3 Pro", None) == "Gemini 3 Pro · cost unknown"
    assert footer("GPT", 0.5, "Over budget soon.") == "GPT · $0.5000\nOver budget soon."


def test_limit_texts():
    assert limit_text(RateLimitedError("x", 3600, kind="daily"), daily_cap_usd=4.0) == (
        "Your team's daily Sidecar cap ($4) is reached. It resets at midnight UTC. "
        "Ask your instructor if you need more.")
    assert limit_text(RateLimitedError("x", 5, kind="concurrency"), max_concurrent=3) == (
        "Your team already has 3 answers running. Try again in a minute.")
    assert limit_text(RateLimitedError("x", 250, kind="rate")) == (
        "You've sent 10 requests in 10 minutes. Try again in about 5 minutes.")
    assert limit_text(RateLimitedError("x", 40, kind="minute", limit=20, span_s=60)) == (
        "Your team has sent 20 requests in the last minute. Try again in about 1 minute.")
    assert limit_text(RateLimitedError("x", 200, kind="minute", limit=50, span_s=300)) == (
        "Your team has sent 50 requests in the last 5 minutes. Try again in about 4 minutes.")


def test_limit_texts_use_defaults_and_format_arguments():
    assert "($4)" in limit_text(RateLimitedError("x", 1, kind="daily"))
    assert "($2.5)" in limit_text(RateLimitedError("x", 1, kind="daily"), daily_cap_usd=2.5)
    assert "7 requests" in limit_text(RateLimitedError("x", 1), user_limit=7)
    assert "about 1 minute." in limit_text(RateLimitedError("x", 1))


def test_budget_text():
    assert budget_text() == ("Your team's monthly Sidecar budget is used up. "
                             "It resets at the start of next month. Ask your instructor.")


def test_provider_error_names_model_and_suggests_alias():
    text = provider_error_text("Gemini 3 Pro", ["gpt", "claude"])
    assert "Gemini 3 Pro" in text and "gpt" in text
    assert "Gemini 3 Pro" in provider_error_text("Gemini 3 Pro", [])


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(slack, clock):
    return WorkingMessage(slack, "C1", "1.1", "Gemini", clock=clock)


def test_working_message_start_posts_as_sidecar_model():
    slack, clock = FakeSlack(), Clock()
    ts = asyncio.run(make(slack, clock).start())
    assert slack.posts == [{"channel": "C1", "thread_ts": "1.1", "text": "Sidecar · Gemini is working…",
                            "username": "Sidecar · Gemini", "ts": ts}]


def test_working_message_throttle():
    async def run():
        slack, clock = FakeSlack(), Clock()
        wm = make(slack, clock)
        await wm.start()
        for t, text in ((0, "reading"), (3, "thinking"), (12, "writing"), (13, "writing"), (30, "writing")):
            clock.now = t
            await wm.step(text)
        return slack.updates

    updates = asyncio.run(run())
    assert [u["text"] for u in updates] == ["writing"]


def test_working_message_ignores_429():
    async def run():
        slack, clock = FakeSlack(), Clock()
        wm = make(slack, clock)
        await wm.start()
        slack.fail_next("update", "ratelimited", 5)
        clock.now = 20
        await wm.step("writing")
        return slack.updates

    assert asyncio.run(run()) == []


def test_working_message_finish_always_edits_and_never_raises():
    async def run():
        slack, clock = FakeSlack(), Clock()
        wm = make(slack, clock)
        await wm.start()
        await wm.finish("done")
        slack.fail_next("update", "ratelimited", 5)
        await wm.finish("again")
        return slack.updates

    assert [u["text"] for u in asyncio.run(run())] == ["done"]


def test_working_message_escapes_every_text_it_posts():
    async def run():
        slack, clock = FakeSlack(), Clock()
        wm = make(slack, clock)
        await wm.start()
        clock.now = 20
        await wm.step("Searched the channel for '<!channel> & <@U1>'")
        await wm.finish("<!here>")
        return slack.updates

    texts = [u["text"] for u in asyncio.run(run())]
    assert texts == ["Searched the channel for '&lt;!channel&gt; &amp; &lt;@U1&gt;'", "&lt;!here&gt;"]


# ---- post_answer and key_text ----

def _post(slack, text, foot="Gemini · $0.0100", secrets=()):
    asyncio.run(post_answer(slack, "C1", "1.1", "Gemini", text, foot, secrets=secrets))


def test_post_answer_parts_with_footer_on_last():
    slack = FakeSlack()
    text = "a" * 3000 + "\n\n" + "b" * 3000
    _post(slack, text)
    assert [p["text"] for p in slack.posts] == ["a" * 3000 + "\n\n", "b" * 3000 + "\n\nGemini · $0.0100"]
    assert {(p["username"], p["thread_ts"]) for p in slack.posts} == {("Sidecar · Gemini", "1.1")}


def test_post_answer_cleans_text_and_footer():
    slack = FakeSlack()
    _post(slack, "<!here> sk-or-v1-abc team-secret", foot="<x> · $1", secrets=["team-secret"])
    assert slack.posts[0]["text"] == "&lt;!here&gt; sk-or-*** ***\n\n&lt;x&gt; · $1"


def test_post_answer_over_max_parts_uploads_markdown():
    slack = FakeSlack()
    text = "\n\n".join(["p" * 3400] * (MAX_PARTS + 1)) + " <!here> team-secret"
    _post(slack, text, secrets=["team-secret"])
    (upload,) = slack.uploads
    assert upload["filename"] == "answer.md" and upload["thread_ts"] == "1.1"
    assert upload["content"].decode().endswith(" <!here> ***")  # a file is not parsed for mentions
    assert [p["text"] for p in slack.posts] == ["Gemini · $0.0100"]


def test_post_answer_retries_each_call_once():
    slack = FakeSlack()
    slack.fail_next("post", "internal_error")
    _post(slack, "hello")
    assert [p["text"] for p in slack.posts] == ["hello\n\nGemini · $0.0100"]


def test_post_answer_raises_after_second_failure():
    slack = FakeSlack()
    slack.fail_next("upload", "internal_error")
    slack.fail_next("upload", "internal_error")
    try:
        _post(slack, "\n\n".join(["p" * 3400] * 4))
    except SlackCallError:
        pass
    else:
        raise AssertionError("expected SlackCallError")
    assert slack.uploads == [] and slack.posts == []


def test_key_text_is_team_wording():
    texts = [key_text(AuthRequiredError(m)) for m in (NOT_READY, DISABLED, "OpenRouter rejected your key")]
    texts.append(key_text(ProviderError(UNAVAILABLE)))
    assert len(set(texts)) == 4
    for text in texts:
        assert "this channel's Sidecar key" in text or "This channel's Sidecar key" in text
        assert "student" not in text.lower() and "Your" not in text and "account_status" not in text
