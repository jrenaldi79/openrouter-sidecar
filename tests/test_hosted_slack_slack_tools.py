import re

import pytest
from slack_fakes import FakeSlack
from slack_tools_kit import ERROR, ROOT, Spy, before_in, fill, msg, numbers, run, ts

from openrouter_image_mcp.hosted.slack.context import DATA_PREFACE, NameCache
from openrouter_image_mcp.hosted.slack.slack_tools import (
    CHARS_CAP,
    FUNCTION_NAMES,
    READ_LIMIT,
    TOOL_SPECS,
    ChannelTools,
)


@pytest.fixture
def slack():
    fake = FakeSlack()
    fake.users["U1"] = {"profile": {"display_name": "Ann"}}
    return fake


@pytest.fixture
def spy(slack):
    return Spy(slack)


@pytest.fixture
def tools(spy):
    return ChannelTools(spy, NameCache(spy), "C1", ROOT)


# --- isolation, specs ---------------------------------------------------------------------


async def test_forged_channel_ignored(tools, slack, spy):
    slack.history["C1"] = [msg(1, "mine")]
    slack.history["C_OTHER"] = [msg(2, "secret")]
    slack.threads[("C_OTHER", ROOT)] = [msg(3, "other thread")]
    slack.threads[("C1", ROOT)] = [msg(4, "my thread")]
    out, _ = await run(tools, "read_channel_history", channel="C_OTHER")
    assert "mine" in out and "secret" not in out
    out, _ = await run(tools, "read_thread", channel="C_OTHER", channel_id="C_OTHER")
    assert "my thread" in out and "other thread" not in out
    out, _ = await run(tools, "search_channel", query="secret", channel="C_OTHER")
    assert "secret" not in out
    assert {args[0] for args in spy.reads("history") + spy.reads("replies")} == {"C1"}


def test_specs_have_no_channel_param():
    assert {spec["name"] for spec in TOOL_SPECS} == FUNCTION_NAMES == {
        "read_thread", "read_channel_history", "search_channel"}
    for spec in TOOL_SPECS:
        assert spec["type"] == "function" and spec["description"]
        assert "strict" not in spec
        assert spec["parameters"]["additionalProperties"] is False
        assert not {"channel", "channel_id"} & set(spec["parameters"]["properties"])
    by_name = {spec["name"]: spec["parameters"] for spec in TOOL_SPECS}
    assert by_name["search_channel"]["required"] == ["query"]
    assert not by_name["read_thread"].get("required")
    assert set(by_name["read_thread"]["properties"]) == {"thread_ts", "before", "limit"}
    assert set(by_name["read_channel_history"]["properties"]) == {"before", "limit"}
    assert set(by_name["search_channel"]["properties"]) == {"query", "limit"}


# --- channel history ----------------------------------------------------------------------


async def test_history_defaults_to_newest_fifty_in_order(tools, slack):
    fill(slack, 80)
    out, label = await run(tools, "read_channel_history")
    assert out.startswith(DATA_PREFACE + "\n")
    assert numbers(out) == list(range(31, 81))
    assert label == "Read 50 earlier messages"


async def test_history_caps(tools, slack):
    fill(slack, 500, text="x" * 200)
    out, _ = await run(tools, "read_channel_history", limit=1000)
    shown = len(re.findall(r"Ann: x", out))
    assert 0 < shown < READ_LIMIT  # the 30,000 characters run out before 200 messages
    assert len(out) <= CHARS_CAP + 1_000
    fill(slack, 500, text="x")
    out, _ = await run(tools, "read_channel_history", limit=1000)
    assert len(re.findall(r"Ann: x", out)) == READ_LIMIT


async def test_history_footer_gives_before_to_continue(tools, slack):
    fill(slack, 100)
    out, _ = await run(tools, "read_channel_history", limit=10)
    assert numbers(out) == list(range(91, 101))
    assert "earlier message" in out.splitlines()[-1]
    assert before_in(out) == ts(91)
    out, _ = await run(tools, "read_channel_history", limit=10, before=ts(91))
    assert numbers(out) == list(range(81, 91))
    assert before_in(out) == ts(81)


async def test_history_footer_after_char_cap(tools, slack):
    fill(slack, 100, text="y" * 1_000)
    out, _ = await run(tools, "read_channel_history", limit=200)
    shown = len(re.findall(r"Ann: y", out))
    assert 0 < shown < 100
    assert f"{100 - shown} earlier messages not shown" in out
    out2, _ = await run(tools, "read_channel_history", before=before_in(out), limit=1)
    assert len(re.findall(r"Ann: y", out2)) == 1


async def test_history_without_more_has_no_footer(tools, slack):
    fill(slack, 3)
    out, _ = await run(tools, "read_channel_history")
    assert "before=" not in out


async def test_history_drops_joins_and_thread_replies_and_names_bots(tools, slack):
    slack.history["C1"] = [
        msg(1, "joined", subtype="channel_join"),
        msg(2, "a reply", thread_ts=ts(1)),
        msg(3, "top level"),
        msg(4, "from claude", user=None, label="CLAUDE"),
        msg(5, "from a thread root", thread_ts=ts(5)),
    ]
    out, _ = await run(tools, "read_channel_history")
    assert "joined" not in out and "a reply" not in out
    assert "Ann: top level" in out and "Claude: from claude" in out and "from a thread root" in out


async def test_history_empty_channel(tools):
    out, label = await run(tools, "read_channel_history")
    assert out == f"{DATA_PREFACE}\nNo messages found."
    assert label


# --- thread -------------------------------------------------------------------------------


async def test_read_thread_defaults_current(tools, slack):
    slack.threads[("C1", ROOT)] = [msg(1, "root", thread_ts=ROOT), msg(2, "reply", thread_ts=ROOT)]
    slack.threads[("C1", ts(9))] = [msg(9, "another thread", thread_ts=ts(9))]
    out, label = await run(tools, "read_thread")
    assert "Ann: root" in out and "Ann: reply" in out and "another thread" not in out
    assert label == "Read the thread"
    out, _ = await run(tools, "read_thread", thread_ts=ts(9))
    assert "another thread" in out and "Ann: root" not in out


async def test_read_thread_before_pages_into_the_middle(tools, slack):
    slack.threads[("C1", ROOT)] = [msg(n, thread_ts=ROOT) for n in range(1, 61)]
    out, _ = await run(tools, "read_thread", limit=10)
    assert numbers(out) == list(range(51, 61))
    assert before_in(out) == ts(51)
    assert "50 earlier messages not shown" in out
    out, _ = await run(tools, "read_thread", limit=10, before=ts(51))
    assert numbers(out) == list(range(41, 51))
    out, _ = await run(tools, "read_thread", limit=100, before=ts(5))
    assert numbers(out) == [1, 2, 3, 4] and "before=" not in out


async def test_read_thread_footer_names_the_thread_to_continue(tools, slack):
    slack.threads[("C1", ts(9))] = [msg(n, thread_ts=ts(9)) for n in range(9, 30)]
    out, _ = await run(tools, "read_thread", thread_ts=ts(9), limit=5)
    assert f'thread_ts="{ts(9)}"' in out and before_in(out) == ts(25)


async def test_read_thread_drops_joins_and_reads_every_page(tools, slack):
    slack.threads[("C1", ROOT)] = (
        [msg(n, thread_ts=ROOT) for n in range(1, 451)] + [msg(451, "joined", subtype="channel_join")])
    out, _ = await run(tools, "read_thread", limit=3)
    assert numbers(out) == [448, 449, 450] and "joined" not in out


# --- errors -------------------------------------------------------------------------------


async def test_tool_error_is_output(tools, slack):
    slack.fail_next("history", "ratelimited")
    out, label = await run(tools, "read_channel_history")
    assert (out, label) == ("Error: Slack said ratelimited", ERROR)


async def test_thread_and_search_errors_are_output(tools, slack):
    slack.fail_next("replies", "thread_not_found")
    assert (await run(tools, "read_thread"))[0] == "Error: Slack said thread_not_found"
    slack.fail_next("history", "channel_not_found")
    assert (await run(tools, "search_channel", query="x"))[0] == "Error: Slack said channel_not_found"


async def test_other_exceptions_give_only_the_type_name(tools, spy, caplog):
    spy.boom = RuntimeError("secret detail xoxb-123")
    out, label = await run(tools, "read_channel_history")
    assert (out, label) == ("Error: RuntimeError", ERROR)
    assert caplog.records  # logged, for the redacting handler
