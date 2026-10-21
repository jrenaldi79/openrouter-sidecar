"""Shared helpers for the Slack ops scripts: environment, stores, the team's model defaults.

Reads DATABASE_URL, VAULT_KEYS, OPENROUTER_MANAGEMENT_KEY and SLACK_BOT_TOKEN from the environment and
never prints any of them. Errors reach the operator as one line through `Refusal`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from slack_sdk.web.async_client import AsyncWebClient

from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.errors import OpenRouterError
from openrouter_image_mcp.hosted.app import storage_target
from openrouter_image_mcp.hosted.openrouter_keys import KeyManager
from openrouter_image_mcp.hosted.provisioning import KeyProvisioner, TeamKey
from openrouter_image_mcp.hosted.slack.api import (
    DOWNLOAD_TIMEOUT_S,
    SdkSlackApi,
    SlackCallError,
)
from openrouter_image_mcp.hosted.slack.principal import acting_as, team_principal
from openrouter_image_mcp.hosted.slack.registry import ChannelRow
from openrouter_image_mcp.hosted.slack.wiring import cohort_services
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from openrouter_image_mcp.preferences import FAMILIES


class Refusal(Exception):
    """A one-line reason the script stops; the message is safe to print."""


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise Refusal(f"Set {name} in the environment first.")
    return value


def target():
    return storage_target(need("DATABASE_URL"))


def keys_store(db_target) -> StudentKeyStore:
    try:
        return StudentKeyStore(db_target, Vault(need("VAULT_KEYS")))
    except ValueError:
        raise Refusal("VAULT_KEYS is not a valid vault key list.") from None


@asynccontextmanager
async def slack_api():
    """An `SdkSlackApi` on SLACK_BOT_TOKEN, closed afterwards."""
    http = httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S)
    api = SdkSlackApi(AsyncWebClient(token=need("SLACK_BOT_TOKEN")), http=http)
    try:
        yield api
    finally:
        await api.aclose()


def parse_pairs(items: list[str], flag: str) -> dict[str, str]:
    pairs = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not (sep and name.strip() and value.strip()):
            raise Refusal(f"{flag} needs name=value, got '{item}'.")
        pairs[name.strip()] = value.strip()
    return pairs


def parse_family_defaults(items: list[str]) -> dict[str, str]:
    found = parse_pairs(items, "--family-default")
    bad = sorted(set(found) - set(FAMILIES))
    if bad:
        raise Refusal(f"--family-default family must be one of {', '.join(FAMILIES)}, not {bad[0]}.")
    return found


@asynccontextmanager
async def model_seeder(transport=None):
    """`seed(row, family_defaults)`: save the team's per-family model defaults.

    The PreferenceService is the server's own (`cohort_services`), acting as the team principal, so the
    catalog check is the one the bot applies. It checks against the catalog with the team's key, so the
    key is created first if it does not exist yet (needs OPENROUTER_MANAGEMENT_KEY and the team guardrail).
    `transport` replaces the OpenRouter HTTP transport in tests.
    """
    db_target = target()
    keys = keys_store(db_target)
    management = os.environ.get("OPENROUTER_MANAGEMENT_KEY", "").strip()
    manager = KeyManager(management, transport=transport) if management else None
    provisioner = KeyProvisioner(keys, manager, set(COHORT_WORKSPACES.values()))
    services = cohort_services(db_target, keys, sandbox_allowed=False, transport=transport)

    async def seed(row: ChannelRow, family_defaults: dict[str, str]) -> None:
        principal = team_principal(row.slack_team_id, row.channel_id, row)
        await provisioner.ensure(principal, team=TeamKey(row.team_label))
        preferences = services[row.cohort].preferences
        with acting_as(principal):
            saved = preferences.get()
            await preferences.update(saved["settings_id"], saved["revision"], family_defaults=family_defaults)

    try:
        yield seed
    finally:
        for service in services.values():
            await service.client.aclose()
        if manager is not None:
            await manager.aclose()


def run_script(main: Callable[[], Awaitable[int]]) -> int:
    """Run `main`, turning the known failures into one stderr line (never a token, key or traceback)."""
    import asyncio

    try:
        return asyncio.run(main())
    except Refusal as error:
        print(error, file=sys.stderr)
    except OpenRouterError as error:
        print(error.message, file=sys.stderr)
    except SlackCallError as error:
        print(f"Slack refused the request ({error.method}): {error.code}.", file=sys.stderr)
    except ValueError as error:  # the registry's own validation messages
        print(error, file=sys.stderr)
    return 1
