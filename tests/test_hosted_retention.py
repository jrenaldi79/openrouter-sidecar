"""Retention purge: old idle chats go (with jobs and files); active and legacy chats stay."""

import time

import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.retention import purge
from openrouter_image_mcp.session_files import FilePacket, SessionFiles

ALICE = Scope("iss|alice", "ws-1")
DAY = 86400


@pytest.fixture
def store(db_target):
    return AdvisorStore(db_target)


def age(store, chat_id, days):
    with storage.connect(store.target) as db:
        db.execute("UPDATE advisor_chats SET last_active=? WHERE id=?", (time.time() - days * DAY, chat_id))


def ids(store):
    with storage.connect(store.target) as db:
        return {row["id"] for row in db.execute("SELECT id FROM advisor_chats").fetchall()}


def with_job(store, chat, status="completed"):
    results = AdvisorResults(store)
    row = results.create(ALICE, chat, "brief", "proc")
    with storage.connect(store.target) as db:
        db.execute("UPDATE advisor_jobs SET status=? WHERE id=?", (status, row["id"]))
        if status not in ("queued", "running"):
            db.execute("UPDATE advisor_chats SET lease_token=NULL, lease_until=0 WHERE id=?", (chat.id,))
    return row["id"]


def test_old_idle_chat_is_deleted_with_jobs_and_files(store):
    old = store.create(ALICE, "m", "old")
    with_job(store, old)
    SessionFiles(store).upsert(ALICE, old.id, [FilePacket(path="a.py", content="x")])
    recent = store.create(ALICE, "m", "recent")
    age(store, old.id, 91)
    report = purge(store.target, days=90, apply=True)
    assert report == {"chats": 1, "jobs": 1, "session_files": 1, "link_attempts": 0,
                      "stamped": 0, "applied": True, "retention_days": 90}
    assert ids(store) == {recent.id}
    with storage.connect(store.target) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM advisor_jobs").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM session_files").fetchone()["n"] == 0


def test_dry_run_counts_without_deleting(store):
    old = store.create(ALICE, "m", "old")
    age(store, old.id, 120)
    report = purge(store.target, days=90)
    assert report["chats"] == 1 and report["applied"] is False
    assert ids(store) == {old.id}


def test_chats_without_activity_stamp_are_stamped_not_deleted(store):
    legacy = store.create(ALICE, "m", "legacy")
    with storage.connect(store.target) as db:
        db.execute("UPDATE advisor_chats SET last_active=0 WHERE id=?", (legacy.id,))
    report = purge(store.target, days=90, apply=True)
    assert report["stamped"] == 1 and report["chats"] == 0
    assert ids(store) == {legacy.id}


def test_busy_or_cleanup_pending_chats_are_kept(store):
    busy = store.create(ALICE, "m", "busy")
    with_job(store, busy, status="running")
    pending = store.create(ALICE, "m", "pending")
    with storage.connect(store.target) as db:
        db.execute("UPDATE advisor_chats SET uploads='[\"or_file_1\"]' WHERE id=?", (pending.id,))
    for chat in (busy, pending):
        age(store, chat.id, 200)
    assert purge(store.target, days=90, apply=True)["chats"] == 0
    assert ids(store) == {busy.id, pending.id}


def test_deletes_in_bounded_batches(store):
    chats = [store.create(ALICE, "m", f"c{i}") for i in range(5)]
    for chat in chats:
        age(store, chat.id, 100)
    assert purge(store.target, days=90, apply=True, batch=2)["chats"] == 5
    assert ids(store) == set()


def test_new_turns_refresh_activity(store):
    chat = store.create(ALICE, "m", "c")
    age(store, chat.id, 100)
    with store.lease(ALICE, chat.id) as token:
        store.append_turn(ALICE, chat.id, "q", "a", token)
    assert purge(store.target, days=90, apply=True)["chats"] == 0


def test_expired_link_attempts_are_deleted(store):
    from openrouter_image_mcp.hosted.links import LinkStore
    from openrouter_image_mcp.hosted.vault import Vault

    links = LinkStore(store.target, Vault("v1:" + "A" * 43 + "="), "k" * 32, "http://127.0.0.1:1")
    with links._db() as db:
        for attempt_id, expires in (("old", time.time() - 2 * DAY), ("live", time.time() + 60)):
            db.execute("INSERT INTO link_attempts (id,principal,workspace,verifier,expires_at) "
                       "VALUES (?,?,?,?,?)", (attempt_id, "p", "w", "v", expires))
    assert purge(store.target, days=90, apply=True)["link_attempts"] == 1


def test_empty_database_is_fine(tmp_path):
    report = purge(tmp_path / "fresh.sqlite3", days=90, apply=True)
    assert report["chats"] == 0 and report["applied"] is True


def test_rejects_unsafe_retention():
    with pytest.raises(ValueError):
        purge("unused", days=0)


def test_active_job_count(store):
    from openrouter_image_mcp.hosted.retention import active_jobs

    assert active_jobs(store.target) == 0
    with_job(store, store.create(ALICE, "m", "a"), status="running")
    with_job(store, store.create(ALICE, "m", "b"), status="completed")
    assert active_jobs(store.target) == 1
