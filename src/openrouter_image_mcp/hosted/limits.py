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
    def __init__(self, message: str, retry_after_s: int):
        self.retry_after_s = retry_after_s
        super().__init__(f"rate_limited: {message} Retry after about {retry_after_s} seconds.")


class Limits:
    """Concurrency and daily spend come from durable job rows; the minute window is in memory."""

    def __init__(self, store, *, max_concurrent=2, per_minute=20, daily_usd=5.0, clock=time.time):
        self.results, self.clock = AdvisorResults(store), clock
        self.max_concurrent, self.per_minute, self.daily_usd = max_concurrent, per_minute, daily_usd
        self._recent: dict[str, deque] = defaultdict(deque)
        self._image_calls: Counter = Counter()
        self._lock = threading.Lock()

    def _window(self, owner: str, what: str = "advisor requests") -> None:
        now = self.clock()
        with self._lock:
            recent = self._recent[owner]
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= self.per_minute:
                raise RateLimitedError(f"Too many {what} this minute.",
                                       max(1, int(recent[0] + 60 - now)))
            recent.append(now)

    def _durable(self, owner: str, workspace: str) -> None:
        today = datetime.now(UTC).date().isoformat()
        with self.results.connection() as db:  # also ensures the jobs table exists
            active = db.execute("SELECT COUNT(*) AS n FROM advisor_jobs WHERE owner=? AND "
                                "workspace=? AND status IN ('queued','running')",
                                (owner, workspace)).fetchone()["n"]
            spent = db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM advisor_jobs WHERE "
                               "owner=? AND workspace=? AND created_at>=?",
                               (owner, workspace, today)).fetchone()["s"]
        if active >= self.max_concurrent:
            raise RateLimitedError(f"Only {self.max_concurrent} advisor jobs can run at a time.", 30)
        if float(spent) >= self.daily_usd:
            raise RateLimitedError(f"Your daily advisor spend limit (${self.daily_usd:g}) is reached.",
                                   3600)

    def admit(self, scope) -> None:
        self._durable(scope.owner, scope.workspace)
        self._window(scope.owner)

    @contextmanager
    def image_call(self, scope):
        """Image calls run inline (no job rows), so in-flight calls are counted in memory."""
        self._window("image|" + scope.owner, "image requests")
        key = (scope.owner, scope.workspace)
        with self._lock:
            if self._image_calls[key] >= self.max_concurrent:
                raise RateLimitedError(f"Only {self.max_concurrent} image calls can run at a time.", 30)
            self._image_calls[key] += 1
        try:
            yield
        finally:
            with self._lock:
                self._image_calls[key] -= 1
