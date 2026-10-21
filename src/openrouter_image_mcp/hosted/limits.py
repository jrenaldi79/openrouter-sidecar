"""Per-principal admission limits for paid work on the shared hosted server."""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import UTC, datetime

from ..advisor_results import AdvisorResults
from ..errors import OpenRouterError


class RateLimitedError(OpenRouterError):
    def __init__(self, message: str, retry_after_s: int, kind: str = "rate", *, limit: int | None = None,
                 span_s: float | None = None):
        # kind: "rate" (a named request window), "minute" (the principal's own per-minute window),
        # "concurrency" or "daily"; a window also carries its size and span. Callers phrase their own replies.
        self.retry_after_s, self.kind, self.limit, self.span_s = retry_after_s, kind, limit, span_s
        super().__init__(f"rate_limited: {message} Retry after about {retry_after_s} seconds.")


def _span_phrase(span_s: float) -> str:
    if span_s == 60:
        return "this minute"
    return f"in the last {int(span_s // 60)} minutes" if span_s % 60 == 0 else f"in {span_s:g} seconds"


SWEEP_EVERY_S = 60  # how often keys with nothing left in their window are dropped


class Limits:
    """Concurrency and daily spend come from durable job rows; the minute window is in memory."""

    def __init__(self, store, *, max_concurrent=2, per_minute=20, daily_usd=5.0, clock=time.time):
        self.results, self.clock = AdvisorResults(store), clock
        self.max_concurrent, self.per_minute, self.daily_usd = max_concurrent, per_minute, daily_usd
        self._recent: dict[tuple[str, float], deque] = defaultdict(deque)  # (key, span) -> call times
        self._swept_at = clock()
        self._image_calls: Counter = Counter()
        self._lock = threading.Lock()

    def window(self, key: str, what: str, *, limit: int, span_s: float, kind: str = "rate") -> None:
        """Sliding window of `limit` calls per `span_s` for `key`; windows of different spans are independent."""
        now = self.clock()
        with self._lock:
            self._sweep(now)
            recent = self._recent[(key, span_s)]
            while recent and recent[0] <= now - span_s:
                recent.popleft()
            if len(recent) >= limit:
                raise RateLimitedError(f"Too many {what} {_span_phrase(span_s)}.",
                                       max(1, int(recent[0] + span_s - now)), kind, limit=limit, span_s=span_s)
            recent.append(now)

    def _sweep(self, now: float) -> None:
        """Drop keys whose calls have all expired, at most once a minute: a key seen once (a sign-in from one
        address) would otherwise stay in memory for good. The caller holds the lock."""
        if now - self._swept_at < SWEEP_EVERY_S:
            return
        self._swept_at = now
        for key in [k for k, calls in self._recent.items() if not calls or calls[-1] <= now - k[1]]:
            del self._recent[key]

    def _window(self, owner: str, what: str = "advisor requests") -> None:
        self.window(owner, what, limit=self.per_minute, span_s=60, kind="minute")

    def _durable(self, owner: str, workspace: str, max_concurrent: int, daily_usd: float) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.results.connection() as db:  # also ensures the jobs table exists
            active = db.execute("SELECT COUNT(*) AS n FROM advisor_jobs WHERE owner=? AND "
                                "workspace=? AND status IN ('queued','running')",
                                (owner, workspace)).fetchone()["n"]
            spent = db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM advisor_jobs WHERE "
                               "owner=? AND workspace=? AND created_at>=?",
                               (owner, workspace, today)).fetchone()["s"]
        if active >= max_concurrent:
            raise RateLimitedError(f"Only {max_concurrent} advisor jobs can run at a time.", 30,
                                   "concurrency")
        if float(spent) >= daily_usd:
            raise RateLimitedError(f"Your daily advisor spend limit (${daily_usd:g}) is reached.",
                                   3600, "daily")

    def admit(self, scope, *, max_concurrent: int | None = None, daily_usd: float | None = None) -> None:
        """Per-principal caps; a Slack team passes its registry row's caps to override the defaults."""
        self._durable(scope.owner, scope.workspace,
                      self.max_concurrent if max_concurrent is None else max_concurrent,
                      self.daily_usd if daily_usd is None else daily_usd)
        self._window(scope.owner)

    @contextmanager
    def image_call(self, scope):
        """Image calls run inline (no job rows), so in-flight calls are counted in memory."""
        self._window("image|" + scope.owner, "image requests")
        key = (scope.owner, scope.workspace)
        with self._lock:
            if self._image_calls[key] >= self.max_concurrent:
                raise RateLimitedError(f"Only {self.max_concurrent} image calls can run at a time.", 30,
                                       "concurrency")
            self._image_calls[key] += 1
        try:
            yield
        finally:
            with self._lock:
                self._image_calls[key] -= 1
