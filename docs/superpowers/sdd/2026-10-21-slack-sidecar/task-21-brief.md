---
title: "2026-10-21-slack-sidecar: task 21 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 21 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 21: Wiring and isolation suite

**Files:**
- Create: `hosted/slack/wiring.py`, `tests/test_hosted_slack_isolation.py`
- Modify: `hosted/app.py` (build Slack only when `settings.slack and settings.slack.enabled`; replace
  `health.start()`'s single attempt with `OwnerLock` hooks: on gain, `recover()` then `gateway.start()`; on lose,
  `gateway.stop()`), `src/openrouter_image_mcp/hosted/CLAUDE.md`
- Test: `tests/test_hosted_slack_wiring.py`, `tests/test_hosted_app.py`

**Interfaces:**
- Produces:
  - Real `SlackServices` (defined in Task 14), one per cohort. `advisors` is an
    `AdvisorService(ws, AdvisorStore(target), AdvisorClient(ws, …), credentials=SlackCredentials(ws, keys), sandbox_default=False, sandbox_allowed=settings.sandbox_enabled)`.
  - `build_slack(settings, target, keys, provisioner, limits, *, transport=None, api: SlackApi | None = None) -> SlackApp`.
  - `SlackApp` has `gateway`, `dispatcher` and `services`, plus async `on_gain()`, `on_lose()` and `close()`.
  - `server.hosted["slack"]` exposes it to tests.

- [ ] **Step 1: Write failing tests.**
  - `test_not_built_when_disabled`.
  - `test_built_and_started_on_gain` (SQLite, with a fake socket client factory).
  - `test_end_to_end_mention_to_answer`: a `running_server` harness with `FakeSlack` and `FakeOpenRouter`. Feeding
    an `app_mention` envelope gives a provisioned team key named `Sidecar · abbott · 2027`, a posted answer with a
    footer, and a job row with `slack_user_id`.
  - Isolation suite (spec "Isolation"):
    - `test_team_cannot_read_other_team_chats_memory_threads_jobs_usage`: with two registered channels, every store
      called with team B's scope returns nothing of A's.
    - `test_usage_has_no_key_text`.
    - `test_forged_channel_in_tool_args_ignored` (end to end).
    - `test_no_query_filters_workspace_without_owner`: a static scan of SQL string literals in
      `hosted/slack/*.py`, `advisor_results.py` and `hosted/limits.py`. Any `WHERE` mentioning `workspace` or
      `cohort` must also mention `owner`. The scan allowlists `registry.py` (keyed by Slack ids) and
      `SlackJobs.orphans`.
- [ ] **Step 2–4:** Run, implement, run, plus the full suite and `-m pg`.
- [ ] **Step 5: Regenerate docs, then commit** `feat(slack): wire the Slack gateway into the hosted server`.

