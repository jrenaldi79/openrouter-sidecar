"""Instructor view of one Slack team's spend: what `@sidecar usage` posts in the channel.

    uv run python scripts/ops/slack_usage.py --team abbott

Reads DATABASE_URL and VAULT_KEYS from the environment (the team's key is read to ask OpenRouter for this
month's usage) and, to show names instead of Slack ids, SLACK_BOT_TOKEN when it is set. Prints no tokens or
keys; the report itself never contains one.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

from slack_common import Refusal, keys_store, run_script, slack_api, target

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.hosted.openrouter_keys import current_key
from openrouter_image_mcp.hosted.slack.api import SlackCallError
from openrouter_image_mcp.hosted.slack.context import NameCache
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs
from openrouter_image_mcp.hosted.slack.principal import team_principal
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry
from openrouter_image_mcp.hosted.slack.usage import usage_report, usage_text


class NoSlack:
    """Stands in for the Slack API when there is no bot token: every name falls back to its id."""

    async def user(self, user_id: str) -> dict:
        raise SlackCallError("user", "no_token")


@asynccontextmanager
async def _names():
    if os.environ.get("SLACK_BOT_TOKEN", "").strip():
        async with slack_api() as api:
            yield NameCache(api)
    else:
        yield NameCache(NoSlack())


async def run(args) -> int:
    db_target = target()
    row = ChannelRegistry(db_target).by_label(args.team)
    if row is None:
        raise Refusal(f"No channel is registered as '{args.team}'.")
    # An inactive channel still has spend to report, so build the principal as if it were active.
    principal = team_principal(row.slack_team_id, row.channel_id, replace(row, active=True))
    key = keys_store(db_target).key_for(principal)
    if key is None:
        raise Refusal("That team has no key yet; it is created the first time someone mentions Sidecar.")
    info = await current_key(key)
    async with _names() as names:
        report = await usage_report(info, SlackJobs(db_target), Scope(principal.key, principal.workspace_id),
                                    row, names, datetime.now(UTC))
    print(usage_text(report))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show one Slack team's spend this month and today.")
    parser.add_argument("--team", required=True, metavar="label", help="the team label from slack_channels.py")
    return parser


if __name__ == "__main__":
    sys.exit(run_script(lambda: run(_parser().parse_args())))
