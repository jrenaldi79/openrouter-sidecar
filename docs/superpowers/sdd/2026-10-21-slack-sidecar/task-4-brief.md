---
title: "2026-10-21-slack-sidecar: task 4 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 4 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 4: Limits per principal, per-user window, Slack job columns

**Files:**
- Modify: `hosted/limits.py`, `advisor_results.py`
- Create: `hosted/slack/jobs.py`
- Test: `tests/test_hosted_limits.py`, `tests/test_advisor_results.py`, `tests/test_hosted_slack_jobs.py`

**Interfaces:**
- Produces:
  - `RateLimitedError(message, retry_after_s, kind: str = "rate")`, where `kind` is one of `"rate"`,
    `"concurrency"` or `"daily"`.
  - `Limits.admit(scope, *, max_concurrent: int | None = None, daily_usd: float | None = None)`.
  - `Limits.window(key: str, what: str, *, limit: int, span_s: float) -> None`. `_window` becomes a call to it with
    `(per_minute, 60)`.
  - `advisor_jobs` gains columns `slack_user_id`, `slack_channel_id` and `slack_working_ts` (TEXT), and
    `slack_posted_at` (REAL). This last one is not in the spec: restart recovery needs to know whether a result was
    posted.
  - `AdvisorResults.create(..., slack_user_id=None, slack_channel_id=None)`. `update` accepts `slack_working_ts` and
    `slack_posted_at`.
  - `SlackJobs(target)` methods:
    - `spend_by_user(scope, since_iso: str) -> list[tuple[str, float]]`
    - `spend_since(scope, since_iso) -> float`
    - `running(scope, chat_ids: Collection[str]) -> list[dict]`
    - `orphans(process_id: str) -> list[dict]`: rows with `slack_channel_id IS NOT NULL` and another
      `process_id`, that are either active or `completed` with `slack_posted_at IS NULL`
    - `mark_posted(scope, job_id) -> None`

- [ ] **Step 1: Write failing tests.**
  - `test_admit_overrides`: with defaults `(2, $5)`, `admit(scope, max_concurrent=3)` allows a third job and
    `daily_usd=4.0` refuses at $4. The error `kind` values are `"concurrency"` and `"daily"`.
  - `test_window_custom_span`: `window("slack-user|U1", "Sidecar requests", limit=10, span_s=600)` passes 10 times,
    then raises with `retry_after_s` of 600 or less. A clock past 600 s allows the call again.
  - `test_slack_columns_roundtrip`: `create(..., slack_user_id="U1", slack_channel_id="C1")`, then
    `update(slack_working_ts="1.2")`, then `get` shows all three.
  - `test_spend_by_user_scoped`: jobs for two team scopes in one workspace each report only their own users.
  - `test_orphans_skip_own_process_and_posted`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use `ensure_column` next to the `shared_*` columns. Every `SlackJobs` query except
  `orphans` binds `owner=? AND workspace=?`. `orphans` is a server-side sweep filtered by `slack_channel_id`.
- [ ] **Step 4: Run** again, plus the full suite and `TEST_DATABASE_URL=… uv run pytest -m pg tests/test_hosted_slack_jobs.py`.
- [ ] **Step 5: Commit** `feat(slack): per-team limits, per-user window and Slack job columns`.

