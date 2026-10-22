---
title: "Task 14 report: Slack job runner"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 14 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 14 report: Slack job runner

Status: DONE_WITH_CONCERNS

Commit: 92b3720 feat(slack): job runner with queue, progress and stop (BASE 735ddb2)

## Design

Three modules, split to stay under 300 lines a file and 50 lines a function (ruling 10):

- `hosted/slack/runner.py` (263 lines): `QUEUE_WAIT_S = 120.0`, `HEARTBEAT_S = 10.0`, `IDLE_POLL_S = 1.0`, `SlackServices`, `SlackJob` (frozen, the brief's fields), and `SlackRunner(api, limits, threads, memory, jobs, outputs=None, *, process_id, clock=time.monotonic, names=None)` with `run`, `stop` and `running`.
  - `run` wraps everything in `acting_as(job.principal)` and takes `key, scope` from `services.advisors._credentials()`. If that fails, it posts team-worded copy and makes no job row.
  - The per-owner `asyncio.Semaphore(row.max_concurrent)` is created on the owner's first job. `wait_for(acquire, QUEUE_WAIT_S)`; on timeout it posts the concurrency `limit_text`.
  - `_admitted` runs `Limits.admit(scope, max_concurrent=, daily_usd=)`, then polls `active_for_chat` every `IDLE_POLL_S`. It calls `results.create(..., slack_user_id, slack_channel_id)` with no await in between, so no other job in this process can take the chat first. An `AdvisorAccessError` from `create` (busy in another process or holding a lease) keeps it polling. All of this happens inside the same queue deadline, which uses the loop clock.
  - `_execute` runs `WorkingMessage.start`, sets status `running` with `slack_working_ts`, starts the heartbeat task, calls `turn.answer()` and handles `LoopStopped`, `CancelledError` and other exceptions. In a `finally` it always cancels and awaits the heartbeat, then delivers the answer. The semaphore is released in the `finally` of `_queued`.
  - `_deliver` calls `post_answer` with footer `"<label> · <model> · $cost"`. A second `SlackCallError` leaves `slack_posted_at` NULL and edits the working message to "Sidecar · <label> couldn't post its answer to Slack." On success it calls `outputs.post(turn)` (Task 15 hook, sandbox only, guarded so it can't fail the answer), then `mark_posted`, `threads.answered`, `threads.seen(newest_ts)` and "Sidecar · <label> answered below."
- `hosted/slack/runner_turn.py` (155 lines): `Turn` is one admitted job, and every job-row write goes through it (`update`, `guard`, `beat`, `fail`, `interrupt`). `answer()` does the following:
  1. Trims the files (ruling 2) and adds the `slack/context.md` packet.
  2. Calls `AdvisorService._prepare(chat_id, brief, None, key, scope, packets)`, which redacts the brief and refuses the key inside files.
  3. Applies the history cap: `ChatFullError` tells the team to use `!restart`.
  4. Calls `SessionFiles.upsert`, which replaces `context.md`; its delta note goes to the model only.
  5. Runs `_cleanup`, then for sandbox chats that are not compares `_sandbox_uploads` with the job row's lease token.
  6. Builds the request with `build_slack_payload(..., listed=)` and `configure_cache`, then runs `run_tool_loop` with `should_stop` reading the stop flag. `_cleanup` runs again in a `finally`.
  7. Checks the result: `panel_failure` for compare jobs, `_check_container` over every round's items for sandbox chats, then `_answer`, with the key replaced.
  8. Calls `append_turn` with the plain redacted brief (ruling 3), then updates the row to completed with the loop's cost.
  - Function tools adapt `ChannelTools.call` to the loop's string signature. The label becomes a progress step unless it is `Tool error`. Compare jobs get `{}` (ruling 4).
  - `on_items` maps server items to step labels and keeps every item for Task 15.
- `hosted/slack/runner_steps.py` (115 lines, pure): `step_label`, `panel_failure`, `trim_files`, `failure_text`, `ChatFullError` and `HISTORY_CHARS`.
- `replies.py` gains `post_answer` (ruling R2) and `key_text`. The retry-once policy lives in `post_answer`, per Slack call: a second failure propagates, and no part is ever posted twice. A 429's `retry_after_s` is honoured up to 5 seconds before the retry. Uploads hold the masked, unescaped text, because Slack doesn't parse files for mentions. The footer is escaped.

How the rulings were handled:
- Ruling 5 (stop): `results.update(cost_usd=stop.cost_usd)`, then `results.interrupt(..., error="Stopped from Slack", suppress=True)`, then `finish("Stopped.")`. No turn is appended.
- Ruling 6 (team wording): `failure_text` sends `AuthRequiredError` and `ProviderError(UNAVAILABLE)` through `key_text`. It never shows `error.message` for key errors or for unexpected errors.
- Ruling 7: the heartbeat is cancelled in a `finally`, the semaphore is released in a `finally`, and `acting_as` wraps the whole run (the heartbeat task copies the context).
- Ruling 8 (`stop` and `running`): both cover queued as well as running jobs. A job stopped while queued posts "Stopped." and creates no row.
- Ruling 9: progress labels are raw text, and `WorkingMessage` swallows `SlackCallError`.

## Deviations and choices

1. **The advisor client now raises typed 402 and 403 errors.** `AdvisorClient._request` and `respond_stream` raised `ProviderError` for every non-2xx status, so the brief's `InsufficientCreditsError` and `ForbiddenError` path could never happen. A new `_status_error` raises `InsufficientCreditsError` for 402 and `ForbiddenError` for 403 with the same message text. Both are still `OpenRouterError`, and MCP tools map every `OpenRouterError` the same way, so the student-facing MCP behaviour is unchanged. The advisor and full suites stay green. A client test was added.
2. **Server tool item types are matched loosely**, for example `"web_search" in type`, because the Task 0 live run is still pending and the exact type names are unknown. `panel_failure` is also a tolerant guess. It looks for a Fusion item with a list under `responses`, `results`, `analyses` or `panel`, where each panelist failed (`error` is truthy or `status` is failed/error/incomplete/cancelled). Adjust it after the live run.
3. **Error reply placement.** Before a working message exists (credentials, limits, queue timeout, stopped while queued), the reply is a new thread post as `Sidecar · <label>`. After that, the working message is edited.
4. **Footer.** It names the alias label and the model id (`Gemini · g/model · $0.0100`), with no 80% warning; that is dispatcher or usage scope.
5. **Extra protections added.**
   - A history cap with Slack copy, mirroring `send`'s 300k check.
   - The row is marked `running`.
   - A heartbeat that loses the key or lease cancels the job, which is then interrupted with "Interrupted. Ask again." and the `CancelledError` is re-raised.
   - A `CancelledError` during `start()` interrupts the row.
   - The delta note ("Updated since last turn: slack/context.md (v2)") goes to the model like `send`'s, but is not stored.
6. **`names`.** `SlackRunner` takes an optional `names: NameCache` and otherwise makes one. `clock` only paces progress edits; queue deadlines use the event loop's clock, so a fake test clock can't expire the queue.
7. **`outputs.post(turn)` signature** is my placeholder for Task 15. `Turn` exposes `items`, `key`, `scope`, `job`, `sandbox` and `services`.

## Tests

- The brief's 13 tests and the ruling tests are in `tests/test_hosted_slack_runner.py`, `test_hosted_slack_runner_errors.py` and `test_hosted_slack_runner_files.py`.
  - Ruling 2: trimmed overflow is listed, and a replaced path doesn't count against the room.
  - Ruling 3: the stored history holds the plain brief.
  - Ruling 4: a compare in a sandbox chat makes no uploads and gets only the Fusion tool.
  - Ruling 5: a stopped job records its cost.
  - Ruling 6: a 401 and a missing key both reply in team wording.
  - Also covered: progress steps, retried-then-posted, `running`/`stop`, panel failure, sandbox bundle and cleanup, and a foreign container in an earlier round.
- `tests/test_hosted_slack_runner_steps.py` covers the pure helpers. `tests/test_hosted_slack_runner_turn.py` covers the heartbeat refresh, lost key mid-job, the full chat and tool errors not becoming steps.
- The rig is shared in `tests/slack_runner_rig.py`: FakeSlack, a KeyBox key, real stores on `db_target`, and an async `MockTransport` with gates. There are no fixed sleeps; `wait_until` polls conditions.
- RED: every new test file failed at collection (ImportError), and the two client tests failed. Recorded in `task-14-red.txt`.
- Results:
  - `uv run pytest`: 1376 passed, 415 skipped, 9 deselected.
  - `TEST_DATABASE_URL=postgresql://$USER@127.0.0.1:54999/sidecar_test uv run pytest -m pg`: 393 passed, 17 skipped.
  - `ruff check` is clean, and `generate.js --check` passes. The pre-commit hooks passed.
  - The runner and turn tests were run 5 times in a row with no flakes.

## Concerns

1. **Later turns lose earlier context.** `slack/context.md` is replaced each turn with only the new thread messages, and history stores only the plain brief (ruling 3). So on turn 2 the model no longer sees turn 1's thread context. The spec says "the chat's own history carries the rest", but history carries only brief and answer. Possible fixes are storing the context delta in the turn, or rendering the full thread each turn.
2. Item type names and the Fusion panel failure shape are guesses until the live spike runs (deviation 2).
3. The per-owner semaphore keeps the `max_concurrent` it was first made with until the process restarts. A per-user request window (`Limits.window`) is not applied here; it is left to the dispatcher (Task 17).

## Fix round 1

Commit: 95006e1 fix(slack): runner heartbeat, spend on failure, fair queue, moderation

1. **Heartbeat race (Important).** `_execute` runs `Turn.work(ts)` in its own task. `_watched` starts the heartbeat, which can cancel only that task, and returns `(answer, None)` or `(None, problem)`. It cancels and awaits the heartbeat in its `finally`, before `Turn.finish` dispatches to deliver, stopped, failed or lease-lost.
   - A heartbeat cancel is told apart from an outer one with `current_task().cancelling()`. A heartbeat cancel becomes `LeaseLost`: the row is interrupted, the working message says "Interrupted. Ask again." and `run` returns.
   - A real outer cancel interrupts the row, finishes the working message and propagates.
   - `test_lost_key_mid_job_interrupts` was updated. New tests: `test_heartbeat_cannot_cancel_a_stopped_finish`, `test_heartbeat_cannot_cancel_a_failed_finish` and `test_outer_cancel_still_propagates`.
   - The ending handlers moved into `Turn` (`finish`, `_stopped`, `_failed`, `_deliver`) to keep `runner.py` under 300 lines.
2. **Cost on failure (Important).**
   - `run_tool_loop` now sets `items` and `cost_usd` on any `OpenRouterError` raised mid-loop. The docstring is updated, and `test_provider_error_mid_loop_carries_items_and_cost_so_far` covers it.
   - `Turn.spent` takes the error's cost or the loop's cost once it returns, and `Turn.fail` writes it to `cost_usd`.
   - Tests: `test_cost_kept_when_the_answer_fails_after_the_loop` (foreign container, $0.02) and `test_cost_kept_when_a_later_round_fails` (402 after one round, $0.03).
3. **Queue fairness (Important).** New `runner_queue.py` holds `Entry` (stop flag plus event), `Shared` (per-key primitives counted by the jobs using them, dropped with the last one) and `wait_turn` (acquire by a deadline, or end when stopped; a won acquisition is released unless it is returned).
   - The order is the chat's FIFO `asyncio.Lock`, then the team `Semaphore`, by one `QUEUE_WAIT_S` deadline. `active_for_chat`/`create` stays as the cross-process check.
   - A chat-wait or idle-poll timeout posts `CHAT_BUSY`: "Sidecar is still answering the previous question in this thread. Ask again in a moment."
   - Tests: three same-chat asks run in order and leave no locks or slots behind; a waiting follow-up holds no team slot (another chat answers meanwhile); a chat-wait timeout posts `CHAT_BUSY`; another process's active job is polled through the job rows. `test_queue_waits_then_runs` now waits on `running()` instead of counting idle polls, since same-process waits now sit on the chat lock.
4. **Stop while queued.** `stop()` sets the entry's event, which wakes `wait_turn`. `_admitted` checks `entry.stopped` before `admit`. `test_stop_wakes_a_queued_job_before_admission` covers both the chat-lock wait and the slot wait, and checks that `admit` ran once (only for the first job) and that one job row exists.
5. **403 moderation.** `_body_text`, `_error_info` and `_moderation_error` moved from `client.py` into the new `error_bodies.py`. `client.py` imports them under the old private names, so `client.py` went from 353 to 310 lines.
   - `AdvisorClient._status_error(response)` returns a `ModerationError` for a moderation-flagged 403 and `ForbiddenError` for any other 403. The streaming path reads the body only on a 403.
   - `failure_text` maps `ModerationError` to `MODERATED`: "The provider flagged this request and didn't answer."
   - Tests are in `tests/test_error_bodies.py`, plus 403 moderation tests for `respond` and `respond_stream`, a runner test and a steps assertion.
6. **Semaphore size.** Slots live in a `Shared` keyed by owner, so the semaphore is rebuilt with the current `max_concurrent` once the team has no queued or running jobs. `test_team_slots_follow_a_changed_row_once_idle` covers it.
7. **Delivery order.** `mark_posted` runs right after `post_answer`, then `outputs.post`, then `answered`/`seen`. A failure in those two is logged by type name and the job still finishes with "answered below". Test: `test_delivery_marks_posted_before_outputs_and_survives_bookkeeping`.
8. **Full session.** `trim_files` already reserved room for the new `context.md` (`room = MAX_SESSION_CHARS - context_chars - staying`). A `BadRequestError` from the upsert, which can only be the size limit since `_prepare` validated the files, now raises `ChatFullError`. Its copy is now "This thread's <label> chat is full. Start over with @sidecar !restart." Test: `test_full_session_asks_for_restart`.

Reviewer probes: 8 of 9 pass against the new code. `test_probe_stop_while_queued_on_semaphore` makes its final assertion after the first job has posted its answer, so `texts()[-1]` is that answer. Its own printout shows "Stopped." was posted when it should be. `test_stop_wakes_a_queued_job_before_admission` asserts this at the right point.

Results:
- `uv run pytest`: 1401 passed, 430 skipped, 9 deselected.
- `-m pg` against 127.0.0.1:54999: 408 passed, 17 skipped.
- `ruff check` is clean, the docs check passes, and the pre-commit hooks passed.
- The runner, queue and turn tests were run 5 times in a row with no flakes.
- New and changed files are all within the 300-line limit (runner 235, runner_turn 237, runner_queue 89, runner_steps 120, client 310 down from 353).

Concerns:
- The first round's concern about later turns losing earlier thread context still stands.
- Same-chat follow-ups that wait longer than `QUEUE_WAIT_S` (a long sandbox job) get `CHAT_BUSY` rather than waiting indefinitely.

## Fix round 2

Commit: 38cc95f fix(slack): runner spend on lost lease, guarded bookkeeping

1. **Spend on a lost lease or outer cancel.** I took the loop route.
   - `run_tool_loop` now has one `except _CARRIES_SPEND` (`LoopStopped`, `OpenRouterError`, `asyncio.CancelledError`) that attaches `items` and `cost_usd` and re-raises.
   - `Turn.answer` reads `cost_usd` from an `OpenRouterError` or a `CancelledError` into `Turn.spent`.
   - `AdvisorResults.interrupt` gained `cost_usd=` (`COALESCE(?, cost_usd)`), so the interrupted row keeps the spend even when the lease or heartbeat has lapsed and `update` would be refused. `Turn.interrupt` always passes `self.spent`, and `_stopped` now uses the same path.
   - Tests: `test_lost_lease_keeps_spend` and `test_outer_cancel_propagates_and_keeps_spend` ($0.04 round, then the request hangs), and `tests/test_advisor_loop_spend.py` (a cancellation and a provider error both carry items and cost). That last file is new because `test_advisor_loop.py` would have gone past 300 lines; the earlier provider-error loop test moved there.
2. **`mark_posted` guarded.** `Turn._logged` wraps `mark_posted`, `answered` and `seen`: each failure is logged by type name, and "answered below" always runs. Test: `test_mark_posted_failure_still_says_answered`.
3. **Cleanup can't mask a cancel.** `Turn._cleanup_after` re-raises a cleanup error only when the task is not cancelling; while cancelling it logs the error and returns False. Test: `test_cleanup_error_cannot_replace_a_cancel` (the second cleanup raises during a lost-lease cancel; the job still ends Interrupted).
4. **Heartbeat logging.** Any non-`OpenRouterError` is logged by type name and ends the beat. The row then lapses on its own, as before. Test: `test_heartbeat_logs_unexpected_errors` checks the type name is logged, the message is not, and the job still answers.
5. **`run_tool_loop` length.** It is now 49 lines, after merging the two except clauses into one tuple.
6. **Bad concurrency values.** The semaphore is built with `max(1, row.max_concurrent)`. A cap below 1 then fails `Limits.admit` with the concurrency copy instead of raising outside the `try`. Test: `test_bad_concurrency_value_posts_limit_instead_of_raising`.
7. **Moderation row error.** The row's `error` is now the fixed "Blocked by provider moderation." instead of the message that quotes `flagged_input`. `test_moderation_block_has_its_own_copy` now asserts this.

All 6 round-2 probes pass.

Results:
- `uv run pytest`: 1407 passed, 435 skipped, 9 deselected.
- `-m pg` against 127.0.0.1:54999: 413 passed, 17 skipped.
- `ruff check` is clean, the docs check passes, and the pre-commit hooks passed.
- The turn, queue and loop-spend tests were run 5 times in a row with no flakes.
- All touched files are within the size limits.
