---
title: "Task 4 report: per-team limits, per-user window, Slack job columns"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 4 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 4 report: per-team limits, per-user window, Slack job columns

Status: DONE. Commit `976f827` (`feat(slack): per-team limits, per-user window and Slack job columns`), pre-commit hook passed unbypassed.

## Implemented
- `hosted/limits.py` (90 lines): `RateLimitedError(message, retry_after_s, kind="rate")`; `Limits.window(key, what, *, limit, span_s)` with deques keyed by `(key, span_s)`, so a 600 s user window never touches the 60 s per-minute budget; `_window` calls it with `(per_minute, 60)`; `admit(scope, *, max_concurrent=None, daily_usd=None)`; `_durable` takes the effective caps and raises kind `"concurrency"` / `"daily"`; `image_call` concurrency error is kind `"concurrency"`. Message keeps "this minute" for 60 s, otherwise "in the last N minutes".
- `advisor_results.py` (192 lines): `slack_user_id`, `slack_channel_id`, `slack_working_ts` TEXT and `slack_posted_at` REAL via `ensure_column`; `create(..., slack_user_id=None, slack_channel_id=None)`; `update` allows `slack_working_ts` and `slack_posted_at`.
- `hosted/slack/jobs.py` (60 lines): `SlackJobs(target)` with `spend_by_user`, `spend_since`, `running`, `orphans`, `mark_posted`. All but `orphans` bind `owner=? AND workspace=?`; `orphans` filters `slack_channel_id IS NOT NULL AND process_id != ?` for active rows plus `completed` with `slack_posted_at IS NULL`. `mark_posted` is a direct UPDATE (not `AdvisorResults.update`, which requires a live job).
- `hosted/slack/CLAUDE.md` Owns list and the `orphans` exception to the owner+workspace invariant; `docs/index.md` regenerated and staged.

## TDD
RED (`task-4-red.txt`): `test_hosted_slack_jobs.py` failed on `ModuleNotFoundError ...slack.jobs`; the new limits and advisor_results tests failed (6 failed: TypeError, AttributeError, KeyError `slack_user_id`). GREEN: 19 passed (sqlite), 19 skipped (pg params). Full: `uv run pytest -q` 921 passed, 349 skipped; `ruff check` clean; file_sizes check clean.

Tests added: limits (`admit_overrides`, `error_kind_defaults_to_rate`, `window_custom_span`, `custom_window_does_not_use_the_principal_minute_budget`); advisor_results (`slack_columns_roundtrip`, null-by-default); slack_jobs (`spend_by_user_scoped` incl. other workspace and old rows, `spend_since`, `running`, `orphans_skip_own_process_and_posted`, `mark_posted_is_scoped`). All use `db_target`.

## Concerns
- The Postgres run (`pytest -m pg`) could not be run locally (`TEST_DATABASE_URL` unset); the tests are written with `db_target`, so CI's postgres job covers them. SQL uses only `?` placeholders, no literal `%`; the `running` IN-list is built from `?` marks only.
- `orphans` is cross-team by design; its caller (later runner task) must act on each row under that row's own team principal. Noted in the folder CLAUDE.md.
