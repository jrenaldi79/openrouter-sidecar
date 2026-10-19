"""Advisor job records: result polls stay off the global write lock unless recovery is due."""

import time
from contextlib import contextmanager

import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope

SCOPE = Scope("o" * 64, "results-workspace")
PROCESS = "p" * 32


@pytest.fixture
def rig(db_target, monkeypatch):
    store = AdvisorStore(db_target)
    results = AdvisorResults(store)
    chat = store.create(SCOPE, "openai/test-advisor", "Critic")
    job = results.create(SCOPE, chat, "Prompt", PROCESS)
    locks = []
    real_connect = storage.connect

    @contextmanager
    def spying_connect(target):
        with real_connect(target) as db:
            execute = db.execute

            def spy(sql, params=()):
                if sql.strip().upper() == "BEGIN IMMEDIATE":
                    locks.append(sql)
                return execute(sql, params)

            db.execute = spy
            yield db

    monkeypatch.setattr(storage, "connect", spying_connect)
    return results, chat.id, job["id"], locks


def test_polls_of_a_live_job_take_no_write_lock(rig):
    results, chat_id, job_id, locks = rig
    for _ in range(3):
        assert results.get(SCOPE, job_id)["status"] == "queued"
        assert [row["job_id"] for row in results.for_chat(SCOPE, chat_id, 20)] == [job_id]
        assert results.active_for_chat(SCOPE, chat_id) == job_id
    assert locks == []


def test_a_poll_still_recovers_a_job_whose_heartbeat_lapsed(rig):
    results, chat_id, job_id, locks = rig
    results.update(SCOPE, job_id, PROCESS, heartbeat_until=time.time() - 1)
    locks.clear()
    row = results.get(SCOPE, job_id)
    assert row["status"] == "interrupted" and "heartbeat expired" in row["error"]
    assert locks == ["BEGIN IMMEDIATE"]  # recovery writes under the lock
    assert results.active_for_chat(SCOPE, chat_id) is None
    # The chat's lease went with the job, so a new job can start.
    chat = results.store.get(SCOPE, chat_id)
    assert results.create(SCOPE, chat, "Next", PROCESS)["status"] == "queued"


def test_polls_of_settled_jobs_take_no_write_lock(rig):
    results, chat_id, job_id, locks = rig
    results.update(SCOPE, job_id, PROCESS, status="completed", answer="Done")
    locks.clear()
    assert results.get(SCOPE, job_id)["answer"] == "Done"
    assert results.for_chat(SCOPE, chat_id, 5)[0]["status"] == "completed"
    assert results.active_for_chat(SCOPE, chat_id) is None
    assert locks == []
