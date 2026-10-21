"""Admin page configuration from the environment. On by default; sign-in needs the Auth0 client values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_OFF = {"false", "0", "no"}
CLIENT_ID_VAR = "SIDECAR_ADMIN_CLIENT_ID"
CLIENT_SECRET_VAR = "SIDECAR_ADMIN_CLIENT_SECRET"
MIN_SECRET_CHARS = 32


@dataclass(frozen=True)
class AdminSettings:
    enabled: bool
    client_id: str | None
    # Auth0 regular-web-app secret for the code exchange; never logged or rendered.
    client_secret: str | None = field(repr=False)
    issuer: str
    resource: str
    public_origin: str
    # The access token claim holding the admin's email; the same one the MCP endpoint reads.
    profile_claim: str

    @property
    def callback_url(self) -> str:
        return f"{self.public_origin}/admin/callback"

    @property
    def logout_return(self) -> str:
        return f"{self.public_origin}/admin"

    @property
    def secure_cookies(self) -> bool:
        # Deferred: hosted/settings.py imports this module to build HostedSettings.admin.
        from ..settings import _LOCAL_HOSTS

        parts = urlsplit(self.public_origin)
        return not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS)

    @property
    def configured(self) -> bool:
        return not self.missing()

    def missing(self) -> list[str]:
        """Names of the unset sign-in variables, for the not-configured page and the startup log."""
        values = ((CLIENT_ID_VAR, self.client_id), (CLIENT_SECRET_VAR, self.client_secret))
        return [name for name, value in values if not value]


def load_admin_settings(env: Mapping[str, str], *, issuer: str, resource: str,
                        public_origin: str, profile_claim: str) -> AdminSettings:
    secret = env.get(CLIENT_SECRET_VAR, "").strip() or None
    # A missing secret only turns sign-in off; a wrong one is a typo worth stopping startup for.
    if secret is not None and len(secret) < MIN_SECRET_CHARS:
        raise ValueError(f"{CLIENT_SECRET_VAR} must be at least {MIN_SECRET_CHARS} characters.")
    return AdminSettings(
        enabled=env.get("SIDECAR_ADMIN_ENABLED", "").strip().lower() not in _OFF,
        client_id=env.get(CLIENT_ID_VAR, "").strip() or None,
        client_secret=secret,
        issuer=issuer,
        resource=resource,
        public_origin=public_origin,
        profile_claim=profile_claim,
    )
