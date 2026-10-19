"""Owned durable advisor job records."""

from __future__ import annotations

import json
import secrets
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from .advisor_store import OPERATION_LEASE_S, AdvisorAccessError

ACTIVE = ("queued", "running")
HEARTBEAT_S = 60
STATUS_FIELDS = ("chat_id", "model", "title", "status", "cost_usd", "cleanup_pending",
                 "error", "kind", "source_job_id")


class AdvisorResults:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def connection(self):
        with self.store._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS advisor_jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                chat_id TEXT NOT NULL, model TEXT NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, prompt TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                cost_usd REAL, cleanup_pending INTEGER NOT NULL DEFAULT 0, error TEXT,
                kind TEXT NOT NULL, source_job_id TEXT, created_at TEXT NOT NULL,
                process_id TEXT NOT NULL, lease_token TEXT NOT NULL,
                heartbeat_until REAL NOT NULL
            )""")
            # Shared with Claude from the viewer (advisor_shares.py).
            for column in ("shared_kind", "shared_summary_id", "shared_at", "share_read_at"):
                db.ensure_column("advisor_jobs", column, "TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS advisor_active_job ON "
                       "advisor_jobs(chat_id) WHERE status IN ('queued','running')")
            db.commit()
            yield db

    @staticmethod
    def status(row):
        return {"job_id": row["id"]} | {key: bool(row[key]) if key == "cleanup_pending"
                                            else row[key] for key in STATUS_FIELDS}

    @staticmethod
    def _stale(db, scope):
        return db.execute("SELECT * FROM advisor_jobs WHERE owner=? AND workspace=? "
                          "AND status IN ('queued','running') AND heartbeat_until<=?",
                          (scope.owner, scope.workspace, time.time())).fetchall()

    def _recover(self, db, scope):
        """Interrupt lapsed jobs; the caller holds the write lock."""
        for row in self._stale(db, scope):
            chat = self.store._row(db, scope, row["chat_id"])
            pending = bool(json.loads(chat["uploads"]))
            db.execute("UPDATE advisor_jobs SET status='interrupted', error=?,cleanup_pending=? WHERE id=?",
                       (("Advisor work was interrupted after its server heartbeat expired; "
                         "no automatic retry was made."), pending, row["id"]))
            self._release(db, scope, row)

    def _recover_for_read(self, db, scope):
        # Polls arrive every few seconds per open viewer. Only a lapsed job needs a
        # write, so only then take the (on Postgres, server-wide) write lock.
        if self._stale(db, scope):
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, scope)

    @staticmethod
    def _release(db, scope, row):
        db.execute("UPDATE advisor_chats SET lease_token=NULL,lease_until=0 "
                   "WHERE id=? AND owner=? AND workspace=? AND lease_token=?",
                   (row["chat_id"], scope.owner, scope.workspace, row["lease_token"]))

    def _row(self, db, scope, job_id):
        row = db.execute("SELECT * FROM advisor_jobs WHERE id=? AND owner=? AND workspace=?",
                         (job_id, scope.owner, scope.workspace)).fetchone()
        if row is None:
            raise AdvisorAccessError("Advisor result is unavailable for this sign-in.")
        # Deleted chats never leave authorized result handles behind.
        chat = self.store._row(db, scope, row["chat_id"])
        # Receipts are authoritative even after an explicit cleanup of old
        # results. Do not leave the viewer showing an obsolete cleanup flag.
        return dict(row) | {"cleanup_pending": bool(json.loads(chat["uploads"]))}

    def get(self, scope, job_id):
        with self.connection() as db:
            self._recover_for_read(db, scope)
            return dict(self._row(db, scope, job_id))

    def for_chat(self, scope, chat_id, limit):
        with self.connection() as db:
            chat = self.store._row(db, scope, chat_id)
            self._recover_for_read(db, scope)
            rows = db.execute("SELECT * FROM advisor_jobs WHERE owner=? AND workspace=? "
                              "AND chat_id=? ORDER BY rowid DESC LIMIT ?",
                              (scope.owner, scope.workspace, chat_id, limit)).fetchall()
            pending = bool(json.loads(chat["uploads"]))
            return [self.status(dict(row) | {"cleanup_pending": pending}) for row in rows]

    def active_for_chat(self, scope, chat_id):
        with self.connection() as db:
            self.store._row(db, scope, chat_id)
            self._recover_for_read(db, scope)
            row = db.execute("SELECT id FROM advisor_jobs WHERE owner=? AND workspace=? AND chat_id=? "
                             "AND status IN ('queued','running')",
                             (scope.owner, scope.workspace, chat_id)).fetchone()
            return row["id"] if row else None

    def create(self, scope, chat, prompt, process_id, *, kind="message", source_job_id=None, model=None):
        job_id, token = "job_" + secrets.token_hex(16), secrets.token_hex(16)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, scope)
            row = self.store._row(db, scope, chat.id)
            self.store._idle(row)
            if db.execute("SELECT id FROM advisor_jobs WHERE chat_id=? AND status IN "
                          "('queued','running')", (chat.id,)).fetchone():
                raise AdvisorAccessError("Advisor chat is busy; wait for the active operation.")
            db.execute("UPDATE advisor_chats SET lease_token=?,lease_until=? WHERE id=?",
                       (token, time.time() + OPERATION_LEASE_S, chat.id))
            db.execute("INSERT INTO advisor_jobs (id,owner,workspace,chat_id,model,title,status,"
                       "prompt,kind,source_job_id,created_at,process_id,lease_token,heartbeat_until) "
                       "VALUES (?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)",
                       (job_id, scope.owner, scope.workspace, chat.id, model or chat.model, chat.title,
                        prompt, kind, source_job_id, datetime.now(UTC).isoformat(),
                        process_id, token, time.time() + HEARTBEAT_S))
            return dict(self._row(db, scope, job_id))

    def guard(self, scope, job_id, process_id):
        with self.connection() as db:
            row = self._row(db, scope, job_id)
            if (row["status"] not in ACTIVE or row["process_id"] != process_id
                    or row["heartbeat_until"] <= time.time()):
                raise AdvisorAccessError("Advisor work is unavailable for this operation.")
            self.store._leased(self.store._row(db, scope, row["chat_id"]), row["lease_token"])

    def update(self, scope, job_id, process_id, **fields):
        allowed = {"status", "answer", "cost_usd", "cleanup_pending", "error", "heartbeat_until"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid internal job update")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, job_id)
            if (row["process_id"] != process_id or row["status"] not in ACTIVE
                    or row["heartbeat_until"] <= time.time()):
                raise AdvisorAccessError("Advisor work is unavailable for this operation.")
            self.store._leased(self.store._row(db, scope, row["chat_id"]), row["lease_token"])
            db.execute("UPDATE advisor_jobs SET " + ",".join(name + "=?" for name in fields)
                       + " WHERE id=? AND owner=? AND workspace=? AND process_id=?",
                       (*fields.values(), job_id, scope.owner, scope.workspace, process_id))
            if fields.get("status") not in (None, *ACTIVE):
                self._release(db, scope, row)

    def interrupt(self, scope, job_id, process_id, *, error, suppress=False, cleanup_pending=False,
                  answer=None):
        """Shutdown/account-change bookkeeping uses captured ownership, no provider I/O.

        ``answer`` is the newest streamed text when it is ahead of the saved row.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, job_id)
            if row["process_id"] == process_id and row["status"] in ACTIVE:
                db.execute("UPDATE advisor_jobs SET status='interrupted',error=?,answer=?,"
                           "cleanup_pending=? WHERE id=?",
                           (error, "" if suppress else row["answer"] if answer is None else answer,
                            cleanup_pending, job_id))
                self._release(db, scope, row)

    def delete_chat(self, scope, chat_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, scope)
            row = self.store._row(db, scope, chat_id)
            self.store._idle(row)
            if self.store.pending_uploads(scope, chat_id):
                raise AdvisorAccessError("Skill upload cleanup is pending; retry cleanup before deleting.")
            db.execute("DELETE FROM advisor_jobs WHERE chat_id=? AND owner=? AND workspace=?",
                       (chat_id, scope.owner, scope.workspace))
            db.execute("DELETE FROM advisor_chats WHERE id=? AND owner=? AND workspace=?",
                       (chat_id, scope.owner, scope.workspace))
