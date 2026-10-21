"""Slack-specific queries over advisor_jobs: usage reports and restart recovery."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs

T1, T2 = Scope("slack|T:C1", "ws"), Scope("slack|T:C2", "ws")
OTHER_WS = Scope("slack|T:C1", "other-ws")
ME, OLD = "proc-me", "proc-old"
LONG_AGO = "2001-01-01T00:00:00+00:00"


@pytest.fixture
def rig(db_target):
    store = AdvisorStore(db_target)
    return store, AdvisorResults(store), SlackJobs(db_target)


def _job(rig, scope, *, user="U1", channel="C1", status="completed", cost=None, process=OLD,
         posted=None, created=None):
    store, results, _ = rig
    chat = store.create(scope, "m", "t")
    job = results.create(scope, chat, "p", process, slack_user_id=user, slack_channel_id=channel)
    fields = {"status": status} if status != "queued" else {}
    if cost is not None:
        fields["cost_usd"] = cost
    if fields:
        results.update(scope, job["id"], process, **fields)
    with results.connection() as db:
        db.execute("UPDATE advisor_jobs SET slack_posted_at=?, created_at=? WHERE id=?",
                   (posted, created or datetime.now(UTC).isoformat(), job["id"]))
    return job["id"], chat.id


def _since():
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def test_spend_by_user_scoped(rig):
    _, _, jobs = rig
    _job(rig, T1, user="U1", cost=0.5)
    _job(rig, T1, user="U1", cost=0.25)
    _job(rig, T1, user="U2", cost=1.0)
    _job(rig, T2, user="U3", cost=9.0)
    _job(rig, OTHER_WS, user="U4", cost=7.0)
    _job(rig, T1, user="U1", cost=3.0, created=LONG_AGO)
    assert jobs.spend_by_user(T1, _since()) == [("U2", 1.0), ("U1", 0.75)]
    assert jobs.spend_by_user(T2, _since()) == [("U3", 9.0)]
    assert jobs.spend_by_user(Scope("slack|T:none", "ws"), _since()) == []


def test_spend_since_is_scoped_and_counts_missing_cost_as_zero(rig):
    _, _, jobs = rig
    _job(rig, T1, cost=0.5)
    _job(rig, T1, user="U2", cost=None)
    _job(rig, T2, cost=4.0)
    _job(rig, T1, cost=2.0, created=LONG_AGO)
    assert jobs.spend_since(T1, _since()) == pytest.approx(0.5)
    assert jobs.spend_since(Scope("slack|T:none", "ws"), _since()) == 0.0


def test_running_lists_active_jobs_for_given_chats_only(rig):
    _, _, jobs = rig
    run_id, run_chat = _job(rig, T1, status="queued", process=ME)
    _, done_chat = _job(rig, T1, status="completed")
    _, foreign_chat = _job(rig, T2, status="queued", process=ME)
    rows = jobs.running(T1, [run_chat, done_chat, foreign_chat])
    assert [row["id"] for row in rows] == [run_id]
    assert jobs.running(T1, []) == []
    assert jobs.running(T1, [run_chat])[0]["slack_user_id"] == "U1"


def test_orphans_skip_own_process_and_posted(rig):
    _, _, jobs = rig
    active_old, _ = _job(rig, T1, status="queued", process=OLD)
    unposted, _ = _job(rig, T2, status="completed", process=OLD, posted=None)
    _job(rig, T1, status="completed", process=OLD, posted=time.time())  # already posted
    _job(rig, T1, status="failed", process=OLD)  # failed rows are not retried
    _job(rig, T2, status="queued", process=ME)  # this process's own work
    store, results, _ = rig
    chat = store.create(T1, "m", "plain")
    results.create(T1, chat, "p", OLD)  # not a Slack job
    assert {row["id"] for row in jobs.orphans(ME)} == {active_old, unposted}


def test_mark_posted_is_scoped(rig):
    _, results, jobs = rig
    job_id, _ = _job(rig, T1, status="completed")
    jobs.mark_posted(T2, job_id)  # a different team cannot stamp it
    assert results.get(T1, job_id)["slack_posted_at"] is None
    jobs.mark_posted(T1, job_id)
    assert results.get(T1, job_id)["slack_posted_at"] is not None
    assert jobs.orphans(ME) == []
