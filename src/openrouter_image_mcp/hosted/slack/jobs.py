"""Slack-specific queries over `advisor_jobs`: usage reports and restart recovery."""

from __future__ import annotations

import time
from collections.abc import Collection

from ...advisor_results import AdvisorResults
from ...advisor_store import AdvisorStore


class SlackJobs:
    """Every query binds the team's owner and workspace, except `orphans` (a server-side sweep)."""

    def __init__(self, target):
        self.results = AdvisorResults(AdvisorStore(target))

    def spend_by_user(self, scope, since_iso: str) -> list[tuple[str, float]]:
        """Spend per Slack user for one team since `since_iso`, largest first."""
        with self.results.connection() as db:
            rows = db.execute(
                "SELECT slack_user_id AS user_id, COALESCE(SUM(cost_usd), 0) AS spent FROM advisor_jobs "
                "WHERE owner=? AND workspace=? AND slack_user_id IS NOT NULL AND created_at>=? "
                "GROUP BY slack_user_id ORDER BY spent DESC, slack_user_id",
                (scope.owner, scope.workspace, since_iso)).fetchall()
        return [(row["user_id"], float(row["spent"])) for row in rows]

    def spend_since(self, scope, since_iso: str) -> float:
        with self.results.connection() as db:
            row = db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM advisor_jobs "
                             "WHERE owner=? AND workspace=? AND created_at>=?",
                             (scope.owner, scope.workspace, since_iso)).fetchone()
        return float(row["spent"])

    def running(self, scope, chat_ids: Collection[str]) -> list[dict]:
        """Queued or running jobs of the team in the given chats."""
        ids = list(chat_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self.results.connection() as db:
            rows = db.execute("SELECT * FROM advisor_jobs WHERE owner=? AND workspace=? AND "
                              "status IN ('queued','running') AND chat_id IN (" + marks + ") "
                              "ORDER BY created_at", (scope.owner, scope.workspace, *ids)).fetchall()
        return [dict(row) for row in rows]

    def orphans(self, process_id: str) -> list[dict]:
        """Slack jobs another process left behind: still active, or finished but never posted."""
        with self.results.connection() as db:
            rows = db.execute(
                "SELECT * FROM advisor_jobs WHERE slack_channel_id IS NOT NULL AND process_id!=? AND "
                "(status IN ('queued','running') OR (status='completed' AND slack_posted_at IS NULL)) "
                "ORDER BY created_at", (process_id,)).fetchall()
        return [dict(row) for row in rows]

    def mark_posted(self, scope, job_id: str) -> None:
        with self.results.connection() as db:
            db.execute("UPDATE advisor_jobs SET slack_posted_at=? WHERE id=? AND owner=? AND workspace=?",
                       (time.time(), job_id, scope.owner, scope.workspace))
            db.commit()
