"""Retention purge for the hosted database: `openrouter-image-mcp purge`.

Deletes advisor chats idle for longer than the retention window, together with
their jobs and session files, in bounded batches. Chats that are busy or still
owe a provider-side upload cleanup are kept. Rows created before activity
stamping existed are stamped on first sight rather than deleted. Expired link
attempts are removed. The report holds counts only, never ids or content.
"""

from __future__ import annotations

import time

from .. import storage
from ..advisor_store import AdvisorStore

DEFAULT_DAYS = 90
ATTEMPT_GRACE_S = 86400
_ELIGIBLE = ("FROM advisor_chats WHERE last_active > 0 AND last_active < ? "
             "AND (lease_token IS NULL OR lease_until <= ?) AND uploads = '[]'")
_NOT_BUSY = " AND id NOT IN (SELECT chat_id FROM advisor_jobs WHERE status IN ('queued','running'))"


def _count(db, sql: str, params: tuple) -> int:
    return db.execute("SELECT COUNT(*) AS n " + sql, params).fetchone()["n"]


def _in(ids: list[str]) -> str:
    return "(" + ",".join("?" * len(ids)) + ")"


def _purge_chats(db, cutoff: float, now: float, *, apply: bool, batch: int) -> dict:
    jobs, files = db.has_table("advisor_jobs"), db.has_table("session_files")
    eligible = _ELIGIBLE + (_NOT_BUSY if jobs else "")
    counts = {"chats": 0, "jobs": 0, "session_files": 0}
    if not apply:
        rows = [row["id"] for row in db.execute("SELECT id " + eligible, (cutoff, now)).fetchall()]
        return _children(db, rows, counts, jobs=jobs, files=files, delete=False)
    while True:
        db.execute("BEGIN IMMEDIATE")
        rows = [row["id"] for row in db.execute(
            "SELECT id " + eligible + " LIMIT ?", (cutoff, now, batch)).fetchall()]
        if not rows:
            db.commit()
            return counts
        _children(db, rows, counts, jobs=jobs, files=files, delete=True)
        db.commit()


def _children(db, rows, counts, *, jobs: bool, files: bool, delete: bool) -> dict:
    if not rows:
        return counts
    for table, present, key in (("advisor_jobs", jobs, "jobs"), ("session_files", files, "session_files")):
        if present:
            counts[key] += _count(db, f"FROM {table} WHERE chat_id IN {_in(rows)}", tuple(rows))
            if delete:
                db.execute(f"DELETE FROM {table} WHERE chat_id IN {_in(rows)}", tuple(rows))
    counts["chats"] += len(rows)
    if delete:
        db.execute(f"DELETE FROM advisor_chats WHERE id IN {_in(rows)}", tuple(rows))
    return counts


def purge(target, *, days: int = DEFAULT_DAYS, apply: bool = False, batch: int = 500,
          now: float | None = None) -> dict:
    if type(days) is not int or days < 1 or batch < 1:
        raise ValueError("Retention must be at least 1 day and batches at least 1 row.")
    now = time.time() if now is None else now
    report = {"chats": 0, "jobs": 0, "session_files": 0, "link_attempts": 0, "stamped": 0,
              "applied": apply, "retention_days": days}
    with AdvisorStore(target)._connection() as db:  # ensures the current chat schema
        stamp = "FROM advisor_chats WHERE last_active = 0"
        report["stamped"] = _count(db, stamp, ())
        if apply:
            db.execute("UPDATE advisor_chats SET last_active=? WHERE last_active = 0", (now,))
            db.commit()
        report |= _purge_chats(db, now - days * 86400, now, apply=apply, batch=batch)
        if db.has_table("link_attempts"):
            expired = ("FROM link_attempts WHERE expires_at < ?", (now - ATTEMPT_GRACE_S,))
            report["link_attempts"] = _count(db, *expired)
            if apply:
                db.execute("DELETE " + expired[0], expired[1])
    return report


def active_jobs(target) -> int:
    """Queued or running advisor jobs: deploy only when this is 0 (deploys interrupt them)."""
    with storage.connect(target) as db:
        if not db.has_table("advisor_jobs"):
            return 0
        return _count(db, "FROM advisor_jobs WHERE status IN ('queued','running')", ())


def target_from(env):
    from .app import storage_target

    url = env.get("DATABASE_URL")
    if not url:
        raise ValueError("Set DATABASE_URL to the hosted database.")
    return storage_target(url)


def main_purge(env, *, apply: bool, days: int | None) -> dict:
    return purge(target_from(env), days=days or int(env.get("RETENTION_DAYS", DEFAULT_DAYS)),
                 apply=apply)
