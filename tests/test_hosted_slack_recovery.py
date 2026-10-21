"""Restart recovery: a new owner posts finished answers and closes jobs a dead owner left running."""

import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from slack_fakes import FakeSlack

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.slack.asks import COMPARE_ALIAS
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs
from openrouter_image_mcp.hosted.slack.recovery import (
    INTERRUPTED_EDIT,
    RESTART_ERROR,
    recover,
)
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row
from openrouter_image_mcp.hosted.slack.threads import ThreadMap

ME, OLD = "proc-me", "proc-old"
NOW = 1_700_000_000.0
THREAD_A, THREAD_B = "1700000000.000001", "1700000000.000002"


class Rig:
    def __init__(self, target):
        self.store, self.slack = AdvisorStore(target), FakeSlack()
        self.results = AdvisorResults(self.store)
        self.jobs, self.threads = SlackJobs(target), ThreadMap(target)
        self.registry = ChannelRegistry(target)
        self.rows = {}
        for team, channel, label in (("T1", "C1", "team-a"), ("T2", "C2", "team-b")):
            row = new_row(team, channel, label, "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                          subagent_model="e/sub")
            self.registry.add(row)
            self.rows[team] = row

    def scope(self, team="T1"):
        row = self.rows[team]
        return Scope(f"slack|{team}:{row.channel_id}", row.workspace_id)

    def add(self, *, team="T1", status="completed", heartbeat=NOW - 100, age_s=60, posted=None, cost=0.02,
            answer="The answer.", working_ts=None, thread=THREAD_A, process=OLD, channel=None, student=False, alias="gemini"):
        scope = self.scope(team)
        if student:
            scope = Scope("https://idp.test/|student-1", scope.workspace)
        chat = self.store.create(scope, "g/model", "t")
        if thread and not student:
            self.threads.put(scope, self.rows[team].channel_id, thread, alias, "inline", chat.id)
        channel = channel or self.rows[team].channel_id
        job = self.results.create(scope, chat, "p", process, slack_user_id="U1", slack_channel_id=channel)
        created = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
        with self.results.connection() as db:
            db.execute("UPDATE advisor_jobs SET status=?, heartbeat_until=?, created_at=?, slack_posted_at=?, "
                       "cost_usd=?, answer=?, slack_working_ts=? WHERE id=?",
                       (status, heartbeat, created, posted, cost, answer, working_ts, job["id"]))
        return job["id"]

    def row_of(self, job_id):
        with self.results.connection() as db:
            return dict(db.execute("SELECT * FROM advisor_jobs WHERE id=?", (job_id,)).fetchone())

    async def recover(self, **kwargs):
        # Default clock: real time, so a heartbeat of `NOW - 100` has lapsed and `time.time() + n` is live.
        kwargs.setdefault("clock", time.time)
        return await recover(self.jobs, lambda workspace: self.results, self.slack, self.registry,
                             self.threads, process_id=ME, **kwargs)


@pytest.fixture
def rig(db_target):
    return Rig(db_target)


async def test_saved_result_posted(rig):
    job = rig.add()
    assert await rig.recover() == {"posted": 1, "closed": 0}
    [post] = rig.slack.posts
    assert (post["channel"], post["thread_ts"]) == ("C1", THREAD_A)
    assert post["username"] == "Sidecar · Gemini"  # the channel's brand for the alias, as a live answer
    assert post["text"].startswith("The answer.") and post["text"].endswith("Gemini · g/model · $0.0200")
    assert rig.row_of(job)["slack_posted_at"] is not None


async def test_running_job_closed_and_message_edited(rig):
    job = rig.add(status="running", working_ts="1700000000.000009", cost=0.03)
    assert await rig.recover() == {"posted": 0, "closed": 1}
    row = rig.row_of(job)
    assert (row["status"], row["error"], row["cost_usd"]) == ("interrupted", RESTART_ERROR, 0.03)
    assert rig.slack.updates == [{"channel": "C1", "ts": "1700000000.000009", "text": INTERRUPTED_EDIT}]
    assert INTERRUPTED_EDIT == "Interrupted by a server restart. Ask again."
    assert rig.slack.posts == []


async def test_queued_job_closed_without_a_working_message(rig):
    job = rig.add(status="queued")
    assert await rig.recover() == {"posted": 0, "closed": 1}
    assert rig.row_of(job)["status"] == "interrupted" and rig.slack.updates == []


async def test_own_process_jobs_untouched(rig):
    running = rig.add(status="running", process=ME)
    done = rig.add(team="T2", process=ME, thread=THREAD_B)  # another scope: create() would stale a lapsed row
    assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.row_of(running)["status"] == "running"
    assert rig.row_of(done)["slack_posted_at"] is None
    assert rig.slack.posts == [] and rig.slack.updates == []


async def test_fresh_heartbeat_means_a_live_owner(rig):
    job = rig.add(status="running", heartbeat=time.time() + 60, working_ts="1700000000.000009")
    assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.row_of(job)["status"] == "running" and rig.slack.updates == []


async def test_lapsed_heartbeat_judged_by_the_injected_clock(rig):
    job = rig.add(status="running", heartbeat=NOW + 5)  # lapsed when heartbeat_until <= now, as advisor_results
    assert await rig.recover(clock=lambda: NOW + 4.9) == {"posted": 0, "closed": 0}
    assert await rig.recover(clock=lambda: NOW + 5) == {"posted": 0, "closed": 1}
    assert rig.row_of(job)["status"] == "interrupted"


async def test_slack_failure_leaves_row_for_next_gain(rig):
    job = rig.add()
    rig.slack.fail_next("post", "ratelimited")
    rig.slack.fail_next("post", "ratelimited")
    assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.row_of(job)["slack_posted_at"] is None
    assert await rig.recover() == {"posted": 1, "closed": 0}
    assert rig.row_of(job)["slack_posted_at"] is not None


async def test_edit_failure_is_logged_and_the_row_still_counts_closed(rig, caplog):
    job = rig.add(status="running", working_ts="1700000000.000009")
    rig.slack.fail_next("update", "message_not_found")
    with caplog.at_level(logging.WARNING):
        assert await rig.recover() == {"posted": 0, "closed": 1}
    assert rig.row_of(job)["status"] == "interrupted"
    assert "SlackCallError" in caplog.text


async def test_each_team_is_handled_under_its_own_scope(rig):
    a = rig.add(team="T1", answer="Answer A")
    b = rig.add(team="T2", answer="Answer B", thread=THREAD_B)
    run_b = rig.add(team="T2", status="running", working_ts="1700000000.000009", thread="1700000000.000005")
    assert await rig.recover() == {"posted": 2, "closed": 1}
    by_channel = {p["channel"]: p for p in rig.slack.posts}
    assert by_channel["C1"]["text"].startswith("Answer A") and by_channel["C1"]["thread_ts"] == THREAD_A
    assert by_channel["C2"]["text"].startswith("Answer B") and by_channel["C2"]["thread_ts"] == THREAD_B
    assert rig.slack.updates[0]["channel"] == "C2"
    assert all(rig.row_of(j)["slack_posted_at"] for j in (a, b))
    assert rig.row_of(run_b)["status"] == "interrupted"


async def test_student_and_unregistered_rows_are_skipped_and_logged(rig, caplog):
    student = rig.add(student=True, status="running")
    mismatch = rig.add(channel="C9")  # owner says C1, the row says C9
    ghost_scope = Scope("slack|T9:C9", rig.scope().workspace)
    chat = rig.store.create(ghost_scope, "g/model", "t")
    ghost = rig.results.create(ghost_scope, chat, "p", OLD, slack_user_id="U1", slack_channel_id="C9")["id"]
    with rig.results.connection() as db:
        db.execute("UPDATE advisor_jobs SET status='completed', slack_posted_at=NULL WHERE id=?", (ghost,))
    with caplog.at_level(logging.WARNING):
        assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.row_of(student)["status"] == "running"
    assert rig.row_of(mismatch)["slack_posted_at"] is None and rig.row_of(ghost)["slack_posted_at"] is None
    assert rig.slack.posts == [] and caplog.text.count("skipped") == 3


async def test_inactive_channel_is_skipped(rig):
    job = rig.add()
    rig.registry.deactivate("T1", "C1", "left")
    assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.row_of(job)["slack_posted_at"] is None


async def test_missing_thread_marks_posted_without_posting(rig, caplog):
    job = rig.add(thread=None)
    with caplog.at_level(logging.WARNING):
        assert await rig.recover() == {"posted": 0, "closed": 0}
    assert rig.slack.posts == [] and rig.row_of(job)["slack_posted_at"] is not None
    assert "no thread" in caplog.text


async def test_old_completed_row_marked_posted_without_posting(rig):
    old = rig.add(age_s=25 * 3600)
    fresh = rig.add(age_s=23 * 3600, thread="1700000000.000007")
    assert await rig.recover() == {"posted": 1, "closed": 0}
    assert rig.row_of(old)["slack_posted_at"] is not None
    assert len(rig.slack.posts) == 1 and rig.row_of(fresh)["slack_posted_at"] is not None


async def test_one_failing_row_does_not_stop_the_others(rig, caplog):
    first = rig.add()
    second = rig.add(team="T2", thread=THREAD_B)
    rig.slack.fail_next("post", "boom")
    rig.slack.fail_next("post", "boom")
    with caplog.at_level(logging.WARNING):
        assert await rig.recover() == {"posted": 1, "closed": 0}
    assert rig.row_of(first)["slack_posted_at"] is None and rig.row_of(second)["slack_posted_at"] is not None
    assert "SlackCallError" in caplog.text


async def test_thread_answered_after_the_post(rig):
    rig.add()
    await rig.recover()
    chat = rig.threads.chats_in(rig.scope(), "C1")[0]
    assert rig.threads.last_answered(rig.scope(), "C1", THREAD_A) == chat


async def test_never_raises_when_the_sweep_itself_fails(rig, caplog):
    class Broken:
        def orphans(self, process_id):
            raise ConnectionError("db down")

    with caplog.at_level(logging.WARNING):
        result = await recover(Broken(), lambda w: rig.results, rig.slack, rig.registry, rig.threads,
                               process_id=ME)
    assert result == {"posted": 0, "closed": 0} and "ConnectionError" in caplog.text


async def test_label_falls_back_to_the_model_id(rig):
    rig.add(alias="unmapped")
    rig.registry.update("T2", "C2", alias_map={"slashed": "vendor/brand", "blank": ""})
    rig.add(team="T2", alias="slashed", thread=THREAD_B)
    rig.add(team="T2", alias="blank", thread="1700000000.000007")
    await rig.recover()
    assert {p["username"] for p in rig.slack.posts} == {"Sidecar · g/model"}
    assert all(p["text"].endswith("g/model · g/model · $0.0200") for p in rig.slack.posts)


async def test_compare_answer_is_labelled_fusion(rig):
    rig.add(alias=COMPARE_ALIAS)
    await rig.recover()
    [post] = rig.slack.posts
    assert post["username"] == "Sidecar · Fusion" and post["text"].endswith("Fusion · g/model · $0.0200")


async def test_mark_posted_failure_after_the_post_is_logged_and_leaves_the_row(rig, caplog, monkeypatch):
    job = rig.add()

    def boom(scope, job_id):
        raise ConnectionError("db down")

    monkeypatch.setattr(rig.jobs, "mark_posted", boom)
    with caplog.at_level(logging.WARNING):
        assert await rig.recover() == {"posted": 0, "closed": 0}
    assert len(rig.slack.posts) == 1 and rig.row_of(job)["slack_posted_at"] is None
    assert "ConnectionError" in caplog.text
