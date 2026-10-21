"""Every non-ask `@sidecar` command, end to end through the dispatcher, and alias resolution."""

import asyncio

import pytest
from slack_dispatch_rig import DispatchRig, FakePreferences
from slack_runner_rig import CHANNEL, body, call, wait_until

from openrouter_image_mcp.errors import BadRequestError
from openrouter_image_mcp.hosted.slack.commands import (
    NO_MODEL_SET,
    RESTARTED,
    NoModelSet,
    UnknownAlias,
    resolve_alias,
)
from openrouter_image_mcp.preferences import NO_DEFAULT


@pytest.fixture
def rig(db_target, keys):
    return DispatchRig(db_target, keys)


async def _said(rig, text, **fields):
    before = len(rig.slack.posts)
    await rig.mention(text, **fields)
    return [p["text"] for p in rig.slack.posts[before:]]


async def test_resolve_alias_brand_and_exact_id(rig):
    row, prefs = rig.registry.get("T1", CHANNEL), FakePreferences()
    assert await resolve_alias(row, "gemini", prefs) == ("g/model", "Gemini")
    assert await resolve_alias(row, "z/exact", prefs) == ("z/exact", "Exact Model")
    with pytest.raises(UnknownAlias) as unknown:
        await resolve_alias(row, "claude", prefs)
    assert unknown.value.valid == ["gemini", "gpt", "grok", "open"]
    with pytest.raises(UnknownAlias):
        await resolve_alias(row, "nobody/missing", prefs)
    assert prefs.calls == ["Gemini", "z/exact", "nobody/missing"]


class _Refuses(FakePreferences):
    def __init__(self, message):
        super().__init__()
        self.message = message

    async def resolve(self, model=None):
        raise BadRequestError(self.message)


async def test_brand_without_a_default_says_no_model_set(rig):
    row = rig.registry.get("T1", CHANNEL)
    with pytest.raises(NoModelSet) as caught:
        await resolve_alias(row, "gemini", _Refuses(NO_DEFAULT))
    assert caught.value.text == NO_MODEL_SET == "This alias has no model set yet. Ask your instructor."
    with pytest.raises(BadRequestError):  # any other refusal is still a provider problem
        await resolve_alias(row, "gemini", _Refuses("Choose an available exact advisor model ID."))
    with pytest.raises(UnknownAlias):  # an exact id is never "not set"
        await resolve_alias(row, "z/exact", _Refuses(NO_DEFAULT))


async def test_models_and_set_default(rig):
    (models,) = await _said(rig, "models")
    assert "• gemini → Gemini (g/model)" in models and "• gpt → ChatGPT (o/gpt)" in models
    assert "Default: gemini" in models and "Fusion panel: a/one, b/two" in models
    assert await _said(rig, "set default gpt") == ["The default model for this channel is now gpt (ChatGPT)."]
    assert rig.registry.get("T1", CHANNEL).default_model == "gpt"
    await rig.mention("a plain question", thread_ts="1700000200.000001")
    assert rig.models_asked() == ["o/gpt"]
    (refused,) = await _said(rig, "set default claude")
    assert refused.startswith("I don't know the model claude.")
    assert rig.registry.get("T1", CHANNEL).default_model == "gpt"


async def test_memory_commands(rig):
    assert await _said(rig, "remember for this channel: use metric units") == ["Saved as note 1."]
    await rig.mention("remember for this channel: <!channel> &amp; &lt;b&gt;")  # as Slack sends it
    (recall,) = await _said(rig, "what do you remember?")
    assert recall == "Notes for this channel\n1. use metric units\n2. &lt;!channel&gt; &amp; &lt;b&gt;"
    (forgot,) = await _said(rig, "forget 2")
    assert forgot == "Forgot note 2: &lt;!channel&gt; &amp; &lt;b&gt;"
    assert await _said(rig, "forget 9") == ["There is no note number 9. Ask what I remember to see the numbers."]
    await rig.mention("forget 1")
    assert await _said(rig, "what do you remember?") == ["I don't have any notes for this channel yet."]


async def test_restart_archives(rig):
    await rig.mention("ask gpt first")
    assert await _said(rig, "!restart") == [RESTARTED]
    assert rig.chat_rows() == []
    await rig.mention("second")  # no thread chat left: back to the channel default, in a new chat
    assert rig.models_asked() == ["o/gpt", "g/model"]


async def test_status_and_stop(rig):
    gate = asyncio.Event()

    async def held(request):  # each job's first round asks for a tool, so a stop lands before round two
        await gate.wait()
        return body(call()) if len(rig.model.requests) <= 2 else body()

    rig.model.respond = held
    await rig.mention("ask gemini and gpt slow question", drain=False)
    await wait_until(lambda: len(rig.model.requests) == 2)
    # Commands start no jobs; drain=False so the held answers keep running.
    assert await _said(rig, "!status", drain=False) == ["Running in this thread: Gemini, ChatGPT."]
    assert await _said(rig, "!status", drain=False, thread_ts=None) == [
        "Running in this channel: Gemini, ChatGPT."]
    assert await _said(rig, "!stop", drain=False) == ["Stopping 2 answers."]
    gate.set()
    await rig.dispatcher.drain()
    assert [u["text"] for u in rig.slack.updates[-2:]] == ["Stopped.", "Stopped."]
    assert len(rig.model.requests) == 2
    assert await _said(rig, "!status") == ["Nothing is running in this thread."]
    assert await _said(rig, "!stop") == ["Nothing is running in this thread."]


async def test_usage_posts_report(rig):
    rig.key_info = {"usage_monthly": 1.5, "effective_budget": {"limit_usd": 75}}
    (usage,) = await _said(rig, "usage")
    assert usage.startswith("This month: $1.50 of $75.00 used, $73.50 left.")
    rig.key_info = RuntimeError("boom")
    assert await _said(rig, "usage") == ["I couldn't read this team's spend right now. Try again in a minute."]


async def test_unknown_alias_escaped_once(rig):
    (reply,) = await _said(rig, "ask <@U2> hello")
    assert reply.startswith("I don't know the model &lt;@u2&gt;.")


async def test_empty_mention_points_to_help(rig):
    (reply,) = await _said(rig, "")
    assert "@Sidecar help" in reply
