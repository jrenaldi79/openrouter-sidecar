"""Provisioned OpenRouter keys at rest: one row per principal and workspace.

A student's row is in their cohort workspace; a Slack team's is in the Slack workspace.

The key is sealed with the vault and bound to the principal; OpenRouter's key hash is
kept in clear for management calls. A row marked `rejected_at` (OpenRouter answered 401)
is not served until `provisioning.py` has rechecked the key with the management API.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass

from .. import storage
from .principal import Principal
from .vault import Vault

TOUCH_EVERY_S = 3600  # last_used_at is for retention; hourly precision is plenty


@dataclass(frozen=True)
class StudentKey:
    principal: str
    workspace: str
    key_hash: str
    key_name: str
    created_at: float
    last_used_at: float
    rejected_at: float | None


def _key(row) -> StudentKey:
    return StudentKey(row["principal"], row["workspace"], row["key_hash"], row["key_name"],
                      row["created_at"], row["last_used_at"], row["rejected_at"])


class StudentKeyStore:
    def __init__(self, target, vault: Vault | None):
        self.target, self.vault = target, vault  # no vault: metadata only (retention)
        self.clock = time.time

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS student_keys (
                principal TEXT NOT NULL, workspace TEXT NOT NULL, sealed_key TEXT NOT NULL,
                key_hash TEXT NOT NULL, key_name TEXT NOT NULL, created_at REAL NOT NULL,
                last_used_at REAL NOT NULL, rejected_at REAL,
                PRIMARY KEY (principal, workspace))""")
            db.commit()
            yield db

    def _select(self, db, owner: str, workspace: str):
        return db.execute("SELECT * FROM student_keys WHERE principal=? AND workspace=?",
                          (owner, workspace)).fetchone()

    def row(self, owner: str, workspace: str) -> StudentKey | None:
        with self._db() as db:
            found = self._select(db, owner, workspace)
        return _key(found) if found else None

    def is_active(self, owner: str, workspace: str) -> bool:
        found = self.row(owner, workspace)
        return found is not None and found.rejected_at is None

    def insert(self, principal: Principal, key: str, key_hash: str, name: str) -> bool:
        """Store a new key; False when the principal already has one (the caller lost a race)."""
        sealed = self.vault.seal(key, principal_key=principal.key)
        now = self.clock()
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO student_keys (principal,workspace,sealed_key,key_hash,key_name,"
                "created_at,last_used_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (principal.key, principal.workspace_id, sealed, key_hash, name[:200], now, now))
            return cursor.rowcount == 1

    def key_for(self, principal: Principal) -> str | None:
        """The plaintext key, or None when there is none or it was rejected."""
        with self._db() as db:
            found = self._select(db, principal.key, principal.workspace_id)
        if found is None or found["rejected_at"] is not None:
            return None
        key = self.vault.open(found["sealed_key"], principal_key=principal.key)
        if self.vault.needs_rotation(found["sealed_key"]):
            self._update(principal, found["sealed_key"], sealed=self.vault.seal(key, principal_key=principal.key))
        if self.clock() - found["last_used_at"] >= TOUCH_EVERY_S:
            self._update(principal, found["sealed_key"], last_used_at=self.clock())
        return key

    def _update(self, principal: Principal, old_sealed: str, *, sealed: str | None = None,
                last_used_at: float | None = None) -> None:
        # Rotation moves rows to the newest vault key as they are read, so an old key
        # can be retired once every active row has been used once.
        with self._db() as db:
            if sealed is not None:
                db.execute("UPDATE student_keys SET sealed_key=? WHERE principal=? AND workspace=? "
                           "AND sealed_key=?", (sealed, principal.key, principal.workspace_id, old_sealed))
            if last_used_at is not None:
                db.execute("UPDATE student_keys SET last_used_at=? WHERE principal=? AND workspace=?",
                           (last_used_at, principal.key, principal.workspace_id))

    def mark_rejected(self, owner: str, workspace: str) -> None:
        with self._db() as db:
            db.execute("UPDATE student_keys SET rejected_at=? WHERE principal=? AND workspace=? "
                       "AND rejected_at IS NULL", (self.clock(), owner, workspace))

    def clear_rejected(self, owner: str, workspace: str) -> None:
        with self._db() as db:
            db.execute("UPDATE student_keys SET rejected_at=NULL WHERE principal=? AND workspace=?",
                       (owner, workspace))

    def remove(self, owner: str, workspace: str, key_hash: str) -> None:
        """Delete the row only if it still holds this key (a newer key is left alone)."""
        with self._db() as db:
            db.execute("DELETE FROM student_keys WHERE principal=? AND workspace=? AND key_hash=?",
                       (owner, workspace, key_hash))

    def idle(self, cutoff: float) -> list[StudentKey]:
        """Keys unused since `cutoff` whose principal has no advisor chats left there."""
        with self._db() as db:
            chats = db.has_table("advisor_chats")
            sql = "SELECT * FROM student_keys k WHERE k.last_used_at < ?"
            if chats:
                sql += (" AND NOT EXISTS (SELECT 1 FROM advisor_chats c WHERE c.owner = k.principal"
                        " AND c.workspace = k.workspace)")
            return [_key(row) for row in db.execute(sql + " ORDER BY k.principal", (cutoff,)).fetchall()]
