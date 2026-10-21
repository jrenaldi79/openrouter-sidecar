"""Auth0 sign-in for the admin page: authorization code with PKCE, `state` and `nonce`.

Only people holding both `sidecar:use` and `sidecar:admin` (and a profile email) get an identity back. Tokens
are checked and dropped; nothing here stores them. The client secret and the code never reach a log.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from ...logs import redact
from ..verifier import Auth0Verifier
from .sessions import PendingLogins
from .settings import AdminSettings

log = logging.getLogger(__name__)

ADMIN_PERMISSIONS = ("sidecar:use", "sidecar:admin")
EXCHANGE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class AdminIdentity:
    subject: str
    email: str


class LoginRefused(Exception):
    """Sign-in stopped; `reason` is one of "state", "exchange", "id_token", "not_admin"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class Auth0Login:
    def __init__(self, settings: AdminSettings, logins: PendingLogins, *, key_resolver=None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.logins, self.transport = settings, logins, transport
        jwks_uri = settings.issuer + ".well-known/jwks.json"
        if key_resolver is None:
            client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=600)

            def key_resolver(token):
                return client.get_signing_key_from_jwt(token).key
        self._key = key_resolver
        # The same checks as the MCP endpoint, minus the workspace requirement admins don't have.
        self._access = Auth0Verifier(issuer=settings.issuer, resource=settings.resource,
                                     key_resolver=key_resolver, profile_claim=None)

    def start(self) -> tuple[str, str]:
        """The Auth0 authorize URL and the `state` to keep in the login cookie."""
        pending = self.logins.create()
        query = urlencode({
            "response_type": "code", "client_id": self.settings.client_id,
            "redirect_uri": self.settings.callback_url, "scope": "openid",
            "audience": self.settings.resource, "state": pending.state, "nonce": pending.nonce,
            "code_challenge": _challenge(pending.verifier), "code_challenge_method": "S256",
        })
        return f"{self.settings.issuer}authorize?{query}", pending.state

    async def finish(self, code: str, state: str, cookie_state: str | None) -> AdminIdentity:
        # The cookie binds the login to the browser that started it; None never matches.
        if cookie_state is None or not hmac.compare_digest(cookie_state.encode(), state.encode()):
            raise LoginRefused("state")
        pending = self.logins.take(state)
        if pending is None:
            raise LoginRefused("state")
        tokens = await self._exchange(code, pending.verifier)
        id_claims = await self._id_claims(tokens["id_token"], pending.nonce)
        return await self._identity(tokens["access_token"], id_claims)

    def logout_url(self) -> str:
        query = urlencode({"client_id": self.settings.client_id, "returnTo": self.settings.logout_return})
        return f"{self.settings.issuer}v2/logout?{query}"

    async def _exchange(self, code: str, verifier: str) -> dict[str, str]:
        form = {"grant_type": "authorization_code", "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,  # pragma: allowlist secret
                "code": code,
                "redirect_uri": self.settings.callback_url, "code_verifier": verifier}
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=EXCHANGE_TIMEOUT_S) as client:
                response = await client.post(f"{self.settings.issuer}oauth/token", data=form)
        except httpx.HTTPError as exc:
            log.warning(redact(f"admin token exchange failed: {type(exc).__name__}"))
            raise LoginRefused("exchange") from None
        if response.status_code != 200:
            # The status only: the body can echo the code or the secret.
            log.warning(redact(f"admin token exchange failed: HTTP {response.status_code}"))
            raise LoginRefused("exchange")
        try:
            body = response.json()
        except ValueError:
            log.warning("admin token exchange returned a body that is not JSON")
            raise LoginRefused("exchange") from None
        tokens = {name: body.get(name) if isinstance(body, dict) else None for name in ("access_token", "id_token")}
        if not all(isinstance(value, str) and value for value in tokens.values()):
            log.warning("admin token exchange returned no access or ID token")
            raise LoginRefused("exchange")
        return tokens

    async def _id_claims(self, id_token: str, nonce: str) -> dict:
        try:
            key = await asyncio.to_thread(self._key, id_token)
            claims = jwt.decode(
                id_token, key, algorithms=["RS256"], audience=self.settings.client_id,
                issuer=self.settings.issuer, options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=30,
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a refusal, never a crash
            log.info("admin ID token rejected: %s", type(exc).__name__)
            raise LoginRefused("id_token") from None
        received = claims.get("nonce")
        if not isinstance(received, str) or not hmac.compare_digest(received.encode(), nonce.encode()):
            log.info("admin ID token rejected: nonce mismatch")
            raise LoginRefused("id_token")
        audience = claims["aud"]
        # OIDC Core 3.1.3.7: with several audiences, the authorized party must be this client.
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.settings.client_id:
            log.info("admin ID token rejected: authorized party mismatch")
            raise LoginRefused("id_token")
        return claims

    async def _identity(self, access_token: str, id_claims: dict) -> AdminIdentity:
        access = await self._access.verify_token(access_token)
        if access is None:
            raise LoginRefused("exchange")
        if access.subject != id_claims["sub"]:
            # No subject or email in the log: the reason is enough to tell a bad exchange from a bad login.
            log.info("admin sign-in refused: access and ID token subjects differ")
            raise LoginRefused("id_token")
        profile = access.claims.get(self.settings.profile_claim)
        email = profile.get("email") if isinstance(profile, dict) else None
        if not all(permission in access.scopes for permission in ADMIN_PERMISSIONS):
            log.info("admin sign-in refused: not_admin (missing permission)")
            raise LoginRefused("not_admin")
        if not isinstance(email, str) or not email:
            log.info("admin sign-in refused: not_admin (no profile email)")
            raise LoginRefused("not_admin")
        return AdminIdentity(subject=access.subject, email=email)
