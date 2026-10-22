---
title: "Final review fixes: Slack Sidecar (e7dbb57..772ac73)"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): report on the fixes from the final review."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Final review fixes: Slack Sidecar (e7dbb57..772ac73)

Fixes for `final-review.md`. Every bug fix has a test that failed before the fix (each was run red first). Nothing was pushed.

## Commits

| SHA | Subject | Stat |
| --- | --- | --- |
| 4f8f216 | fix(slack): Slack transport errors are Slack call errors | 10 files, +180 −9 |
| 408bf73 | fix(slack): recovery revisits rows whose owner died | 5 files, +146 −25 |
| 701070b | fix(slack): fetch domains come from people's messages only | 5 files, +83 −5 |
| 7ad2cfc | fix(slack): stop, limits, cost and copy fixes from the final review | 21 files, +335 −51 |
| 772ac73 | docs(slack): final review follow-ups | 5 files, +41 −20 |

## Gates at 772ac73

| Gate | Result |
| --- | --- |
| `uv run pytest` | 1694 passed, 627 skipped, 9 deselected (baseline at e7dbb57: 1644 passed) |
| `pytest -m pg` (throwaway Postgres on :54999) | 606 passed, 17 skipped |
| `uv run ruff check` | clean |
| `file_sizes --all` | only the grandfathered files; no new file over 300 lines |
| `generate.js --check` | clean |

No function is over 50 lines (AST scan of the changed modules). `run_tool_loop` briefly reached 55 lines; the M3 commit extracts `_total` and merges the exits, bringing it back to 47.

## Per finding

### I1: Slack transport errors (4f8f216)

- **`api.py` `_call`:** `SlackApiError` keeps Slack's code. Timeouts and dropped connections become `SlackCallError(method, "network")`, raised `from None` so no message or context can carry a URL with a token. That covers `aiohttp.ClientError`, `httpx.HTTPError`, `TimeoutError` (which `asyncio.TimeoutError` aliases) and `OSError`. Any other `SlackClientError` becomes `"client_error"`. Programming errors such as `KeyError` still propagate.
  - Pinned by `tests/test_hosted_slack_api_transport.py`.
- **Progress edit:** `WorkingMessage._edit` is best effort for any exception, logging the type only. A failed progress edit mid-answer now leaves the job completed with cost 0.75, the answer posted and "answered below".
  - Pinned by `test_hosted_slack_runner_transport.py::test_failed_progress_edit_does_not_fail_the_job` (the reviewer's probe), for a raw `TimeoutError` and a `RuntimeError`.
- **Working-message start:** `SlackRunner._execute` treats a start that fails for any exception as a failed start, so the row is `failed` with `START_FAILED` and is never stuck in `queued`.
  - Pinned by `test_failed_working_message_leaves_no_queued_row` (the reviewer's probe).
- **Cost on every failure path:** `run_tool_loop` attaches `items` and `cost_usd` to any exception, and `Turn.answer` reads them from any exception. Other failure paths already kept spend: `fail`/`interrupt` record `self.spent`, which is set before `_final`, `guard`, `append_turn` and the deadline path.
  - Pinned by `test_unexpected_error_mid_loop_keeps_the_spend`, an unexpected error after two priced responses that leaves the row `failed` with cost 0.75.
  - Also pinned by `test_advisor_loop_spend.py::test_any_unexpected_error_carries_items_and_cost`.
- **Guest mark:** in `GuestGuard.on_member_joined`, any failed lookup sets the recheck mark (Slack errors are logged redacted, others by type).
  - Pinned by `test_hosted_slack_guests.py::test_any_join_lookup_failure_sets_the_mark`.

### I2: recovery revisits dead owners' rows (408bf73)

- **Waiting count:** `recover` still skips fresh heartbeats, and now counts them as `waiting` in its result.
- **Second pass:** when `waiting` is non-zero, `SlackApp._recover` sleeps `RECOVERY_RECHECK_S` and sweeps once more, then returns the summed counts.
  - The delay is the 60-second row heartbeat plus 15 s, through the injectable `SlackApp.sleep`.
  - The wait lives in the held `recovery_task`, so `on_lose` and `close` cancel it.
- **Test:** `tests/test_hosted_slack_wiring_recovery.py` sets up three fresh `running` rows from `proc-dead` at `max_concurrent=3`.
  - At the first pass the rows are untouched, no edits are made, and `Limits.admit` raises.
  - After the second pass all three rows are `interrupted`, the three working messages read `INTERRUPTED_EDIT`, and `admit` passes.
  - Two more tests cover no second pass when nothing waited, and `on_lose` cancelling the pending pass.
  - Existing recovery and wiring assertions gained `"waiting": n`.

### I3: fetch domains from people only (701070b)

- **People only:** `context._urls` keeps only links from messages that `by_a_person` accepts. It rejects a message with any of these:
  - a `bot_id`;
  - the `bot_message` subtype;
  - a label (Sidecar's `username`, Claude Tag's bot profile);
  - no author;
  - Sidecar's own user.
- **Plumbing:** `SlackMessage` gains `bot_id`. `ContextBuilder.bot_user_id` is set from `auth.test` together with the gateway and dispatcher. Bot posts still render as context.
- **Tests:** `tests/test_hosted_slack_context_urls.py` has six parametrized bot and model cases, plus a human link that is still allowed, rendering unchanged and `from_raw` keeping `bot_id`. The wiring test asserts the builder's bot id.

### Minors (7ad2cfc)

- **M1:**
  - **Change:** `Turn.answer` raises `LoopStopped(result.items, result.cost_usd)` after the loop when the entry was stopped. The answer is discarded, the spend kept, no turn is stored and the thread shows "Stopped.".
  - **Test:** `test_hosted_slack_runner_endings.py::test_stop_during_the_final_request_discards_the_answer`.
- **M2:**
  - **Change:** `Limits.admit`'s per-minute window raises kind `"minute"` with `limit` and `span_s`; `window()` carries both too. `limit_text` says "Your team has sent 20 requests in the last minute. Try again in about 1 minute.". Students' message text is unchanged.
  - **Tests:** `test_hosted_limits.py` (`test_per_minute_window`, `test_named_window_reports_its_size_and_span`, `test_error_kinds`), `test_hosted_slack_replies.py::test_limit_texts` and `test_hosted_slack_runner_steps.py::test_failure_text_maps_each_error`.
- **M3:**
  - **Change:** the loop's cost is the sum of known costs. It is `None`, and the footer says "cost unknown", only when no response reported one (or when no request was made, which gives 0 as before).
  - **Tests:** `test_advisor_loop.py::test_cost_is_the_known_sum_when_a_round_lacks_numeric_cost` (replacing the old "None when any lacks" test) and `test_hosted_slack_runner_endings.py::test_a_response_without_a_cost_keeps_the_known_spend` (row cost 0.25, footer `$0.2500`).
- **M4:**
  - **Change:** `JOB_DEADLINE_S = 1800` in `runner.py`, below `OPERATION_LEASE_S`, the 40-minute chat lease. `_watched` arms a timer through the injectable `SlackRunner.call_later` (the loop's `call_later` by default) that cancels the work. The outcome is `DeadlinePassed`: the row fails with `DEADLINE_ERROR` and keeps the spend, and the working message ends with "Sidecar · <label> ran for 30 minutes and stopped. Try a smaller question.". The timer is cancelled on every ending.
  - **Tests:** `test_deadline_fails_the_job_and_keeps_its_spend` (injected timer fired mid-request, cost 0.30 kept), `test_a_job_that_finishes_cancels_its_deadline` and `test_deadline_is_below_the_chat_lease`.
- **M6:**
  - **Change:** `asks._chat_id` checks the mapped chat with `store.get`. On `AdvisorAccessError` (purged) it starts a fresh chat, and `put` replaces the dead link.
  - **Test:** `test_hosted_slack_asks.py::test_a_purged_chat_starts_fresh`. It was confirmed red with the check removed.
- **M7:**
  - **Change:** `asks.MAX_MODELS = 4`. A larger ask gets the one escaped line "I can ask up to 4 models at once.", with no reads, chats or jobs.
  - **Test:** `test_hosted_slack_asks.py::test_at_most_four_models_per_ask`.
- **M8:**
  - **Change:** `OwnerLock.failed` is True after an attempt raised, and is set before the lose hooks run. `on_refused` hooks run when a lock try is refused right after a failure. Health uses `RECONNECTING` ("The Sidecar is reconnecting to its database; retry shortly.") on a loss or start that follows a failed check, and switches to `NOT_OWNER` on refusal. The not-owner text is unchanged.
  - **Tests:** `test_hosted_health.py::test_a_failed_database_check_closes_the_gate_with_a_neutral_reason` and `test_hosted_ownership.py::test_pg_failed_check_is_flagged_and_a_refusal_after_it_runs_on_refused` (pg).
- **M10:**
  - **Change:** `Limits._recent: dict[tuple[str, float], deque]`.
  - **Test:** none (annotation only).
- **Registry:**
  - **Change:** `_validate` rejects a non-finite or non-positive `daily_cap_usd`, `max_concurrent < 1`, and a `default_model` that isn't in `alias_map`, on both `add` and `update`.
  - **Tests:** `test_hosted_slack_registry.py::test_money_and_default_guards` (8 cases). Two existing fixtures were made valid: `test_add_get_roundtrip` now uses default `x`, and the recovery label test passes `default_model`.
- **Parser:**
  - **Change:** `_alias` strips trailing `,:;`. The `and <alias>` join uses the stripped form.
  - **Tests:** `test_hosted_slack_parser.py::test_ask_strips_punctuation_after_an_alias`, `..._after_a_joined_alias`.

### Docs (772ac73)

- **Spec, Welcome:** the quoted welcome now says "Your team has a monthly budget (`@Sidecar usage`)", with the controller ruling noted.
- **Spec, Isolation and security:**
  - The fetch domains come from people's posts only.
  - New residual-risk bullet: allowlisting a multi-tenant host such as `docs.google.com` allows GETs to any form on that host. It is recorded at live acceptance step 5, and the team's `fetch_allowlist` is the control. Step 5 now says to record it.
  - The Tools table row was corrected to match.
- **Spec, Commands and Errors:**
  - the 4-model cap;
  - the recovery second pass;
  - the 30-minute deadline;
  - purged chats starting fresh.
- **Folder notes:**
  - `hosted/slack/CLAUDE.md` covers all the behaviours above, including the recovery second pass, the deadline and the model cap.
  - `openrouter_image_mcp/CLAUDE.md` covers the loop's cost.
  - `hosted/CLAUDE.md` covers `OwnerLock.failed`/`on_refused` and `RECONNECTING`.
- **`HOSTED_OPERATIONS.md` §9:**
  - Malformed `SIDECAR_SLACK_*` numbers stop startup even with Slack off.
  - The registry refuses bad caps.
- **Index:** `generate.js` was run; the index was already current, and `--check` is clean.

## Not done, and concerns

- **`Limits._durable` still counts lapsed rows.** The reviewer's I2 fix had two parts; the brief asked only for the second pass, so I left `_durable` alone.
  - Between a crash and the second pass (about 75 s after `auth.test` succeeds), a team at its cap is still refused.
  - Rows that this process left behind across a lose and regain are still never swept, because recovery skips its own process id. The I1 fixes remove the known way to leave such rows.
- **The silent interrupt can still beat recovery.** `active_for_chat`'s silent `_recover_for_read` interrupt (review I2, last bullet) can still close a previous process's lapsed row before recovery edits its working message. This was not in the brief.
- **M2 also changes the image window's kind.** It now reports kind `"minute"` too. Only `.kind` changed; no student text changed, and nothing outside Slack reads `.kind`.
- **M8 at boot and on standby:**
  - If the database is unreachable at boot, Health now says `RECONNECTING` instead of `NOT_OWNER`, until a lock try is refused or succeeds.
  - A waiting replica whose own checks fail keeps `NOT_OWNER`.
- **`SIDECAR_SLACK_DAILY_CAP_USD=inf` still passes `settings._number`.** The registry now rejects it at `add`, so it cannot reach a row.
- **Registry validation and existing rows:**
  - Validation now runs on every `update`. A stored row that is already invalid, for example one whose `default_model` is not among its aliases, must be fixed in the same `update` call.
  - The ops script already enforced the default rule, so none should exist.
- **History rewrite:** I rewrote my own last two local commits once (`git reset --soft HEAD~2`, then recommitted with hooks) to fold the `run_tool_loop` length fix into the M3 commit. Nothing was pushed.
- **Deadline coverage:** the deadline covers `Turn.work` only. The chat lease starts at row creation, a working-message post earlier, so the margin is about 10 minutes, not exactly 10.
- **Test import:** `tests/test_hosted_slack_wiring_recovery.py` imports `World` and `gain` from `test_hosted_slack_wiring.py`, because that file was at 278 lines.
