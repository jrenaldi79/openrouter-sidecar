---
title: "Task 20 report: restart recovery"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 20 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 20 report: restart recovery

Status: done.

## What was built
- `hosted/slack/recovery.py`: `recover(jobs, results_for, api, registry, threads, *, process_id, clock=time.time) -> {"posted", "closed"}`. `results_for(workspace_id)` returns the cohort's `AdvisorResults`.
- `ThreadMap.thread_for(scope, chat_id) -> str | None` (scope-bound; unarchived row first, then newest; an archived thread still resolves).
- Tests: `tests/test_hosted_slack_recovery.py` (16 tests, each on SQLite and pg) and two `thread_for` tests in `tests/test_hosted_slack_threads.py`. Written first, seen failing (import error), then green.
- `hosted/slack/CLAUDE.md` and `docs/index.md` updated.

## Rulings applied
1. Each row is handled under its own team: owner parsed as `slack|T:C`, C must equal `slack_channel_id`, the registry row must exist and be active, `team_principal` + `acting_as` wrap the work. A student row, a channel mismatch, an unregistered channel or an inactive channel is skipped and logged ("skipped"), untouched.
2. Active rows are orphaned only if `heartbeat_until < clock()` (injected clock, tested at the boundary).
3. Lapsed active rows: `interrupt(scope, id, row["process_id"], error="Interrupted by a server restart")` (cost kept; `cost_usd=None` keeps the recorded value), then `api.update(..., "Interrupted by a server restart. Ask again.")` if `slack_working_ts`; an edit failure is logged and the row still counts as closed.
4. Completed and unposted: thread via `thread_for`, `post_answer`, then `mark_posted`, then `threads.answered`. No thread, or a row created over 24 h ago: `mark_posted` without posting (a missing thread is logged). A Slack failure leaves the row unposted for the next gain.
5. Never raises: each row has its own try, the `orphans()` call is guarded, failures are logged by exception type.
6. Nothing is retried.

## Verification
- `uv run ruff check`: clean.
- `uv run pytest`: 1611 passed, 576 skipped (pg cases skip without a URL).
- `TEST_DATABASE_URL=postgresql://$USER@127.0.0.1:54999/sidecar_test uv run pytest -m pg`: 555 passed, 17 skipped.
- `generate.js --check`: current. New files are under 300 lines (recovery.py 129).

## Concerns
- The alias label (for example "Gemini") is not stored on the job row, so a recovered post is labelled and footed with the model id (`Sidecar · google/...`). Storing the label on the row would fix this.
- A recovered answer leaves the original "working" message as it was (no "answered below" edit); the rulings didn't ask for one.
- `results.create()` runs `_recover` on its scope, which interrupts lapsed active rows of that scope; tests that create several jobs in one scope take that into account.

## Fix round 1
- `ThreadMap.thread_for` became `thread_chat(scope, chat_id) -> (thread_ts, alias) | None` (scope-bound, unarchived first).
- A recovered post is labelled like a live answer: `COMPARE_LABEL` ("Fusion") for `COMPARE_ALIAS`, else `alias_map[alias]` when it is a non-empty string without `/`, else the model id (recovery has no catalog). Footer is `<label> · <model> · <cost>`. Tests: brand path, fallback (unmapped, slashed, blank) and compare.
- Test added: `mark_posted` raising after a successful post is logged by type, `recover` doesn't raise, the row stays unposted.
- Heartbeat check is now `heartbeat_until <= clock()` (as `advisor_results`); the boundary test checks `NOW + 4.9` (left alone) and `NOW + 5` (closed).
- Checks: ruff clean; recovery + threads tests 30 passed on SQLite (pg variants pass in the pg run); full pytest 1614 passed; pg run 558 passed.
