"""Registered Slack channels: one row per (Slack team, channel), one OpenRouter team key per label.

An operator adds rows with the ops scripts; the bot only reads them, flips `active`, and records
the one-time welcome and the monthly budget warning.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace

import psycopg

from ... import config, storage

DEFAULT_ALIASES = {"gpt": "ChatGPT", "gemini": "Gemini", "grok": "Grok", "open": "open weight"}
LABEL = re.compile(r"^[a-z0-9-]{1,40}$")
PANEL_SIZE = range(1, 9)
_JSON = {"alias_map": dict, "fusion_panel": tuple, "fetch_allowlist": tuple, "invoker_allowlist": tuple}
_KEYS = ("slack_team_id", "channel_id")
_COLUMNS = (
    "slack_team_id", "channel_id", "team_label", "cohort", "workspace_id", "default_model", "alias_map",
    "fusion_panel", "fusion_analyst", "subagent_model", "fetch_allowlist", "daily_cap_usd", "max_concurrent",
    "invoker_allowlist", "active", "inactive_reason", "welcomed_at", "warned_month", "created_at")
_SCHEMA = """CREATE TABLE IF NOT EXISTS slack_channels (
    slack_team_id TEXT NOT NULL, channel_id TEXT NOT NULL, team_label TEXT NOT NULL UNIQUE,
    cohort TEXT NOT NULL, workspace_id TEXT NOT NULL, default_model TEXT NOT NULL,
    alias_map TEXT NOT NULL, fusion_panel TEXT NOT NULL, fusion_analyst TEXT NOT NULL,
    subagent_model TEXT NOT NULL, fetch_allowlist TEXT NOT NULL, daily_cap_usd REAL NOT NULL,
    max_concurrent INTEGER NOT NULL, invoker_allowlist TEXT NOT NULL, active INTEGER NOT NULL,
    inactive_reason TEXT, welcomed_at REAL, warned_month TEXT, created_at REAL NOT NULL,
    PRIMARY KEY (slack_team_id, channel_id))"""


# DDL for the admin audit table, set by the hosted wiring (hosted/admin/audit.py). When set, it runs on the same
# connection just before an `audit` callback, so a callback can write its row without owning the schema.
AUDIT_SCHEMA: str | None = None


@dataclass(frozen=True)
class ChannelRow:
    slack_team_id: str
    channel_id: str
    team_label: str
    cohort: str
    workspace_id: str
    default_model: str
    alias_map: dict[str, str]
    fusion_panel: tuple[str, ...]
    fusion_analyst: str
    subagent_model: str
    fetch_allowlist: tuple[str, ...] = ()
    daily_cap_usd: float = 4.0
    max_concurrent: int = 3
    invoker_allowlist: tuple[str, ...] = ()
    active: bool = True
    inactive_reason: str | None = None
    welcomed_at: float | None = None
    warned_month: str | None = None
    created_at: float = field(default=0.0, compare=False)  # set by the registry on add


def new_row(team_id: str, channel_id: str, label: str, cohort: str, *, fusion_panel, fusion_analyst: str,
            subagent_model: str, **optional) -> ChannelRow:
    if cohort not in config.COHORT_WORKSPACES:
        raise ValueError(f"Unknown cohort {cohort!r}.")
    optional.setdefault("default_model", "gemini")
    optional["alias_map"] = dict(optional.get("alias_map", DEFAULT_ALIASES))
    return ChannelRow(team_id, channel_id, label, cohort, config.COHORT_WORKSPACES[cohort],
                      fusion_panel=tuple(fusion_panel), fusion_analyst=fusion_analyst,
                      subagent_model=subagent_model, **optional)


def _validate(row: ChannelRow) -> None:
    if not LABEL.match(row.team_label):
        raise ValueError("team_label must match ^[a-z0-9-]{1,40}$.")
    if row.cohort not in config.COHORT_WORKSPACES:
        raise ValueError(f"Unknown cohort {row.cohort!r}.")
    if len(row.fusion_panel) not in PANEL_SIZE:
        raise ValueError("fusion_panel needs 1 to 8 models.")
    if not (row.slack_team_id and row.channel_id):
        raise ValueError("slack_team_id and channel_id are required.")
    # Money guards: argparse's float takes "inf" and "nan", and a cap below 1 would refuse every answer.
    if not (isinstance(row.daily_cap_usd, int | float) and math.isfinite(row.daily_cap_usd)
            and row.daily_cap_usd > 0):
        raise ValueError("daily_cap_usd must be a positive number of dollars.")
    if not isinstance(row.max_concurrent, int) or row.max_concurrent < 1:
        raise ValueError("max_concurrent must be at least 1.")
    if row.default_model not in row.alias_map:
        raise ValueError(f"default_model {row.default_model!r} is not one of the channel's aliases.")


def _values(row: ChannelRow) -> tuple:
    data = asdict(row)
    for name in _JSON:
        data[name] = json.dumps(data[name])
    data["active"] = int(row.active)
    return tuple(data[name] for name in _COLUMNS)


def _row(found) -> ChannelRow:
    data = {name: found[name] for name in _COLUMNS}
    for name, kind in _JSON.items():
        data[name] = kind(json.loads(data[name]))
    data["active"] = bool(data["active"])
    return ChannelRow(**data)


# Called on the write's own connection, after the write and before the commit; raising rolls the write back.
Audit = Callable[[object, "ChannelRow | None", "ChannelRow"], None]


def _read(db, team_id: str, channel_id: str) -> ChannelRow | None:
    found = db.execute("SELECT * FROM slack_channels WHERE slack_team_id=? AND channel_id=?",
                       (team_id, channel_id)).fetchone()
    return _row(found) if found else None


def _audit(db, audit: Audit, before: ChannelRow | None, after: ChannelRow | None) -> None:
    if AUDIT_SCHEMA:
        db.execute(AUDIT_SCHEMA)
    audit(db, before, after)


class ChannelRegistry:
    def __init__(self, target):
        self.target = target
        self.clock = time.time

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute(_SCHEMA)
            db.commit()
            yield db

    def _one(self, where: str, args: tuple) -> ChannelRow | None:
        with self._db() as db:
            found = db.execute(f"SELECT * FROM slack_channels WHERE {where}", args).fetchone()
        return _row(found) if found else None

    def get(self, team_id: str, channel_id: str) -> ChannelRow | None:
        return self._one("slack_team_id=? AND channel_id=?", (team_id, channel_id))

    def by_label(self, label: str) -> ChannelRow | None:
        return self._one("team_label=?", (label,))

    def all(self) -> list[ChannelRow]:
        with self._db() as db:
            found = db.execute("SELECT * FROM slack_channels ORDER BY team_label").fetchall()
        return [_row(r) for r in found]

    def add(self, row: ChannelRow, *, audit: Audit | None = None) -> None:
        _validate(row)
        stored = replace(row, created_at=self.clock())
        marks = ",".join("?" * len(_COLUMNS))
        with self._db() as db:
            cursor = db.execute(f"INSERT INTO slack_channels ({','.join(_COLUMNS)}) VALUES ({marks}) "
                                "ON CONFLICT DO NOTHING", _values(stored))
            if cursor.rowcount != 1:
                raise ValueError("That channel or team label is already registered.")
            if audit:
                _audit(db, audit, None, stored)

    def update(self, team_id: str, channel_id: str, *, audit: Audit | None = None, **changes) -> ChannelRow:
        current = self.get(team_id, channel_id)
        if current is None:
            raise ValueError("That channel is not registered.")
        allowed = {f.name for f in fields(ChannelRow)} - set(_KEYS) - {"created_at", "workspace_id"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Cannot update: {', '.join(sorted(unknown))}.")
        if "cohort" in changes and changes["cohort"] in config.COHORT_WORKSPACES:
            changes["workspace_id"] = config.COHORT_WORKSPACES[changes["cohort"]]
        for name, kind in _JSON.items():
            if name in changes:
                changes[name] = kind(changes[name])
        updated = replace(current, **changes)
        _validate(updated)
        # Write only the columns asked for: the snapshot may predate a racing mark_welcomed, deactivate or
        # mark_warned, and rewriting every column from it would undo that guarded write.
        values = dict(zip(_COLUMNS, _values(updated), strict=True))
        names = [c for c in _COLUMNS if c in changes]
        if names:
            sets = ",".join(f"{c}=?" for c in names)
            with self._db() as db:
                before = _read(db, team_id, channel_id) if audit else None
                try:
                    db.execute(f"UPDATE slack_channels SET {sets} WHERE slack_team_id=? AND channel_id=?",
                               (*(values[c] for c in names), team_id, channel_id))
                except (sqlite3.IntegrityError, psycopg.errors.IntegrityError) as error:
                    raise ValueError("That team label is already registered.") from error
                if audit:  # a call that changes no column writes nothing, so it leaves no audit entry
                    _audit(db, audit, before, _read(db, team_id, channel_id))
        return self.get(team_id, channel_id) or updated

    def _claim(self, assignment: str, guard: str, args: tuple) -> bool:
        """Run one guarded UPDATE; True only when it changed the row (the caller won)."""
        with self._db() as db:
            cursor = db.execute(f"UPDATE slack_channels SET {assignment} WHERE slack_team_id=? "
                                f"AND channel_id=? AND {guard}", args)
            return cursor.rowcount == 1

    def deactivate(self, team_id: str, channel_id: str, reason: str, *, audit: Audit | None = None) -> bool:
        if audit is None:
            return self._claim("active=0, inactive_reason=?", "active=1", (reason, team_id, channel_id))
        with self._db() as db:
            before = _read(db, team_id, channel_id)
            cursor = db.execute("UPDATE slack_channels SET active=0, inactive_reason=? WHERE slack_team_id=? "
                                "AND channel_id=? AND active=1", (reason, team_id, channel_id))
            if cursor.rowcount != 1:
                return False
            _audit(db, audit, before, _read(db, team_id, channel_id))
            return True

    def mark_welcomed(self, team_id: str, channel_id: str) -> bool:
        return self._claim("welcomed_at=?", "welcomed_at IS NULL", (self.clock(), team_id, channel_id))

    def mark_warned(self, team_id: str, channel_id: str, month: str) -> bool:
        return self._claim("warned_month=?", "(warned_month IS NULL OR warned_month<>?)",
                           (month, team_id, channel_id, month))
