from datetime import UTC, datetime

import pytest

from openrouter_image_mcp import storage
from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.limits import Limits, RateLimitedError

A, B = Scope("iss|a", "ws"), Scope("iss|b", "ws")


@pytest.fixture
def store(db_target):
    store = AdvisorStore(db_target)
    with AdvisorResults(store).connection():
        pass  # create the jobs table
    return store


def _job(store, scope, *, status="completed", cost=None, created=None):
    chat = store.create(scope, "m", "t")
    with storage.connect(store.target) as db:
        db.execute("INSERT INTO advisor_jobs (id,owner,workspace,chat_id,model,title,status,prompt,"
                   "kind,created_at,process_id,lease_token,heartbeat_until,cost_usd) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("job_" + chat.id[5:], scope.owner, scope.workspace, chat.id, "m", "t", status,
                    "p", "message", created or datetime.now(UTC).isoformat(), "proc", "tok", 0, cost))


def test_allows_within_limits(store):
    Limits(store, max_concurrent=2, per_minute=5, daily_usd=1.0).admit(A)


def test_concurrency_counts_active_jobs_per_principal(store):
    limits = Limits(store, max_concurrent=2, per_minute=50, daily_usd=10)
    _job(store, A, status="running")
    _job(store, A, status="queued")
    _job(store, B, status="running")
    with pytest.raises(RateLimitedError, match="at a time"):
        limits.admit(A)
    limits.admit(B)


def test_per_minute_window(store):
    clock = [1000.0]
    limits = Limits(store, max_concurrent=10, per_minute=2, daily_usd=10, clock=lambda: clock[0])
    limits.admit(A)
    limits.admit(A)
    with pytest.raises(RateLimitedError) as caught:
        limits.admit(A)
    assert 0 < caught.value.retry_after_s <= 60
    limits.admit(B)
    clock[0] += 61
    limits.admit(A)


def test_daily_spend_cap_uses_recorded_costs(store):
    limits = Limits(store, max_concurrent=10, per_minute=50, daily_usd=1.0)
    _job(store, A, cost=0.6)
    _job(store, A, cost=0.5)
    _job(store, A, cost=5.0, created="2001-01-01T00:00:00+00:00")  # an old day
    with pytest.raises(RateLimitedError, match="daily"):
        limits.admit(A)
    limits.admit(B)


def test_image_calls_have_their_own_window_and_concurrency(store):
    clock = [1000.0]
    limits = Limits(store, max_concurrent=1, per_minute=2, daily_usd=10, clock=lambda: clock[0])
    with limits.image_call(A):
        with pytest.raises(RateLimitedError, match="image"), limits.image_call(A):
            pass
        with limits.image_call(B):  # other principals are unaffected
            pass
    # Third this minute (the refused one counted too).
    with pytest.raises(RateLimitedError, match="image"), limits.image_call(A):
        pass
    limits.admit(A)  # advisor admission is separate
    clock[0] += 61
    with limits.image_call(A):
        pass
