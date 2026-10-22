---
title: "2026-10-21-slack-sidecar: task 18 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 18 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 18: Owner lock with gain and lose hooks

**Files:** Create `hosted/ownership.py`. Modify `hosted/health.py` to use it. Test `tests/test_hosted_ownership.py`
and `tests/test_hosted_health.py`.

**Interfaces:**
- Produces: `OwnerLock(target, *, every_s=15.0, on_gain: list[Callable[[], Awaitable[None]]], on_lose: list[...])`,
  with `async start()`, `async stop()` and `held: bool`.
  - On SQLite it is always held, and `on_gain` runs once at start.
  - On Postgres a loop calls `pg_try_advisory_lock(OWNER_LOCK)` every `every_s` while not held. While held, it runs
    `SELECT 1` on the owner connection; a failure marks it not held, runs `on_lose`, and reconnects.
  - `stop()` runs `on_lose` if held, then closes.
  - `Health` keeps its public API. It registers hooks that set `gate.reason` (owner: `MAINTENANCE` or None;
    non-owner: the existing "Another Sidecar instance is active" text).

- [ ] **Step 1: Write failing tests.**
  - `test_sqlite_always_owner_runs_gain_once`.
  - `test_pg_second_lock_waits_then_gains` (marker `pg`): A holds and B doesn't; stop A; within `every_s` B gains
    and its `on_gain` ran.
  - `test_pg_connection_loss_runs_on_lose` (marker `pg`): terminate A's backend with
    `pg_terminate_backend` from a third connection, and A's `on_lose` runs.
  - The existing health tests stay green.
- [ ] **Step 2–4:** Run (including `-m pg`), implement, run.
- [ ] **Step 5: Commit** `feat(hosted): owner lock with gain and lose hooks`.

