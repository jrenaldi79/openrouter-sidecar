"""Slack request flow: event checks, gate, welcome, per-user limit, team key, identity and background jobs."""

import logging

import pytest
from slack_dispatch_rig import BOT, DispatchRig
from slack_runner_rig import CHANNEL, THREAD

from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.provisioning import DISABLED, NOT_SET_UP, TeamKey
from openrouter_image_mcp.hosted.slack import dispatch
from openrouter_image_mcp.hosted.slack import principal as slack_principal
from openrouter_image_mcp.hosted.slack.principal import team_principal
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.slack.welcome import NOT_SET_UP as CHANNEL_NOT_SET_UP


@pytest.fixture
def rig(db_target, keys):
    return DispatchRig(db_target, keys)


def _texts(rig):
    return [post["text"] for post in rig.slack.posts]


async def test_unregistered_channel_one_line(rig):
    event = await rig.mention("ask gemini hi", channel="C9", thread_ts=None)
    assert rig.slack.posts == [{"channel": "C9", "thread_ts": event["ts"], "text": CHANNEL_NOT_SET_UP,
                                "username": "Sidecar", "ts": rig.slack.posts[0]["ts"]}]
    assert rig.provisioner.calls == []


async def test_inactive_channel_silent(rig):
    rig.registry.deactivate("T1", CHANNEL, "bot removed")
    await rig.mention("ask gemini hi")
    assert rig.slack.posts == [] and rig.provisioner.calls == [] and rig.model.requests == []


async def test_guest_check_failure_silent(rig):
    rig.slack.users["U1"] = {"is_restricted": True}
    await rig.mention("ask gemini hi")
    assert "Sidecar · Gemini" not in [p["username"] for p in rig.slack.posts]
    assert rig.model.requests == [] and rig.registry.get("T1", CHANNEL).active is False


async def test_invoker_allowlist(rig):
    rig.registry.update("T1", CHANNEL, invoker_allowlist=("U2",))
    await rig.mention("help")
    assert _texts(rig) == ["Only some members of this channel can use Sidecar."]
    await rig.mention("help", user="U2")
    assert _texts(rig)[-1].startswith("Sidecar answers with models other than Claude.")


async def test_welcome_once_then_answer(db_target, keys):
    rig = DispatchRig(db_target, keys, welcomed=False)
    await rig.mention("ask gemini hi")
    welcome, _working, answer = rig.slack.posts
    assert welcome["text"].startswith("Hi, I'm Sidecar.") and welcome["thread_ts"] == THREAD
    assert "Your team has a monthly budget" in welcome["text"]
    assert answer["username"] == "Sidecar · Gemini" and answer["text"].startswith("The answer.")
    assert rig.registry.get("T1", CHANNEL).welcomed_at is not None
    await rig.mention("ask gemini again")
    assert sum(p["text"].startswith("Hi, I'm Sidecar.") for p in rig.slack.posts) == 1


async def test_user_rate_limit(rig):
    for _ in range(10):
        await rig.mention("help")
    await rig.mention("help")
    assert _texts(rig)[-1] == "You've sent 10 requests in 10 minutes. Try again in about 10 minutes."
    await rig.mention("help", user="U2")  # the window is per Slack user
    assert _texts(rig)[-1].startswith("Sidecar answers")


async def test_unknown_alias_lists_valid(rig):
    await rig.mention("ask claude is this right?")
    assert _texts(rig) == [("I don't know the model claude. Use one of: gemini, gpt, grok, open. "
                            "An exact OpenRouter model id also works.")]
    assert rig.model.requests == []


async def test_mention_mid_text_runs_command(rig):
    """Review Focus 1: the mention is found anywhere, entities decoded before parsing."""
    event = rig.event(f"hey <@{BOT}> ask gpt is &lt;x&gt; right?")
    await rig.dispatcher.on_mention("T1", event)
    await rig.dispatcher.drain()
    (request,) = rig.model.requests
    assert request["model"] == "o/gpt"
    assert rig.job_rows()[0]["prompt"] == "is <x> right?"


async def test_principal_from_event_not_text(rig):
    other = new_row("T1", "C2", "team-b", "2027", fusion_panel=("a/one",), fusion_analyst="d/analyst",
                    subagent_model="e/sub")
    rig.registry.add(other)
    await rig.mention("ask gemini as channel C2")
    ((principal, team),) = rig.provisioner.calls
    assert principal == team_principal("T1", CHANNEL, rig.registry.get("T1", CHANNEL))
    assert team == TeamKey("team-a")
    assert {p["channel"] for p in rig.slack.posts} == {CHANNEL}
    assert [row["slack_channel_id"] for row in rig.job_rows()] == [CHANNEL]


@pytest.mark.parametrize("change", [
    {"channel": ""}, {"channel": None}, {"user": ""}, {"user": 7}, {"ts": ""}, {"ts": None}, {"team": ""},
    {"user": BOT},
])
async def test_event_without_ids_ignored(rig, change):
    """Ruling 4: channel, user, ts and team must be non-empty strings; the bot never answers itself."""
    event = rig.event(f"<@{BOT}> help")
    team = change.pop("team", "T1")
    event.update(change)
    await rig.dispatcher.on_mention(team, event)
    assert rig.slack.posts == [] and rig.provisioner.calls == []


async def test_top_level_mention_replies_in_its_thread(rig):
    event = await rig.mention("ask gemini hi", thread_ts=None)
    assert {p["thread_ts"] for p in rig.slack.posts} == {event["ts"]}


@pytest.mark.parametrize(("error", "expected"), [
    (AuthRequiredError(DISABLED), ("This channel's Sidecar key was disabled by your instructor. "
                                   "Ask them to re-enable it.")),
    (AuthRequiredError(NOT_SET_UP), dispatch.KEY_PROBLEM),
    (RuntimeError("management API exploded sk-or-v1-x"), dispatch.KEY_PROBLEM),
])
async def test_key_problem_team_copy(rig, error, expected):
    """Ruling 6: team copy, never the student wording or the exception text."""
    rig.provisioner.error = error
    await rig.mention("ask gemini hi")
    assert _texts(rig) == [expected]
    assert "student" not in expected and "Your Sidecar" not in expected
    assert rig.model.requests == []


async def test_help_needs_no_key(rig):
    rig.provisioner.error = AuthRequiredError(DISABLED)
    await rig.mention("help")
    assert _texts(rig)[0].startswith("Sidecar answers") and rig.provisioner.calls == []


async def test_thread_map_failure_plain_copy(rig, monkeypatch):
    def broken(*args):
        raise RuntimeError("Could not link the thread to its chat; the mapping was not saved.")

    monkeypatch.setattr(rig.threads, "put", broken)
    await rig.mention("ask gemini hi")
    assert _texts(rig) == ["Something went wrong starting this answer. Ask again."]
    assert rig.model.requests == []


async def test_background_failure_logged_by_type(rig, monkeypatch, caplog):
    """Ruling 5: a job task's exception is logged by type, not swallowed or echoed."""
    seen = []

    async def failing(services, job):
        seen.append(slack_principal._current())
        raise ValueError("secret detail")

    monkeypatch.setattr(rig.runner, "run", failing)
    with caplog.at_level(logging.WARNING, logger=dispatch.__name__):
        await rig.mention("ask gemini hi")
    assert seen == [team_principal("T1", CHANNEL, rig.registry.get("T1", CHANNEL))]
    assert "ValueError" in caplog.text and "secret detail" not in caplog.text
    assert not rig.dispatcher._tasks
