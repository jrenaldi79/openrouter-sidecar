"""Instructor tool for the Slack channels Sidecar answers in (one registry row, one team key per label).

Reads DATABASE_URL from the environment, plus SLACK_BOT_TOKEN for `add` and `update --active` (the guest and
sharing check), and VAULT_KEYS and OPENROUTER_MANAGEMENT_KEY when `--family-default` is given (the team key
is created if needed so the catalog check can run). Prints no tokens or keys.

    uv run python scripts/ops/slack_channels.py add --team-id T0 --channel C0 --label abbott --cohort 2027 \\
        --fusion-panel a/one,b/two,c/three --fusion-analyst a/one --subagent-model c/three \\
        [--default-model gemini] [--family-default openai=a/one ...] \\
        [--alias k=Brand ...] [--fetch-allow example.com ...] [--daily-cap 4] [--max-concurrent 3] \\
        [--invoker U0 ...]
    uv run python scripts/ops/slack_channels.py update <label> [the same options] [--active]
    uv run python scripts/ops/slack_channels.py deactivate <label> --reason "..."
    uv run python scripts/ops/slack_channels.py list

When `add` is given no `--daily-cap` or `--max-concurrent`, it uses SIDECAR_SLACK_DAILY_CAP_USD and
SIDECAR_SLACK_MAX_CONCURRENT from the environment the script runs in (4 and 3 when unset). `update` only
changes the options it is given.

`--default-model` is the channel's default alias, the one Sidecar uses when a mention names none; it must be
one of the channel's aliases. `--family-default family=model-id` sets the team's model for a family (what an
alias like `gemini` resolves to), checked against the OpenRouter catalog. `--alias` adds or replaces aliases
and cannot remove one (an alias maps to a brand such as ChatGPT or to an exact model id).

In `update`, put the label first, before the multi-value flags (`--alias`, `--family-default`,
`--fetch-allow`, `--invoker` take one or more values and would swallow it).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import replace

from slack_common import (
    Refusal,
    model_seeder,
    parse_family_defaults,
    parse_pairs,
    run_script,
    slack_api,
    target,
)

from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.errors import AuthRequiredError, BadRequestError
from openrouter_image_mcp.hosted.slack.guests import channel_problem
from openrouter_image_mcp.hosted.slack.registry import (
    DEFAULT_ALIASES,
    ChannelRegistry,
    new_row,
)
from openrouter_image_mcp.hosted.slack.settings import registry_defaults
from openrouter_image_mcp.preferences import ALIASES

ALIAS_NAME = re.compile(r"^[a-z0-9]{1,20}$")
MODEL_REFUSED = ("The OpenRouter catalog can't confirm one of those model ids (or it is in the wrong family). "
                 "Nothing was saved.")


def _aliases(given: list[str], base: dict[str, str]) -> dict[str, str]:
    found = parse_pairs(given, "--alias")
    for name, brand in found.items():
        if not ALIAS_NAME.match(name):
            raise Refusal(f"Alias '{name}' must be 1 to 20 lowercase letters or digits.")
        if brand.casefold() not in ALIASES and "/" not in brand:
            raise Refusal(f"Alias '{name}' maps to '{brand}', which is not a known brand or a model id.")
    return {**base, **found}


def _options(args, base_aliases: dict[str, str]) -> dict:
    """The registry columns the operator gave (for `update`, only those)."""
    given = {"fusion_panel": tuple(p.strip() for p in args.fusion_panel.split(",") if p.strip())
             if args.fusion_panel else None,
             "fusion_analyst": args.fusion_analyst, "subagent_model": args.subagent_model,
             "default_model": args.default_model, "daily_cap_usd": args.daily_cap,
             "max_concurrent": args.max_concurrent,
             "fetch_allowlist": tuple(args.fetch_allow) if args.fetch_allow else None,
             "invoker_allowlist": tuple(args.invoker) if args.invoker else None}
    options = {name: value for name, value in given.items() if value is not None}
    if args.alias:
        options["alias_map"] = _aliases(args.alias, base_aliases)
    default = options.get("default_model")
    if default and default not in options.get("alias_map", base_aliases):
        valid = ", ".join(sorted(options.get("alias_map", base_aliases)))
        raise Refusal(f"--default-model '{default}' is not one of this channel's aliases ({valid}).")
    return options


async def _check_channel(channel_id: str) -> None:
    async with slack_api() as api:
        problem = await channel_problem(api, channel_id)
    if problem:
        raise Refusal(f"Refused: Sidecar can't run in this channel ({problem}).")


async def _seed(row, args) -> None:
    """Save `--family-default` for the team; a no-op (and no key is created) without it."""
    families = parse_family_defaults(args.family_default or [])
    if not families:
        return
    async with model_seeder() as seed:
        try:
            await seed(row, families)
        except BadRequestError:
            raise Refusal(MODEL_REFUSED) from None
        except AuthRequiredError as error:
            raise Refusal(f"The team's key isn't ready: {error.message}") from None


def _add_fields(args) -> dict:
    options = _options(args, DEFAULT_ALIASES)
    options.setdefault("alias_map", dict(DEFAULT_ALIASES))
    # Omitted caps come from the environment this script runs in, with the server's validation (4.0 and 3
    # when unset). `update` never does this: it changes only what it is given.
    daily_cap, max_concurrent = registry_defaults(os.environ)
    options.setdefault("daily_cap_usd", daily_cap)
    options.setdefault("max_concurrent", max_concurrent)
    return options


async def cmd_add(registry: ChannelRegistry, args) -> int:
    row = new_row(args.team_id, args.channel, args.label, args.cohort, **_add_fields(args))
    if registry.get(row.slack_team_id, row.channel_id) or registry.by_label(row.team_label):
        raise Refusal("That channel or team label is already registered; use update.")
    await _check_channel(row.channel_id)
    await _seed(row, args)
    registry.add(row)
    print(f"Registered {row.team_label} ({row.slack_team_id}/{row.channel_id}, cohort {row.cohort}).")
    return 0


async def cmd_update(registry: ChannelRegistry, args) -> int:
    current = registry.by_label(args.label)
    if current is None:
        raise Refusal(f"No channel is registered as '{args.label}'.")
    changes = _options(args, current.alias_map)
    if args.active:
        await _check_channel(current.channel_id)
        changes |= {"active": True, "inactive_reason": None}
    if not (changes or args.family_default):
        raise Refusal("Nothing to update: give at least one option.")
    await _seed(replace(current, active=True), args)
    if changes:
        registry.update(current.slack_team_id, current.channel_id, **changes)
    print(f"Updated {current.team_label}.")
    return 0


async def cmd_deactivate(registry: ChannelRegistry, args) -> int:
    current = registry.by_label(args.label)
    if current is None:
        raise Refusal(f"No channel is registered as '{args.label}'.")
    if registry.deactivate(current.slack_team_id, current.channel_id, args.reason):
        print(f"Deactivated {current.team_label}.")
    else:
        print(f"{current.team_label} was already inactive.")
    return 0


async def cmd_list(registry: ChannelRegistry, args) -> int:
    rows = registry.all()
    if not rows:
        print("No Slack channels are registered.")
    for r in rows:
        state = "active" if r.active else f"inactive ({r.inactive_reason or 'no reason'})"
        print(f"{r.team_label}: {r.slack_team_id}/{r.channel_id} cohort {r.cohort}, {state}, "
              f"default {r.default_model}, ${r.daily_cap_usd:g}/day, {r.max_concurrent} at once, "
              f"welcomed {'yes' if r.welcomed_at else 'no'}")
    return 0


COMMANDS = {"add": cmd_add, "update": cmd_update, "deactivate": cmd_deactivate, "list": cmd_list}


def _shared_options(command: argparse.ArgumentParser, *, required: bool) -> None:
    command.add_argument("--fusion-panel", required=required, help="comma-separated model ids, 1 to 8")
    command.add_argument("--fusion-analyst", required=required)
    command.add_argument("--subagent-model", required=required)
    command.add_argument("--default-model", help="the channel's default alias, used when a mention names none")
    command.add_argument("--family-default", nargs="+", metavar="family=model-id",
                         help="the team's model per family (google, openai, x-ai, open_weight), checked "
                              "against the catalog")
    command.add_argument("--alias", nargs="+", metavar="k=v",
                         help="alias=brand or alias=model-id; adds or replaces aliases, cannot remove one")
    command.add_argument("--fetch-allow", nargs="+", metavar="domain")
    command.add_argument("--daily-cap", type=float, help="USD per day for the team")
    command.add_argument("--max-concurrent", type=int)
    command.add_argument("--invoker", nargs="+", metavar="U...", help="Slack user ids allowed to ask")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Slack channels Sidecar answers in.")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="register a channel")
    add.add_argument("--team-id", required=True)
    add.add_argument("--channel", required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--cohort", required=True, choices=sorted(COHORT_WORKSPACES))
    _shared_options(add, required=True)
    update = sub.add_parser("update", help="change a registered channel")
    update.add_argument("label", help="the team label; give it before the multi-value flags")
    update.add_argument("--active", action="store_true", help="turn a deactivated channel back on")
    _shared_options(update, required=False)
    off = sub.add_parser("deactivate", help="turn a channel off")
    off.add_argument("label")
    off.add_argument("--reason", required=True)
    sub.add_parser("list", help="show every registered channel")
    return parser


async def run(args) -> int:
    return await COMMANDS[args.command](ChannelRegistry(target()), args)


if __name__ == "__main__":
    sys.exit(run_script(lambda: run(_parser().parse_args())))
