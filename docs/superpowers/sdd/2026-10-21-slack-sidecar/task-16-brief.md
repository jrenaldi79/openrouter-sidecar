---
title: "2026-10-21-slack-sidecar: task 16 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 16 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 16: Usage report and budget warning

**Files:** Create `hosted/slack/usage.py`. Test `tests/test_hosted_slack_usage.py`.

**Interfaces:**
- Consumes: `openrouter_keys.current_key(key)`, `SlackJobs.spend_by_user`, `spend_since`, `NameCache`,
  `ChannelRegistry.mark_warned`.
- Produces:
  - `UsageReport(month_spend, budget_usd, remaining, today_spend, daily_cap, by_user: list[tuple[str, float]])`.
  - `async usage_report(key_info: dict, jobs, scope, row, names, now: datetime) -> UsageReport`. `month_spend`,
    `budget_usd` and `remaining` come from `key_info` (`usage_monthly`, `effective_budget`); `today_spend` and
    `by_user` come from job rows.
  - `usage_text(r) -> str` is plain lines with no key text.
  - `WARN_AT = 0.8`.
  - `budget_warning(r, row, month: str, registry) -> str | None` gives
    "This team has used N% of this month's Sidecar budget." once per month, through `mark_warned`.

- [ ] **Step 1: Write failing tests.**
  - `test_usage_text_has_no_key`: the key passed in `key_info` and its hash never appear.
  - `test_breakdown_by_user_names`.
  - `test_today_vs_cap`.
  - `test_warning_once_per_month`.
  - `test_usage_scoped_to_team`.
- [ ] **Step 2–4:** Run, implement, run. Then wire `budget_warning` into the runner footer (step 12).
- [ ] **Step 5: Commit** `feat(slack): usage report and 80% budget warning`.

