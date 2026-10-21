"""Team usage report (`@sidecar usage`) and the once-a-month 80% budget warning.

Month figures come from OpenRouter's `GET /key` for the team key; today's spend and the per-person
breakdown come from the team's job rows. Nothing from the key info beyond two numbers reaches the text.
`usage_report` escapes the display names (they come from Slack), so `usage_text` is safe to post as is.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .context import NameCache, one_line
from .jobs import SlackJobs
from .registry import ChannelRegistry, ChannelRow
from .replies import escape

log = logging.getLogger(__name__)

WARN_AT = 0.8
MAX_USERS = 10
WARN_FETCH_S = 4.0  # a slow GET /key must not hold up the answer


@dataclass(frozen=True)
class UsageReport:
    month_spend: float
    budget_usd: float | None  # None: the key has no monthly limit
    remaining: float | None
    today_spend: float
    daily_cap: float
    by_user: list[tuple[str, float]]  # escaped display name and this month's spend, largest first
    others: int = 0  # people beyond the top `MAX_USERS`, not listed


def _number(value) -> float | None:
    """A finite real number, else None (booleans, strings and NaN are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _utc(now: datetime) -> datetime:
    return now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)


def month_key(now: datetime) -> str:
    """OpenRouter's monthly usage resets on UTC months."""
    return f"{_utc(now):%Y-%m}"


def budget_figures(key_info: dict) -> tuple[float, float | None, float | None]:
    """(month spend, monthly budget, remaining) from `GET /key`; odd or missing fields never raise."""
    info = key_info if isinstance(key_info, dict) else {}
    spent = _number(info.get("usage_monthly")) or 0.0
    budget = info.get("effective_budget")
    limit = _number(budget.get("limit_usd")) if isinstance(budget, dict) else None
    if limit is None or limit <= 0:
        return spent, None, None
    return spent, limit, max(0.0, limit - spent)


def budget_report(key_info: dict, daily_cap: float = 0.0) -> UsageReport:
    """Only the month figures: enough for the warning, with no job queries or Slack lookups."""
    spent, limit, remaining = budget_figures(key_info)
    return UsageReport(spent, limit, remaining, 0.0, daily_cap, [])


async def usage_report(key_info: dict, jobs: SlackJobs, scope, row: ChannelRow, names: NameCache,
                       now: datetime) -> UsageReport:
    """Month figures from the key, today's spend (the day boundary `Limits.admit` uses) and this month's
    spend per person from the team's job rows. Only the top `MAX_USERS` are named, and their names are
    one line and entity-escaped (`replies.escape`), so the text can't form a mention or a link."""
    now = _utc(now)
    spent, limit, remaining = budget_figures(key_info)
    today = jobs.spend_since(scope, now.date().isoformat())
    people = jobs.spend_by_user(scope, f"{month_key(now)}-01")
    top = people[:MAX_USERS]
    named = await asyncio.gather(*(names.name(user) for user, _ in top))
    by_user = [(escape(one_line(name) or user), amount) for name, (user, amount) in zip(named, top, strict=True)]
    return UsageReport(spent, limit, remaining, today, row.daily_cap_usd, by_user, len(people) - len(top))


def _money(value: float) -> str:
    return f"${value:.2f}"


def usage_text(r: UsageReport) -> str:
    if r.budget_usd is None:
        month = f"This month: {_money(r.month_spend)} used. This team has no monthly limit set."
    else:
        month = (f"This month: {_money(r.month_spend)} of {_money(r.budget_usd)} used, "
                 f"{_money(r.remaining or 0.0)} left.")
    today = (f"Today: {_money(r.today_spend)} of the {_money(r.daily_cap)} daily cap "
             "(resets at midnight UTC).")
    lines = [month, today]
    if not r.by_user:
        return "\n".join([*lines, "No requests yet this month (UTC)."])
    lines.append("Spend by person this month (UTC):")
    lines += [f"{name}: {_money(amount)}" for name, amount in r.by_user[:MAX_USERS]]
    if r.others:
        lines.append(f"...and {r.others} others")
    return "\n".join(lines)


def budget_warning(r: UsageReport, row: ChannelRow, month: str, registry: ChannelRegistry) -> str | None:
    """The one-line warning when the month's spend reached `WARN_AT` of the budget and this caller is the
    first to claim it for `month`; claiming comes last, so a below-threshold answer never uses it up."""
    if not r.budget_usd or r.month_spend / r.budget_usd < WARN_AT:
        return None
    if not registry.mark_warned(row.slack_team_id, row.channel_id, month):
        return None
    return f"This team has used {int(r.month_spend / r.budget_usd * 100)}% of this month's Sidecar budget."


async def answer_warning(key_info: Callable[[str], Awaitable[dict]], key: str, row: ChannelRow,
                         registry: ChannelRegistry | None, now: datetime | None = None) -> str | None:
    """The warning line for one answer's footer. Nothing is fetched when the row already shows this month's
    warning; the fetch is bounded by `WARN_FETCH_S` (a timeout means no warning). Any failure is logged by
    type: the answer still posts."""
    month = month_key(now or datetime.now(UTC))
    if registry is None or row.warned_month == month:
        return None
    try:
        report = budget_report(await asyncio.wait_for(key_info(key), WARN_FETCH_S))
        return budget_warning(report, row, month, registry)
    except Exception as error:  # noqa: BLE001 - a warning must never cost the student their answer
        log.warning("slack budget warning failed: %s", type(error).__name__)
        return None
