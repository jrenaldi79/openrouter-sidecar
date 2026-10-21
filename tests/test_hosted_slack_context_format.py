from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from slack_fakes import FakeSlack

from openrouter_image_mcp.hosted.slack.api import SlackMessage
from openrouter_image_mcp.hosted.slack.context import NameCache, format_message

CHI = ZoneInfo("America/Chicago")


def ts_at(month, day, hour, minute, seq=0):
    stamp = datetime(2026, month, day, hour, minute, tzinfo=CHI).timestamp()
    return f"{int(stamp)}.{seq:06d}"


def msg(ts, text, user="U1", *, label=None):
    return SlackMessage(ts, user, text, label, None, None, ())


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def slack():
    fake = FakeSlack()
    fake.users["U1"] = {"profile": {"display_name": "Ann", "real_name": "Ann Lee"}}
    fake.users["U2"] = {"profile": {"display_name": "", "real_name": "Bo Chen"}}
    fake.users["U3"] = {"profile": {}}
    return fake


def test_format_message_shape():
    stamp = ts_at(10, 20, 10, 14)
    assert format_message(msg(stamp, "hello"), "Ann") == "[Oct 20 10:14] Ann: hello"


def test_format_message_unpadded_day_and_zero_padded_time():
    assert format_message(msg(ts_at(10, 5, 9, 3), "x"), "Ann") == "[Oct 5 09:03] Ann: x"


def test_format_message_uses_label_for_bots():
    stamp = ts_at(10, 20, 10, 14)
    assert format_message(msg(stamp, "x", label="Sidecar · Gemini"), "ignored").endswith("Sidecar · Gemini: x")
    assert format_message(msg(stamp, "x", label="CLAUDE"), "ignored").endswith("] Claude: x")


def test_format_message_bad_timestamp_does_not_raise():
    assert format_message(msg("nonsense", "x"), "Ann") == "[?] Ann: x"


async def test_name_cache_display_then_real_then_id(slack):
    names = NameCache(slack)
    assert await names.name("U1") == "Ann"
    assert await names.name("U2") == "Bo Chen"
    assert await names.name("U3") == "U3"
    assert await names.name("U9") == "U9"  # unknown user: falls back, no raise


async def test_name_cache_caches_success_until_ttl(slack):
    clock = Clock()
    names = NameCache(slack, ttl_s=100.0, clock=clock)
    assert await names.name("U1") == "Ann"
    slack.users["U1"] = {"profile": {"display_name": "Renamed"}}
    clock.now = 50.0
    assert await names.name("U1") == "Ann"
    clock.now = 101.0
    assert await names.name("U1") == "Renamed"


async def test_name_cache_failure_is_retried_soon(slack):
    clock = Clock()
    names = NameCache(slack, ttl_s=3600.0, clock=clock)
    slack.fail_next("user", "ratelimited")
    assert await names.name("U1") == "U1"
    assert await names.name("U1") == "U1"  # inside the short failure window: no new call
    clock.now = 120.0
    assert await names.name("U1") == "Ann"
