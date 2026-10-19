"""Command-line entry point: serve (default), login, logout, status, serve-http, purge,
active-jobs, export-local, print-config."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import keyring.errors

from . import keystore
from .auth import LoginManager, LoginState
from .client import OpenRouterClient
from .config import COHORT_WORKSPACES, load_settings
from .errors import OpenRouterError
from .keystore import InsecureKeyringError
from .logs import configure_logging, redact

SERVER_NAME = "openrouter-sidecar"
SIGNED_OUT_MSG = (
    "Not signed in. Run `openrouter-image-mcp login`. Keep this cohort selected."
)
LOGOUT_MSG = (
    "Signed out. To revoke the key on OpenRouter, delete it at "
    "https://openrouter.ai/settings/keys"
)


def render_local_config(project: Path, executable: Path, *, cohort: str) -> str:
    """Launch one Northwestern server using the selected cohort's workspace."""
    settings = load_settings({"OPENROUTER_IMAGE_COHORT": cohort})
    return json.dumps(
        {
            "mcpServers": {
                SERVER_NAME: {
                    "command": str(executable),
                    "args": ["-m", "openrouter_image_mcp.cli"],
                    "env": {
                        "PYTHONPATH": str(project / "src"),
                        "OPENROUTER_IMAGE_COHORT": cohort,
                        "OPENROUTER_IMAGE_WORKSPACE_ID": settings.workspace_id,
                    },
                }
            }
        },
        indent=2,
    )


def render_plugin_config(cohort: str = "2027") -> str:
    """Local instructor plugin template; student configuration selects their cohort."""
    settings = load_settings({"OPENROUTER_IMAGE_COHORT": cohort})
    return json.dumps(
        {
            "mcpServers": {
                SERVER_NAME: {
                    "command": "uv",
                    "args": [
                        "run",
                        "--directory",
                        chr(36) + "{CLAUDE_PLUGIN_ROOT}/..",
                        "--no-sync",
                        "openrouter-image-mcp",
                    ],
                    "env": {
                        "OPENROUTER_IMAGE_COHORT": cohort,
                        "OPENROUTER_IMAGE_WORKSPACE_ID": settings.workspace_id,
                    },
                }
            }
        },
        indent=2,
    )


# ----------------------------------------------------------------- commands


def _serve(args: argparse.Namespace) -> int:
    configure_logging()
    importlib.import_module("openrouter_image_mcp.server").run()
    return 0


async def _login(switch: bool) -> int:
    settings = load_settings()
    client = OpenRouterClient(
        timeout_s=settings.timeout_s, workspace_id=settings.workspace_id
    )
    try:
        print(settings.target)
        manager = LoginManager(client, settings)
        started = await manager.start(switch_account=switch)
        print(started["message"])
        if started["state"] == LoginState.PENDING.value:
            try:
                final = await manager.wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                manager.cancel()  # close the listener; a late callback is ignored
                raise
        else:
            final = LoginState(started["state"])
        if final is LoginState.SIGNED_IN:
            return 0
        print(manager.status()["message"])
        return 1
    finally:
        await client.aclose()


def _login_cmd(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_login(args.switch))
    except KeyboardInterrupt:
        print("Sign-in cancelled.")
        return 130


def _logout(args: argparse.Namespace) -> int:
    settings = load_settings()
    keystore.delete_key(workspace_id=settings.workspace_id)
    print(settings.target)
    print(LOGOUT_MSG)
    return 0


async def _key_info(settings) -> dict:
    client = OpenRouterClient(
        timeout_s=settings.timeout_s, workspace_id=settings.workspace_id
    )
    try:
        return await client.key_info()
    finally:
        await client.aclose()


def _money(value: object) -> str:
    return f"${float(value or 0):.2f}"


def _status(args: argparse.Namespace) -> int:
    settings = load_settings()
    print(settings.target)
    if keystore.get_key(workspace_id=settings.workspace_id) is None:
        print(SIGNED_OUT_MSG)
        return 1
    try:
        info = asyncio.run(_key_info(settings))
    except OpenRouterError as exc:
        print(exc.message)
        return 1
    if info.get("label"):
        print(redact(f"Signed in: {info['label']}"))
    else:
        print("Signed in.")
    print(
        f"Usage today: {_money(info.get('usage_daily'))} / "
        f"month: {_money(info.get('usage_monthly'))}"
    )
    return 0


def _serve_http(_args: argparse.Namespace) -> int:
    hosted = importlib.import_module("openrouter_image_mcp.hosted.main")
    hosted.serve(os.environ)
    return 0


def _purge(args: argparse.Namespace) -> int:
    retention = importlib.import_module("openrouter_image_mcp.hosted.retention")
    report = retention.main_purge(os.environ, apply=args.apply, days=args.days)
    print(json.dumps(report))
    return 0


def _active_jobs(_args: argparse.Namespace) -> int:
    retention = importlib.import_module("openrouter_image_mcp.hosted.retention")
    print(json.dumps({"active_jobs": retention.active_jobs(retention.target_from(os.environ))}))
    return 0


def _export_local(args: argparse.Namespace) -> int:
    bundles = importlib.import_module("openrouter_image_mcp.local_bundle")
    try:
        print(bundles.run_export(args.cohort, Path(args.out)))
    except OpenRouterError as exc:
        print(redact(exc.message), file=sys.stderr)
        return 1
    return 0


def _print_config(args: argparse.Namespace) -> int:
    project = Path(__file__).resolve().parents[2]
    print(render_local_config(project, Path(sys.executable), cohort=args.cohort))
    return 0


# --------------------------------------------------------------------- main


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openrouter-image-mcp",
        description="OpenRouter Sidecar: local image tools and side-advisor chats.",
    )
    parser.set_defaults(func=_serve)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "serve", help="run the MCP server over stdio (default)"
    ).set_defaults(func=_serve)
    login = sub.add_parser("login", help="sign in to OpenRouter in your browser")
    login.add_argument(
        "--switch", action="store_true", help="sign in as a different account"
    )
    login.set_defaults(func=_login_cmd)
    sub.add_parser("logout", help="remove the stored API key").set_defaults(
        func=_logout
    )
    sub.add_parser("status", help="show sign-in state and usage").set_defaults(
        func=_status
    )
    sub.add_parser(
        "serve-http", help="run the hosted multi-user server over Streamable HTTP"
    ).set_defaults(func=_serve_http)
    purge = sub.add_parser("purge", help="hosted: delete idle chats past retention (dry run by default)")
    purge.add_argument("--apply", action="store_true", help="delete instead of only counting")
    purge.add_argument("--days", type=int, help="retention window (default RETENTION_DAYS or 90)")
    purge.set_defaults(func=_purge)
    sub.add_parser("active-jobs", help="hosted: count running advisor jobs (deploy when 0)"
                   ).set_defaults(func=_active_jobs)
    export = sub.add_parser("export-local", help="export local chats and settings for the hosted connector")
    export.add_argument("--cohort", required=True, choices=sorted(COHORT_WORKSPACES))
    export.add_argument("--out", required=True, help="where to write the JSON export")
    export.set_defaults(func=_export_local)
    pc = sub.add_parser("print-config", help="print an MCP client config snippet")
    pc.add_argument("--cohort", required=True, choices=sorted(COHORT_WORKSPACES))
    pc.set_defaults(func=_print_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except InsecureKeyringError as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 1
    except keyring.errors.KeyringError as exc:
        print(
            redact(f"Couldn't access the OS credential store: {exc}"), file=sys.stderr
        )
        return 1
    except (OSError, ValueError) as exc:  # e.g. the sign-in listener couldn't bind
        print(redact(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
