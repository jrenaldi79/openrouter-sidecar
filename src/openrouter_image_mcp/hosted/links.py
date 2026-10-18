"""Per-principal OpenRouter links and single-use, browser-bound link attempts.

A link attempt is created for an authenticated principal and handed out as a
signed URL. The first browser to open it gets a cookie nonce; only that browser
can finish the OpenRouter callback and confirm (prevents login-CSRF, where a
victim would otherwise link *their* key to the attacker's account).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager

from .. import storage
from ..auth import make_pkce
from ..errors import AuthRequiredError
from .principal import Principal
from .vault import Vault

ATTEMPT_TTL_S = 600
LINK_REQUIRED = "openrouter_link_required"


class LinkRequiredError(AuthRequiredError):
    code = LINK_REQUIRED

    def __init__(self, link_url: str):
        self.link_url = link_url
        super().__init__(
            f"{LINK_REQUIRED}: Link your OpenRouter account to continue. Open this link "
            f"in your browser within 10 minutes, then retry: {link_url}")


class LinkError(Exception):
    """A link attempt is invalid, expired, used, or opened in the wrong browser."""


class LinkStore:
    def __init__(self, target, vault: Vault, signing_key: str, public_origin: str):
        self.target, self.vault, self.origin = target, vault, public_origin
        self._signing = signing_key.encode()
        self.clock = time.time

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS openrouter_links (
                principal TEXT NOT NULL, workspace TEXT NOT NULL, sealed_key TEXT NOT NULL,
                key_label TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL,
                PRIMARY KEY (principal, workspace))""")
            db.execute("""CREATE TABLE IF NOT EXISTS link_attempts (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, workspace TEXT NOT NULL,
                verifier TEXT NOT NULL, browser_nonce TEXT, pending_key TEXT,
                pending_label TEXT, account TEXT, expires_at REAL NOT NULL, used_at REAL)""")
            db.commit()
            yield db

    # ------------------------------------------------------------- links

    def _active_row(self, db, owner: str, workspace: str):
        return db.execute("SELECT * FROM openrouter_links WHERE principal=? AND workspace=? "
                          "AND revoked_at IS NULL", (owner, workspace)).fetchone()

    def is_active(self, owner: str, workspace: str) -> bool:
        with self._db() as db:
            return self._active_row(db, owner, workspace) is not None

    def key_for(self, principal: Principal) -> str:
        with self._db() as db:
            row = self._active_row(db, principal.key, principal.workspace_id)
        if row is None:
            raise LinkRequiredError(self.issue(principal))
        return self.vault.open(row["sealed_key"], principal_key=principal.key)

    def label_for(self, principal: Principal) -> str | None:
        with self._db() as db:
            row = self._active_row(db, principal.key, principal.workspace_id)
        return row["key_label"] if row else None

    def save(self, principal: Principal, key: str, label: str) -> None:
        sealed = self.vault.seal(key, principal_key=principal.key)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM openrouter_links WHERE principal=? AND workspace=?",
                       (principal.key, principal.workspace_id))
            db.execute("INSERT INTO openrouter_links (principal,workspace,sealed_key,key_label,"
                       "created_at) VALUES (?,?,?,?,?)",
                       (principal.key, principal.workspace_id, sealed, label[:200], self.clock()))

    def revoke(self, owner: str, workspace: str) -> None:
        with self._db() as db:
            db.execute("UPDATE openrouter_links SET revoked_at=? WHERE principal=? AND "
                       "workspace=? AND revoked_at IS NULL", (self.clock(), owner, workspace))

    # ---------------------------------------------------------- attempts

    def _sign(self, message: str) -> str:
        digest = hmac.new(self._signing, message.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def issue(self, principal: Principal) -> str:
        attempt_id = "lnk_" + secrets.token_hex(16)
        verifier, _ = make_pkce()
        expires = int(self.clock() + ATTEMPT_TTL_S)
        with self._db() as db:
            # The account label is shown before confirming, so a user who received a
            # forwarded link can see it would bind their key to someone else.
            db.execute("INSERT INTO link_attempts (id,principal,workspace,verifier,account,"
                       "expires_at) VALUES (?,?,?,?,?,?)",
                       (attempt_id, principal.key, principal.workspace_id, verifier,
                        principal.email or principal.subject, expires))
        token = f"{attempt_id}.{expires}.{self._sign(f'{attempt_id}.{expires}')}"
        return f"{self.origin}/link/openrouter/start?t={token}"

    def _live(self, db, attempt_id: str):
        row = db.execute("SELECT * FROM link_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= self.clock():
            raise LinkError("This link is no longer valid. Ask Claude for a new one.")
        return row

    def begin(self, token: str) -> tuple[dict, str]:
        """Validate a link token and bind the attempt to this browser (once)."""
        attempt_id, _, rest = token.partition(".")
        expires, _, signature = rest.partition(".")
        if not hmac.compare_digest(self._sign(f"{attempt_id}.{expires}"), signature):
            raise LinkError("This link is not valid.")
        nonce = secrets.token_urlsafe(24)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._live(db, attempt_id)
            if row["browser_nonce"] is not None:
                raise LinkError("This link was already opened. Ask Claude for a new one.")
            db.execute("UPDATE link_attempts SET browser_nonce=? WHERE id=?", (nonce, attempt_id))
            return dict(row), nonce

    def attempt(self, attempt_id: str, nonce: str | None) -> dict:
        with self._db() as db:
            row = self._live(db, attempt_id)
        if not nonce or not row["browser_nonce"] or not hmac.compare_digest(row["browser_nonce"], nonce):
            raise LinkError("Finish linking in the same browser that opened the link.")
        return dict(row)

    def stage(self, attempt_id: str, nonce: str, key: str, label: str) -> None:
        row = self.attempt(attempt_id, nonce)
        sealed = self.vault.seal(key, principal_key=row["principal"])
        with self._db() as db:
            db.execute("UPDATE link_attempts SET pending_key=?, pending_label=? WHERE id=?",
                       (sealed, label[:200], attempt_id))

    def csrf_for(self, attempt_id: str, nonce: str) -> str:
        return self._sign(f"csrf.{attempt_id}.{nonce}")

    def confirm(self, attempt_id: str, nonce: str, csrf: str) -> str:
        row = self.attempt(attempt_id, nonce)
        if not hmac.compare_digest(self.csrf_for(attempt_id, nonce), csrf or "") or not row["pending_key"]:
            raise LinkError("This confirmation is not valid. Ask Claude for a new link.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._live(db, attempt_id)
            db.execute("UPDATE link_attempts SET used_at=?, pending_key=NULL WHERE id=?",
                       (self.clock(), attempt_id))
            db.execute("DELETE FROM openrouter_links WHERE principal=? AND workspace=?",
                       (row["principal"], row["workspace"]))
            db.execute("INSERT INTO openrouter_links (principal,workspace,sealed_key,key_label,"
                       "created_at) VALUES (?,?,?,?,?)",
                       (row["principal"], row["workspace"], row["pending_key"],
                        row["pending_label"] or "OpenRouter key", self.clock()))
        return row["pending_label"] or "OpenRouter key"
