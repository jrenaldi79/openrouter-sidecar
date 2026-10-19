"""Local stand-in for Auth0 (development only): serves a JWKS and mints test tokens.

Tokens follow the MPD auth contract (mpd-auth/docs/CONTRACT.md): RS256, exact
issuer, audience = the sidecar resource, `permissions`, and the profile claim.
The private key lives in a local state directory; nothing here talks to Auth0.

    python scripts/dev/fake_identity.py serve --state DIR --port 9400
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

KID = "local-dev-1"
PROFILE = "https://mpd.northwestern.edu/profile"


def issuer_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def load_key(state: Path) -> rsa.RSAPrivateKey:
    path = state / "issuer-key.pem"
    if not path.exists():
        state.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        os.chmod(path, 0o600)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def jwks(key: rsa.RSAPrivateKey) -> dict:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [public | {"kid": KID, "use": "sig", "alg": "RS256"}]}


def mint(state: Path, *, port: int, audience: str, sub: str, email: str, cohort: str,
         workspace_id: str | None, team: str | None = None, permissions=("sidecar:use",),
         lifetime_s: int = 600) -> str:
    now = int(time.time())
    claims = {"iss": issuer_for(port), "sub": sub, "aud": [audience], "iat": now,
              "exp": now + lifetime_s, "scope": "openid", "permissions": list(permissions)}
    profile = {"email": email, "cohort": cohort, "team": team, "program": "Test Program",
               "team_id": f"{cohort}-{team.lower()}" if team else None}
    if workspace_id is not None:
        profile["workspace_id"] = workspace_id
    claims[PROFILE] = profile
    return jwt.encode(claims, load_key(state), algorithm="RS256", headers={"kid": KID})


def app(state: Path, port: int) -> Starlette:
    keys = jwks(load_key(state))

    async def jwks_route(_request):
        return JSONResponse(keys)

    async def discovery(_request):
        issuer = issuer_for(port)
        return JSONResponse({"issuer": issuer, "jwks_uri": issuer + ".well-known/jwks.json"})

    return Starlette(routes=[Route("/.well-known/jwks.json", jwks_route),
                             Route("/.well-known/openid-configuration", discovery)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9400)
    args = parser.parse_args()
    uvicorn.run(app(args.state, args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
