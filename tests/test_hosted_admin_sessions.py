import hashlib

import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.hosted.admin import sessions
from openrouter_image_mcp.hosted.admin.sessions import (
    AdminSessions,
    PendingLogins,
    TooManyLogins,
)


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _count(target, table: str) -> int:
    with storage.connect(target) as db:
        return db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_pending_login_single_use(db_target):
    logins = PendingLogins(db_target, clock=Clock())
    pending = logins.create()
    assert len({pending.state, pending.nonce, pending.verifier}) == 3
    assert logins.take(pending.state) == pending
    assert logins.take(pending.state) is None
    assert logins.take("unknown") is None


def test_pending_login_expires_after_10_minutes(db_target):
    clock = Clock()
    logins = PendingLogins(db_target, clock=clock)
    fresh, stale = logins.create(), logins.create()
    clock.now += sessions.LOGIN_S - 1
    assert logins.take(fresh.state) == fresh
    clock.now += 2
    assert logins.take(stale.state) is None


def test_create_purges_expired_logins(db_target):
    clock = Clock()
    logins = PendingLogins(db_target, clock=clock)
    logins.create()
    logins.create()
    clock.now += sessions.LOGIN_S + 1
    logins.create()
    assert _count(db_target, "admin_logins") == 1


def test_pending_cap(db_target):
    clock = Clock()
    logins = PendingLogins(db_target, clock=clock)
    with storage.connect(db_target) as db:
        db.execute(sessions.LOGINS_SCHEMA)
        for i in range(sessions.MAX_PENDING):
            db.execute("INSERT INTO admin_logins (state_hash, nonce, verifier, created_at) VALUES (?, ?, ?, ?)",
                       (f"h{i}", "n", "v", clock.now))
    with pytest.raises(TooManyLogins):
        logins.create()
    clock.now += sessions.LOGIN_S + 1  # expired rows don't count
    assert logins.create().state


def test_session_idle_expiry(db_target):
    clock = Clock()
    store = AdminSessions(db_target, clock=clock)
    raw, _ = store.create("auth0|a", "a@example.edu")
    clock.now += sessions.IDLE_S - 1
    assert store.get(raw) is not None
    clock.now += sessions.IDLE_S + 1
    assert store.get(raw) is None


def test_session_absolute_expiry_even_when_active(db_target):
    clock = Clock()
    start = clock.now
    store = AdminSessions(db_target, clock=clock)
    raw, _ = store.create("auth0|a", "a@example.edu")
    step = sessions.IDLE_S // 2
    while clock.now + step < start + sessions.ABSOLUTE_S:
        clock.now += step
        assert store.get(raw) is not None
    clock.now = start + sessions.ABSOLUTE_S + 1
    assert store.get(raw) is None


def test_get_moves_last_seen(db_target):
    clock = Clock()
    store = AdminSessions(db_target, clock=clock)
    raw, created = store.create("auth0|a", "a@example.edu")
    clock.now += sessions.IDLE_S - 10
    assert store.get(raw) == created
    clock.now += sessions.IDLE_S - 10  # past idle from creation, inside idle from the last get
    assert store.get(raw) == created


def test_raw_id_not_stored(db_target):
    store = AdminSessions(db_target, clock=Clock())
    raw, session = store.create("auth0|a", "a@example.edu")
    assert len(session.csrf_token) >= 40
    with storage.connect(db_target) as db:
        row = db.execute("SELECT * FROM admin_sessions").fetchone()
    assert row["id_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in [str(v) for v in dict(row).values()]


def test_create_purges_expired_sessions(db_target):
    clock = Clock()
    store = AdminSessions(db_target, clock=clock)
    store.create("auth0|a", "a@example.edu")
    store.create("auth0|b", "b@example.edu")
    clock.now += sessions.IDLE_S + 1
    store.create("auth0|c", "c@example.edu")
    assert _count(db_target, "admin_sessions") == 1


def test_delete(db_target):
    store = AdminSessions(db_target, clock=Clock())
    raw, _ = store.create("auth0|a", "a@example.edu")
    store.delete(raw)
    assert store.get(raw) is None
    store.delete(raw)  # deleting an unknown session is harmless
    assert store.get("nope") is None
