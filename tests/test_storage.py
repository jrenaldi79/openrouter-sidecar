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
