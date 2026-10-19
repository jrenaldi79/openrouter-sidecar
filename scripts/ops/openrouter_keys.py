"""Instructor tool for the Sidecar's provisioned student OpenRouter keys.

Reads OPENROUTER_MANAGEMENT_KEY from the environment and never prints it. Talks to the
real OpenRouter management API through the server's own seam (`hosted/openrouter_keys.py`).

    uv run python scripts/ops/openrouter_keys.py list [--cohort 2027]
    uv run python scripts/ops/openrouter_keys.py show <email>
    uv run python scripts/ops/openrouter_keys.py disable|enable <email> [--cohort 2027]
    uv run python scripts/ops/openrouter_keys.py delete <email> [--cohort 2027] [--yes]
    uv run python scripts/ops/openrouter_keys.py usage [--csv]
    uv run python scripts/ops/openrouter_keys.py guardrails

`disable` blocks a student (their calls report "disabled by your instructor"). `delete`
resets them: the Sidecar issues a fresh key on their next call.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.errors import OpenRouterError
from openrouter_image_mcp.hosted.openrouter_keys import BUDGET_GUARDRAIL, KeyManager

PREFIX = "Sidecar · "


def _email(key: dict) -> str:
    parts = key.get("name", "").split(" · ")
    return parts[1] if len(parts) == 3 else "?"


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, int | float) else "?"


async def sidecar_keys(manager: KeyManager, cohort: str | None) -> list[tuple[str, dict]]:
    """(cohort, key) for every Sidecar key in the cohort workspaces."""
    found = []
    for year, workspace in sorted(COHORT_WORKSPACES.items()):
        if cohort in (None, year):
            found += [(year, key) for key in await manager.list_keys(workspace)
                      if key.get("name", "").startswith(PREFIX)]
    return found


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
    print("cohort  created     this month  state     name")
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
        print("No Sidecar key for that email (it is created on the student's first use).")
    return 0 if rows else 1


async def cmd_toggle(manager, args) -> int:
    rows = await matching(manager, args.email, args.cohort)
    for year, key in rows:
        updated = await manager.set_disabled(key["hash"], args.command == "disable")
        print(_row(year, key | updated))
    if not rows:
        print("No Sidecar key for that email.")
    return 0 if rows else 1


async def cmd_delete(manager, args) -> int:
    rows = await matching(manager, args.email, args.cohort)
    if not rows:
        print("No Sidecar key for that email.")
        return 1
    for year, key in rows:
        if not args.yes:
            print("would delete: " + _row(year, key))
            continue
        await manager.delete(key["hash"])
        print("deleted: " + _row(year, key))
    if not args.yes:
        print("Dry run. Add --yes to delete; the student gets a fresh key on their next call.")
    return 0


async def cmd_usage(manager, args) -> int:
    rows = await sidecar_keys(manager, args.cohort)
    if args.csv:
        out = csv.writer(sys.stdout)
        out.writerow(["cohort", "email", "usage_monthly", "usage_total", "disabled", "created_at"])
        for year, key in rows:
            out.writerow([year, _email(key), key.get("usage_monthly"), key.get("usage"),
                          bool(key.get("disabled")), key.get("created_at")])
        return 0
    for year in sorted({year for year, _ in rows}):
        spend = sum(key.get("usage_monthly") or 0 for y, key in rows if y == year)
        print(f"{year}: {sum(1 for y, _ in rows if y == year)} keys, {_money(spend)} this month")
    return 0


async def cmd_guardrails(manager, args) -> int:
    assigned = {row.get("key_hash"): row.get("guardrail_id") for row in await manager.key_assignments()}
    for year, workspace in sorted(COHORT_WORKSPACES.items()):
        if args.cohort not in (None, year):
            continue
        guardrails = {g.get("id"): g for g in await manager.guardrails(workspace)}
        budget = next((g for g in guardrails.values() if g.get("name") == BUDGET_GUARDRAIL), None)
        keys = [key for y, key in await sidecar_keys(manager, year)]
        print(f"{year} (workspace {workspace})")
        if budget is None:
            print(f"  NO '{BUDGET_GUARDRAIL}' GUARDRAIL -> the server refuses to create keys here")
        else:
            print(f"  {BUDGET_GUARDRAIL}: {_money(budget.get('limit_usd'))} per key, reset "
                  f"{budget.get('reset_interval') or 'never'}")
        for key in keys:
            if budget is None or assigned.get(key.get("hash")) != budget.get("id"):
                other = guardrails.get(assigned.get(key.get("hash")), {})
                print(f"  NOT ON THE BUDGET: {key.get('name')} -> {other.get('name') or 'no guardrail'}")
        on_budget = sum(1 for key in keys if budget and assigned.get(key.get("hash")) == budget.get("id"))
        print(f"  {on_budget} of {len(keys)} Sidecar keys on the budget")
    return 0


COMMANDS = {"list": cmd_list, "show": cmd_show, "disable": cmd_toggle, "enable": cmd_toggle,
            "delete": cmd_delete, "usage": cmd_usage, "guardrails": cmd_guardrails}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Sidecar's student OpenRouter keys.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        command.add_argument("--cohort", choices=sorted(COHORT_WORKSPACES))
        if name in ("show", "disable", "enable", "delete"):
            command.add_argument("email")
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
