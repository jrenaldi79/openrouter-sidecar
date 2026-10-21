"""Local RS256 signing for hosted-mode tests: a synthetic issuer and test users."""

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://tenant.us.auth0.com/"
RESOURCE = "https://sidecar.example/mcp"
PROFILE = "https://mpd.northwestern.edu/profile"
WORKSPACE = "ws-2027"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(sub="email|alice", workspace=WORKSPACE, **overrides):
    now = int(time.time())
    base = {
        "iss": ISSUER, "aud": [RESOURCE, ISSUER + "userinfo"], "sub": sub,
        "azp": "client-1", "iat": now, "exp": now + 600,
        "permissions": ["sidecar:use"],
        PROFILE: {"email": str(sub).split("|")[-1] + "@u.northwestern.edu", "cohort": "2027",
                  "workspace_id": workspace, "team": "t1", "program": "MPD"},
    }
    return {k: v for k, v in (base | overrides).items() if v is not None}


def token(key=KEY, alg="RS256", **overrides):
    return jwt.encode(claims(**overrides), key, algorithm=alg, headers={"kid": "k1"})


def resolver(_token):
    return KEY.public_key()


def id_token(client_id, nonce, key=KEY, alg="RS256", **overrides):
    """An OIDC ID token as the admin client's code exchange returns it."""
    now = int(time.time())
    base = {"iss": ISSUER, "aud": client_id, "sub": "email|alice", "iat": now, "exp": now + 600,
            "nonce": nonce}
    payload = {k: v for k, v in (base | overrides).items() if v is not None}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": "k1"})
