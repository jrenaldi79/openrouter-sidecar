# Hosted Sidecar M0–M1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run the existing sidecar as an authenticated, multi-tenant Streamable HTTP MCP server on Railway (M1), after spikes that de-risk job durability and client timeouts (M0).

**Architecture:** Keep one codebase. Credentials stop coming from the global OS keyring and come from an injected `CredentialSource`: the keyring locally, and in hosted mode the request's Auth0 principal plus an encrypted vault. Storage gets a small dialect adapter so the existing SQLite stores also run on Postgres. The MCP SDK's built-in resource-server support (`AuthSettings` + `TokenVerifier`) does bearer enforcement and protected-resource metadata.

**Tech Stack:** Python 3.12, `mcp` 2.3.0 (MCPServer, Streamable HTTP, `mcp.server.auth`), PyJWT[crypto], cryptography (AES-GCM), psycopg 3, uvicorn/starlette, pytest + pytest-asyncio + respx, Railway (service + Postgres).

**Spec:** `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md`

**Baseline:** `uv run pytest -q` → `650 passed, 4 deselected`. Every task must keep the suite green.

**Conventions to follow:**
- Tests live in `tests/test_<module>.py`. `asyncio_mode = "auto"`, so async tests need no decorator.
- Use the `memory_keyring` fixture (`tests/conftest.py`) for anything touching `keystore`.
- Postgres tests are marked `pg` and skip unless `TEST_DATABASE_URL` is set. Run them locally with
  `docker run --rm -d -p 55433:5432 -e POSTGRES_PASSWORD=test --name sidecar-pg postgres:17` and
  `TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/postgres`.
- Files ≤300 lines and functions ≤50 lines (repo CLAUDE.md). New modules go under `src/openrouter_image_mcp/hosted/`.
- Commit after every green task. Message style: `feat: …`, `test: …`, `docs: …`, ending with the `Co-Authored-By` trailer.

---

## M0 — Spikes (no production code; results recorded in `docs/superpowers/specs/2026-10-18-hosted-spikes.md`)

Each spike is time-boxed to about 1 hour and produces a short written finding with the evidence: request shape, status codes, timings. None of them spends more than $0.10 of inference. Throwaway scripts go under `experiments/hosted_spikes/` (gitignored like `experiments/advisor_probe`).

### Task 0.1: OpenRouter background responses

**Question:** Does `POST /api/v1/responses` accept `"background": true` and return a response id that `GET /api/v1/responses/{id}` can poll after the creating connection closes? This requires `store: true`. Today we send `store: False` (`advisor.py:261`).

**Steps:**
1. Write `experiments/hosted_spikes/background.py`. Use `httpx` and the signed-in key (`keystore.get_key(workspace_id=COHORT_WORKSPACES["2027"])`) to POST a tiny prompt (`max_output_tokens: 16`, cheap text model from `list_chat_models`) with `background: true, store: true`.
2. Record the status and body shape. If an id comes back, close the client, wait 5s, then GET the id with a new client and record `status` transitions.
3. Repeat with `tools: [openrouter:shell …]` to see whether background mode supports the hosted shell tool.
4. Write the result: **supported / unsupported / partial**, with exact field names. If supported, note retention and whether `store: true` exposes transcripts to other workspace keys (try GET with a second key if one is available; otherwise flag it untested).

**Decision rule:** If supported, M1 Task 1.12 persists `provider_response_id` and resumes on startup. Otherwise M1 uses drain-before-deploy (Task 1.13).

### Task 0.2: Railway Streamable HTTP and long requests

**Steps:**
1. Deploy `experiments/hosted_spikes/slow_mcp.py` to a scratch Railway service (`railway init` in the experiments folder; never in the repo root). It's an MCPServer with one tool, `sleep_with_progress(seconds)`, that calls `ctx.report_progress` every 10s, served by `server.run(transport="streamable-http", host="0.0.0.0", port=$PORT)`.
2. From a local MCP SDK client, call it with 30, 60, 120 and 300 seconds. Record which complete, and whether Railway's edge cuts idle SSE.
3. Delete the scratch service afterwards (`railway down`, then remove the project after the user confirms).

### Task 0.3: Client tool-call timeouts and image rendering

**Steps:** Use the Task 0.2 server (with no auth, a scratch URL, and nothing sensitive) as a custom connector in claude.ai, Desktop, Claude Code CLI and Claude Code cloud, and ask the user to try Claude Tag. In each client, call `sleep_with_progress` with 30/60/120s and a `tiny_image` tool that returns one 64×64 `ImageContent`. Record the timeout and whether progress extends it, plus whether the image renders. **The user performs the client actions**. Record their observations verbatim.

**Decision rule:** Set `ask_advisor` default and maximum `wait_s` to at most half of the smallest observed timeout (the spec's working values are 25s/45s).

### Task 0.4: Commit the spike record

```bash
git add docs/superpowers/specs/2026-10-18-hosted-spikes.md .gitignore
git commit -m "docs: record hosted sidecar M0 spike results"
```

---

## M1 — Hosted foundation

### Task 1.1: Dependencies and the hosted package skeleton

**Files:**
- Modify: `pyproject.toml` (dependencies, pytest markers)
- Create: `src/openrouter_image_mcp/hosted/__init__.py`

**Step 1:** Add dependencies (verify that the latest stable versions are at least two weeks old before pinning minimums):

```toml
dependencies = [
    "mcp>=2.3.0",
    "httpx>=0.28.1",
    "pillow>=12.3.0",
    "keyring>=25.7.0",
    "pyjwt[crypto]>=2.15.1",
    "cryptography>=50.0.2",
    "psycopg[binary]>=3.2",
    "uvicorn>=0.54.0",
]
```

and markers:

```toml
markers = [
    "live: hits real OpenRouter, needs signed-in key",
    "pg: needs TEST_DATABASE_URL (disposable Postgres)",
]
```

**Step 2:** Create `hosted/__init__.py` containing only `"""Hosted (remote, multi-tenant) mode."""`.

**Step 3:** Run `uv sync && uv run pytest -q`. Expected: 650 passed.

**Step 4:** Commit: `chore: add hosted-mode dependencies`.

### Task 1.2: HostedSettings

**Files:**
- Create: `src/openrouter_image_mcp/hosted/settings.py`
- Test: `tests/test_hosted_settings.py`

**Step 1: Write failing tests**

```python
import pytest

from openrouter_image_mcp.hosted.settings import HostedSettings, load_hosted_settings

BASE = {
    "AUTH0_ISSUER": "https://tenant.us.auth0.com/",
    "MCP_RESOURCE_URI": "https://sidecar.example/mcp",
    "DATABASE_URL": "postgresql://app@db.railway.internal/sidecar",
    "VAULT_KEYS": "v1:" + "A" * 43 + "=",
    "LINK_SIGNING_KEY": "x" * 32,
}


def test_loads_required_values():
    s = load_hosted_settings(BASE)
    assert s.issuer == "https://tenant.us.auth0.com/"
    assert s.jwks_uri == "https://tenant.us.auth0.com/.well-known/jwks.json"
    assert s.permission == "sidecar:use"
    assert s.profile_claim == "https://mpd.northwestern.edu/profile"
    assert s.public_origin == "https://sidecar.example"


@pytest.mark.parametrize("key,value", [
    ("AUTH0_ISSUER", "https://tenant.us.auth0.com"),      # missing trailing slash
    ("AUTH0_ISSUER", "http://tenant.us.auth0.com/"),      # not https
    ("MCP_RESOURCE_URI", "https://sidecar.example/mcp/"),  # must end exactly /mcp
    ("LINK_SIGNING_KEY", "short"),
])
def test_rejects_unsafe_values(key, value):
    with pytest.raises(ValueError):
        load_hosted_settings(BASE | {key: value})


@pytest.mark.parametrize("missing", sorted(BASE))
def test_requires_every_value(missing):
    env = dict(BASE)
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        load_hosted_settings(env)


def test_localhost_resource_allowed_for_development():
    s = load_hosted_settings(BASE | {"MCP_RESOURCE_URI": "http://localhost:8000/mcp"})
    assert s.public_origin == "http://localhost:8000"
```

**Step 2:** `uv run pytest tests/test_hosted_settings.py -v` → FAIL (module missing).

**Step 3: Implement**

```python
"""Hosted-mode configuration from the environment. Fails closed on unsafe values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

_REQUIRED = ("AUTH0_ISSUER", "MCP_RESOURCE_URI", "DATABASE_URL", "VAULT_KEYS", "LINK_SIGNING_KEY")
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


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


def _origin(resource: str) -> str:
    parts = urlsplit(resource)
    local = parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        raise ValueError("MCP_RESOURCE_URI must use https (http only for localhost).")
    if parts.path != "/mcp" or parts.query or parts.fragment:
        raise ValueError("MCP_RESOURCE_URI must end exactly with /mcp.")
    return f"{parts.scheme}://{parts.netloc}"


def load_hosted_settings(env: Mapping[str, str]) -> HostedSettings:
    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise ValueError("Missing hosted settings: " + ", ".join(missing))
    issuer = env["AUTH0_ISSUER"]
    if not issuer.startswith("https://") or not issuer.endswith("/"):
        raise ValueError("AUTH0_ISSUER must be https and end with '/'.")
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
    )
```

**Step 4:** Run the tests → PASS. Run the full suite → green.

**Step 5:** Commit: `feat: add hosted settings with fail-closed validation`.

### Task 1.3: Principal and the Auth0 JWT verifier

**Files:**
- Create: `src/openrouter_image_mcp/hosted/principal.py`, `src/openrouter_image_mcp/hosted/verifier.py`
- Test: `tests/test_hosted_verifier.py`, plus helper `tests/jwt_helpers.py`

**Step 1: Test helper.** It mints RS256 tokens with a local key and serves a matching JWKS through an injected key resolver, so tests never hit the network.

```python
"""Local RS256 signing for verifier tests."""

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://tenant.us.auth0.com/"
RESOURCE = "https://sidecar.example/mcp"
PROFILE = "https://mpd.northwestern.edu/profile"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(**overrides):
    now = int(time.time())
    base = {
        "iss": ISSUER, "aud": [RESOURCE, ISSUER + "userinfo"], "sub": "email|abc123",
        "azp": "client-1", "iat": now, "exp": now + 600,
        "permissions": ["sidecar:use"],
        PROFILE: {"email": "s@u.northwestern.edu", "cohort": "2027",
                  "workspace_id": "ws-2027", "team": "t1", "program": "MPD"},
    }
    return {k: v for k, v in (base | overrides).items() if v is not None}


def token(key=KEY, alg="RS256", **overrides):
    return jwt.encode(claims(**overrides), key, algorithm=alg, headers={"kid": "k1"})


def resolver(_token):
    return KEY.public_key()
```

**Step 2: Write failing tests**

```python
import pytest

from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.verifier import Auth0Verifier
from tests import jwt_helpers as h


@pytest.fixture
def verifier():
    return Auth0Verifier(issuer=h.ISSUER, resource=h.RESOURCE, profile_claim=h.PROFILE,
                         key_resolver=h.resolver)


async def test_valid_token_maps_permissions_to_scopes(verifier):
    access = await verifier.verify_token(h.token())
    assert access.scopes == ["sidecar:use"]
    assert access.resource == h.RESOURCE
    assert access.subject == "email|abc123"
    assert access.client_id == "client-1"
    principal = Principal.from_access_token(access, h.PROFILE)
    assert principal.key == "https://tenant.us.auth0.com/|email|abc123"
    assert principal.workspace_id == "ws-2027"


@pytest.mark.parametrize("overrides", [
    {"iss": "https://evil.example/"},
    {"aud": "https://other.example/mcp"},
    {"exp": 1},
    {"sub": None},
])
async def test_rejects_bad_claims(verifier, overrides):
    assert await verifier.verify_token(h.token(**overrides)) is None


async def test_rejects_wrong_signature(verifier):
    assert await verifier.verify_token(h.token(key=h.OTHER_KEY)) is None


async def test_rejects_hs256_confusion(verifier):
    forged = h.token(key="secret" * 8, alg="HS256")
    assert await verifier.verify_token(forged) is None


async def test_missing_workspace_fails_closed(verifier):
    access = await verifier.verify_token(h.token(**{h.PROFILE: {"email": "x@y"}}))
    with pytest.raises(PermissionError, match="workspace"):
        Principal.from_access_token(access, h.PROFILE)


async def test_missing_permissions_yields_empty_scopes(verifier):
    access = await verifier.verify_token(h.token(permissions=None))
    assert access.scopes == []  # the SDK's required_scopes then returns 403
```

Note: `from tests import jwt_helpers` works because `pythonpath = ["."]`. Add an empty `tests/__init__.py` only if the import fails. Check first: `tests/__init__.py` doesn't exist today.

**Step 3: Implement `principal.py`**

```python
"""Authenticated caller identity for hosted mode. Stable key = issuer|subject."""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.auth.provider import AccessToken


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
        if not isinstance(workspace, str) or not workspace:
            raise PermissionError("Your class profile has no OpenRouter workspace; ask your instructor.")
        return cls(claims["iss"], access.subject, workspace,
                   profile.get("cohort"), profile.get("email"))
```

**Step 4: Implement `verifier.py`**

```python
"""Auth0 access-token verification (RS256 only, exact issuer and audience)."""

from __future__ import annotations

import logging

import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger(__name__)


class Auth0Verifier:
    """MCP TokenVerifier. Returns None for every invalid token (the SDK answers 401)."""

    def __init__(self, *, issuer: str, resource: str, profile_claim: str,
                 jwks_uri: str | None = None, key_resolver=None):
        self.issuer, self.resource, self.profile_claim = issuer, resource, profile_claim
        if key_resolver is None:
            client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=600)
            key_resolver = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self._key = key_resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token, self._key(token), algorithms=["RS256"], audience=self.resource,
                issuer=self.issuer, options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=30,
            )
        except (jwt.PyJWTError, ValueError) as exc:
            log.info("bearer rejected: %s", type(exc).__name__)  # never log the token
            return None
        permissions = claims.get("permissions") or []
        return AccessToken(
            token=token, client_id=str(claims.get("azp", "")), subject=claims["sub"],
            scopes=[p for p in permissions if isinstance(p, str)],
            expires_at=claims["exp"], resource=self.resource, claims=claims,
        )
```

The resolver runs JWKS HTTP synchronously but caches keys for 10 minutes. If profiling shows blocking, wrap it in `asyncio.to_thread`.

**Step 5:** Run the tests → PASS, then the full suite.

**Step 6:** Commit: `feat: verify Auth0 access tokens and derive hosted principal`.

### Task 1.4: Vault (AES-256-GCM, versioned keys)

**Files:**
- Create: `src/openrouter_image_mcp/hosted/vault.py`
- Test: `tests/test_hosted_vault.py`

**Step 1: Failing tests**

```python
import base64
import os

import pytest

from openrouter_image_mcp.hosted.vault import Vault

K1 = "v1:" + base64.b64encode(os.urandom(32)).decode()
K2 = "v2:" + base64.b64encode(os.urandom(32)).decode()


def test_round_trip_bound_to_principal():
    vault = Vault(K1)
    sealed = vault.seal("sk-or-v1-secret", principal_key="iss|sub")
    assert "sk-or" not in sealed and sealed.startswith("v1:")
    assert vault.open(sealed, principal_key="iss|sub") == "sk-or-v1-secret"


def test_wrong_principal_cannot_open():
    vault = Vault(K1)
    sealed = vault.seal("secret", principal_key="iss|a")
    with pytest.raises(ValueError):
        vault.open(sealed, principal_key="iss|b")


def test_rotation_reads_old_writes_new():
    old = Vault(K1).seal("secret", principal_key="p")
    rotated = Vault(K2 + "," + K1)  # first key is current
    assert rotated.open(old, principal_key="p") == "secret"
    assert rotated.seal("secret", principal_key="p").startswith("v2:")
    assert rotated.needs_rotation(old)


@pytest.mark.parametrize("bad", ["", "v1:short", "nocolon"])
def test_rejects_bad_key_config(bad):
    with pytest.raises(ValueError):
        Vault(bad)
```

**Step 2:** Run → FAIL.

**Step 3: Implement**

```python
"""Encrypt OpenRouter keys at rest. Associated data binds a ciphertext to its principal."""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Vault:
    def __init__(self, spec: str):
        self._keys: dict[str, AESGCM] = {}
        self._current = None
        for item in filter(None, (part.strip() for part in spec.split(","))):
            version, sep, encoded = item.partition(":")
            raw = base64.b64decode(encoded) if sep else b""
            if not version or len(raw) != 32:
                raise ValueError("VAULT_KEYS entries must be '<version>:<base64 32-byte key>'.")
            self._keys[version] = AESGCM(raw)
            self._current = self._current or version
        if not self._current:
            raise ValueError("VAULT_KEYS is empty.")

    def seal(self, secret: str, *, principal_key: str) -> str:
        nonce = os.urandom(12)
        data = self._keys[self._current].encrypt(nonce, secret.encode(), principal_key.encode())
        return self._current + ":" + base64.b64encode(nonce + data).decode()

    def open(self, sealed: str, *, principal_key: str) -> str:
        version, _, encoded = sealed.partition(":")
        if version not in self._keys:
            raise ValueError("Unknown vault key version.")
        blob = base64.b64decode(encoded)
        try:
            return self._keys[version].decrypt(blob[:12], blob[12:], principal_key.encode()).decode()
        except InvalidTag:
            raise ValueError("Vault entry does not belong to this principal.") from None

    def needs_rotation(self, sealed: str) -> bool:
        return not sealed.startswith(self._current + ":")
```

**Step 4:** Tests → PASS, then the full suite.

**Step 5:** Commit: `feat: add versioned AES-GCM vault for OpenRouter keys`.

### Task 1.5: CredentialSource seam (local behavior unchanged)

This replaces every direct `keystore.get_key(workspace_id=…)` used during inference (`advisor.py:59,66`, `client.py:192,246`) with an injected source. The local source wraps the keyring, so all existing tests pass unchanged.

**Files:**
- Create: `src/openrouter_image_mcp/credentials.py`
- Modify: `src/openrouter_image_mcp/advisor.py:52-69`, `src/openrouter_image_mcp/client.py:183-195,243-247`, `src/openrouter_image_mcp/server.py:336-355`
- Test: `tests/test_credentials.py`

**Step 1: Failing tests**

```python
import pytest

from openrouter_image_mcp import keystore
from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.credentials import KeyringCredentials
from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.advisor_store import AdvisorAccessError


def test_keyring_source_matches_legacy_scope(memory_keyring):
    keystore.set_key("sk-test", workspace_id="ws")
    source = KeyringCredentials("ws")
    key, scope = source.resolve()
    assert key == "sk-test"
    assert scope.workspace == "ws" and len(scope.owner) == 64  # sha256 hex, as before


def test_keyring_source_requires_sign_in(memory_keyring):
    with pytest.raises(AuthRequiredError):
        KeyringCredentials("ws").resolve()


def test_check_detects_credential_change(memory_keyring):
    keystore.set_key("sk-a", workspace_id="ws")
    source = KeyringCredentials("ws")
    _, scope = source.resolve()
    keystore.set_key("sk-b", workspace_id="ws")
    with pytest.raises(AdvisorAccessError):
        source.check(scope)


def test_forget_deletes_local_key(memory_keyring):
    keystore.set_key("sk-a", workspace_id="ws")
    KeyringCredentials("ws").forget()
    assert keystore.get_key(workspace_id="ws") is None
```

**Step 2:** Run → FAIL.

**Step 3: Implement `credentials.py`**

```python
"""Where the OpenRouter key and ownership scope come from for the current call."""

from __future__ import annotations

import hashlib
from typing import Protocol

from . import keystore
from .advisor_store import AdvisorAccessError, Scope
from .errors import AuthRequiredError

SIGN_IN = "Not signed in. Call auth_login before using an advisor."


class CredentialSource(Protocol):
    workspace_id: str

    def resolve(self) -> tuple[str, Scope]: ...
    def check(self, scope: Scope) -> None: ...
    def forget(self) -> None: ...


class KeyringCredentials:
    """Local stdio mode: one OS-keyring credential per cohort workspace."""

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id

    def resolve(self) -> tuple[str, Scope]:
        key = keystore.get_key(workspace_id=self.workspace_id)
        if not key:
            raise AuthRequiredError(SIGN_IN)
        # Scope tracks this credential, not a provider-verified student identity.
        return key, Scope(hashlib.sha256(key.encode()).hexdigest(), self.workspace_id)

    def check(self, scope: Scope) -> None:
        key = keystore.get_key(workspace_id=self.workspace_id)
        if (scope.workspace != self.workspace_id or not key
                or hashlib.sha256(key.encode()).hexdigest() != scope.owner):
            raise AdvisorAccessError("Advisor sign-in changed during the operation; reply withheld.")

    def forget(self) -> None:
        keystore.delete_key(workspace_id=self.workspace_id)
```

**Step 4: Refactor `AdvisorService`.** Add a keyword-only `credentials: CredentialSource | None = None` to `__init__`, defaulting to `KeyringCredentials(workspace_id)`. Then make `_credentials()` return `self.credentials.resolve()`, and have `_current(scope)` keep its workspace and client check and then call `self.credentials.check(scope)`. Remove the now-unused `keystore` and `hashlib` imports.

**Step 5: Refactor `OpenRouterClient`.** Add `credentials=None` to `__init__` (default `KeyringCredentials(workspace_id)`). Line 192 becomes `key = self.credentials.resolve()[0]` (keep raising `AuthRequiredError(_MSG_NO_KEY)` when it raises). Line 246 becomes `self.credentials.forget()`.

**Step 6:** Run `uv run pytest -q`. Expected: 650 + 4 new tests pass. Fix only call sites the suite flags. Don't change test expectations.

**Step 7:** Commit: `refactor: inject credential source instead of reading the keyring directly`.

### Task 1.6: Request-scoped hosted credentials

**Files:**
- Create: `src/openrouter_image_mcp/hosted/credentials.py`
- Test: `tests/test_hosted_credentials.py`

**Behavior:**
- `HostedCredentials(links, vault, profile_claim)`. `links` is the Task 1.8 repository; tests use a dict-backed fake.
- `workspace_id` is a property that reads the current principal.
- `resolve()` reads `get_access_token()` (`mcp.server.auth.middleware.auth_context`) and builds a `Principal`. It raises `AuthRequiredError("OpenRouter is not linked…")` with code `openrouter_link_required` when no link exists. It returns `(vault.open(...), Scope(principal.key, principal.workspace_id))`.
- `check(scope)` does **not** use the request context (heartbeats and background tasks may run outside it). It verifies that `links.active(scope.owner, scope.workspace)` still exists and isn't revoked, and raises `AdvisorAccessError` otherwise.
- `forget()` marks the current principal's link revoked (the OpenRouter key stays; deletion on OpenRouter is manual).

**Tests (write first):** principal from a patched `auth_context_var`; no link → `AuthRequiredError`; link for a different workspace → denied; revoked after `resolve` → `check` raises; two principals get different scopes; a background `asyncio.create_task` started inside the request still resolves (contextvars copy), and `check` works after the context var is reset.

Implement it, run the tests, then commit: `feat: resolve OpenRouter keys from the authenticated principal`.

### Task 1.7: Storage dialect adapter (SQLite and Postgres)

The stores use raw `sqlite3`: `advisor_store.py`, `advisor_results.py`, `preferences.py`, `onboarding.py`. Their SQL is portable except for `?` placeholders, `BEGIN IMMEDIATE`, `rowid` ordering, and `INSERT OR …`. Add one adapter instead of rewriting four stores.

**Files:**
- Create: `src/openrouter_image_mcp/storage.py`
- Modify: the four modules above, which call `storage.connect(target)` instead of `sqlite3.connect(path)`
- Test: `tests/test_storage.py`, plus parametrize the existing store fixtures

**Adapter contract:**

```python
class Database(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> Cursor: ...   # '?' placeholders everywhere
    def begin_immediate(self) -> None: ...  # sqlite: BEGIN IMMEDIATE; pg: BEGIN + lock rows read via lock_row()
    def lock_row(self, sql: str, params: tuple) -> Row | None: ...  # sqlite: plain SELECT; pg: SELECT ... FOR UPDATE
```

- `connect(target)` returns a context manager. A `Path` gives a SQLite connection with `row_factory = sqlite3.Row`. A `postgresql://` string gives psycopg with `dict_row`, translating `?`→`%s` (assert there's no `%` in the SQL).
- Replace the `_row(...)` reads inside `BEGIN IMMEDIATE` blocks with `db.lock_row(...)`.
- Replace `ORDER BY rowid` with an explicit `created_seq INTEGER` column (SQLite `INTEGER PRIMARY KEY` alias is unavailable because `id` is TEXT, so use a monotonic `created_at REAL` plus `id` tiebreak).
- Add `migrations.py` with the ordered DDL for both dialects (`TEXT`/`REAL`/`INTEGER` map directly; Postgres uses `DOUBLE PRECISION` for REAL). Keep `CREATE TABLE IF NOT EXISTS` as today, and add a `schema_migrations` table so future changes are versioned.
- `AdvisorResults.export` (filesystem) stays local-only for now. In hosted mode it raises `BadRequestError("Export is unavailable in hosted mode.")` (removed from the tool list in Task 1.10).

**Tests:**
- Turn `store` fixtures in `test_advisor_store.py`, `test_advisor_jobs.py`, `test_preferences.py` and `test_onboarding.py` into `params=["sqlite", pytest.param("pg", marks=pytest.mark.pg)]`. The `pg` param creates a fresh schema per test (`CREATE SCHEMA t_<hex>; SET search_path`) and drops it afterwards.
- New `test_storage.py`: placeholder translation, `lock_row` blocks a concurrent writer on Postgres (two connections, the second times out with `lock_timeout=200ms`), list ordering stable without `rowid`.

Run `uv run pytest -q` (SQLite) and then `TEST_DATABASE_URL=… uv run pytest -q -m "pg or not live"`. Commit: `refactor: run advisor and preference stores on SQLite or Postgres`.

### Task 1.8: Link repository and the OpenRouter link flow

**Files:**
- Create: `src/openrouter_image_mcp/hosted/links.py` (repository plus signed link tokens), `src/openrouter_image_mcp/hosted/link_routes.py` (Starlette handlers)
- Test: `tests/test_hosted_links.py`

**Schema** (added to `migrations.py`):

```sql
CREATE TABLE IF NOT EXISTS openrouter_links (
  principal TEXT NOT NULL, workspace TEXT NOT NULL, sealed_key TEXT NOT NULL,
  key_label TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL,
  PRIMARY KEY (principal, workspace)
);
CREATE TABLE IF NOT EXISTS link_attempts (
  id TEXT PRIMARY KEY, principal TEXT NOT NULL, workspace TEXT NOT NULL,
  verifier TEXT NOT NULL, browser_nonce TEXT, expires_at REAL NOT NULL, used_at REAL
);
```

**Flow, test-first:**
1. `links.issue(principal)` → link URL `https://<origin>/link/openrouter/start?t=<token>`. The token is an HMAC-SHA256 (`LINK_SIGNING_KEY`) over `attempt_id|expires_at`, valid for 10 minutes. The attempt row stores the PKCE verifier and is **single-use**.
2. `GET /link/openrouter/start?t=` validates the token and attempt (unused, unexpired). It sets cookie `sidecar_link=<browser_nonce>` (HttpOnly, Secure, SameSite=Lax, Path=/link, 10 min), stores `browser_nonce` on the attempt, and redirects (302) to `build_auth_url(...)` (`auth.py:89`, reused) with `callback_url=https://<origin>/link/openrouter/callback`, `state=<attempt_id>`, `workspace_id` taken from the attempt.
3. `GET /link/openrouter/callback?code&state` requires the cookie to equal the attempt's `browser_nonce` (the login-CSRF fix). It exchanges the code (`OpenRouterClient.exchange_code`, reused), then **verifies workspace membership** by calling OpenRouter with the new key and checking the workspace id the response reports. If M0 or this task shows no such endpoint exists, record it in the spikes doc and treat the key as linked only after its first successful workspace-scoped request. The new key goes into a pending state, and the handler renders the confirmation page: "Link OpenRouter key '<label>' to your Sidecar account?" with a POST form carrying a CSRF field.
4. `POST /link/openrouter/confirm` seals the key with the vault, upserts the link, marks the attempt used, and renders "Linked. Return to Claude." Pending keys that are never confirmed are deleted on OpenRouter after expiry (best effort) and never stored.

**Tests cover:** token tampering, expiry, replay (second use rejected), missing or mismatched cookie, workspace mismatch, confirm without a pending attempt, relink replaces the sealed key and preserves chats, and that no response body or log contains the key (assert with `caplog` and on response text).

Commit: `feat: add CSRF-safe OpenRouter link flow for hosted principals`.

### Task 1.9: Per-principal limits

**Files:**
- Create: `src/openrouter_image_mcp/hosted/limits.py`
- Modify: `advisor_jobs.py:_start`, `service.py` entry points (one wrapper call each)
- Test: `tests/test_hosted_limits.py`

**Behavior:**
- `Limits(max_concurrent=2, per_minute=20, daily_usd=5.0, usage_repo)`.
- `limits.admit(principal_key)` is a context manager. It raises `RateLimitedError(retry_after_s)` (a new `OpenRouterError` subclass with a user-facing message) when concurrent or per-minute limits are exceeded, or when today's summed `usage.cost` is at or above `daily_usd`.
- Usage rows: `usage(id, principal, kind, cost_usd, created_at, correlation_id)`, written when jobs complete.
- Local mode uses `NoLimits` (a no-op), so existing tests are unaffected.

TDD it with a fake clock. Commit: `feat: enforce per-principal concurrency, rate and spend limits`.

### Task 1.10: Hosted server assembly

**Files:**
- Modify: `src/openrouter_image_mcp/server.py`. Split `build_server` into `_register_tools(server, services, *, hosted: bool)` plus the existing local builder. Stay under 300 lines by moving the image tool wrappers into `src/openrouter_image_mcp/image_tools.py` if needed.
- Create: `src/openrouter_image_mcp/hosted/app.py`
- Test: `tests/test_hosted_app.py`

**`hosted/app.py` sketch:**

```python
def build_hosted_server(settings: HostedSettings) -> MCPServer:
    verifier = Auth0Verifier(issuer=settings.issuer, resource=settings.resource,
                             profile_claim=settings.profile_claim, jwks_uri=settings.jwks_uri)
    auth = AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource,
                        required_scopes=[settings.permission], validate_token_resource=True)
    # services built with HostedCredentials + Postgres DATABASE_URL + Limits
    server = MCPServer(SERVER_NAME, ..., auth=auth, token_verifier=verifier)
    _register_tools(server, services, hosted=True)   # omits auth_login/auth_logout/export
    register_link_routes(server, links, settings)     # custom_route: /link/openrouter/*
    register_health(server, db)                       # /health, /ready
    return server
```

**Hosted tool differences:**
- No `auth_login`/`auth_logout`. Add `link_openrouter` (returns the link URL), and `account_status` shows the link state and cohort.
- `export_advisor_result` is not registered.
- `generate_image`/`edit_image`/`remask_image` aren't registered until M3, because they need local paths.

**Tests (with the SDK's in-process client over `streamable_http_app()` and `httpx.ASGITransport`; follow `tests/test_server.py` client patterns):**
- No bearer → 401 with `WWW-Authenticate` containing `resource_metadata=`.
- `GET /.well-known/oauth-protected-resource/mcp` → `{"resource": RESOURCE, "authorization_servers": [ISSUER]}`.
- Valid token without `sidecar:use` → 403 `insufficient_scope`.
- Valid token → `tools/list` includes `start_advisor_chat` and `link_openrouter`, and excludes `auth_login` and `generate_image`.
- Unlinked principal calling `list_chat_models` → tool error with `openrouter_link_required` and a link URL.
- Two principals cannot see each other's chats (`list_advisor_chats` empty for B after A creates one).

Commit: `feat: assemble authenticated hosted MCP server`.

### Task 1.11: Health, readiness and the single-owner lock

**Files:**
- Create: `src/openrouter_image_mcp/hosted/health.py`
- Test: `tests/test_hosted_health.py`

**Behavior:**
- `/health` → 200 `{"ok": true}` (liveness only, no DB).
- `/ready` → 200 only when the DB answers `SELECT 1`, this process holds `pg_try_advisory_lock(<constant>)` (taken at startup in the lifespan), and `SIDECAR_MAINTENANCE` is not true. Otherwise 503.
- If a second process can't get the lock, it refuses MCP admissions: `/ready` stays 503 and tool calls return "Server is starting or under maintenance".

Test with Postgres (`pg` marker) for the lock, and an env flag for maintenance. Commit: `feat: add health, readiness and single-owner lock`.

### Task 1.12 (only if Task 0.1 says supported): resumable advisor jobs

Add a `provider_response_id` column to `advisor_jobs`. `AdvisorService.send` uses `background: true, store: true` in hosted mode, persists the id, and polls `GET /responses/{id}`. On startup, `AdvisorJobs.resume()` reloads `running` jobs owned by this deployment and continues polling instead of marking them interrupted. Write tests first with respx: a restart in the middle of a job resumes, the final answer is persisted once, and a cancelled provider response maps to `failed`. Commit: `feat: resume background advisor jobs across restarts`.

### Task 1.13: CLI entry point, container and Railway deploy

**Files:**
- Modify: `src/openrouter_image_mcp/cli.py` (new `serve-http` subcommand)
- Create: `Dockerfile`, `docs/HOSTED_OPERATIONS.md`
- Test: `tests/test_cli.py` (subcommand parses and builds the app with a fake settings env)

**`serve-http`:** loads `HostedSettings` from `os.environ`, configures structured JSON logging with a correlation id per request, and runs `uvicorn.run(build_hosted_server(s).streamable_http_app(), host="0.0.0.0", port=int(os.environ["PORT"]), proxy_headers=True, timeout_graceful_shutdown=30)`.

**Dockerfile:** `python:3.12-slim`, `uv sync --frozen --no-dev`, non-root user, `CMD ["openrouter-image-mcp", "serve-http"]`, `HEALTHCHECK` on `/health`.

**`docs/HOSTED_OPERATIONS.md`:**
- Required variables and how to generate `VAULT_KEYS` and `LINK_SIGNING_KEY`.
- Railway setup: a service plus Postgres, private networking, a single replica, autodeploy **off**, health check `/health`.
- The deploy procedure: set `SIDECAR_MAINTENANCE=true`, wait for running jobs (or rely on 1.12), deploy, verify `/ready`, clear maintenance.
- Key rotation.
- Retention purge command (Task 1.14).
- Auth0 API creation and the RBAC steps, referencing the unified-auth spec.

**Deploy (needs user confirmation before each outward step):**
1. `railway init` (project `openrouter-sidecar`), add Postgres, and set variables with `railway variables --set …`. Secrets are entered by the user, never echoed into chat.
2. Create the Auth0 API `https://<railway-domain>/mcp` with RBAC and permissions-in-token, and add `sidecar:use`. This waits for the unified-auth sub-project, or else the user approves a one-off API.
3. `railway up` and verify `/health` and `/ready`. Then add the connector in claude.ai with an instructor test account, link OpenRouter, and run `list_chat_models` → `start_advisor_chat` → `send_advisor_message`.

Commit: `feat: add serve-http entry point, container and hosted runbook`.

### Task 1.14: Retention purge and the decision record

**Files:**
- Create: `src/openrouter_image_mcp/hosted/retention.py`, `docs/superpowers/specs/2026-10-18-hosted-decision-record.md`
- Test: `tests/test_hosted_retention.py`

**Purge:** chats, jobs and results whose last activity is older than `RETENTION_DAYS` (default 90) are deleted, in bounded batches of 500. Usage rows older than 400 days are deleted. It's exposed as `openrouter-image-mcp purge --dry-run|--apply` and run manually or by a Railway cron. Its report shows counts only.

**Decision record:** hosting location (Railway region), what is stored (transcripts, files, keys encrypted), retention, student notice text, the note that this supersedes the 2026-10-05 "no remote deployment" clause, and that sandbox mode stays disabled.

Commit: `feat: add retention purge and hosted decision record`.

### M1 exit criteria

- `uv run pytest -q` and the `pg` suite are green, with CI running both. Add a Postgres service to `.github/workflows/ci.yml`.
- Instructor account on claude.ai: connector auth, OpenRouter link, advisor chat round trip, and a second test account cannot see the first account's chats. Record this in `docs/superpowers/specs/2026-10-18-hosted-spikes.md` under "M1 acceptance".
- No student access until the unified-auth roster sync and the limits are live.
