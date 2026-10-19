"""Command-line entry point: serve-http, purge, active-jobs, export-local.

The hosted server (`serve-http`) is the only way to run the Sidecar; the local stdio
server and its sign-in commands are retired. `export-local` remains so students can
move chats from the old local install to the hosted connector. With no command, the
CLI prints help instead of starting anything.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import keyring.errors

from .config import COHORT_WORKSPACES
from .errors import OpenRouterError
from .keystore import InsecureKeyringError
from .logs import redact


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openrouter-image-mcp",
        description="OpenRouter Sidecar: the hosted image tools and side-advisor server.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "serve-http", help="run the hosted multi-user server over Streamable HTTP"
    ).set_defaults(func=_serve_http)
    purge = sub.add_parser("purge", help="hosted: delete idle chats past retention (dry run by default)")
    purge.add_argument("--apply", action="store_true", help="delete instead of only counting")
    purge.add_argument("--days", type=int, help="retention window (default RETENTION_DAYS or 90)")
    purge.set_defaults(func=_purge)
    sub.add_parser("active-jobs", help="hosted: count running advisor jobs (deploy when 0)"
                   ).set_defaults(func=_active_jobs)
    export = sub.add_parser(
        "export-local", help="export chats and settings from the old local Sidecar for the hosted connector")
    export.add_argument("--cohort", required=True, choices=sorted(COHORT_WORKSPACES))
    export.add_argument("--out", required=True, help="where to write the JSON export")
    export.set_defaults(func=_export_local)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except InsecureKeyringError as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 1
    except keyring.errors.KeyringError as exc:
        print(redact(f"Couldn't access the OS credential store: {exc}"), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:  # e.g. missing hosted settings, unwritable --out
        print(redact(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
