import pytest
from slack_fakes import FakeSlack

from openrouter_image_mcp.hosted.slack.guests import GuestGuard, channel_problem
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row
from openrouter_image_mcp.hosted.slack.welcome import GUEST_OFF

SHARED = "shared outside the organization"
GUEST = "has a guest member"


def make_row(channel="C1"):
    return new_row("T1", channel, "team-a", "2027", fusion_panel=("a/one",),
                   fusion_analyst="a/one", subagent_model="a/one")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def slack():
    fake = FakeSlack()
    fake.channels["C1"] = {"id": "C1"}
    fake.members["C1"] = ["U1", "U2"]
    fake.users.update({"U1": {"id": "U1"}, "U2": {"id": "U2", "is_restricted": False},
                       "UG": {"id": "UG", "is_restricted": True},
                       "UU": {"id": "UU", "is_ultra_restricted": True},
                       "UB": {"id": "UB", "is_bot": True}})
    return fake


@pytest.fixture
def registry(db_target):
    reg = ChannelRegistry(db_target)
    reg.add(make_row())
    return reg


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def guard(registry, slack, clock):
    return GuestGuard(registry, slack, clock=clock)


async def test_ext_shared_refused(slack):
    slack.channels["C1"]["is_ext_shared"] = True
    slack.members["C1"] = ["UG"]
    assert await channel_problem(slack, "C1") == SHARED


async def test_pending_ext_shared_refused(slack):
    slack.channels["C1"]["is_pending_ext_shared"] = True
    assert await channel_problem(slack, "C1") == SHARED


@pytest.mark.parametrize("guest", ["UG", "UU"])
async def test_guest_member_refused(slack, guest):
    slack.members["C1"] = ["U1", guest]
    assert await channel_problem(slack, "C1") == GUEST


async def test_clean_channel_ok(slack):
    slack.members["C1"] = ["U1", "U2", "UB"]
    assert await channel_problem(slack, "C1") is None


async def test_shared_channel_skips_member_scan(slack):
    slack.channels["C1"]["is_ext_shared"] = True
    slack.fail_next("members", "boom")
    assert await channel_problem(slack, "C1") == SHARED


async def test_guest_join_deactivates_and_posts_once(guard, registry, slack):
    await guard.on_member_joined("T1", "C1", "UG")
    await guard.on_member_joined("T1", "C1", "UU")
    row = registry.get("T1", "C1")
    assert not row.active and row.inactive_reason == "guest joined"
    assert [(p["channel"], p["thread_ts"], p["text"]) for p in slack.posts] == [("C1", None, GUEST_OFF)]


async def test_member_join_non_guest_noop(guard, registry, slack):
    await guard.on_member_joined("T1", "C1", "U1")
    assert registry.get("T1", "C1").active and slack.posts == []


async def test_failed_join_lookup_then_failing_check_denies(guard, registry, slack, clock):
    row = registry.get("T1", "C1")
    assert await guard.allowed(row)  # recent success
    slack.fail_next("user", "ratelimited")
    await guard.on_member_joined("T1", "C1", "UG")
    assert registry.get("T1", "C1").active and slack.posts == []
    slack.fail_next("channel_info", "ratelimited")
    assert not await guard.allowed(row)  # the cache and the success window are both ignored


async def test_failed_join_lookup_then_clean_check_clears_mark(guard, registry, slack):
    row = registry.get("T1", "C1")
    assert await guard.allowed(row)
    slack.fail_next("user", "ratelimited")
    await guard.on_member_joined("T1", "C1", "UG")
    assert await guard.allowed(row)  # clean check runs despite the cache
    slack.fail_next("channel_info", "ratelimited")
    assert await guard.allowed(row)  # cached again: no Slack call, so the queued failure is unused
    assert slack._failures["channel_info"]


async def test_failed_join_lookup_then_guest_found_deactivates(guard, registry, slack):
    slack.fail_next("user", "ratelimited")
    await guard.on_member_joined("T1", "C1", "UG")
    slack.members["C1"] = ["UG"]
    assert not await guard.allowed(registry.get("T1", "C1"))
    assert registry.get("T1", "C1").inactive_reason == GUEST


async def test_channel_shared_deactivates(guard, registry, slack):
    await guard.on_channel_shared("T1", "C1")
    await guard.on_channel_shared("T1", "C1")
    row = registry.get("T1", "C1")
    assert not row.active and row.inactive_reason == SHARED
    assert [p["text"] for p in slack.posts] == [GUEST_OFF]


async def test_events_for_unregistered_channels_ignored(guard, slack):
    await guard.on_channel_shared("T1", "C9")
    await guard.on_member_joined("T1", "C9", "UG")
    await guard.on_member_joined("T2", "C1", "UG")
    assert slack.posts == []


async def test_post_failure_still_deactivates(guard, registry, slack):
    slack.fail_next("post", "channel_not_found")
    await guard.on_channel_shared("T1", "C1")
    assert not registry.get("T1", "C1").active


async def test_hourly_backstop_cached(guard, registry, slack, clock):
    calls = []
    original = slack.members._call

    async def counting(channel):
        calls.append(channel)
        return await original(channel)

    slack.members._call = counting
    row = registry.get("T1", "C1")
    assert await guard.allowed(row) and await guard.allowed(row)
    assert len(calls) == 1
    clock.now += 3599
    assert await guard.allowed(row)
    assert len(calls) == 1
    clock.now += 2
    assert await guard.allowed(row)
    assert len(calls) == 2


async def test_backstop_finds_guest_and_deactivates_once(guard, registry, slack, clock):
    row = registry.get("T1", "C1")
    assert await guard.allowed(row)
    slack.members["C1"] = ["U1", "UG"]
    clock.now += 3601
    assert not await guard.allowed(row)
    assert not await guard.allowed(registry.get("T1", "C1"))
    row = registry.get("T1", "C1")
    assert row.inactive_reason == GUEST and [p["text"] for p in slack.posts] == [GUEST_OFF]


async def test_inactive_row_makes_no_slack_calls(guard, registry, slack):
    registry.deactivate("T1", "C1", "manual")
    slack.fail_next("channel_info", "boom")
    assert not await guard.allowed(registry.get("T1", "C1"))
    assert slack._failures["channel_info"]  # never consumed


async def test_failure_without_prior_success_denies(guard, registry, slack):
    slack.fail_next("channel_info", "ratelimited")
    assert not await guard.allowed(registry.get("T1", "C1"))


async def test_failure_fails_open_only_within_two_intervals(guard, registry, slack, clock, caplog):
    row = registry.get("T1", "C1")
    assert await guard.allowed(row)  # success at t0
    clock.now += 3601
    slack.fail_next("channel_info", "ratelimited")
    assert await guard.allowed(row)  # one interval past the success: still open
    assert "ratelimited" in caplog.text
    clock.now += 3600  # 7201 s after the last success; the failure did not refresh it
    slack.fail_next("channel_info", "ratelimited")
    assert not await guard.allowed(row)


async def test_failure_is_retried_on_the_next_call(guard, registry, slack, clock):
    row = registry.get("T1", "C1")
    assert await guard.allowed(row)
    clock.now += 3601
    slack.fail_next("channel_info", "ratelimited")
    assert await guard.allowed(row)
    slack.members["C1"] = ["UG"]
    assert not await guard.allowed(row)  # no success was recorded, so this call re-checked


async def test_non_slack_exception_follows_the_same_rule(guard, registry, slack, clock):
    row = registry.get("T1", "C1")
    slack.members["C1"] = ["U2"]
    assert await guard.allowed(row)
    clock.now += 3601

    async def broken(channel):
        raise KeyError("members")

    slack.members._call = broken
    assert await guard.allowed(row)
    clock.now += 3600
    assert not await guard.allowed(row)


async def test_non_slack_exception_without_success_denies(guard, registry, slack):
    async def broken(channel):
        raise KeyError("members")

    slack.members._call = broken
    assert not await guard.allowed(registry.get("T1", "C1"))


async def test_stale_active_row_after_deactivation_is_denied(guard, registry, slack):
    stale = registry.get("T1", "C1")
    registry.deactivate("T1", "C1", "manual")
    slack.fail_next("channel_info", "boom")
    assert not await guard.allowed(stale)
    assert slack._failures["channel_info"]  # no Slack call


async def test_row_removed_from_registry_is_denied(guard, slack):
    assert not await guard.allowed(make_row("C9"))
