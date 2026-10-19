"""Per-chat text files supplied by the caller and restored on every later turn.

The server has no access to the caller's repository, so the caller passes the files
it wants the advisor to see. They are stored with the chat (owner-scoped like the
transcript) and never enter the conversation history itself.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .advisor_store import AdvisorStore, Scope
from .errors import BadRequestError

MAX_FILE_CHARS = 100_000
MAX_SESSION_CHARS = 200_000
MAX_FILES = 200
MAX_PATH_CHARS = 300


class FilePacket(BaseModel):
    """One UTF-8 text file, addressed by a relative POSIX path."""

    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(max_length=1000)
    content: str = Field(max_length=MAX_FILE_CHARS + 1)


@dataclass
class FileDelta:
    added: list[tuple[str, int]] = field(default_factory=list)
    updated: list[tuple[str, int]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.updated or self.removed)

    def note(self) -> str:
        if self.empty:
            return ""
        parts = [f"{path} (v{version})" for path, version in sorted(self.added + self.updated)]
        parts += [f"removed {path}" for path in self.removed]
        return "Updated since last turn: " + ", ".join(parts)


def normalize_path(path: str) -> str:
    bad = (not path.strip() or len(path) > MAX_PATH_CHARS or "\\" in path
           or any(ord(char) < 32 for char in path) or path.startswith("/"))
    parts = [part for part in PurePosixPath(path).parts if part != "."]
    if bad or not parts or ".." in parts or ":" in parts[0]:
        raise BadRequestError("File paths must be relative POSIX paths without '..' "
                              f"(at most {MAX_PATH_CHARS} characters).")
    return "/".join(parts)


def _check_text(path: str, content: str) -> None:
    if len(content) > MAX_FILE_CHARS:
        raise BadRequestError(f"{path}: files are limited to {MAX_FILE_CHARS} characters.")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise BadRequestError(f"{path}: only UTF-8 text files are supported.") from None
    if "\x00" in content:
        raise BadRequestError(f"{path}: binary files are not supported; send text only.")


def validate_files(files: list[FilePacket] | None) -> dict[str, str]:
    """Normalized {path: content}; raises before anything is stored."""
    files = files or []
    if len(files) > MAX_FILES:
        raise BadRequestError(f"Send at most {MAX_FILES} files per call.")
    result: dict[str, str] = {}
    for packet in files:
        path = normalize_path(packet.path)
        if path in result:
            raise BadRequestError(f"Duplicate file path: {path}")
        _check_text(path, packet.content)
        result[path] = packet.content
    return result


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SessionFiles:
    def __init__(self, store: AdvisorStore):
        self.store = store

    def _connection(self):
        return self.store._connection()

    @staticmethod
    def _ensure(db):
        db.execute("""CREATE TABLE IF NOT EXISTS session_files (
            chat_id TEXT NOT NULL, owner TEXT NOT NULL, workspace TEXT NOT NULL,
            path TEXT NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL,
            version INTEGER NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (chat_id, path)
        )""")

    @staticmethod
    def _rows(db, scope: Scope, chat_id: str) -> list[dict]:
        rows = db.execute("SELECT path, content, sha256, version FROM session_files "
                          "WHERE chat_id=? AND owner=? AND workspace=? ORDER BY path",
                          (chat_id, scope.owner, scope.workspace)).fetchall()
        return [dict(row) for row in rows]

    def current(self, scope: Scope, chat_id: str) -> list[dict]:
        with self._connection() as db:
            self._ensure(db)
            self.store._row(db, scope, chat_id)  # ownership check
            return self._rows(db, scope, chat_id)

    def upsert(self, scope: Scope, chat_id: str, files: list[FilePacket] | None, *,
               replace: bool = False) -> FileDelta:
        incoming = validate_files(files)
        with self._connection() as db:
            self._ensure(db)
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            self.store._row(db, scope, chat_id)
            existing = {row["path"]: row for row in self._rows(db, scope, chat_id)}
            delta = self._delta(existing, incoming, replace)
            kept = {path: row["content"] for path, row in existing.items()
                    if path not in delta.removed}
            if sum(map(len, (kept | incoming).values())) > MAX_SESSION_CHARS:
                raise BadRequestError(f"Session files are limited to {MAX_SESSION_CHARS} "
                                      "characters in total; drop or replace files.")
            self._write(db, scope, chat_id, incoming, existing, delta)
            return delta

    @staticmethod
    def _delta(existing: dict, incoming: dict[str, str], replace: bool) -> FileDelta:
        delta = FileDelta()
        for path, content in sorted(incoming.items()):
            row = existing.get(path)
            if row is None:
                delta.added.append((path, 1))
            elif row["sha256"] != _sha(content):
                delta.updated.append((path, row["version"] + 1))
        if replace:
            delta.removed = sorted(set(existing) - set(incoming))
        return delta

    @staticmethod
    def _write(db, scope, chat_id, incoming, existing, delta: FileDelta) -> None:
        now = time.time()
        for path, version in delta.added + delta.updated:
            if path in existing:
                db.execute("DELETE FROM session_files WHERE chat_id=? AND path=?", (chat_id, path))
            db.execute("INSERT INTO session_files (chat_id,owner,workspace,path,content,sha256,"
                       "version,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (chat_id, scope.owner, scope.workspace, path, incoming[path],
                        _sha(incoming[path]), version, now))
        for path in delta.removed:
            db.execute("DELETE FROM session_files WHERE chat_id=? AND path=?", (chat_id, path))
