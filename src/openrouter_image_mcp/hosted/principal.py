"""Authenticated caller identity for hosted mode. Stable key = issuer|subject."""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from ..errors import AuthRequiredError

NO_WORKSPACE = "Your class profile has no OpenRouter workspace; ask your instructor."


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    workspace_id: str
    cohort: str | None = None
    email: str | None = None

    @property
    def key(self) -> str:
        return f"{self.issuer}|{self.subject}"

    @classmethod
    def from_access_token(cls, access: AccessToken, profile_claim: str) -> Principal:
        claims = access.claims or {}
        profile = claims.get(profile_claim)
        workspace = profile.get("workspace_id") if isinstance(profile, dict) else None
        # Fail closed: never fall back to a default cohort workspace.
        if not isinstance(workspace, str) or not workspace:
            raise PermissionError(NO_WORKSPACE)
        cohort, email = profile.get("cohort"), profile.get("email")
        return cls(claims["iss"], access.subject, workspace,
                   cohort if isinstance(cohort, str) else None,
                   email if isinstance(email, str) else None)


def current_principal(profile_claim: str) -> Principal:
    """The principal of the HTTP request being served (raises outside a request)."""
    access = get_access_token()
    if access is None:
        raise AuthRequiredError("Sign in to the Sidecar connector first.")
    try:
        return Principal.from_access_token(access, profile_claim)
    except PermissionError as exc:
        raise AuthRequiredError(str(exc)) from None
