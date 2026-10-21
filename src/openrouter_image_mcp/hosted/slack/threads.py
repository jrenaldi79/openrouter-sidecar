"""Thread map: which advisor chat answers a Slack thread, per model alias and mode.

Mode is part of the key, so a thread can hold an inline chat and a sandbox chat for the same model.
Each row also remembers the newest Slack message that chat has seen, so a follow-up sends only what is
new. Every query binds the team principal's owner and workspace.
"""

from __future__ import annotations

import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from ... import storage
from ...advisor_store import Scope

Mode = Literal["inline", "sandbox"]
_SCHEMA = """CREATE TABLE IF NOT EXISTS slack_threads (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL, channel_id TEXT NOT NULL,
    thread_ts TEXT NOT NULL, alias TEXT NOT NULL, mode TEXT NOT NULL, chat_id TEXT NOT NULL,
    last_seen_ts TEXT, last_answered_at REAL, archived_at REAL, created_at REAL NOT NULL)"""
_ACTIVE_KEY = """CREATE UNIQUE INDEX IF NOT EXISTS slack_threads_active_key ON slack_threads
    (owner, workspace, channel_id, thread_ts, alias, mode) WHERE archived_at IS NULL"""
_SCOPED = "owner=? AND workspace=?"
_KEY = f"{_SCOPED} AND channel_id=? AND thread_ts=? AND alias=? AND mode=? AND archived_at IS NULL"
_CHAT = "SELECT chat_id, alias, mode, last_seen_ts FROM slack_threads"


@dataclass(frozen=True)
class ThreadChat:
    chat_id: str
    alias: str
    mode: Mode
    last_seen_ts: str | None


def _chat(found) -> ThreadChat:
    return ThreadChat(found["chat_id"], found["alias"], found["mode"], found["last_seen_ts"])


def _ts_value(ts: str | None) -> Decimal:
    """Slack timestamps order by numeric value; an unparseable one sorts before everything."""
    try:
        return Decimal(ts) if ts else Decimal(-1)
    except InvalidOperation:
        return Decimal(-1)


class ThreadMap:
    def __init__(self, target):
        self.target = target
        self.clock = time.time

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute(_SCHEMA)
            db.execute(_ACTIVE_KEY)
            db.commit()
            yield db

    def get(self, scope: Scope, channel_id: str, thread_ts: str, alias: str, mode: Mode) -> ThreadChat | None:
        with self._db() as db:
            found = db.execute(f"{_CHAT} WHERE {_KEY}",
                               (scope.owner, scope.workspace, channel_id, thread_ts, alias, mode)).fetchone()
        return _chat(found) if found else None

    def put(self, scope: Scope, channel_id: str, thread_ts: str, alias: str, mode: Mode, chat_id: str) -> None:
        """Link the key to a fresh chat; an unarchived row for the same key is replaced, not duplicated."""
        key = (scope.owner, scope.workspace, channel_id, thread_ts, alias, mode)
        with self._db() as db:
            for _ in range(2):  # a concurrent put can win the insert; its row is then ours to replace
                cursor = db.execute(f"UPDATE slack_threads SET chat_id=?, last_seen_ts=NULL, "
                                    f"last_answered_at=NULL WHERE {_KEY}", (chat_id, *key))
                if cursor.rowcount == 1:
                    return
                cursor = db.execute(
                    "INSERT INTO slack_threads (id, owner, workspace, channel_id, thread_ts, alias, mode, "
                    "chat_id, created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    ("thr_" + secrets.token_hex(16), *key, chat_id, self.clock()))
                if cursor.rowcount == 1:
                    return
        raise RuntimeError("Could not link the thread to its chat; the mapping was not saved.")

    def seen(self, scope: Scope, chat_id: str, ts: str) -> None:
        """Advance the newest message the chat has seen; never move it back."""
        where = f"{_SCOPED} AND chat_id=? AND archived_at IS NULL"
        with self._db() as db:
            found = db.execute(f"SELECT last_seen_ts FROM slack_threads WHERE {where}",
                               (scope.owner, scope.workspace, chat_id)).fetchone()
            if found is None or _ts_value(ts) <= _ts_value(found["last_seen_ts"]):
                return
            # Guarded on the value just read, so a concurrent newer ts is never overwritten. NULL and a
            # value are separate statements: Postgres rejects a bound parameter after IS.
            previous = found["last_seen_ts"]
            guard, extra = ("last_seen_ts IS NULL", ()) if previous is None else ("last_seen_ts=?", (previous,))
            db.execute(f"UPDATE slack_threads SET last_seen_ts=? WHERE {where} AND {guard}",
                       (ts, scope.owner, scope.workspace, chat_id, *extra))

    def answered(self, scope: Scope, chat_id: str) -> None:
        with self._db() as db:
            db.execute(f"UPDATE slack_threads SET last_answered_at=? WHERE {_SCOPED} AND chat_id=? "
                       "AND archived_at IS NULL", (self.clock(), scope.owner, scope.workspace, chat_id))

    def last_answered(self, scope: Scope, channel_id: str, thread_ts: str) -> ThreadChat | None:
        with self._db() as db:
            found = db.execute(
                f"{_CHAT} WHERE {_SCOPED} AND channel_id=? AND thread_ts=? AND archived_at IS NULL "
                "AND last_answered_at IS NOT NULL ORDER BY last_answered_at DESC, rowid DESC LIMIT 1",
                (scope.owner, scope.workspace, channel_id, thread_ts)).fetchone()
        return _chat(found) if found else None

    def archive(self, scope: Scope, channel_id: str, thread_ts: str) -> int:
        with self._db() as db:
            cursor = db.execute(
                f"UPDATE slack_threads SET archived_at=? WHERE {_SCOPED} AND channel_id=? AND thread_ts=? "
                "AND archived_at IS NULL", (self.clock(), scope.owner, scope.workspace, channel_id, thread_ts))
            return cursor.rowcount

    def chats_in(self, scope: Scope, channel_id: str, thread_ts: str | None = None) -> list[ThreadChat]:
        where = f"{_SCOPED} AND channel_id=? AND archived_at IS NULL"
        args = (scope.owner, scope.workspace, channel_id)
        if thread_ts is not None:
            where, args = where + " AND thread_ts=?", (*args, thread_ts)
        with self._db() as db:
            found = db.execute(f"{_CHAT} WHERE {where} ORDER BY created_at, rowid", args).fetchall()
        return [_chat(r) for r in found]
