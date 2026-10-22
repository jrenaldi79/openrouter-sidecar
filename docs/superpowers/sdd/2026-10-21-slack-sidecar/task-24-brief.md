---
title: "2026-10-21-slack-sidecar: task 24 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 24 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 24: Live acceptance (manual, with the user)

**Files:** Modify `docs/superpowers/specs/2026-10-18-hosted-decision-record.md` (Slack Sidecar section). When it
passes, set the spec's status to `completed`.

Follow the spec's Rollout steps 1 to 3 and Live acceptance items 1 to 6, in a test Slack workspace with channels A
and B. The deploy, the Railway variables and the guardrail creation are the user's steps; ask before each one. For
each item, record pass or fail and the evidence (thread link, OpenRouter activity row, no key text). Item 4 reuses
`docs/superpowers/plans/2026-10-20-sandbox-isolation-live-test.md` with A and B as the two accounts. Item 5 also
records whether amendment 4 (Fusion `tools`) blocked the panel fetch.

- [ ] **Step 1: Run acceptance items 1 to 6** with the user.
- [ ] **Step 2: Record the results**, then commit `docs(slack): live acceptance results`.
- [ ] **Step 3: Pilot one capstone channel** (Rollout 4) only after the user approves.

