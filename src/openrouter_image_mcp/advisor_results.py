"""Owned durable job records and server-selected Markdown export paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from .advisor_store import OPERATION_LEASE_S, AdvisorAccessError
from .errors import BadRequestError, ProviderError

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
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS advisor_active_job ON "
                       "advisor_jobs(chat_id) WHERE status IN ('queued','running')")
            db.commit()
            yield db

    @staticmethod
    def status(row):
        return {"job_id": row["id"]} | {key: bool(row[key]) if key == "cleanup_pending"
                                            else row[key] for key in STATUS_FIELDS}

    def _recover(self, db, scope):
        rows = db.execute("SELECT * FROM advisor_jobs WHERE owner=? AND workspace=? "
                          "AND status IN ('queued','running') AND heartbeat_until<=?",
                          (scope.owner, scope.workspace, time.time())).fetchall()
        for row in rows:
            chat = self.store._row(db, scope, row["chat_id"])
            pending = bool(json.loads(chat["uploads"]))
            db.execute("UPDATE advisor_jobs SET status='interrupted', error=?,cleanup_pending=? WHERE id=?",
                       (("Advisor work was interrupted after its server heartbeat expired; "
                         "no automatic retry was made."), pending, row["id"]))
            self._release(db, scope, row)

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
            db.execute("BEGIN IMMEDIATE")
            self._recover(db, scope)
            return dict(self._row(db, scope, job_id))

    def for_chat(self, scope, chat_id, limit):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            chat = self.store._row(db, scope, chat_id)
            self._recover(db, scope)
            rows = db.execute("SELECT * FROM advisor_jobs WHERE owner=? AND workspace=? "
                              "AND chat_id=? ORDER BY rowid DESC LIMIT ?",
                              (scope.owner, scope.workspace, chat_id, limit)).fetchall()
            pending = bool(json.loads(chat["uploads"]))
            return [self.status(dict(row) | {"cleanup_pending": pending}) for row in rows]

    def create(self, scope, chat, prompt, process_id, *, kind="message", source_job_id=None):
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
                       (job_id, scope.owner, scope.workspace, chat.id, chat.model, chat.title,
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

    def interrupt(self, scope, job_id, process_id, *, error, suppress=False, cleanup_pending=False):
        """Shutdown/account-change bookkeeping uses captured ownership, no provider I/O."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, job_id)
            if row["process_id"] == process_id and row["status"] in ACTIVE:
                db.execute("UPDATE advisor_jobs SET status='interrupted',error=?,answer=?,"
                           "cleanup_pending=? WHERE id=?",
                           (error, "" if suppress else row["answer"], cleanup_pending, job_id))
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

    def export(self, scope, row):
        if row["status"] in ACTIVE:
            raise BadRequestError("Wait for advisor work to finish before exporting.")
        if not re.fullmatch(r"job_[0-9a-f]{32}", row["id"]):
            raise ProviderError("Advisor export rejected an invalid result identifier.")
        if self.store.path is None:
            raise BadRequestError("Saving results to this computer is unavailable on the hosted server.")
        base = self.store.path.absolute().parent
        # A single compact owner/workspace hash also fits Windows MAX_PATH with
        # the default DB directory (including longer test/application roots).
        directory_id = hashlib.sha256((scope.owner + "\0" + scope.workspace).encode()).hexdigest()[:32]
        destination = base / "advisor-exports" / directory_id

        def reject_link(path):
            try:
                info = path.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ProviderError("Advisor export refused a linked path.")

        # Never follow a symlink/junction in existing export ancestors, including
        # the configured DB parent. Creation stays on this server-selected path.
        for ancestor in reversed([destination, *destination.parents]):
            reject_link(ancestor)
            if not ancestor.exists():
                ancestor.mkdir()
            reject_link(ancestor)
        path = destination / (row["id"] + ".md")
        reject_link(path)
        if path.exists():
            if not path.is_file():
                raise ProviderError("Advisor export refused an invalid destination.")
            # Durable exports are immutable/idempotent and never overwrite files.
            return {"job_id": row["id"], "path": str(path), "exported": True}
        label = "Completed" if row["status"] == "completed" else "Partial / " + row["status"]
        text = (f"# Advisor result\n\nModel: {row['model']}\n\nResult: {row['id']}\n\n"
                f"Status: {label}\n\nCreated: {row['created_at']}\n\n"
                f"## Original prompt\n\n{row['prompt']}\n\n## Answer\n\n{row['answer']}\n")
        # Fully flush a private temporary file, then publish with an atomic,
        # non-overwriting hard link. A failed write cannot leave an apparently
        # successful immutable export. Temporary names stay short on Windows.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temporary = destination / ("tmp_" + secrets.token_hex(8) + ".tmp")
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            reject_link(path)
            try:
                os.link(temporary, path)
            except FileExistsError:
                reject_link(path)
                if not path.is_file():
                    raise ProviderError("Advisor export refused an invalid destination.") from None
        except OSError:
            raise ProviderError("Advisor export could not be saved locally.") from None
        finally:
            temporary.unlink(missing_ok=True)
        return {"job_id": row["id"], "path": str(path), "exported": True}
