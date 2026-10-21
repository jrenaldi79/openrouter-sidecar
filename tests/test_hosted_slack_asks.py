"""The question path of a mention: models, modes, chats, the shared context and Fusion."""

import asyncio

import pytest
from slack_dispatch_rig import DispatchRig
from slack_runner_rig import CHANNEL, THREAD

from openrouter_image_mcp.hosted.slack.api import SlackMessage
from openrouter_image_mcp.hosted.slack.asks import COMPARE_ALIAS, SANDBOX_OFF
from openrouter_image_mcp.hosted.slack.commands import NO_MODEL_SET
from openrouter_image_mcp.hosted.slack.context import CONTEXT_PATH
from openrouter_image_mcp.hosted.slack.payload import FUSION_CHOICE, fusion_tool
from openrouter_image_mcp.preferences import PreferenceService


@pytest.fixture
def rig(db_target, keys):
    return DispatchRig(db_target, keys)


def _answers(rig):
    return [p for p in rig.slack.posts if p["text"].startswith("The answer.")]


def _context_file(rig, chat_id):
    return {f["path"]: f["content"] for f in rig.services.files.current(rig.scope, chat_id)}[CONTEXT_PATH]


async def test_plain_question_uses_channel_default(rig):
    await rig.mention("what is our riskiest assumption?")
    assert rig.models_asked() == ["g/model"]
    assert [p["username"] for p in _answers(rig)] == ["Sidecar · Gemini"]


async def test_follow_up_uses_last_model(rig):
    await rig.mention("ask gpt what should we price?")
    await rig.mention("and per site?")
    assert rig.models_asked() == ["o/gpt", "o/gpt"]
    first, second = rig.job_rows()
    assert first["chat_id"] == second["chat_id"]
    assert [p["username"] for p in _answers(rig)] == ["Sidecar · ChatGPT"] * 2


async def test_exact_model_id_labelled_with_catalog_name(rig):
    await rig.mention("ask z/exact hello")
    assert rig.models_asked() == ["z/exact"]
    assert _answers(rig)[0]["username"] == "Sidecar · Exact Model"


async def test_two_models_two_answers(rig):
    await rig.mention("ask gemini and gpt which pricing model?")
    assert sorted(rig.models_asked()) == ["g/model", "o/gpt"]
    assert sorted(p["username"] for p in _answers(rig)) == ["Sidecar · ChatGPT", "Sidecar · Gemini"]
    assert {row["prompt"] for row in rig.job_rows()} == {"which pricing model?"}


async def test_context_gathered_once_for_two_models(rig):
    rig.slack.threads[(CHANNEL, THREAD)] = [SlackMessage(THREAD, "U1", "Root idea", None, None, THREAD)]
    replies, pins = rig.count("replies"), rig.count("pins")
    await rig.mention("ask gemini and gpt thoughts?")
    assert len(replies) == 1 and len(pins) == 1
    first, second = (row["chat_id"] for row in rig.job_rows())
    assert _context_file(rig, first) == _context_file(rig, second)
    assert "Root idea" in _context_file(rig, first)


async def test_every_turn_rereads_the_whole_thread(rig):
    """Ruling 1: first_turn=True and since_ts=None on every turn, so earlier discussion stays in context."""
    rig.slack.threads[(CHANNEL, THREAD)] = [SlackMessage(THREAD, "U1", "Root idea", None, None, THREAD)]
    await rig.mention("ask gemini first?")
    pins = rig.count("pins")
    await rig.mention("second?")
    assert len(pins) == 1
    chat_id = rig.job_rows()[-1]["chat_id"]
    assert "Root idea" in _context_file(rig, chat_id) and "## Thread" in _context_file(rig, chat_id)


async def test_thread_links_become_fetch_domains(rig):
    rig.slack.threads[(CHANNEL, THREAD)] = [
        SlackMessage(THREAD, "U1", "see <https://example.com/a|this>", None, None, THREAD)]
    await rig.mention("ask gemini summarize the link")
    (request,) = rig.model.requests
    fetch = [t for t in request["tools"] if t.get("type") == "openrouter:web_fetch"]
    assert fetch[0]["parameters"]["allowed_domains"] == ["example.com"]


async def test_sandbox_trigger_creates_sandbox_chat(rig):
    await rig.mention("ask gemini plot this data by month")
    (thread,) = rig.chat_rows()
    assert (thread.alias, thread.mode) == ("gemini", "sandbox")
    assert rig.store.get(rig.scope, thread.chat_id).sandbox is True


async def test_follow_up_keeps_sandbox_mode(rig):
    await rig.mention("ask gemini plot this data by month")
    await rig.mention("now by week please")
    first, second = rig.job_rows()
    assert first["chat_id"] == second["chat_id"]
    assert [(t.alias, t.mode) for t in rig.chat_rows()] == [("gemini", "sandbox")]


async def test_other_alias_does_not_inherit_sandbox(rig):
    await rig.mention("ask gemini plot this data by month")
    await rig.mention("ask gpt what do you think?")
    assert sorted((t.alias, t.mode) for t in rig.chat_rows()) == [("gemini", "sandbox"), ("gpt", "inline")]


async def test_sandbox_off_answers_inline_with_note(db_target, keys):
    rig = DispatchRig(db_target, keys, sandbox_enabled=False)
    await rig.mention("ask gemini plot this data by month")
    assert rig.slack.posts[0]["text"] == SANDBOX_OFF
    assert [(t.alias, t.mode) for t in rig.chat_rows()] == [("gemini", "inline")]
    assert len(_answers(rig)) == 1


async def test_compare_uses_fusion_payload(rig):
    await rig.mention("compare should we price per seat or per site?")
    (request,) = rig.model.requests
    assert request["tools"] == [fusion_tool(rig.registry.get("T1", CHANNEL), [])]
    assert request["tool_choice"] == FUSION_CHOICE
    assert [p["username"] for p in _answers(rig)] == ["Sidecar · Fusion"]
    (thread,) = rig.chat_rows()
    assert (thread.alias, thread.mode) == (COMPARE_ALIAS, "inline")
    assert rig.job_rows()[0]["prompt"] == "should we price per seat or per site?"


async def test_compare_never_sandbox(rig):
    await rig.mention("compare plot this data by month")
    assert [(t.alias, t.mode) for t in rig.chat_rows()] == [(COMPARE_ALIAS, "inline")]
    assert SANDBOX_OFF not in [p["text"] for p in rig.slack.posts]


async def test_start_failure_names_another_model(rig):
    rig.model.catalog = [row for row in rig.model.catalog if row["id"] != "o/gpt"]
    await rig.mention("ask gpt and gemini hello")
    assert "ChatGPT didn't answer this time. Try another model, for example gemini." in [
        p["text"] for p in rig.slack.posts]
    assert rig.models_asked() == ["g/model"]


async def test_alias_without_a_team_default_says_no_model_set(rig):
    rig.services.preferences = PreferenceService(rig.advisors)  # the team never saved a google default
    await rig.mention("ask gemini hello")
    assert [p["text"] for p in rig.slack.posts] == [NO_MODEL_SET]
    assert rig.models_asked() == []


async def test_concurrent_first_mentions_share_one_chat(rig, monkeypatch):
    """Two first mentions racing in one thread find or start the same chat; neither put replaces the other."""
    start = rig.services.advisors.start

    async def slow_start(*args, **kwargs):  # yield between get and put, as a real catalog call does
        for _ in range(5):
            await asyncio.sleep(0)
        return await start(*args, **kwargs)

    monkeypatch.setattr(rig.services.advisors, "start", slow_start)
    first, second = rig.event("<@UBOT> ask gemini one"), rig.event("<@UBOT> ask gemini two")
    await asyncio.gather(rig.dispatcher.on_mention("T1", first), rig.dispatcher.on_mention("T1", second))
    await rig.dispatcher.drain()
    one, two = rig.job_rows()
    assert one["chat_id"] == two["chat_id"]
    assert len(rig.store.list(rig.scope)) == 1 and len(rig.chat_rows()) == 1
    assert len(rig.deps.chat_locks) == 0  # dropped once idle
