"""The local-to-hosted migration bundle: export on this computer, validate on import.

The retired local stdio Sidecar kept chats in a SQLite file on the student's computer,
owned by the SHA-256 of that cohort's OpenRouter key in the OS keyring. `export-local`
reads both, read-only. A bundle carries a cohort's advisor chats (title, model,
transcript, session files) and preference defaults. It never carries keys, chat or container ids, the
credential fingerprint, job rows or skill uploads, and key-shaped text is redacted.
The hosted import creates new chats under the signed-in student (`hosted/imports.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import keystore, storage
from .advisor_store import AdvisorStore, Scope
from .config import COHORT_WORKSPACES
from .errors import AuthRequiredError, BadRequestError
from .logs import redact
from .preferences import _MODEL_ID, FAMILIES, ensure_table
from .session_files import (
    MAX_FILES,
    MAX_SESSION_CHARS,
    FilePacket,
    SessionFiles,
    validate_files,
)

FORMAT = "openrouter-sidecar-local-export"
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_CHATS = 500
_STRICT = ConfigDict(extra="forbid", strict=True)


def local_store_path() -> Path:
    """Where the retired local Sidecar kept its chats and preferences."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "Northwestern AI" / "advisor.sqlite3"


class BundleError(ValueError):
    """The bundle is unreadable or outside the limits; nothing was imported."""


class _Turn(BaseModel):
    model_config = _STRICT
    role: Literal["user", "assistant"]
    content: str


class _Chat(BaseModel):
    model_config = _STRICT
    title: str = Field(min_length=1, max_length=200)
    model: str = Field(max_length=200, pattern=_MODEL_ID.pattern)
    sandbox: bool
    history: list[_Turn]
    files: list[FilePacket] = Field(max_length=MAX_FILES)


class _Preferences(BaseModel):
    model_config = _STRICT
    general_default: str | None = Field(max_length=200, pattern=_MODEL_ID.pattern)
    family_defaults: dict[str, str | None]
    onboarding_completed: bool

    @field_validator("family_defaults")
    @classmethod
    def _families(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        for family, model in value.items():
            if family not in FAMILIES or model is not None and not _MODEL_ID.fullmatch(model):
                raise ValueError(f"unknown family or model id: {family}")
        return value


class _Bundle(BaseModel):
    model_config = _STRICT
    format: Literal["openrouter-sidecar-local-export"]
    version: Literal[1]
    exported_at: str
    cohort: str
    workspace: str
    chats: list[_Chat] = Field(max_length=MAX_CHATS)
    preferences: _Preferences | None


def parse_bundle(data: bytes) -> dict:
    """Validate everything before anything is written; raises BundleError."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"The export is larger than {MAX_BUNDLE_BYTES // (1024 * 1024)} MB.")
    try:
        bundle = _Bundle.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BundleError("The upload is not a Sidecar export (expected JSON).") from None
    except ValidationError as exc:
        where = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise BundleError(f"The export is not valid at {where or 'top level'}.") from None
    for index, chat in enumerate(bundle.chats):
        try:
            if sum(map(len, validate_files(chat.files).values())) > MAX_SESSION_CHARS:
                raise BadRequestError(f"session files exceed {MAX_SESSION_CHARS} characters")
        except BadRequestError as exc:
            raise BundleError(f"Chat {index + 1}: {exc.message}") from None
    return bundle.model_dump()


def bundle_digest(bundle: dict) -> str:
    """Identifies the content, not the export run, so re-exports import once."""
    content = {key: bundle[key] for key in ("workspace", "chats", "preferences")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"))
                          .encode()).hexdigest()


def export_bundle(target, scope: Scope, *, cohort: str) -> dict:
    store = AdvisorStore(target)
    files = SessionFiles(store)
    chats = [{"title": redact(chat.title), "model": chat.model, "sandbox": chat.sandbox,
              "history": [{"role": turn["role"], "content": redact(turn["content"])}
                          for turn in chat.history],
              "files": [{"path": row["path"], "content": redact(row["content"])}
                        for row in files.current(scope, chat.id)]}
             for chat in store.all(scope)]
    return {"format": FORMAT, "version": 1, "exported_at": datetime.now(UTC).isoformat(),
            "cohort": cohort, "workspace": scope.workspace, "chats": chats,
            "preferences": read_preferences(target, scope)}


def run_export(cohort: str, out: Path) -> str:
    """`export-local`: this cohort's chats for the key the local Sidecar signed in with."""
    scope = keystore.local_scope(COHORT_WORKSPACES[cohort])
    if scope is None:
        raise AuthRequiredError(
            f"No local sign-in for the Class of {cohort} on this computer, so there is nothing "
            "to export. Check the class year.")
    path = local_store_path()
    if not path.exists():
        raise AuthRequiredError(f"No local Sidecar data at {path}; nothing to export.")
    bundle = export_bundle(path, scope, cohort=cohort)
    write_bundle(bundle, out)
    count, files = len(bundle["chats"]), sum(len(chat["files"]) for chat in bundle["chats"])
    return (f"Exported {count} chat{'' if count == 1 else 's'} and {files} session file"
            f"{'' if files == 1 else 's'} for the Class of {cohort} to {out}.\n"
            "Next: in the hosted connector, ask Claude to import your local Sidecar chats; "
            "it gives you a one-time upload command. Keep this file private.")


def write_bundle(bundle: dict, out: Path) -> None:
    """Transcripts are private: owner-only file permissions where the OS has them."""
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ preferences


def _preference_row(db, scope: Scope):
    return db.execute("SELECT * FROM sidecar_preferences WHERE owner=? AND workspace=?",
                      (scope.owner, scope.workspace)).fetchone()


def read_preferences(target, scope: Scope) -> dict | None:
    with storage.connect(target) as db:
        ensure_table(db)
        db.commit()
        row = _preference_row(db, scope)
    if row is None:
        return None
    families = json.loads(row["family_defaults"])
    return {"general_default": row["general_default"],
            "family_defaults": {family: families.get(family) for family in FAMILIES},
            "onboarding_completed": bool(row["onboarding_completed"])}


def _is_empty(row) -> bool:
    families = json.loads(row["family_defaults"])
    return not (row["general_default"] or any(families.values()) or row["onboarding_completed"])


def seed_preferences(target, scope: Scope, prefs: dict) -> bool:
    """Fill defaults only for an account that has chosen none; never overwrite choices."""
    families = json.dumps({family: prefs["family_defaults"].get(family) for family in FAMILIES})
    values = (prefs["general_default"], families, bool(prefs["onboarding_completed"]))
    with storage.connect(target) as db:
        ensure_table(db)
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        row = _preference_row(db, scope)
        if row is not None and not _is_empty(row):
            return False
        if row is None:
            db.execute("INSERT INTO sidecar_preferences (settings_id, owner, workspace, "
                       "general_default, family_defaults, onboarding_completed) "
                       "VALUES (?,?,?,?,?,?)",
                       ("settings_" + secrets.token_hex(16), scope.owner, scope.workspace, *values))
        else:  # a revision bump makes any open settings view reload before saving
            db.execute("UPDATE sidecar_preferences SET general_default=?, family_defaults=?, "
                       "onboarding_completed=?, revision=revision+1 WHERE owner=? AND workspace=?",
                       (*values, scope.owner, scope.workspace))
        return True
