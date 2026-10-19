"""Authenticated, multi-tenant Streamable HTTP MCP server (hosted Sidecar)."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .. import __version__, storage
from ..advisor_store import AdvisorStore
from ..advisor_tools import enforce_advisor_arguments, register_advisor_tools
from ..apps import (
    ImageApps,
    app_icons,
    negotiate_apps,
    register_advisor_viewer,
    register_gallery,
    register_settings,
)
from ..auth import AUTH_URL
from ..preference_tools import register_preference_tools
from ..server import SERVER_NAME, _tool_errors
from .health import Health
from .limits import Limits
from .link_routes import link_routes
from .links import LinkStore
from .openrouter_keys import exchange_code, verify_key
from .principal import current_principal
from .settings import HostedSettings
from .vault import Vault
from .verifier import Auth0Verifier
from .workspaces import ByWorkspace, Gate, build_workspaces

log = logging.getLogger(__name__)
LOCAL_ONLY_TOOLS = ("export_advisor_result",)


def storage_target(database_url: str):
    """postgresql:// URLs for hosting; a sqlite:/// path for local development."""
    if storage.is_postgres(database_url):
        return database_url
    return Path(database_url.removeprefix("sqlite:///"))


def transport_security(settings: HostedSettings) -> TransportSecuritySettings:
    host = urlsplit(settings.public_origin).netloc
    return TransportSecuritySettings(allowed_hosts=[host], allowed_origins=[settings.public_origin])


def _register_account_tools(server: MCPServer, links: LinkStore, settings: HostedSettings, gate: Gate):
    def principal():
        gate.check()
        return current_principal(settings.profile_claim)

    async def account_status() -> str:
        """Show your Sidecar sign-in, class cohort and whether OpenRouter is linked.

        No inference and no cost. If OpenRouter is not linked, call link_openrouter.
        """
        with _tool_errors():
            who = principal()
            label = links.label_for(who)
            linked = f"Linked OpenRouter key: {label}" if label else "OpenRouter: not linked (call link_openrouter)."
            return f"Signed in as {who.email or who.subject}. Class of {who.cohort or '?'}.\n{linked}"

    async def link_openrouter() -> str:
        """Get a one-time link to connect your OpenRouter account to the Sidecar.

        Give the link to the user to open in their browser within 10 minutes. It runs
        OpenRouter sign-in for your class workspace, then asks them to confirm. Only
        the browser that opens the link can finish it.
        """
        with _tool_errors():
            url = links.issue(principal())
            return f"Open this link in your browser within 10 minutes to link OpenRouter:\n{url}"

    async def unlink_openrouter() -> str:
        """Stop using your linked OpenRouter key on the Sidecar (chats are kept).

        To fully revoke the key, also delete it at https://openrouter.ai/settings/keys.
        """
        with _tool_errors():
            who = principal()
            links.revoke(who.key, who.workspace_id)
            return "Unlinked. Delete the key at https://openrouter.ai/settings/keys to revoke it fully."

    for fn in (account_status, link_openrouter, unlink_openrouter):
        server.add_tool(fn, description=inspect.cleandoc(fn.__doc__), structured_output=False)


def build_hosted_server(settings: HostedSettings, *, key_resolver=None, transport=None,
                        exchange=exchange_code, verify=verify_key, auth_url: str = AUTH_URL,
                        limits: dict | None = None) -> MCPServer:
    target = storage_target(settings.database_url)
    links = LinkStore(target, Vault(settings.vault_keys), settings.link_signing_key,
                      settings.public_origin)
    gate = Gate()
    services = {}
    admission = Limits(AdvisorStore(target), **(limits or {}))
    workspaces = build_workspaces(target, links, profile_claim=settings.profile_claim,
                                  admit=admission.admit, transport=transport,
                                  sandbox_enabled=settings.sandbox_enabled)
    health = Health(target, gate, maintenance=settings.maintenance)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict]:
        await health.start()
        try:
            yield {}
        finally:
            gate.reason = "Server is shutting down."
            for item in workspaces.values():
                await item.close()
            await health.stop()

    server = MCPServer(
        SERVER_NAME, version=__version__, lifespan=lifespan, icons=app_icons(),
        extensions=[ImageApps()], middleware=[enforce_advisor_arguments, negotiate_apps],
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource,
                          required_scopes=[settings.permission], validate_token_resource=True),
        token_verifier=Auth0Verifier(issuer=settings.issuer, resource=settings.resource,
                                     jwks_uri=settings.jwks_uri, key_resolver=key_resolver,
                                     profile_claim=settings.profile_claim),
    )
    for name in ("advisors", "jobs", "preferences", "demos", "catalog", "settings"):
        services[name] = ByWorkspace(workspaces, settings.profile_claim, gate, name)
    register_gallery(server)
    register_advisor_viewer(server)
    register_settings(server)
    register_advisor_tools(server, services["advisors"], services["jobs"], _tool_errors,
                           preferences=services["preferences"])
    register_preference_tools(server, services["preferences"], services["demos"],
                              services["catalog"], services["settings"], _tool_errors)
    for name in LOCAL_ONLY_TOOLS:
        server.remove_tool(name)
    _register_account_tools(server, links, settings, gate)
    for route in link_routes(links, exchange=exchange, verify=verify, auth_url=auth_url):
        server.custom_route(route.path, sorted(route.methods - {"HEAD"}))(route.endpoint)
    health.register(server)
    server.hosted = {"links": links, "workspaces": workspaces, "gate": gate, "health": health}
    return server
