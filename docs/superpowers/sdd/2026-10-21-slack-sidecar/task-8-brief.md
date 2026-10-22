---
title: "2026-10-21-slack-sidecar: task 8 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 8 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 8: Welcome, help and data notice

**Files:** Create `hosted/slack/welcome.py`. Test `tests/test_hosted_slack_welcome.py`.

**Interfaces:**
- Produces:
  - `NOT_SET_UP = "Sidecar isn't set up for this channel. Ask your instructor to add it."`
  - `GUEST_OFF`, the spec's sentence.
  - `DATA_NOTICE`, the spec's four bullets.
  - `welcome_text(row: ChannelRow, budget_usd: float | None) -> str` is the spec's welcome, verbatim. When
    `budget_usd` is None the budget sentence reads "Your team has a monthly budget (`@sidecar usage`)."
  - `help_text(row: ChannelRow) -> str` covers aliases and their targets, the default, the Fusion panel, `ask`,
    `compare`, follow-ups, memory, thread commands, `usage`, and `DATA_NOTICE`.

- [ ] **Step 1: Write failing tests.**
  - `test_welcome_matches_spec`: compare against the spec text with `$75`.
  - `test_welcome_without_budget`.
  - `test_help_lists_every_alias_and_command`: every `row.alias_map` key, and every command word from the parser.
  - `test_help_has_data_notice`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): welcome, help and data notice`.

