"""The storage seam behaves the same on SQLite and Postgres."""

import threading
import time

import pytest

from openrouter_image_mcp import storage

DDL = """CREATE TABLE IF NOT EXISTS things (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, score REAL, flag INTEGER NOT NULL DEFAULT 0
)"""


def _seed(target):
    with storage.connect(target) as db:
        db.execute(DDL)
        db.commit()
        for index in range(3):
            db.execute("INSERT INTO things (id, owner, score, flag) VALUES (?,?,?,?)",
                       (f"id{index}", "a", index + 0.5, index == 1))


def test_placeholders_rows_and_booleans(db_target):
    _seed(db_target)
    with storage.connect(db_target) as db:
        row = db.execute("SELECT * FROM things WHERE id=?", ("id1",)).fetchone()
        assert row["owner"] == "a" and row["score"] == 1.5 and row["flag"] == 1
        assert "rowid" not in dict(row)
        assert db.has_table("things") and not db.has_table("missing")


def test_insertion_order_via_rowid(db_target):
    _seed(db_target)
    with storage.connect(db_target) as db:
        rows = db.execute("SELECT id FROM things ORDER BY rowid DESC").fetchall()
    assert [row["id"] for row in rows] == ["id2", "id1", "id0"]


def test_rowcount_and_rollback_on_error(db_target):
    _seed(db_target)
    with pytest.raises(RuntimeError), storage.connect(db_target) as db:
        changed = db.execute("UPDATE things SET owner=? WHERE owner=?", ("b", "a")).rowcount
        assert changed == 3
        raise RuntimeError("abort")
    with storage.connect(db_target) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM things WHERE owner='a'").fetchone()["n"] == 3


def test_begin_immediate_serializes_writers(db_target):
    _seed(db_target)
    order = []

    def writer(name, hold):
        with storage.connect(db_target) as db:
            db.execute("BEGIN IMMEDIATE")
            order.append(name + "-start")
            time.sleep(hold)
            db.execute("UPDATE things SET owner=? WHERE id='id0'", (name,))
            order.append(name + "-end")

    first = threading.Thread(target=writer, args=("w1", 0.3))
    first.start()
    time.sleep(0.1)
    second = threading.Thread(target=writer, args=("w2", 0))
    second.start()
    first.join()
    second.join()
    assert order == ["w1-start", "w1-end", "w2-start", "w2-end"]


def test_target_describes_backend(tmp_path):
    assert storage.is_postgres("postgresql://u@h/db")
    assert not storage.is_postgres(tmp_path / "x.sqlite3")


def test_database_errors_tuple_covers_both_backends():
    import sqlite3

    import psycopg

    assert issubclass(sqlite3.OperationalError, storage.DB_ERRORS)
    assert issubclass(psycopg.OperationalError, storage.DB_ERRORS)


def _postgres_only(target):
    if not storage.is_postgres(target):
        pytest.skip("connection pooling is Postgres-only; SQLite opens a local file")


def _backend(db):
    return db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]


def _backends(target, calls):
    pids = []
    for _ in range(calls):
        with storage.connect(target) as db:
            pids.append(_backend(db))
    return pids


def test_postgres_reuses_sessions_across_calls(db_target):
    _postgres_only(db_target)
    # Sequential calls share a couple of pooled sessions, not a fresh connect + TLS each.
    assert len(set(_backends(db_target, 20))) <= 2


def test_postgres_nested_calls_get_their_own_sessions(db_target):
    _postgres_only(db_target)
    with storage.connect(db_target) as outer, storage.connect(db_target) as inner:
        assert _backend(outer) != _backend(inner)


def test_postgres_reused_session_starts_clean_after_an_error(db_target):
    _postgres_only(db_target)
    _seed(db_target)
    with pytest.raises(RuntimeError), storage.connect(db_target) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE things SET owner='b'")
        failed = _backend(db)
        raise RuntimeError("abort")
    reused = False
    for _ in range(4):  # the pool rotates its idle sessions; the failed one comes round
        with storage.connect(db_target) as db:
            reused = reused or _backend(db) == failed
            assert db.execute("SELECT COUNT(*) AS n FROM things WHERE owner='a'").fetchone()["n"] == 3
            # The aborted transaction's write lock went with its rollback.
            assert db.execute("SELECT pg_try_advisory_xact_lock(?) AS ok",
                              (storage._WRITE_LOCK,)).fetchone()["ok"]
    assert reused


def test_closing_a_pool_releases_its_sessions(db_target):
    _postgres_only(db_target)
    before = set(_backends(db_target, 4))
    storage.close_pool(db_target)
    assert not before & set(_backends(db_target, 4))


def test_postgres_schema_counts_as_done_only_once_committed(db_target):
    # Another session must not skip CREATE TABLE (and then hit UndefinedTable) while
    # the session that ran it has not committed yet, or after it rolled back.
    _postgres_only(db_target)
    key = (db_target, DDL.strip())
    with pytest.raises(RuntimeError), storage.connect(db_target) as db:
        db.execute(DDL)
        assert key not in storage._schema_done
        raise RuntimeError("roll back")
    assert key not in storage._schema_done
    with storage.connect(db_target) as db:
        db.execute(DDL)
        assert key not in storage._schema_done
        db.commit()
        assert key in storage._schema_done
