"""Team usage report and the once-a-month budget warning (hosted/slack/usage.py)."""

from datetime import UTC, datetime

import pytest
from slack_fakes import FakeSlack

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.slack.context import NameCache
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row
from openrouter_image_mcp.hosted.slack.usage import (
    WARN_AT,
    UsageReport,
    budget_warning,
    month_key,
    usage_report,
    usage_text,
)

SCOPE, OTHER_TEAM, STUDENT = Scope("slack|T1:C1", "ws"), Scope("slack|T1:C2", "ws"), Scope("student|x", "ws")
NOW = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
KEY, HASH = "sk-or-v1-alice", "hash-9f8e7d"  # obviously fake


def info(spent=10.0, limit=75.0, **extra):
    budget = None if limit is None else {"limit_usd": limit, "spend_usd": spent, "reset_interval": "monthly"}
    return {"usage_monthly": spent, "effective_budget": budget, "hash": HASH, "label": KEY, **extra}


@pytest.fixture
def world(db_target):
    store = AdvisorStore(db_target)
    registry = ChannelRegistry(db_target)
    row = new_row("T1", "C1", "team-a", "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                  subagent_model="e/s")
    registry.add(row)
    slack = FakeSlack()
    slack.users = {"U1": {"profile": {"display_name": "Ana"}},
                   "U2": {"profile": {"display_name": "Bo\nBell\x07"}}}
    return store, AdvisorResults(store), SlackJobs(db_target), registry, row, NameCache(slack)


def add_job(world, scope, user, cost, created="2026-10-21T01:00:00+00:00"):
    store, results, *_ = world
    chat = store.create(scope, "m", "t")
    job = results.create(scope, chat, "p", "proc", slack_user_id=user, slack_channel_id="C1")
    results.update(scope, job["id"], "proc", status="completed", cost_usd=cost)
    with results.connection() as db:
        db.execute("UPDATE advisor_jobs SET created_at=? WHERE id=?", (created, job["id"]))


async def report(world, key_info, now=NOW):
    _, _, jobs, _, row, names = world
    return await usage_report(key_info, jobs, SCOPE, row, names, now)


async def test_usage_text_has_no_key(world):
    text = usage_text(await report(world, info()))
    assert KEY not in text and HASH not in text
    assert "student" not in text.lower()
    assert "$10.00 of $75.00" in text and "$65.00 left" in text


async def test_breakdown_by_user_names(world):
    add_job(world, SCOPE, "U1", 0.25)
    add_job(world, SCOPE, "U1", 0.5)
    add_job(world, SCOPE, "U2", 2.0)
    add_job(world, SCOPE, "U3", 0.1, created="2026-10-02T00:00:00+00:00")  # earlier this month
    add_job(world, SCOPE, "U1", 5.0, created="2026-09-30T23:59:00+00:00")  # last month
    r = await report(world, info())
    assert r.by_user == [("Bo Bell", 2.0), ("Ana", 0.75), ("U3", 0.1)]
    text = usage_text(r)
    assert "this month (UTC)" in text and "Bo Bell: $2.00" in text and "\x07" not in text


async def test_many_users_show_ten_then_others(world):
    for n in range(13):
        add_job(world, SCOPE, f"U{n + 10}", 1.0 + n)
    text = usage_text(await report(world, info()))
    shown = text.partition("...and 3 others")[0]
    assert "...and 3 others" in text
    assert shown.count(": $") == 10 + 2  # ten people, the month line and the today line


async def test_today_vs_cap(world):
    add_job(world, SCOPE, "U1", 0.4, created="2026-10-21T00:00:00+00:00")
    add_job(world, SCOPE, "U1", 0.3, created="2026-10-20T23:59:59+00:00")
    r = await report(world, info(), now=NOW)
    assert (r.today_spend, r.daily_cap) == (pytest.approx(0.4), 4.0)
    assert "Today: $0.40 of the $4.00 daily cap" in usage_text(r)


async def test_usage_scoped_to_team(world):
    add_job(world, SCOPE, "U1", 1.0)
    add_job(world, OTHER_TEAM, "U9", 50.0)
    add_job(world, STUDENT, "U8", 20.0)
    r = await report(world, info())
    assert r.by_user == [("Ana", 1.0)] and r.today_spend == pytest.approx(1.0)


ODD_INFO = [info(limit=None), {}, {"usage_monthly": "lots", "effective_budget": "x"},
            {"usage_monthly": 5, "effective_budget": {"limit_usd": "n/a"}},
            {"usage_monthly": 5, "effective_budget": {"limit_usd": 0}},
            {"usage_monthly": float("nan"), "effective_budget": {"limit_usd": True}}]


@pytest.mark.parametrize("odd", ODD_INFO)
async def test_no_limit_has_no_budget_and_no_warning(world, odd):
    r = await report(world, odd)
    assert r.budget_usd is None and r.remaining is None
    assert "no monthly limit" in usage_text(r)
    assert budget_warning(r, world[4], "2026-10", world[3]) is None


async def test_non_numeric_month_spend_counts_as_zero(world):
    r = await report(world, {"usage_monthly": None, "effective_budget": {"limit_usd": 75}})
    assert (r.month_spend, r.remaining) == (0.0, 75.0)


def test_month_key_is_utc():
    assert month_key(datetime(2026, 10, 31, 23, 59, tzinfo=UTC)) == "2026-10"
    assert month_key(datetime(2026, 11, 1, 0, 0, tzinfo=UTC)) == "2026-11"


def figures(spent, budget=100.0):
    return UsageReport(spent, budget, max(0.0, budget - spent), 0.0, 4.0, [])


def test_warning_once_per_month(world):
    registry, row = world[3], world[4]
    assert WARN_AT == 0.8
    assert budget_warning(figures(79.99), row, "2026-10", registry) is None
    first = budget_warning(figures(87.9), row, "2026-10", registry)
    assert first == "This team has used 87% of this month's Sidecar budget."
    assert budget_warning(figures(90.0), row, "2026-10", registry) is None
    assert registry.get("T1", "C1").warned_month == "2026-10"
    assert budget_warning(figures(80.0), row, "2026-11", registry) == (
        "This team has used 80% of this month's Sidecar budget.")


def test_warning_text_over_budget_floors_percent(world):
    registry, row = world[3], world[4]
    assert "104%" in budget_warning(figures(104.9), row, "2026-10", registry)


async def test_names_are_escaped_and_only_the_top_ten_are_resolved(world):
    api = world[5]._api
    resolved, original = [], api.user

    async def spy(user_id):
        resolved.append(user_id)
        return await original(user_id)

    api.user = spy
    api.users["U99"] = {"profile": {"display_name": "<!channel> & <@U1>"}}
    add_job(world, SCOPE, "U99", 99.0)
    for n in range(12):
        add_job(world, SCOPE, f"U{n + 10}", 1.0 + n)
    r = await report(world, info())
    assert r.by_user[0] == ("&lt;!channel&gt; &amp; &lt;@U1&gt;", 99.0)
    assert len(r.by_user) == 10 and r.others == 3 and len(resolved) == 10
    text = usage_text(r)
    assert "<" not in text and ">" not in text and "...and 3 others" in text
    assert text.count("\n") == 3 + 10  # month, today, heading, ten people, others
