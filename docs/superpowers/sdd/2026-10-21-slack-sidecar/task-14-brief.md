---
title: "2026-10-21-slack-sidecar: task 14 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 14 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 14: Slack runner

**Files:** Create `hosted/slack/runner.py`. Test `tests/test_hosted_slack_runner.py`, using a `slack_rig` fixture
in that file (FakeSlack, `db_target`, `keys`, a scripted `MockTransport`).

**Interfaces:**
- Consumes:
  - `PreferenceService`, `AdvisorService`, `AdvisorStore`, `AdvisorResults`, `SessionFiles`, `AdvisorClient`
  - `run_tool_loop`, `build_slack_payload`, `ChannelTools`
  - `WorkingMessage`, `clean`, `split_answer`, `footer`, `limit_text`, `budget_text`, `provider_error_text`
  - `ThreadMap`, `ChannelMemory`, `Limits.admit`, `SlackJobs`, `acting_as`
- Produces:
  - `QUEUE_WAIT_S = 120.0`, `HEARTBEAT_S = 10.0`.
  - `SlackServices(advisors, preferences, store, results, files, client)` (dataclass), one per cohort. Task 21
    builds the real ones; the test rig builds them by hand.
  - `SlackJob` (frozen) fields:
    - `principal`, `row`, `channel_id`, `thread_ts`, `user_id`
    - `alias`, `label`, `mode`, `chat_id`
    - `brief`, `context: Context`, `domains: list[str]`, `compare: bool`
  - `SlackRunner(api, limits, threads, memory, jobs: SlackJobs, outputs=None, *, process_id, clock=time.monotonic)`.
    `outputs` stays None until Task 15 wires it:
    - `async run(services, job) -> None`
    - `stop(owner, channel_id, thread_ts) -> int`
    - `running(owner, channel_id, thread_ts=None) -> list[str]` (labels)

`run` steps:
1. Wait on a per-owner `asyncio.Semaphore(row.max_concurrent)` for at most `QUEUE_WAIT_S`. On timeout, post
   `limit_text` (concurrency).
2. Inside `acting_as`, call `limits.admit(scope, max_concurrent=row.max_concurrent, daily_usd=row.daily_cap_usd)`.
3. Wait for the chat to be idle: `results.active_for_chat`. Poll each second inside the same queue window; this is
   Review Focus 3.
4. `results.create(..., slack_user_id, slack_channel_id)`.
5. `WorkingMessage.start()`, then `update(slack_working_ts=…)`.
6. Upsert `context.files` and a `FilePacket(CONTEXT_PATH, context.text)` through `SessionFiles.upsert`; it's
   replaced, not added.
7. `build_slack_payload`, then `run_tool_loop` with `ChannelTools.call`. `on_items` maps server items to steps:
   - `openrouter:web_search` gives "Searched the web";
   - shell gives "Ran code in the sandbox";
   - `web_fetch` gives "Read a web page";
   - fusion gives "Compared the panel's answers";
   - subagent gives "Ran a helper".
8. A heartbeat task refreshes `heartbeat_until` every `HEARTBEAT_S`.
9. Get the answer with `services.advisors._answer(body, chat.container_id)`, after `_check_container` for sandbox
   chats.
10. Fusion: if every panelist failed, the answer is "Every model in the panel failed: …".
11. `store.append_turn`, then `results.update(status="completed", answer, cost_usd)`.
12. Post: `clean`, then `split_answer`. Up to 3 parts are posted as `Sidecar · <label>`, with the footer on the last
    part. More than 3 parts are posted as an upload `answer.md` plus the footer. Then `outputs.post(...)`
    (Task 15).
13. Retry the final post once on `SlackCallError`. On success, `jobs.mark_posted`, `threads.answered` and
    `threads.seen(context.newest_ts)`.
14. `WorkingMessage.finish("Sidecar · <label> answered below.")`.

Errors:
- `RateLimitedError` gives `limit_text`.
- `InsufficientCreditsError` or `ForbiddenError` gives `budget_text`.
- Any other `OpenRouterError` gives `provider_error_text`, and the job is failed through `results.update`.
- `LoopStopped` gives "Stopped.", and the job is interrupted with error "Stopped from Slack". Its result is
  discarded.

- [ ] **Step 1: Write failing tests.**
  - `test_happy_path_posts_answer_with_footer`.
  - `test_context_file_replaced_not_added`: two turns leave one `slack/context.md` at v2.
  - `test_queue_waits_then_runs` (Review Focus 3): two jobs on the same chat both complete, in order.
  - `test_queue_timeout_posts_limit`.
  - `test_daily_cap_text`.
  - `test_budget_hit_text`.
  - `test_provider_error_names_model_others_continue`: two jobs, one provider fails and the other posts.
  - `test_long_answer_uploaded_as_file`: more than 3 parts gives an upload named `answer.md`.
  - `test_progress_edit_failure_ignored` (Review Focus 5).
  - `test_final_post_retried_then_left_unposted` (Review Focus 5): two post failures leave `slack_posted_at` NULL.
  - `test_stop_discards_result`.
  - `test_job_row_attribution`: `slack_user_id` and `slack_channel_id` are stored.
  - `test_answer_escaped` (Review Focus 4, end to end).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** If the file passes 260 lines, move the post step into `replies.py` as
  `async post_answer(api, channel, thread_ts, label, text, footer) -> None`.
- [ ] **Step 4: Run**, plus the full suite.
- [ ] **Step 5: Commit** `feat(slack): job runner with queue, progress and stop`.

