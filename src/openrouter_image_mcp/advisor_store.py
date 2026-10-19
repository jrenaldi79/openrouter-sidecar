"""Local advisor state. Every query binds both credential owner and workspace.

This is an application boundary, not protection from another process running as
the same OS user. The database contains briefs and final answers, never API keys.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import storage
from .config import ADVISOR_MAX_TIMEOUT_S
from .errors import OpenRouterError

# Cover inference plus bounded upload/cleanup and local transcript persistence.
OPERATION_LEASE_S = ADVISOR_MAX_TIMEOUT_S + 600


class AdvisorAccessError(OpenRouterError):
    """Uniform denial for absent, foreign, or busy advisor state."""


@dataclass(frozen=True)
class Scope:
    owner: str
    workspace: str


@dataclass(frozen=True)
class Chat:
    id: str
    container_id: str
    model: str
    title: str
    history: list[dict[str, str]]
    # Sandbox chats get the hosted shell; inline chats get files in the prompt only.
    sandbox: bool = True


def default_store_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "Northwestern AI" / "advisor.sqlite3"


class AdvisorStore:
    def __init__(self, target: Path | str):
        # A SQLite file path (local) or a postgresql:// URL (hosted).
        self.target = target
        self.path = None if storage.is_postgres(target) else Path(target)

    @contextmanager
    def _connection(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS advisor_chats (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                container_id TEXT NOT NULL, model TEXT NOT NULL, title TEXT NOT NULL,
                history TEXT NOT NULL DEFAULT '[]', uploads TEXT NOT NULL DEFAULT '[]',
                lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0
            )""")
            # Added for headless sessions; legacy rows keep the shell they were made with.
            db.ensure_column("advisor_chats", "sandbox", "INTEGER NOT NULL DEFAULT 1")
            db.commit()
            yield db

    @staticmethod
    def _row(db, scope: Scope, chat_id: str):
        row = db.execute(
            "SELECT * FROM advisor_chats WHERE id=? AND owner=? AND workspace=?",
            (chat_id, scope.owner, scope.workspace),
        ).fetchone()
        if row is None:
            raise AdvisorAccessError("Advisor chat is unavailable for this sign-in.")
        return row

    @staticmethod
    def _chat(row) -> Chat:
        return Chat(row["id"], row["container_id"], row["model"], row["title"],
                    json.loads(row["history"]), bool(row["sandbox"]))

    @staticmethod
    def _idle(row):
        if row["lease_token"] and row["lease_until"] > time.time():
            raise AdvisorAccessError("Advisor chat is busy; wait for the active operation.")

    @staticmethod
    def _leased(row, token: str):
        if not token or row["lease_token"] != token or row["lease_until"] <= time.time():
            raise AdvisorAccessError("Advisor chat is unavailable for this operation.")

    def create(self, scope: Scope, model: str, title: str, *, sandbox: bool = True) -> Chat:
        chat = Chat("chat_" + secrets.token_hex(16), "nu_" + secrets.token_hex(16),
                    model, title, [], sandbox)
        with self._connection() as db:
            db.execute(
                "INSERT INTO advisor_chats (id,owner,workspace,container_id,model,title,sandbox) "
                "VALUES (?,?,?,?,?,?,?)",
                (chat.id, scope.owner, scope.workspace, chat.container_id, model, title, sandbox),
            )
        return chat

    def get(self, scope: Scope, chat_id: str) -> Chat:
        with self._connection() as db:
            return self._chat(self._row(db, scope, chat_id))

    def list(self, scope: Scope) -> list[Chat]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM advisor_chats WHERE owner=? AND workspace=? "
                "ORDER BY rowid DESC LIMIT 100", (scope.owner, scope.workspace),
            ).fetchall()
            return [self._chat(row) for row in rows]

    @contextmanager
    def lease(self, scope: Scope, chat_id: str):
        token = secrets.token_hex(16)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._idle(self._row(db, scope, chat_id))
            db.execute("UPDATE advisor_chats SET lease_token=?,lease_until=? WHERE id=?",
                       (token, time.time() + OPERATION_LEASE_S, chat_id))
        try:
            yield token
        finally:
            with self._connection() as db:
                db.execute("UPDATE advisor_chats SET lease_token=NULL,lease_until=0 "
                           "WHERE id=? AND owner=? AND workspace=? AND lease_token=?",
                           (chat_id, scope.owner, scope.workspace, token))

    def append_turn(self, scope: Scope, chat_id: str, prompt: str, answer: str, token: str):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, chat_id)
            self._leased(row, token)
            history = json.loads(row["history"]) + [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
            db.execute("UPDATE advisor_chats SET history=? WHERE id=?",
                       (json.dumps(history), chat_id))

    def reset(self, scope: Scope, chat_id: str) -> Chat:
        return self._remove_or_reset(scope, chat_id, delete=False)

    def delete(self, scope: Scope, chat_id: str):
        self._remove_or_reset(scope, chat_id, delete=True)

    def _remove_or_reset(self, scope, chat_id, *, delete):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, chat_id)
            self._idle(row)
            if json.loads(row["uploads"]):
                raise AdvisorAccessError("Skill upload cleanup is pending; send a message to retry.")
            if delete:
                db.execute("DELETE FROM advisor_chats WHERE id=?", (chat_id,))
                if db.has_table("session_files"):
                    db.execute("DELETE FROM session_files WHERE chat_id=?", (chat_id,))
                return None
            db.execute("UPDATE advisor_chats SET container_id=? WHERE id=?",
                       ("nu_" + secrets.token_hex(16), chat_id))
            return self._chat(self._row(db, scope, chat_id))

    def pending_uploads(self, scope: Scope, chat_id: str, token: str | None = None) -> list[str]:
        with self._connection() as db:
            row = self._row(db, scope, chat_id)
            if token is not None:
                self._leased(row, token)
            return json.loads(row["uploads"])

    def record_upload(self, scope: Scope, chat_id: str, file_id: str, token: str):
        self._upload(scope, chat_id, file_id, token, add=True)

    def forget_upload(self, scope: Scope, chat_id: str, file_id: str, token: str):
        self._upload(scope, chat_id, file_id, token, add=False)

    def _upload(self, scope, chat_id, file_id, token, *, add):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, scope, chat_id)
            self._leased(row, token)
            uploads = json.loads(row["uploads"])
            if add:
                if file_id not in uploads:
                    uploads.append(file_id)
            elif file_id in uploads:
                uploads.remove(file_id)
            db.execute("UPDATE advisor_chats SET uploads=? WHERE id=?",
                       (json.dumps(uploads), chat_id))
