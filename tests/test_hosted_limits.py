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
    # Its own kind, with its size and span, so a caller can name the principal's window, not a user's.
    assert (caught.value.kind, caught.value.limit, caught.value.span_s) == ("minute", 2, 60)
    assert "Too many advisor requests this minute." in caught.value.message  # students' text unchanged
    limits.admit(B)
    clock[0] += 61
    limits.admit(A)


def test_named_window_reports_its_size_and_span(store):
    limits = Limits(store)
    limits.window("k", "Sidecar requests", limit=1, span_s=600)
    with pytest.raises(RateLimitedError) as caught:
        limits.window("k", "Sidecar requests", limit=1, span_s=600)
    assert (caught.value.kind, caught.value.limit, caught.value.span_s) == ("rate", 1, 600)


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


def test_admit_overrides(store):
    limits = Limits(store, max_concurrent=2, per_minute=50, daily_usd=5.0)
    _job(store, A, status="running")
    _job(store, A, status="running")
    limits.admit(A, max_concurrent=3)  # the team's own cap allows a third job
    with pytest.raises(RateLimitedError) as concurrency:
        limits.admit(A)
    assert concurrency.value.kind == "concurrency"
    _job(store, B, cost=4.0)
    with pytest.raises(RateLimitedError) as daily:
        limits.admit(B, daily_usd=4.0)
    assert daily.value.kind == "daily"
    limits.admit(B)  # the default $5 cap still has room


def test_error_kinds(store):
    limits = Limits(store, max_concurrent=10, per_minute=1, daily_usd=10)
    limits.admit(A)
    with pytest.raises(RateLimitedError) as caught:
        limits.admit(A)
    assert caught.value.kind == "minute"  # the principal's own window has its own kind
    assert RateLimitedError("x", 5).kind == "rate"


def test_window_custom_span(store):
    clock = [1000.0]
    limits = Limits(store, clock=lambda: clock[0])
    for _ in range(10):
        limits.window("slack-user|U1", "Sidecar requests", limit=10, span_s=600)
    with pytest.raises(RateLimitedError, match="Sidecar requests") as caught:
        limits.window("slack-user|U1", "Sidecar requests", limit=10, span_s=600)
    assert 0 < caught.value.retry_after_s <= 600 and caught.value.kind == "rate"
    limits.window("slack-user|U2", "Sidecar requests", limit=10, span_s=600)  # keys are independent
    clock[0] += 601
    limits.window("slack-user|U1", "Sidecar requests", limit=10, span_s=600)


def test_custom_window_does_not_use_the_principal_minute_budget(store):
    limits = Limits(store, max_concurrent=10, per_minute=2, daily_usd=10)
    for _ in range(5):
        limits.window(A.owner, "Sidecar requests", limit=10, span_s=600)
    limits.admit(A)
    limits.admit(A)
    with pytest.raises(RateLimitedError, match="this minute"):
        limits.admit(A)


def test_expired_window_keys_are_dropped_so_memory_does_not_grow(store):
    clock = [1000.0]
    limits = Limits(store, clock=lambda: clock[0])
    for n in range(300):  # a sign-in key per client address, each used once
        limits.window(f"admin-login|10.0.{n // 250}.{n % 250}", "sign-ins", limit=20, span_s=600)
    assert len(limits._recent) == 300
    clock[0] += 601
    limits.window("admin-login|10.9.9.9", "sign-ins", limit=20, span_s=600)
    assert list(limits._recent) == [("admin-login|10.9.9.9", 600)]
    clock[0] += 1
    limits.window("admin-login|10.9.9.9", "sign-ins", limit=20, span_s=600)  # a live key keeps counting
    assert len(limits._recent[("admin-login|10.9.9.9", 600)]) == 2
