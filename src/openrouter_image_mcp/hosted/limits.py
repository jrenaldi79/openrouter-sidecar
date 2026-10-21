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
    def __init__(self, message: str, retry_after_s: int, kind: str = "rate"):
        # kind: "rate" (a request window), "concurrency" or "daily"; callers phrase their own replies.
        self.retry_after_s, self.kind = retry_after_s, kind
        super().__init__(f"rate_limited: {message} Retry after about {retry_after_s} seconds.")


def _span_phrase(span_s: float) -> str:
    if span_s == 60:
        return "this minute"
    return f"in the last {int(span_s // 60)} minutes" if span_s % 60 == 0 else f"in {span_s:g} seconds"


class Limits:
    """Concurrency and daily spend come from durable job rows; the minute window is in memory."""

    def __init__(self, store, *, max_concurrent=2, per_minute=20, daily_usd=5.0, clock=time.time):
        self.results, self.clock = AdvisorResults(store), clock
        self.max_concurrent, self.per_minute, self.daily_usd = max_concurrent, per_minute, daily_usd
        self._recent: dict[str, deque] = defaultdict(deque)
        self._image_calls: Counter = Counter()
        self._lock = threading.Lock()

    def window(self, key: str, what: str, *, limit: int, span_s: float) -> None:
        """Sliding window of `limit` calls per `span_s` for `key`; windows of different spans are independent."""
        now = self.clock()
        with self._lock:
            recent = self._recent[(key, span_s)]
            while recent and recent[0] <= now - span_s:
                recent.popleft()
            if len(recent) >= limit:
                raise RateLimitedError(f"Too many {what} {_span_phrase(span_s)}.",
                                       max(1, int(recent[0] + span_s - now)))
            recent.append(now)

    def _window(self, owner: str, what: str = "advisor requests") -> None:
        self.window(owner, what, limit=self.per_minute, span_s=60)

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
