"""Auth0 access-token verification (RS256 only, exact issuer and audience)."""

from __future__ import annotations

import asyncio
import logging

import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger(__name__)


class Auth0Verifier:
    """MCP TokenVerifier. Returns None for every invalid token (the SDK answers 401)."""

    def __init__(self, *, issuer: str, resource: str, jwks_uri: str | None = None,
                 key_resolver=None):
        self.issuer, self.resource = issuer, resource
        if key_resolver is None:
            client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=600)

            def key_resolver(token):
                return client.get_signing_key_from_jwt(token).key
        self._key = key_resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            # JWKS fetches are blocking HTTP; keep them off the event loop.
            key = await asyncio.to_thread(self._key, token)
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.resource, issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]}, leeway=30,
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a rejection, never a crash
            log.info("bearer rejected: %s", type(exc).__name__)  # never log the token
            return None
        permissions = claims.get("permissions") or []
        return AccessToken(
            token=token, client_id=str(claims.get("azp", "")), subject=claims["sub"],
            scopes=[p for p in permissions if isinstance(p, str)],
            expires_at=claims["exp"], resource=self.resource, claims=claims,
        )
