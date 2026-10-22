"""Rotate the sidecar service's secrets in Railway, reading new keys from the macOS clipboard.

For each key made elsewhere (OpenRouter, Slack, Auth0) the script tells the operator what to copy,
waits for Enter, reads the clipboard with `pbpaste`, checks the value's shape and clears the clipboard.
VAULT_KEYS and LINK_SIGNING_KEY are generated here. Nothing is set until the operator confirms the
summary; each value then goes to `railway variable set KEY --stdin --skip-deploys`, so no value is
printed, passed on a command line or deployed by this script.

    uv run python scripts/ops/rotate_secrets.py [--only NAME ...]
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

PROJECT = "openrouter-sidecar"
ENVIRONMENT = "production"
SERVICE = "sidecar"


@dataclass(frozen=True)
class Step:
    name: str
    where: str  # what to create and copy; empty for a generated value
    check: str  # the shape a pasted value must have, said to the operator
    pattern: str = ""
    min_len: int = 0


STEPS = [
    Step(
        "OPENROUTER_MANAGEMENT_KEY",
        "OpenRouter > organization settings > Management keys: create a new key and copy it.",
        "starts with sk-or-",
        pattern=r"sk-or-\S+",
        min_len=32,
    ),
    Step(
        "SLACK_BOT_TOKEN",
        "Slack app Sidecar > OAuth & Permissions: after revoking and reinstalling, copy the Bot User OAuth Token.",
        "starts with xoxb-",
        pattern=r"xoxb-\S+",
        min_len=20,
    ),
    Step(
        "SLACK_APP_TOKEN",
        "Slack app Sidecar > Basic Information > App-Level Tokens: generate one with connections:write and copy it.",
        "starts with xapp-",
        pattern=r"xapp-\S+",
        min_len=20,
    ),
    Step(
        "SIDECAR_ADMIN_CLIENT_SECRET",
        "Auth0 > Applications > MPD Sidecar Admin > Credentials: rotate the client secret and copy it.",
        "at least 32 characters, no spaces",
        pattern=r"\S+",
        min_len=32,
    ),
    Step("VAULT_KEYS", "", ""),
    Step("LINK_SIGNING_KEY", "", ""),
]


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


def check_target() -> None:
    result = railway("status", "--json")
    if result.returncode != 0:
        raise Stop("`railway status` failed. Run `railway login` and `railway link` in this repo.")
    status = json.loads(result.stdout)
    project = status.get("name")
    environments = [e["node"]["name"] for e in status.get("environments", {}).get("edges", [])]
    if project != PROJECT or ENVIRONMENT not in environments:
        raise Stop(f"This checkout is linked to project {project!r}, not {PROJECT!r} with {ENVIRONMENT!r}.")


def confirm(values: dict[str, str]) -> None:
    print(f"\nReady to set on {PROJECT} / {ENVIRONMENT} / {SERVICE} (no deploy):")
    for name, value in values.items():
        print(f"  {name}  ({len(value)} characters)")
    if input("Set these now? Type yes: ").strip().lower() != "yes":
        raise Stop("Stopped. Nothing was set in Railway.")


def apply(values: dict[str, str]) -> None:
    done: list[str] = []
    for name, value in values.items():
        result = railway(
            "variable", "set", name, "--stdin", "--skip-deploys",
            "--service", SERVICE, "--environment", ENVIRONMENT, value=value,
        )
        if result.returncode != 0:
            raise Stop(f"Setting {name} failed (exit {result.returncode}). Already set: {', '.join(done) or 'none'}.")
        done.append(name)
        print(f"  Set {name}.")


NEXT_STEPS = """
Done. Nothing has restarted; the new values load on the next deploy or restart (HOSTED_OPERATIONS.md section 4).
- Revoke or delete the old OpenRouter management key, Slack tokens and Auth0 secret if you haven't already.
- The old vault key is gone, so stored student and team keys can no longer be opened:
  delete each with scripts/ops/openrouter_keys.py delete <email-or-label> --yes and clear the student_keys rows.
- If your clipboard manager keeps history, clear it.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    names = [step.name for step in STEPS]
    parser.add_argument("--only", nargs="+", choices=names, metavar="NAME", help="rotate only these: " + ", ".join(names))
    args = parser.parse_args(argv)
    steps = [step for step in STEPS if not args.only or step.name in args.only]
    try:
        check_target()
        values = collect(steps)
        if not values:
            raise Stop("Nothing to set.")
        confirm(values)
        apply(values)
    except (Stop, KeyboardInterrupt, EOFError) as stop:
        clear_clipboard()
        # An interrupt during apply() leaves the "Set NAME." lines above as the record of what was set.
        print("\n" + (str(stop) if isinstance(stop, Stop) else "Stopped."), file=sys.stderr)
        return 1
    print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
