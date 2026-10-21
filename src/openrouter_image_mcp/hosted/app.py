"""Authenticated, multi-tenant Streamable HTTP MCP server (hosted Sidecar)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .. import __version__, storage
from ..advisor_shares import AdvisorShares, register_share_tools, share_reminder
from ..advisor_store import AdvisorStore, Scope
from ..advisor_tools import enforce_advisor_arguments, register_advisor_tools
from ..apps import (
    ImageApps,
    app_icons,
    negotiate_apps,
    register_advisor_viewer,
    register_gallery,
    register_settings,
    register_uploader,
)
from ..assets import AssetStore, LocalDirBlobs
from ..config import COHORT_WORKSPACES, SLACK_WORKSPACE
from ..preference_tools import register_preference_tools
from ..tool_annotations import annotate
from ..tool_support import SERVER_NAME, tool_errors
from .account import register_account_tools
from .admin.wiring import register_admin
from .guidance import INSTRUCTIONS, register_guidance
from .health import MAINTENANCE, Health
from .image_tools import register_image_tools
from .images import HostedImages
from .imports import LocalImports, import_routes, register_import_tool
from .limits import Limits
from .openrouter_keys import KeyManager
from .principal import current_principal
from .provisioning import KeyProvisioner, key_middleware
from .settings import HostedSettings
from .student_keys import StudentKeyStore
from .sweeper import Sweeper
from .uploads import TransferTokens, transfer_routes
from .vault import Vault
from .verifier import Auth0Verifier
from .workspaces import SHUTTING_DOWN, ByWorkspace, Gate, build_workspaces

log = logging.getLogger(__name__)


def storage_target(database_url: str):
    """postgresql:// URLs for hosting; a sqlite:/// path for local development."""
    if storage.is_postgres(database_url):
        return database_url
    return Path(database_url.removeprefix("sqlite:///"))


def _scope_for(settings: HostedSettings, gate: Gate):
    def scope_for() -> Scope:
        gate.check()
        who = current_principal(settings.profile_claim)
        return Scope(who.key, who.workspace_id)
    return scope_for


def _register_imports(server: MCPServer, settings: HostedSettings, target, gate: Gate) -> None:
    """Local-export import (M5); needs no blob volume."""
    tokens = TransferTokens(target, settings.link_signing_key, settings.public_origin)
    register_import_tool(server, tokens, _scope_for(settings, gate), tool_errors)
    for route in import_routes(tokens, LocalImports(target)):
        server.custom_route(route.path, sorted(route.methods - {"HEAD"}))(route.endpoint)


def transport_security(settings: HostedSettings) -> TransportSecuritySettings:
    host = urlsplit(settings.public_origin).netloc
    return TransportSecuritySettings(allowed_hosts=[host], allowed_origins=[settings.public_origin])


def _principal(settings: HostedSettings, gate: Gate):
    def principal():
        gate.check()
        return current_principal(settings.profile_claim)
    return principal


def _register_images(server: MCPServer, settings: HostedSettings, target, services: dict,
                     gate: Gate, admission: Limits) -> dict:
    """Image tools and transfer routes; only with a blob volume (ASSET_DIR)."""
    scope_for = _scope_for(settings, gate)
    assets = AssetStore(target, LocalDirBlobs(settings.asset_dir))
    transfers = TransferTokens(target, settings.link_signing_key, settings.public_origin)
    hosted = HostedImages(assets, transfers, services["images"], scope_for, max_input_edge=2048,
                          admit=admission.image_call)
    register_image_tools(server, hosted, services["catalog"], tool_errors)
    register_uploader(server, settings.public_origin)
    for route in transfer_routes(transfers, assets):
        server.custom_route(route.path, sorted(route.methods - {"HEAD"}))(route.endpoint)
    # Only the instance holding the single-owner lock (serving, or in maintenance) sweeps.
    sweeper = Sweeper(assets, transfers, active=lambda: gate.reason in (None, MAINTENANCE))
    return {"assets": assets, "transfers": transfers, "sweeper": sweeper}


def _build_slack(settings: HostedSettings, target, keys, provisioner, admission: Limits, health: Health,
                 transport, options: dict | None):
    """The Slack bot, hooked to the owner lock; None (and nothing Slack imported) unless enabled."""
    if not (settings.slack and settings.slack.enabled):
        return None
    from .slack.wiring import build_slack

    slack = build_slack(settings, target, keys, provisioner, admission, transport=transport, **(options or {}))
    health.lock.on_gain.append(slack.on_gain)
    health.lock.on_lose.append(slack.on_lose)
    return slack


def build_hosted_server(settings: HostedSettings, *, key_resolver=None, transport=None,
                        limits: dict | None = None, slack: dict | None = None,
                        admin: dict | None = None) -> MCPServer:
    """`slack` passes test doubles to `build_slack` (`api`, `client_factory`); `admin` to `Auth0Login`
    (`key_resolver`, `transport`)."""
    target = storage_target(settings.database_url)
    keys = StudentKeyStore(target, Vault(settings.vault_keys))
    # Without a management key the server still runs; only new student keys are refused.
    manager = KeyManager(settings.management_key, transport=transport) if settings.management_key else None
    provisioner = KeyProvisioner(keys, manager, set(COHORT_WORKSPACES.values()),
                                 team_workspaces=frozenset({SLACK_WORKSPACE}))
    gate = Gate()
    services = {}
    admission = Limits(AdvisorStore(target), **(limits or {}))
    workspaces = build_workspaces(target, keys, profile_claim=settings.profile_claim,
                                  admit=admission.admit, transport=transport,
                                  sandbox_enabled=settings.sandbox_enabled,
                                  summary_model=settings.summary_model)
    health = Health(target, gate, maintenance=settings.maintenance, asset_dir=settings.asset_dir)
    shares = AdvisorShares(ByWorkspace(workspaces, settings.profile_claim, gate, "jobs"))
    slack_app = _build_slack(settings, target, keys, provisioner, admission, health, transport, slack)

    images: dict = {}

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict]:
        await health.start()
        if "sweeper" in images:
            await images["sweeper"].start()
        try:
            yield {}
        finally:
            gate.reason = SHUTTING_DOWN
            if slack_app is not None:
                await slack_app.close()
            if "sweeper" in images:
                await images["sweeper"].stop()
            for item in workspaces.values():
                await item.close()
            if manager is not None:
                await manager.aclose()
            await health.stop()
            await asyncio.to_thread(storage.close_pool, target)

    server = MCPServer(
        SERVER_NAME, version=__version__, lifespan=lifespan, icons=app_icons(),
        instructions=INSTRUCTIONS,
        extensions=[ImageApps()], middleware=[enforce_advisor_arguments, negotiate_apps,
                    key_middleware(provisioner, settings.profile_claim, gate), share_reminder(shares)],
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource,
                          required_scopes=[settings.permission], validate_token_resource=True),
        token_verifier=Auth0Verifier(issuer=settings.issuer, resource=settings.resource,
                                     jwks_uri=settings.jwks_uri, key_resolver=key_resolver,
                                     profile_claim=settings.profile_claim),
    )
    for name in ("advisors", "jobs", "preferences", "demos", "catalog", "settings", "images"):
        services[name] = ByWorkspace(workspaces, settings.profile_claim, gate, name)
    register_gallery(server)
    register_advisor_viewer(server)
    register_settings(server)
    register_advisor_tools(server, services["advisors"], services["jobs"], tool_errors,
                           preferences=services["preferences"])
    register_share_tools(server, shares, tool_errors)
    register_preference_tools(server, services["preferences"], services["demos"],
                              services["catalog"], services["settings"], tool_errors)
    register_account_tools(server, provisioner, _principal(settings, gate), transport=transport,
                           tool_errors=tool_errors)
    _register_imports(server, settings, target, gate)
    register_guidance(server)
    if settings.asset_dir:
        images.update(_register_images(server, settings, target, services, gate, admission))
    health.register(server)
    # Served by every replica: the admin page doesn't depend on the owner lock.
    admin_app = register_admin(server, settings, target, slack_app, provisioner, admission, options=admin)
    server.hosted = {"keys": keys, "provisioner": provisioner, "workspaces": workspaces, "gate": gate, "health": health,
                     "slack": slack_app, "admin": admin_app, **images}
    return annotate(server)
