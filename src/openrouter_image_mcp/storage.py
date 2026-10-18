"""One small database seam: the stores run on SQLite (local) or Postgres (hosted).

Stores keep writing portable SQL with ``?`` placeholders. For Postgres this module
translates the few SQLite-specific pieces they rely on:

- ``BEGIN IMMEDIATE`` (SQLite's single-writer lock) becomes a transaction-scoped
  advisory lock, so mutating transactions keep the same serialized semantics.
- ``rowid`` insertion order becomes a real ``BIGSERIAL`` column named ``rowid``.
- ``REAL`` maps to ``DOUBLE PRECISION``; Python booleans bind as integers.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DB_ERRORS = (sqlite3.Error, psycopg.Error)
_WRITE_LOCK = 0x5ADEC0  # one global writer lock, matching SQLite semantics
_CREATE = re.compile(r"^\s*CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", re.IGNORECASE)
_REAL = re.compile(r"\bREAL\b")
_schema_done: set[tuple[str, str]] = set()
_schema_lock = threading.Lock()


def is_postgres(target) -> bool:
    return isinstance(target, str) and target.startswith(("postgresql://", "postgres://"))


def _translate(sql: str) -> str:
    if "%" in sql:
        raise ValueError("Portable SQL must not contain '%'.")
    sql = _CREATE.sub(lambda m: f"CREATE TABLE IF NOT EXISTS {m.group(1)} (rowid BIGSERIAL, ", sql)
    return _REAL.sub("DOUBLE PRECISION", sql).replace("?", "%s")


def _row_without_rowid(cursor):
    make = dict_row(cursor)

    def row(values):
        result = make(values)
        result.pop("rowid", None)
        return result
    return row


class _Postgres:
    def __init__(self, url: str):
        self.url = url
        self.connection = psycopg.connect(url, row_factory=_row_without_rowid)

    def execute(self, sql: str, params: tuple = ()):
        text = sql.strip()
        if text.upper() == "BEGIN IMMEDIATE":
            return self.connection.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK,))
        if text.upper().startswith("CREATE"):
            return self._schema(text)
        values = tuple(int(value) if isinstance(value, bool) else value for value in params)
        return self.connection.execute(_translate(text), values)

    def _schema(self, text: str):
        key = (self.url, text)
        with _schema_lock:
            if key in _schema_done:
                return None
        # Concurrent first-time DDL races on the catalog; serialize it.
        self.connection.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK,))
        cursor = self.connection.execute(_translate(text))
        with _schema_lock:
            _schema_done.add(key)
        return cursor

    def has_table(self, name: str) -> bool:
        row = self.connection.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()
        return row["t"] is not None

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


class _Sqlite:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple = ()):
        return self.connection.execute(sql, params)

    def has_table(self, name: str) -> bool:
        return self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                       "AND name=?", (name,)).fetchone() is not None

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def connect(target):
    """Commit on success, roll back on error, always close (like ``with sqlite3``)."""
    db = _Postgres(target) if is_postgres(target) else _Sqlite(Path(target))
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
