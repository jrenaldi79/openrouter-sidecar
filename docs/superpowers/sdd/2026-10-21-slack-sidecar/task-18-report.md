---
title: "Task 18 report: owner lock with gain and lose hooks"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 18 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 18 report: owner lock with gain and lose hooks

Status: done.

## Changes
- `hosted/ownership.py` (new): `OwnerLock(target, *, every_s=15.0, on_gain, on_lose)` with `start()`, `stop()`, `held`, and public mutable `on_gain`/`on_lose` lists. `OWNER_LOCK` moved here (still importable from `health`).
- `hosted/health.py`: `Health` keeps its API; exposes `Health.lock`, registers `_gained`/`_lost` gate hooks first. `_lost` keeps a more specific reason (e.g. the shutdown notice). Non-owner at start logs the same error and sets the existing text.
- `tests/conftest.py`: extracted a Postgres-only `pg_target` fixture; `db_target` reuses it.
- Tests: `tests/test_hosted_ownership.py` (SQLite gain once, hook order and failure isolation by type with no message leak, late-appended hooks, idempotent stop, stop without start; pg: second lock waits then gains, connection loss runs on_lose then regains, stop releases lock, waiting lock closes connection on stop, unreachable DB never escapes or leaks the DSN). `tests/test_hosted_health.py`: gate follows hooks, pg second instance unready until owner stops.
- Docs: hosted/CLAUDE.md note, docs/index.md regenerated.

## Behaviour
- Start decides ownership synchronously (first tick), then a loop ticks every `every_s`: waiting replicas retry `pg_try_advisory_lock` on one kept-open connection; owner runs `SELECT 1`. Any psycopg/OS error logs the exception type only, runs on_lose if held, closes the connection and reconnects next tick.
- Thread calls are shielded and drained (<=2 s) on cancellation, and a connect that completes after cancellation closes itself, so cancelling the loop leaks no connection.

## Verification
- Red: collection failure (module missing) before implementation.
- `uv run ruff check` clean; `uv run pytest`: 1550 passed, 520 skipped; `-m pg` full: 499 passed, 17 skipped (local Postgres on 54999).
- Sizes: ownership.py 130ish lines, health.py under 100, all under 300; no function over 50.

## Fix round 1
- Gate: `STARTING` and `SHUTTING_DOWN` are shared constants in `workspaces.py` (`app.py` uses `SHUTTING_DOWN`). `Health._gained`/`_lost`/start only overwrite reasons Health owns (`None`, `STARTING`, `MAINTENANCE`, `NOT_OWNER`); `STARTING` is included because the gate begins with it and the first gain must clear it. Test: shutdown reason survives lose and regain.
- Loop safety: `_attempt` catches `Exception` (including connect `ValueError`), logs the type, does lose+close; `_close` catches `OSError` too. Test injects `RuntimeError` twice and checks the loop continues, on_lose runs each time and the lock is retaken.
- Bounded checks: connect uses `connect_timeout=max(1, int(every_s))`; `SELECT 1` and the lock try use `wait_for(..., every_s)`; a failed owner reconnects and retries the lock in the same tick. Documented in `docs/HOSTED_OPERATIONS.md` and the module docstring.
- `_close` waits (up to 2 s) for the in-flight call future, then closes from its done-callback if still running.
- `_lose` runs its hooks in a shielded task that `stop()` awaits; test cancels `stop()` mid-hook and all hooks still run.
- Health start log is neutral; dead `OWNER_LOCK` re-export removed.
- Verification: ruff clean; full suite and full pg results in the commit run.
