---
title: "2026-10-21-slack-sidecar: task 23 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 23 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 23: Documentation

**Files:**
- Modify: the spec (apply the five amendments, set `SIDECAR_TEAM_GUARDRAIL_ID` to removed, add `slack_posted_at`
  and `warned_month`, keep `status: "pending"` until acceptance)
- Modify: `docs/HOSTED_OPERATIONS.md` (new "Slack Sidecar" section):
  - create the `Sidecar team budget` guardrail in each cohort workspace ($75/month);
  - create the app from `deploy/slack/manifest.yaml`;
  - set `SIDECAR_SLACK_ENABLED`, `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`;
  - register channels with `slack_channels.py`;
  - read usage with `slack_usage.py`;
  - turning it off.
- Modify: `hosted/slack/CLAUDE.md` (final Owns list), the root `CLAUDE.md` gotchas (one line: Slack identity comes
  from the signed event and the registry; team keys use `Sidecar team budget`)

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Run** `node scripts/self-documentation/generate.js`, then `--check`. Expected: clean.
- [ ] **Step 3: Commit** `docs(slack): operations, folder notes and spec amendments`.

