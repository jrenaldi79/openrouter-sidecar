"""Instructor tool for the Sidecar's provisioned OpenRouter keys: students' and Slack teams'.

Reads OPENROUTER_MANAGEMENT_KEY from the environment and never prints it. Talks to the
real OpenRouter management API through the server's own seam (`hosted/openrouter_keys.py`).

    uv run python scripts/ops/openrouter_keys.py list [--cohort 2027]
    uv run python scripts/ops/openrouter_keys.py show <email-or-team-label>
    uv run python scripts/ops/openrouter_keys.py disable|enable <email-or-team-label> [--cohort 2027]
    uv run python scripts/ops/openrouter_keys.py delete <email-or-team-label> [--cohort 2027] [--yes]
    uv run python scripts/ops/openrouter_keys.py usage [--csv]
    uv run python scripts/ops/openrouter_keys.py guardrails

Students' keys live in their cohort's workspace; every Slack team's key lives in the one Slack workspace
(`config.SLACK_WORKSPACE`, shown as `slack`; `--cohort slack` limits a command to it). A team's key is named
after its label, which the commands take where a student's takes the email.

`usage` shows each key's class in the `cohort` column of its CSV (a team's is the last part of its key name) and
the workspace group (a cohort year, or `slack`) in `group`.

`disable` blocks a student or team (their calls report "disabled by your instructor"). `delete`
resets them: the Sidecar issues a fresh key on their next call.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

from openrouter_image_mcp.config import COHORT_WORKSPACES, SLACK_WORKSPACE
from openrouter_image_mcp.errors import OpenRouterError
from openrouter_image_mcp.hosted.openrouter_keys import (
    BUDGET_GUARDRAIL,
    TEAM_GUARDRAIL,
    KeyManager,
)

PREFIX = "Sidecar · "
SLACK = "slack"  # the group name of the Slack workspace, beside the cohort years


def _email(key: dict) -> str:
    """The email of a student's key, or the label of a team's (the middle part of `Sidecar · <who> · <class>`)."""
    parts = key.get("name", "").split(" · ")
    return parts[1] if len(parts) == 3 else "?"


def _class_of(group: str, key: dict) -> str:
    """The class a key belongs to: its cohort year for a student, the last part of its name for a team."""
    if group != SLACK:
        return group
    parts = key.get("name", "").split(" · ")
    return parts[2] if len(parts) == 3 else "?"


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, int | float) else "?"


def targets(group: str | None) -> list[tuple[str, str, str]]:
    """(group, workspace id, guardrail name): each cohort's student workspace, then the Slack workspace."""
    found = [(year, workspace, BUDGET_GUARDRAIL) for year, workspace in sorted(COHORT_WORKSPACES.items())]
    found.append((SLACK, SLACK_WORKSPACE, TEAM_GUARDRAIL))
    return [target for target in found if group in (None, target[0])]


async def _sidecar_keys_in(manager: KeyManager, workspace: str) -> list[dict]:
    return [key for key in await manager.list_keys(workspace) if key.get("name", "").startswith(PREFIX)]


async def sidecar_keys(manager: KeyManager, cohort: str | None) -> list[tuple[str, dict]]:
    """(group, key) for every Sidecar key in the student workspaces and the Slack workspace."""
    return [(group, key) for group, workspace, _ in targets(cohort)
            for key in await _sidecar_keys_in(manager, workspace)]


async def matching(manager: KeyManager, email: str, cohort: str | None) -> list[tuple[str, dict]]:
    email = email.strip().lower()
    return [(year, key) for year, key in await sidecar_keys(manager, cohort)
            if _email(key).lower() == email]


def _row(year: str, key: dict) -> str:
    state = "DISABLED" if key.get("disabled") else "active"
    return (f"{year}  {str(key.get('created_at', '?'))[:10]}  {_money(key.get('usage_monthly')):>8}  "
            f"{state:8}  {key.get('name')}")


async def cmd_list(manager, args) -> int:
    rows = await sidecar_keys(manager, args.cohort)
    print("group   created     this month  state     name")
    for year, key in rows:
        print(_row(year, key))
    print(f"{len(rows)} Sidecar keys")
    return 0


async def cmd_show(manager, args) -> int:
    rows = await matching(manager, args.email, args.cohort)
    for year, key in rows:
        print(_row(year, key))
        print(f"    hash {key.get('hash')}  last used {key.get('last_used_at') or 'never'}  "
              f"total {_money(key.get('usage'))}  limit {_money(key.get('limit'))}")
    if not rows:
        print("No Sidecar key for that email or team label (it is created on first use).")
    return 0 if rows else 1


async def cmd_toggle(manager, args) -> int:
    rows = await matching(manager, args.email, args.cohort)
    for year, key in rows:
        updated = await manager.set_disabled(key["hash"], args.command == "disable")
        print(_row(year, key | updated))
    if not rows:
        print("No Sidecar key for that email or team label.")
    return 0 if rows else 1


async def cmd_delete(manager, args) -> int:
    rows = await matching(manager, args.email, args.cohort)
    if not rows:
        print("No Sidecar key for that email or team label.")
        return 1
    for year, key in rows:
        if not args.yes:
            print("would delete: " + _row(year, key))
            continue
        await manager.delete(key["hash"])
        print("deleted: " + _row(year, key))
    if not args.yes:
        print("Dry run. Add --yes to delete; the student or team gets a fresh key on their next call.")
    return 0


async def cmd_usage(manager, args) -> int:
    rows = await sidecar_keys(manager, args.cohort)
    if args.csv:
        out = csv.writer(sys.stdout)
        out.writerow(["cohort", "group", "email", "usage_monthly", "usage_total", "disabled", "created_at"])
        for group, key in rows:
            out.writerow([_class_of(group, key), group, _email(key), key.get("usage_monthly"), key.get("usage"),
                          bool(key.get("disabled")), key.get("created_at")])
        return 0
    # Slack teams are summed per class, so a class label shows up on the summary too.
    sections = sorted({(group, _class_of(group, key)) for group, key in rows})
    for group, cohort in sections:
        mine = [key for g, key in rows if (g, _class_of(g, key)) == (group, cohort)]
        spend = sum(key.get("usage_monthly") or 0 for key in mine)
        label = f"{group} ({cohort})" if group == SLACK else group
        print(f"{label}: {len(mine)} keys, {_money(spend)} this month")
    return 0


async def _report_guardrail(manager, group: str, workspace: str, name: str, assigned: dict) -> None:
    guardrails = {g.get("id"): g for g in await manager.guardrails(workspace)}
    wanted = next((g for g in guardrails.values() if g.get("name") == name), None)
    keys = await _sidecar_keys_in(manager, workspace)
    print(f"{group} (workspace {workspace})")
    if wanted is None:
        print(f"  NO '{name}' GUARDRAIL -> the server refuses to create keys here")
    else:
        print(f"  {name}: {_money(wanted.get('limit_usd'))} per key, reset {wanted.get('reset_interval') or 'never'}")
    for key in keys:
        if wanted is None or assigned.get(key.get("hash")) != wanted.get("id"):
            other = guardrails.get(assigned.get(key.get("hash")), {})
            print(f"  NOT ON THE BUDGET: {key.get('name')} -> {other.get('name') or 'no guardrail'}")
    on_budget = sum(1 for key in keys if wanted and assigned.get(key.get("hash")) == wanted.get("id"))
    print(f"  {on_budget} of {len(keys)} Sidecar keys on the budget")


async def cmd_guardrails(manager, args) -> int:
    """Each student workspace needs its student budget guardrail; the Slack workspace, the team budget one."""
    assigned = {row.get("key_hash"): row.get("guardrail_id") for row in await manager.key_assignments()}
    for group, workspace, name in targets(args.cohort):
        await _report_guardrail(manager, group, workspace, name, assigned)
    return 0


COMMANDS = {"list": cmd_list, "show": cmd_show, "disable": cmd_toggle, "enable": cmd_toggle,
            "delete": cmd_delete, "usage": cmd_usage, "guardrails": cmd_guardrails}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Sidecar's OpenRouter keys for students and Slack teams.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        command.add_argument("--cohort", choices=[*sorted(COHORT_WORKSPACES), SLACK])
        if name in ("show", "disable", "enable", "delete"):
            command.add_argument("email", metavar="email_or_team_label")
    sub.choices["delete"].add_argument("--yes", action="store_true", help="really delete")
    sub.choices["usage"].add_argument("--csv", action="store_true")
    return parser


async def run(args) -> int:
    key = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if not key:
        print("Set OPENROUTER_MANAGEMENT_KEY (an OpenRouter management key) first.", file=sys.stderr)
        return 2
    manager = KeyManager(key)
    try:
        return await COMMANDS[args.command](manager, args)
    except OpenRouterError as exc:  # messages are redacted and never carry the key
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        await manager.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parser().parse_args())))
