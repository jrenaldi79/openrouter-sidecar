---
title: "Final review: Slack Sidecar (709c182..e7dbb57)"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): final whole-branch review."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Final review: Slack Sidecar (709c182..e7dbb57)

Reviewer: whole-branch, cross-task integration review. Read-only; probes lived in the scratchpad only.

## Passes done

1. **Orientation.** Read the spec (binding), the plan's Global Constraints and Review Focus, and the whole ledger with its rulings and deferred minors.
2. **Gates.** Ran these at head:
   - `uv run pytest -q`: 1644 passed, 601 skipped, 9 deselected.
   - `uv run ruff check`: clean.
   - `-m pg` against the throwaway Postgres: 580 passed, 17 skipped.
   - `file_sizes --all`: no new file over 300 lines.
   - `missing_tests --all`: no Slack module missing a test.
   - `generate.js --check`: clean.
   - An AST scan for functions over 50 lines.
   - A grep for token-shaped literals.
3. **Request path.** Followed one mention end to end, through gateway, dispatch, asks/commands, runner, runner_queue, runner_turn, advisor_loop, replies and outputs.
4. **Team isolation.** Covered the principal, `acting_as`, the contextvars copied into `create_task`, `SlackCredentials`, ThreadMap, memory, jobs, usage, ChannelTools args, the sandbox container and `output_refs`, recovery's cross-team sweep, and the retention purge.
5. **Untrusted Slack text.** Covered fetch domains, Fusion and subagent tools, instructions vs data, escaping, unfurl, `answer.md`, upload names and progress labels.
6. **Money and limits.** Covered the daily cap, concurrency (semaphore plus durable count), the per-user window, cost on every exit path of the loop and runner, and the 402/403/moderation mapping.
7. **Lifecycle.** Covered OwnerLock gain and lose, the Health gate, SlackApp start-task backoff, gateway start and stop, recovery, the shutdown drain and cancellation paths.
8. **Storage.** Covered SQLite/Postgres parity (`ON CONFLICT`, rowid via BIGSERIAL, `IS NULL` guards), additive schema and indices for the new queries.
9. **Effect on existing users.** Checked:
   - `advisor_client` 402/403 typing;
   - `advisor_payload`: probed and confirmed that `INSTRUCTIONS` and `_sandbox_instructions` are byte-identical to base;
   - `client.py` extraction, Limits refactor, Health/OwnerLock, `logs.redact`, `preferences.NO_DEFAULT`.
10. **Ops, docs and manifest.** Covered `slack_channels.py`, `slack_usage.py`, `slack_common.py`, HOSTED_OPERATIONS §9 and `deploy/slack/manifest.yaml`.
11. **Probes.** Wrote two scratch pytest probes on the real runner rig to confirm Important 1 and Important 2 (results quoted below).

## Strengths

- **Identity is derived, never parsed.**
  - `team_principal` is built only from the signed event's ids plus the registry row.
  - `ChannelTools` binds the channel at construction, so a forged `channel` argument is inert.
  - `SlackCredentials.check` never reads the contextvar, so heartbeats outside the context stay safe.
  - The isolation suite scans every Slack module for workspace-only queries.
- **The cost-carrying exits in `run_tool_loop` are well thought through.** `LoopStopped`, `OpenRouterError` and `CancelledError` all carry `items`/`cost_usd`, and `Turn.fail`/`interrupt` keep spend even when the lease is gone (`interrupt(cost_usd=)` uses `COALESCE`).
- **Queueing is correct and fair.**
  - The chat lock is taken before the team slot, under one deadline.
  - `wait_turn` releases a won acquisition on stop or cancel.
  - `Shared` drops primitives when the last user leaves.
  - Review Focus 3 holds.
- **Posting is safe by construction.**
  - `escape` is applied where text is composed.
  - `WorkingMessage` escapes every step itself.
  - The key is masked and token shapes are redacted before posting.
  - Oversized answers go out as `answer.md`.
  - `unfurl_*` is off.
- **The exfiltration design is tight on the request side.**
  - `web_fetch` is absent with an empty domain set, and the same rule applies to the subagent and Fusion tools.
  - IPs, localhost, shared suffixes and ccTLD registries are rejected.
  - No Slack-sourced host or file name enters the instructions.
- **Lifecycle hooks are robust.**
  - `on_gain` only spawns a held start task, with `auth.test` backoff.
  - `_closed` blocks restarts after shutdown.
  - `stop()` sets the flag, then calls `close()` (no auto-reconnect), then drains.
  - Recovery runs per orphan under its own principal and skips unregistered or inactive channels instead of posting into them.
- **Existing MCP behaviour is preserved where it matters.**
  - The advisor instructions are byte-identical.
  - The student `Limits` messages are unchanged.
  - `admit(scope)` keeps its defaults.
  - The Slack package is imported only when enabled.
- **Ops scripts reuse the server's own seams.** They call `cohort_services`, `PreferenceService` and `channel_problem`, and never print keys.

## Issues

### Critical

None found. No path reads or spends across teams, or between a team and a student, and no key or token reaches Slack or logs.

### Important

**I1. Slack transport errors escape `SlackCallError`, so a cosmetic Slack failure fails paid jobs, drops their spend and leaves stuck rows.**

- **Where:**
  - `src/openrouter_image_mcp/hosted/slack/api.py:104-110` (`_call` maps only `SlackApiError`);
  - consumers at `replies.py:189-195` (`WorkingMessage._edit`), `runner_turn.py:194-199` (`_on_items`), `runner.py:197-206` (`_execute` start) and `guests.py:82-88` (join lookup).
- **What:**
  - slack_sdk's async client re-raises raw `aiohttp.ClientError` / `asyncio.TimeoutError` after its retry handlers (`async_internal_utils.py`, `raise last_error`).
  - Every consumer catches only `SlackCallError`.
- **Why it matters.** This is cross-task: the Task 2 deferred minor meets the Task 6, 9, 13 and 14 code.
  - A progress edit that times out inside `_on_items` propagates out of `run_tool_loop`. It is not in `_CARRIES_SPEND`, so the job is marked failed with `cost_usd = NULL`.
    - Probe: a $0.50 round followed by a timed-out "Searched the web" edit left the row `('failed', None, 'Slack job failed locally…')`.
    - So a Slack network blip throws away a paid answer and hides its spend from the daily cap.
  - A timeout on the working-message post leaves the job row `queued`, owned by this process, with no finish.
    - Probe: the row stays `('queued', 'proc-test')`.
    - Restart recovery skips own-process rows, so these feed I2.
  - `WorkingMessage.finish` ("never raises") raises. `post_answer`'s retry-once doesn't retry.
  - A failed `users.info` on a guest join raises instead of setting the "deny until rechecked" mark. That weakens the Task 9 ruling.
- **Fix:**
  - In `_call`, also map `slack_sdk.errors.SlackClientError` (covers `SlackRequestError`), `aiohttp.ClientError` and `TimeoutError`/`asyncio.TimeoutError` to `SlackCallError(method, type(error).__name__)`.
  - Make progress edits best effort: catch `Exception` in `WorkingMessage._edit`, since a progress edit must never decide a paid job's fate.
  - Add a runner test with a `TimeoutError` from `update` mid-loop asserting the job completes and the cost is recorded.

**I2. Lapsed job rows left by a dead process can lock a team out until the next restart, and their working messages stay "working…" forever.**

- **Where:**
  - `src/openrouter_image_mcp/hosted/limits.py:53-67`: `_durable` counts every `queued`/`running` row, lapsed or not;
  - `hosted/slack/runner.py:170-173`: `admit` runs before `active_for_chat`, which is the only Slack call that triggers `_recover_for_read`;
  - `hosted/slack/recovery.py:95-96`: rows with a fresh heartbeat are skipped, and recovery runs once per gain.
- **What:**
  - Railway restarts a crashed container in seconds. Row heartbeats run up to 60 s ahead (`advisor_results.HEARTBEAT_S`), so the new owner's recovery usually sees the dead process's running rows as fresh and skips them, per the Task 20 ruling.
  - Nothing ever revisits them. Once they lapse, `admit` still counts them.
  - A team that had `max_concurrent` answers running at the crash gets "Your team already has 3 answers running. Try again in a minute." on every mention, because `admit` raises before the runner reaches `active_for_chat`. That lasts until the next process restart.
  - Probe: 3 lapsed `running` rows from `proc-dead`, then two mentions. Both got the concurrency text and the rows stayed `running`.
  - With fewer stuck rows, the next admitted job's `active_for_chat` interrupts them silently, but their Slack working messages are never edited. The same silent interrupt can also beat restart recovery to a previous process's orphans, because recovery and the gateway start together.
  - Own-process stuck rows from I1 hit the same wall.
  - MCP students share the `_durable` ordering, but their viewer polls (`get`/`for_chat`) recover stale rows. Slack has no such reader, so the lockout is new with this branch.
- **Why it matters.** It locks a paying team out with a false explanation after an ordinary crash.
- **Fix (both parts, small):**
  - In `_durable`, count only rows with `heartbeat_until > now`. This is safe for students too, because a lapsed row can't be running.
  - In recovery, revisit rows skipped for a fresh heartbeat once their heartbeat has passed. For example, the recovery task sleeps until the latest skipped `heartbeat_until` plus a margin and runs `recover` again, while the lock is still held.
  - Add a test: lapsed orphan rows at `max_concurrent` must not block admission, and a fresh orphan is closed (and its working message edited) on the second pass.

**I3. Model and bot output can widen the next turn's `web_fetch` allowlist.**

- **Where:** `src/openrouter_image_mcp/hosted/slack/context.py:165-172` and `:193`. `Gathered.urls = _urls(thread)` reads every thread message; the result feeds `fetch_domains` in `asks.py:145`.
- **What:**
  - The allowed domains include hosts linked by any message in the thread, including Sidecar's own earlier answers (posted with `username`, so `label` is set) and Claude Tag's replies.
  - A prompt-injected answer (via a fetched page or search result) that contains `https://attacker.tld/...`, plus a "next time, fetch …" note, makes `attacker.tld` fetchable on the next mention in that thread. The model then reads that note as context.
  - Claude Tag's replies routinely carry search-result URLs, which turns the "domains the team linked" rule into "domains any bot linked".
- **Why it matters:**
  - The spec's exfiltration bullet relies on the domain set being chosen by people in the thread.
  - Live acceptance step 5 only tests a plain ask "no links posted", so this path wouldn't be caught.
- **Fix:**
  - In `_urls`, take links only from human-authored messages (`message.label is None and message.user`), keeping the trigger.
  - Add a context test where a `Sidecar · Gemini` reply links a host that must not appear in `Context.urls`.

### Minor

**M1. `!stop` during the final request still posts the answer.** `runner_turn.py:132-143`. `should_stop` is checked only before a request, so a stop that lands while the last request is in flight is ignored. The thread then shows "Stopping 1 answer." and the answer anyway. Fix: after the loop returns, if `self.entry.stopped`, raise `LoopStopped(result.items, result.cost_usd)`. Spend stays and the answer is discarded.

**M2. The team's per-minute window shows per-user copy.** `runner_steps.py:107-108` with `replies.py:84-93`. `limits.admit` → `_window(scope.owner)` (20 a minute for the team) raises `kind="rate"`. `failure_text` then renders "You've sent 10 requests in 10 minutes…", which is wrong about who, how many and how long. Fix: give the team window its own kind (or pass the window size and span) and its own sentence.

**M3. One response without `usage.cost` discards the job's known spend.** `advisor_loop.py:128-129` (`total()` is None if any cost is None). A multi-round or Fusion job whose last response lacks a cost records `NULL`, so the daily cap undercounts. Fix: record the sum of known costs, as a lower bound, and keep the footer's "cost unknown" only when no cost was reported.

**M4. Slack jobs have no overall deadline.**
- **Where:** `runner.py:197-214`, `runner_turn.py:156-167`.
- **What:**
  - Each request may take up to `ADVISOR_MAX_TIMEOUT_S` (30 min), with up to 5 per job.
  - `!stop` can't interrupt an in-flight request.
  - The chat lease (`OPERATION_LEASE_S` = 40 min) expires under a long job, so `self.guard()` fails after the spend and the paid answer is lost.
  - Meanwhile the job holds a team slot and the chat.
- **Fix:** wrap `turn.work` in a deadline below the lease (for example 30 min total) and fail with team copy. Alternatively, renew the chat lease in `beat()`.

**M5. The welcome never states the budget.** `dispatch.py:120` always passes `budget_usd=None`, so the welcome says "Your team has a monthly budget" instead of the spec-quoted "Your team's budget is $75 a month". Fix: pass the team guardrail amount (a constant, or the key's `effective_budget` when known), or amend the spec.

**M6. Retention purge leaves dangling thread links.** `retention.py` deletes idle chats; `slack_threads` keeps rows pointing to them (`asks.py:108-110` returns the dead `chat_id`). After 90 idle days, a mention in that thread fails as "<Model> didn't answer this time" on every try until `!restart`. Fix: in `_chat_id`, treat a missing chat (`store.get` raising) as not found and start fresh; or delete `slack_threads` rows with their chats in the purge.

**M7. The number of models per ask is unbounded.** `parser.py:139-152`. Exact ids (`a/x and b/y and …`) are accepted without limit. One mention can enqueue dozens of jobs; each beyond the slots waits 120 s and then posts a busy line, flooding the thread. Fix: cap aliases per ask (for example 4) with plain copy.

**M8. The Health gate wording during a database outage is misleading.** `health.py:41-43`. An owner whose lock connection fails now closes the MCP gate with "Another Sidecar instance is active; this one is not serving." This is a new, brief, user-visible state for MCP students; previously a DB blip didn't touch the gate. Fix: use a neutral reason (for example "The Sidecar is reconnecting to its database; retry shortly.") when the loss came from a failed check rather than another holder.

**M9. Fetch allowlisting of multi-tenant hosts is a residual exfiltration route.** `payload.py:42-47`. A thread link to `docs.google.com` (common for capstone teams) allows fetches to any Google Forms `formResponse?entry…` URL, which records a GET submission for whoever owns the form. Fix: record this at live acceptance step 5, and consider denying known form or webhook hosts (`docs.google.com/forms` can't be path-filtered, so deny the host when it isn't in the channel's `fetch_allowlist`).

**M10. Housekeeping.**
- `limits.py:34`: the `_recent: dict[str, deque]` annotation is stale; keys are `(key, span)` tuples.
- `app.py:120`: `build_hosted_server` grew to 72 lines (it was already over 50; this branch added about 5).
- `asks.py:111-113`: a chat created by `advisors.start` is orphaned when `ThreadMap.put` fails or the handler is cancelled before `put`. This is harmless but accumulates until purge.

## Rulings flagged

- **Task 2, `_call` maps only `SlackApiError` (deferred as minor).** The severity is wrong in hindsight. Combined with the runner and loop it causes I1: lost spend and stuck rows. Fix it before merge.
- **Task 20, "fresh heartbeat = another live owner → leave it".** This is right for the takeover window, but recovery never comes back. Combined with `Limits._durable` counting lapsed rows, it causes I2. Keep the skip and add the second pass.
- **Task 10 fix round, "urls from full thread".** Right that the URLs must come from the whole thread, not the delta. It should also exclude bot-authored messages (I3).
- **R3 (`limit_text` formats numbers from the row or settings).** Sound, but `failure_text` reuses the per-user sentence for the team's per-minute window (M2). This is a narrow follow-up, not a reversal.

Every other ruling in the ledger (R1–R5, R1 revised, and Tasks 2–23) is sound on its merits.

## Deferred minors to fix before merge

| Ledger item | Fix before merge? | Why |
| --- | --- | --- |
| T0: shell spike prints, download 302, vacuous replay spike, fusion spike prints only keys, unused `workspace` param | No (fix before the Task 24 live run) | Test-only; but the replay and fusion ones must be fixed before their results are trusted |
| T1: deferred `_TRUE` import | No | Style |
| T1: malformed `SIDECAR_SLACK_*` blocks startup with Slack off | No | Operator error; add a line to HOSTED_OPERATIONS §9 when convenient |
| T1: `inf` daily cap / T2: `_validate` doesn't check caps or `default_model` | **Yes** (cheap) | A money guard: reject non-finite or non-positive `daily_cap_usd`, `max_concurrent < 1` and a `default_model` not in `alias_map` in `registry._validate`; argparse `float` accepts `inf`/`nan` |
| T1: `_number` type hints; lookalike masking | No | |
| T2: `LABEL.match` with `$` accepts a trailing newline | No (one-word fix if touched) | Operator input |
| T2: `_call` maps only `SlackApiError` | **Yes** | I1 |
| T2: 200 HTML login page passes as file bytes | No | The trusted host only |
| T2: FakeSlack `latest` inclusive; callable-dict state | No | Test fidelity; `_recent` filters by ts anyway |
| T2: upload `username` ignored | No | Cosmetic |
| T2: download allows non-443 ports and userinfo on files.slack.com | No | URL comes from the signed event; host is still Slack's |
| T3: `pending_reason()` in `SlackCredentials`; recheck and caching test gaps; key-name test | No | Harmless (contextvar unset in Slack tasks) |
| T3: "student"/"Your key" copy | Resolved | `key_text` gives team copy |
| T4: spend SQL duplicated; `SELECT *`; window deques never pruned; other-owner test | No | |
| T5: "ask gemini and yes/no only" | No | Gives the unknown-alias reply, which explains itself |
| T5: text before a mid-text mention dropped | No | |
| T5: trailing comma or colon stays on an alias ("ask gemini, what…") | **Yes** (cheap) | A common phrasing that a reasonable student will type; strip `,:;` from alias tokens |
| T5: "reuse sandbox"; `log`/`md` tokens | No | Cost is bounded; it only picks sandbox mode |
| T6: `finish` gives no signal; `ts=None` before start; "10 minutes" hardcoded; CRLF; test gaps | No (the first is covered by the I1 fix) | |
| T7: Decimal NaN; memory cap race; `put` RuntimeError | No (the last is resolved in `asks._started`) | |
| T8: welcome escape; `:g` format; weak tests | No (escape resolved in dispatch) | |
| T9: GUEST_OFF retry; `_checked` pruning; duplicate scans; serial `users.info` | No | |
| T9: non-`SlackCallError` on join lookup doesn't set the mark | **Yes** (comes with the I1 fix) | Guest gate |
| T10: no 10-page marker; delta-oldest-as-root; NameCache unbounded | No | Delta path unused |
| T11: names before cap; duplication; test gaps | No | |
| T12: `SANDBOX_SAFETY` "no reports in files" vs the outputs rule | No (check at live run) | |
| T14: follow-up past `QUEUE_WAIT_S` gets CHAT_BUSY; loose item types | No | Copy says to ask again; types settle at live run |
| T15: output names and content not redacted | No | Spec posts files as written |
| T22: raw traceback on DB connection failure | No | No secret in psycopg messages; nice-to-have |
| T23: §6 `railway run … purge` private URL | No (Task 24) | |

## Declined to judge

- **OpenRouter wire facts:** `FUSION_FORCED`/`allowed_tools`, Fusion `tools` and allowed domains, the shell `files` shape, server item type names, `/key` `effective_budget`, and whether `allowed_domains` matches subdomains. Pending the Task 24 live run by the R1 revision.
- **Whether 402 or 403 is OpenRouter's "key budget reached" status.** Both map to budget copy, and that depends on live behaviour.
- **Synchronous DB calls from async Slack code (registry, threads, results).** Same pattern as the existing advisor code; not a regression; outside the plan.
- **Words from the conversation carried in search queries.** The spec explicitly accepts it.
- **A job that posts after its channel was deactivated mid-job (a guest joined).** The spec is silent; the guest can already read the channel; marginal.
- **Slack Enterprise Grid and multi-workspace installs; event redelivery while the socket is closed during a lock gap.** Outside the spec, and Slack-side behaviour.
- **`channel_left`/`group_left` not in the manifest.** `member_left_channel` with the bot's id covers removal in the spec; event-subscription details are Slack-side.
- **Idle team keys deleted by `purge_student_keys`.** Consistent with "data stays until the retention purge"; re-provisioned on next use.
- **The split-brain window of the advisory lock.** Ruled (Task 18), bounded and documented.
- **An unposted answer waits for the next lock gain rather than retrying in-process.** The spec and Review Focus 5 define restart recovery as the path, and the working message tells the team.
- **Spec and plan prose quality, and the decision-record entries.** Docs-only; Task 23 was reviewed.

## Assessment

**Ready to merge? With fixes.** The design is sound: isolation and posting safety held up under cross-task review, and nothing is Critical. Fix three things first because they cost money or lock teams out:

- I1: transport errors drop spend and leave stuck rows;
- I2: lapsed orphans block a team after a crash;
- I3: bot output widens the fetch allowlist.

All three are small, testable changes. Do them, plus the three cheap deferred minors marked Yes, before the Task 24 live run.
