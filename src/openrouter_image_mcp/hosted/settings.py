"""Hosted-mode configuration from the environment. Fails closed on unsafe values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..advisor_summary import DEFAULT_SUMMARY_MODEL
from .admin.settings import AdminSettings, load_admin_settings
from .slack.settings import SlackSettings, load_slack_settings

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
    # Fast model for share summaries (advisor_summary.py); changeable without a deploy.
    summary_model: str = DEFAULT_SUMMARY_MODEL
    maintenance: bool = False
    dev_openrouter_url: str | None = None
    asset_dir: str | None = None
    # Org management key: creates and deletes student keys only. Unset: no new keys.
    management_key: str | None = field(default=None, repr=False)
    # Slack Sidecar (Socket Mode bot); None only when a settings object is built by hand.
    slack: SlackSettings | None = None
    # The /admin page (Auth0 sign-in for Sidecar admins); None only when built by hand.
    admin: AdminSettings | None = None


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


def _check_issuer(issuer: str, resource: str, env: Mapping[str, str]) -> None:
    if not issuer.endswith("/"):
        raise ValueError("AUTH0_ISSUER must end with '/'.")
    if urlsplit(issuer).scheme == "https":
        return
    # A plain-http issuer is a local test identity provider: only with an explicit
    # developer flag, and only while the resource itself is local too.
    if env.get("SIDECAR_DEV_INSECURE_ISSUER", "").lower() not in _TRUE:
        raise ValueError("AUTH0_ISSUER must be https (set SIDECAR_DEV_INSECURE_ISSUER=1 for a local test issuer).")
    _secure_or_local(issuer, "AUTH0_ISSUER")
    if urlsplit(resource).hostname not in _LOCAL_HOSTS:
        raise ValueError("A local test issuer is only allowed for a localhost resource.")


def _dev_openrouter(env: Mapping[str, str], resource: str) -> str | None:
    url = env.get("SIDECAR_DEV_OPENROUTER_URL") or None
    if url is None:
        return None
    # A local fake OpenRouter is for development only: explicit flag, local resource,
    # local target. Production always talks to openrouter.ai.
    local = urlsplit(url).hostname in _LOCAL_HOSTS and urlsplit(resource).hostname in _LOCAL_HOSTS
    if env.get("SIDECAR_DEV_INSECURE_ISSUER", "").lower() not in _TRUE or not local:
        raise ValueError("SIDECAR_DEV_OPENROUTER_URL is only for local development "
                         "(localhost target and resource, SIDECAR_DEV_INSECURE_ISSUER=1).")
    return url.rstrip("/")


def _asset_dir(env: Mapping[str, str]) -> str | None:
    # Image blobs live on a mounted volume; unset keeps the image tools off.
    path = env.get("ASSET_DIR") or None
    # POSIX-rooted (the Railway volume) or absolute on this OS (e.g. C:\ in Windows CI).
    if path is not None and not (path.startswith("/") or Path(path).is_absolute()):
        raise ValueError("ASSET_DIR must be an absolute path (a mounted volume).")
    return path


def load_hosted_settings(env: Mapping[str, str]) -> HostedSettings:
    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise ValueError("Missing hosted settings: " + ", ".join(missing))
    issuer, resource = env["AUTH0_ISSUER"], env["MCP_RESOURCE_URI"]
    _check_issuer(issuer, resource, env)
    if len(env["LINK_SIGNING_KEY"]) < 32:
        raise ValueError("LINK_SIGNING_KEY must be at least 32 characters.")
    public_origin = _origin(resource)
    return HostedSettings(
        issuer=issuer,
        jwks_uri=env.get("AUTH0_JWKS_URI") or issuer + ".well-known/jwks.json",
        resource=resource,
        public_origin=public_origin,
        database_url=env["DATABASE_URL"],
        vault_keys=env["VAULT_KEYS"],
        link_signing_key=env["LINK_SIGNING_KEY"],
        permission=env.get("MCP_PERMISSION", "sidecar:use"),
        profile_claim=env.get("PROFILE_CLAIM", "https://mpd.northwestern.edu/profile"),
        sandbox_enabled=env.get("SIDECAR_SANDBOX_ENABLED", "").lower() in _TRUE,
        summary_model=env.get("SIDECAR_SUMMARY_MODEL", "").strip() or DEFAULT_SUMMARY_MODEL,
        maintenance=env.get("SIDECAR_MAINTENANCE", "").lower() in _TRUE,
        dev_openrouter_url=_dev_openrouter(env, resource),
        asset_dir=_asset_dir(env),
        management_key=env.get("OPENROUTER_MANAGEMENT_KEY") or None,
        slack=load_slack_settings(env),
        admin=load_admin_settings(env, issuer=issuer, resource=resource, public_origin=public_origin),
    )
