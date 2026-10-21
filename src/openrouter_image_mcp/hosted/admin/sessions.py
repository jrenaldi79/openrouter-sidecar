"""Admin sign-in state: pending Auth0 logins and signed-in sessions.

Both are stored by the SHA-256 of the secret the browser holds, so a database read never yields a usable
cookie or `state`. Clocks are injected so tests control expiry.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from ... import storage

IDLE_S = 7200
ABSOLUTE_S = 43200
LOGIN_S = 600
MAX_PENDING = 500

LOGINS_SCHEMA = """CREATE TABLE IF NOT EXISTS admin_logins (
    state_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, created_at REAL NOT NULL)"""
SESSIONS_SCHEMA = """CREATE TABLE IF NOT EXISTS admin_sessions (
    id_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, email TEXT NOT NULL, csrf_token TEXT NOT NULL,
    created_at REAL NOT NULL, last_seen_at REAL NOT NULL)"""


class TooManyLogins(Exception):
    """Too many unexpired sign-ins are waiting for their callback."""


@dataclass(frozen=True)
class Pending:
    state: str
    nonce: str
    verifier: str


@dataclass(frozen=True)
class Session:
    subject: str
    email: str
    csrf_token: str = field(repr=False)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class _Store:
    schema = ""

    def __init__(self, target, *, clock=time.time):
        self.target = target
        self.clock = clock

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute(self.schema)
            db.commit()
            yield db


class PendingLogins(_Store):
    """Sign-ins waiting for their callback. The `MAX_PENDING` cap is soft: the count and the insert are not one
    atomic step, so concurrent `create` calls can overshoot it by a few, which is acceptable for a flood guard."""

    schema = LOGINS_SCHEMA

    def create(self) -> Pending:
        pending = Pending(*(secrets.token_urlsafe(32) for _ in range(3)))
        now = self.clock()
        with self._db() as db:
            db.execute("DELETE FROM admin_logins WHERE created_at < ?", (now - LOGIN_S,))
            waiting = db.execute("SELECT COUNT(*) AS n FROM admin_logins").fetchone()["n"]
            if waiting >= MAX_PENDING:
                raise TooManyLogins("Too many sign-ins are in progress.")
            db.execute("INSERT INTO admin_logins (state_hash, nonce, verifier, created_at) VALUES (?, ?, ?, ?)",
                       (_hash(pending.state), pending.nonce, pending.verifier, now))
        return pending

    def take(self, state: str) -> Pending | None:
        key = _hash(state)
        with self._db() as db:
            found = db.execute("SELECT nonce, verifier, created_at FROM admin_logins WHERE state_hash=?",
                               (key,)).fetchone()
            # Only the caller whose delete removes the row gets the login, so a replay finds nothing.
            if found is None or db.execute("DELETE FROM admin_logins WHERE state_hash=?", (key,)).rowcount != 1:
                return None
        if self.clock() - found["created_at"] > LOGIN_S:
            return None
        return Pending(state, found["nonce"], found["verifier"])


class AdminSessions(_Store):
    schema = SESSIONS_SCHEMA

    def create(self, subject: str, email: str) -> tuple[str, Session]:
        raw = secrets.token_urlsafe(32)
        session = Session(subject, email, secrets.token_urlsafe(32))
        now = self.clock()
        with self._db() as db:
            db.execute("DELETE FROM admin_sessions WHERE last_seen_at < ? OR created_at < ?",
                       (now - IDLE_S, now - ABSOLUTE_S))
            db.execute("INSERT INTO admin_sessions (id_hash, subject, email, csrf_token, created_at, last_seen_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (_hash(raw), subject, email, session.csrf_token, now, now))
        return raw, session

    def get(self, raw_id: str) -> Session | None:
        key, now = _hash(raw_id), self.clock()
        with self._db() as db:
            found = db.execute("SELECT * FROM admin_sessions WHERE id_hash=?", (key,)).fetchone()
            if found is None:
                return None
            if now - found["last_seen_at"] > IDLE_S or now - found["created_at"] > ABSOLUTE_S:
                db.execute("DELETE FROM admin_sessions WHERE id_hash=?", (key,))
                return None
            db.execute("UPDATE admin_sessions SET last_seen_at=? WHERE id_hash=?", (now, key))
        return Session(found["subject"], found["email"], found["csrf_token"])

    def delete(self, raw_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM admin_sessions WHERE id_hash=?", (_hash(raw_id),))
