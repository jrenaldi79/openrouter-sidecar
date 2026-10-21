"""The admin change log: who registered, changed or turned a Slack channel on or off.

`insert` writes on a connection the caller owns, so a registry write and its audit entry share one
transaction. `AdminAudit.record` opens its own for changes that don't touch the registry (model saves).
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ... import storage

if TYPE_CHECKING:
    from ..slack.registry import ChannelRow
    from .sessions import Session

REGISTERED = "registered"
CHANGED = "changed"
MODELS_CHANGED = "models changed"
TURNED_OFF = "turned off"
TURNED_ON = "turned on"

# No id column: entries read back by time, then insertion order (the implicit or serial rowid).
SCHEMA = """CREATE TABLE IF NOT EXISTS admin_audit (
    at REAL NOT NULL, actor_email TEXT NOT NULL, actor_subject TEXT NOT NULL, action TEXT NOT NULL,
    team_id TEXT NOT NULL, channel_id TEXT NOT NULL, label TEXT NOT NULL, changes TEXT NOT NULL)"""


@dataclass(frozen=True)
class AuditEntry:
    at: float
    actor_email: str
    action: str
    team_id: str
    channel_id: str
    label: str
    changes: dict[str, list]


def insert(db, *, at: float, actor: Session, action: str, row: ChannelRow, changes: dict[str, list]) -> None:
    """Add one entry on the caller's connection; the caller commits (or rolls back).

    The table is created here, inside the caller's transaction, so registry writes need not know its schema."""
    db.execute(SCHEMA)
    db.execute("INSERT INTO admin_audit (at, actor_email, actor_subject, action, team_id, channel_id, label, "
               "changes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (at, actor.email, actor.subject, action, row.slack_team_id, row.channel_id, row.team_label,
                json.dumps(changes)))


class AdminAudit:
    def __init__(self, target, *, clock=time.time):
        self.target = target
        self.clock = clock

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute(SCHEMA)
            db.commit()
            yield db

    def record(self, actor: Session, action: str, row: ChannelRow, changes: dict[str, list]) -> None:
        with self._db() as db:
            insert(db, at=self.clock(), actor=actor, action=action, row=row, changes=changes)

    def recent(self, limit: int = 200) -> list[AuditEntry]:
        with self._db() as db:
            found = db.execute("SELECT * FROM admin_audit ORDER BY at DESC, rowid DESC LIMIT ?",
                               (max(0, limit),)).fetchall()
        return [AuditEntry(r["at"], r["actor_email"], r["action"], r["team_id"], r["channel_id"], r["label"],
                           json.loads(r["changes"])) for r in found]
