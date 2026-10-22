"""Rotate the sidecar service's Railway secrets, reading new keys from the macOS clipboard.

For each key made elsewhere (OpenRouter, Slack, Auth0) the script tells the operator what
to copy, waits for Enter, reads the clipboard with `pbpaste`, checks the value's shape and clears the
clipboard. Values that need no provider are generated here. Nothing is set until the operator confirms
the summary; each value then goes to `railway variable set KEY --stdin --skip-deploys`, so no value is
printed, passed on a command line or deployed by this script.

    uv run python scripts/ops/rotate_secrets.py [--only NAME ...]

The Interview Coach (mpd-class-mcp) has its own: `npm run secrets:rotate` in that repo.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass

ENVIRONMENT = "production"


@dataclass(frozen=True)
class Step:
    name: str
    where: str  # what to create and copy; empty for a value generated here
    check: str = ""  # the shape a pasted value must have, said to the operator
    pattern: str = ""
    min_len: int = 0


@dataclass(frozen=True)
class Target:
    project: str
    service: str
    steps: list[Step]
    after: str  # what the operator still has to do, printed at the end


def pasted(name: str, where: str, prefix: str = "", min_len: int = 20) -> Step:
    check = f"starts with {prefix}" if prefix else "no spaces"
    return Step(name, where, check, re.escape(prefix) + r"\S+", min_len)


SLACK_REVOKE = "OAuth & Permissions: Revoke All OAuth Tokens, reinstall to the workspace, then copy the Bot User OAuth Token."

SIDECAR = Target(
    "openrouter-sidecar",
    "sidecar",
    [
        pasted("OPENROUTER_MANAGEMENT_KEY", "OpenRouter > organization settings > Management keys: create a new key and copy it.", "sk-or-", 32),
        pasted("SLACK_BOT_TOKEN", "Slack app Sidecar > " + SLACK_REVOKE, "xoxb-"),
        pasted("SLACK_APP_TOKEN", "Slack app Sidecar > Basic Information > App-Level Tokens: generate one with connections:write and copy it.", "xapp-"),
        pasted("SIDECAR_ADMIN_CLIENT_SECRET", "Auth0 > Applications > MPD Sidecar Admin > Credentials: rotate the client secret and copy it.", min_len=32),
        Step("VAULT_KEYS", ""),
        Step("LINK_SIGNING_KEY", ""),
    ],
    """- Revoke or delete the old OpenRouter management key, Slack tokens and Auth0 secret if you haven't already.
- The old vault key is gone, so stored student and team keys can no longer be opened:
  after the deploy, delete each with scripts/ops/openrouter_keys.py delete <email-or-label> --yes and clear the
  student_keys rows (docs/HOSTED_OPERATIONS.md section 7, "Railway's variables leaked").
- Deploy at a quiet time from a clean main: railway up --service sidecar --detach (section 4).""",
)



class Stop(Exception):
    """A one-line reason the script ends; the message never holds a value."""


def generated(name: str) -> str:
    if name == "VAULT_KEYS":
        # A fresh version label, and no old entry: under a leak the old key must stop decrypting.
        label = "v" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M")
        return label + ":" + base64.b64encode(os.urandom(32)).decode()
    return secrets.token_urlsafe(48)


def clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout.strip()


def clear_clipboard() -> None:
    subprocess.run(["pbcopy"], input="", text=True, check=True)


def shape_problem(step: Step, value: str, taken: list[str]) -> str:
    if not value:
        return "The clipboard is empty. Copy the key, then press Enter."
    if not re.fullmatch(step.pattern, value) or len(value) < step.min_len:
        return f"That doesn't look like {step.name} ({step.check}, {step.min_len}+ characters)."
    if value in taken:
        return "That is a value you already pasted for another variable."
    return ""


def ask_paste(step: Step, taken: list[str]) -> str | None:
    print(f"\n{step.name}\n  {step.where}")
    while True:
        answer = input("  Copy it, then press Enter (s = skip, q = quit): ").strip().lower()
        if answer == "q":
            raise Stop("Stopped. Nothing was set in Railway.")
        if answer == "s":
            print("  Skipped.")
            return None
        value = clipboard()
        clear_clipboard()
        problem = shape_problem(step, value, taken)
        if not problem:
            print(f"  Read {len(value)} characters; clipboard cleared.")
            return value
        print("  " + problem)


def collect(steps: list[Step]) -> dict[str, str]:
    values: dict[str, str] = {}
    for step in steps:
        if not step.where:
            values[step.name] = generated(step.name)
            print(f"\n{step.name}\n  Generated here ({len(values[step.name])} characters).")
            continue
        value = ask_paste(step, list(values.values()))
        if value is not None:
            values[step.name] = value
    return values


def railway(*args: str, value: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["railway", *args], input=value, capture_output=True, text=True, check=False)


def project_id(target: Target) -> str:
    result = railway("list", "--json")
    if result.returncode != 0:
        raise Stop("`railway list` failed. Run `railway login` first.")
    for project in json.loads(result.stdout):
        environments = [e["node"]["name"] for e in project.get("environments", {}).get("edges", [])]
        if project.get("name") == target.project and ENVIRONMENT in environments:
            return project["id"]
    raise Stop(f"No Railway project {target.project!r} with a {ENVIRONMENT!r} environment on this login.")


def confirm(target: Target, values: dict[str, str]) -> None:
    print(f"\nReady to set on {target.project} / {ENVIRONMENT} / {target.service} (no deploy):")
    for name, value in values.items():
        print(f"  {name}  ({len(value)} characters)")
    if input("Set these now? Type yes: ").strip().lower() != "yes":
        raise Stop("Stopped. Nothing was set in Railway.")


def apply(target: Target, project: str, values: dict[str, str]) -> None:
    done: list[str] = []
    for name, value in values.items():
        result = railway(
            "variable", "set", name, "--stdin", "--skip-deploys", "--project", project,
            "--service", target.service, "--environment", ENVIRONMENT, value=value,
        )
        if result.returncode != 0:
            raise Stop(f"Setting {name} failed (exit {result.returncode}). Already set: {', '.join(done) or 'none'}.")
        done.append(name)
        print(f"  Set {name}.")


def chosen(target: Target, only: list[str] | None) -> list[Step]:
    names = [step.name for step in target.steps]
    unknown = sorted(set(only or []) - set(names))
    if unknown:
        raise Stop(f"Not a {target.service} variable: {', '.join(unknown)}. Choose from: {', '.join(names)}.")
    return [step for step in target.steps if not only or step.name in only]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="NAME", help="rotate only these variables")
    args = parser.parse_args(argv)
    target = SIDECAR
    try:
        steps = chosen(target, args.only)
        project = project_id(target)
        values = collect(steps)
        if not values:
            raise Stop("Nothing to set.")
        confirm(target, values)
        apply(target, project, values)
    except (Stop, KeyboardInterrupt, EOFError) as stop:
        clear_clipboard()
        # An interrupt during apply() leaves the "Set NAME." lines above as the record of what was set.
        print("\n" + (str(stop) if isinstance(stop, Stop) else "Stopped."), file=sys.stderr)
        return 1
    print("\nDone. Nothing has restarted; the new values load on the next deploy or restart.")
    print(target.after + "\n- If your clipboard manager keeps history, clear it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
