---
title: "2026-10-21-slack-sidecar: task 20 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 20 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 20: Restart recovery

**Files:** Create `hosted/slack/recovery.py`. Test `tests/test_hosted_slack_recovery.py`.

**Interfaces:**
- Consumes: `SlackJobs.orphans(process_id)`, `AdvisorResults.interrupt`, `replies.post_answer` (or the runner's
  post step), `SlackApi.update`.
- Produces: `async recover(jobs, results_for: Callable[[str], AdvisorResults], api, registry, *, process_id) -> dict`.
  It returns `{"posted": n, "closed": m}`.
  - Completed and unposted: post the saved answer with its footer, then `mark_posted`.
  - Active: `interrupt(error="Interrupted by a server restart")`, then edit the working message (from
    `slack_working_ts`) to the spec text "Interrupted by a server restart. Ask again.".
  - It never retries the job.

- [ ] **Step 1: Write failing tests.**
  - `test_saved_result_posted`.
  - `test_running_job_closed_and_message_edited`.
  - `test_own_process_jobs_untouched`.
  - `test_slack_failure_leaves_row_for_next_gain`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): restart recovery on lock gain`.

