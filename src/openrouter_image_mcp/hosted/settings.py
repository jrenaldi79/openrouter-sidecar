"""Hosted-mode configuration from the environment. Fails closed on unsafe values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

_REQUIRED = ("AUTH0_ISSUER", "MCP_RESOURCE_URI", "DATABASE_URL", "VAULT_KEYS", "LINK_SIGNING_KEY")
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
_TRUE = {"1", "true", "yes"}


@dataclass(frozen=True)
class HostedSettings:
    issuer: str
    jwks_uri: str
    resource: str
    public_origin: str
    database_url: str
    vault_keys: str
    link_signing_key: str
    permission: str = "sidecar:use"
    profile_claim: str = "https://mpd.northwestern.edu/profile"
    sandbox_enabled: bool = False
    maintenance: bool = False


def _secure_or_local(url: str, name: str) -> None:
    parts = urlsplit(url)
    local = parts.hostname in _LOCAL_HOSTS
    # Plain http is only for a developer's own machine (local issuer/resource).
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        raise ValueError(f"{name} must use https (http only for localhost).")


def _origin(resource: str) -> str:
    _secure_or_local(resource, "MCP_RESOURCE_URI")
    parts = urlsplit(resource)
    if parts.path != "/mcp" or parts.query or parts.fragment:
        raise ValueError("MCP_RESOURCE_URI must end exactly with /mcp.")
    return f"{parts.scheme}://{parts.netloc}"


def load_hosted_settings(env: Mapping[str, str]) -> HostedSettings:
    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise ValueError("Missing hosted settings: " + ", ".join(missing))
    issuer = env["AUTH0_ISSUER"]
    _secure_or_local(issuer, "AUTH0_ISSUER")
    if not issuer.endswith("/"):
        raise ValueError("AUTH0_ISSUER must end with '/'.")
    if len(env["LINK_SIGNING_KEY"]) < 32:
        raise ValueError("LINK_SIGNING_KEY must be at least 32 characters.")
    resource = env["MCP_RESOURCE_URI"]
    return HostedSettings(
        issuer=issuer,
        jwks_uri=env.get("AUTH0_JWKS_URI") or issuer + ".well-known/jwks.json",
        resource=resource,
        public_origin=_origin(resource),
        database_url=env["DATABASE_URL"],
        vault_keys=env["VAULT_KEYS"],
        link_signing_key=env["LINK_SIGNING_KEY"],
        permission=env.get("MCP_PERMISSION", "sidecar:use"),
        profile_claim=env.get("PROFILE_CLAIM", "https://mpd.northwestern.edu/profile"),
        sandbox_enabled=env.get("SIDECAR_SANDBOX_ENABLED", "").lower() in _TRUE,
        maintenance=env.get("SIDECAR_MAINTENANCE", "").lower() in _TRUE,
    )
