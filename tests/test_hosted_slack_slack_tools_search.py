import json
import re

import pytest
from slack_fakes import FakeSlack
from slack_tools_kit import ERROR, ROOT, Spy, before_in, fill, msg, numbers, run, ts

from openrouter_image_mcp.hosted.slack.context import NameCache
from openrouter_image_mcp.hosted.slack.slack_tools import (
    SEARCH_MESSAGES,
    SEARCH_PAGES,
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


# --- search -------------------------------------------------------------------------------


async def test_search_pages_and_filters(tools, slack, spy):
    slack.history["C1"] = [msg(n, "Budget Review" if n % 100 == 0 else f"chatter {n}") for n in range(1, 1401)]
    out, label = await run(tools, "search_channel", query="review BUDGET", limit=100)
    assert len(spy.reads("history")) == SEARCH_PAGES
    assert len(re.findall(r"Ann: Budget Review", out)) == 10  # 500..1400; 100..400 are past the 1,000 cap
    assert f"Searched {SEARCH_MESSAGES} messages" in out
    assert label == "Searched the channel for 'review BUDGET'"


async def test_search_never_reads_beyond_the_message_cap(tools, slack, spy):
    slack.history["C1"] = [msg(n, "needle" if n == 1 else "hay") for n in range(1, 1401)]
    out, _ = await run(tools, "search_channel", query="needle")
    assert "No messages found." in out
    assert f"Searched {SEARCH_MESSAGES} messages" in out
    assert before_in(out) == ts(401)  # how to read further back
    assert len(spy.reads("history")) == SEARCH_PAGES


async def test_search_requires_every_term_and_decodes_entities(tools, slack):
    slack.history["C1"] = [
        msg(1, "Tom &amp; Jerry"), msg(2, "tom only"), msg(3, "a &lt;b&gt; tag"), msg(4, "jerry only")]
    out, _ = await run(tools, "search_channel", query="TOM   jerry")
    assert "Ann: Tom & Jerry" in out
    assert "tom only" not in out and "jerry only" not in out
    out, _ = await run(tools, "search_channel", query="<b>")
    assert "a <b> tag" in out
    out, _ = await run(tools, "search_channel", query="&amp;")
    assert "No messages found." in out


async def test_search_limit_and_footer(tools, slack):
    slack.history["C1"] = [msg(n, f"match {n}") for n in range(1, 31)]
    out, _ = await run(tools, "search_channel", query="match", limit=5)
    assert re.findall(r"Ann: match (\d+)", out) == ["26", "27", "28", "29", "30"]
    assert "25 more matches not shown" in out
    assert "Searched 30 messages" in out


async def test_search_excludes_replies_and_joins(tools, slack):
    slack.history["C1"] = [msg(1, "needle reply", thread_ts=ts(0)), msg(2, "needle", subtype="channel_join"),
                           msg(3, "needle top")]
    out, _ = await run(tools, "search_channel", query="needle")
    assert "needle top" in out and "needle reply" not in out
    assert "Searched 1 message" in out


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "   "}, {"query": 5}, {"query": None}])
async def test_search_empty_query_is_an_error(tools, spy, args):
    out, label = await run(tools, "search_channel", **args)
    assert out.startswith("Error:") and label == ERROR
    assert spy.calls == []


async def test_search_label_clips_the_query(tools):
    _, label = await run(tools, "search_channel", query="a" * 100)
    assert label.startswith("Searched the channel for '") and len(label) <= len("Searched the channel for ''") + 40
    _, label = await run(tools, "search_channel", query="line\nbreak")
    assert "\n" not in label


# --- arguments ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool,field", [("read_thread", "thread_ts"), ("read_thread", "before"),
                                        ("read_channel_history", "before")])
@pytest.mark.parametrize("bad", ["abc", "1700000000", "1700000000.", "1700000000.000001\n", "1.2.3",
                                 "1234567890123.1", "1.123456789", "1700000000.000001 ", "", 17.5, ["1.1"], {"a": 1}])
async def test_bad_timestamp(tools, spy, tool, field, bad):
    out, label = await tools.call(tool, json.dumps({field: bad}))
    assert (out, label) == ("Error: bad timestamp", ERROR)
    assert spy.calls == []


async def test_null_timestamp_is_absent(tools, slack):
    fill(slack, 2)
    out, _ = await tools.call("read_channel_history", '{"before": null}')
    assert numbers(out) == [1, 2]


@pytest.mark.parametrize("raw", ["[]", '"x"', "5", "null", "{bad", "true"])
async def test_non_object_arguments(tools, raw):
    assert await tools.call("read_channel_history", raw) == ("Error: bad arguments", ERROR)


async def test_blank_arguments_mean_no_arguments(tools, slack):
    fill(slack, 2)
    out, _ = await tools.call("read_channel_history", "")
    assert numbers(out) == [1, 2]


async def test_unknown_tool_is_an_error_output(tools):
    out, label = await tools.call("delete_everything", "{}")
    assert out.startswith("Error:") and label == ERROR


@pytest.mark.parametrize("limit,expected", [(0, 1), (-5, 1), (7, 7), (200, 200), (9999, 200),
                                            ("5", 50), (2.5, 50), (True, 50), (None, 50), ([3], 50)])
async def test_limit_is_clamped_or_defaulted(tools, slack, limit, expected):
    fill(slack, 300)
    out, _ = await run(tools, "read_channel_history", limit=limit)
    assert len(numbers(out)) == expected


async def test_search_default_limit_is_twenty(tools, slack):
    fill(slack, 60, text="same")
    out, _ = await run(tools, "search_channel", query="same", limit="lots")
    assert len(re.findall(r"Ann: same", out)) == 20
