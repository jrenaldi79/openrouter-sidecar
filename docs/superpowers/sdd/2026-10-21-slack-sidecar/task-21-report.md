---
title: "Task 21 report: wiring and isolation suite"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 21 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 21 report: wiring and isolation suite

**Status:** done. One commit, `7796820` `feat(slack): wire the Slack gateway into the hosted server`, on
`feat/slack-sidecar`. It has the `Co-Authored-By: Claude Opus 5.5` trailer, and the pre-commit hooks passed:
docs, ruff, the full pytest run, secret scan, file sizes and missing tests.

## Tests

- **Unit suite:** `uv run pytest` gave 1634 passed, 595 skipped, 9 deselected.
- **Postgres suite:** `TEST_DATABASE_URL=...:54999/sidecar_test uv run pytest -m pg` gave 574 passed, 17 skipped.
- **Lint:** `uv run ruff check` is clean.
- **Docs:** `generate.js --check` is clean.
- **Repeat runs:** the new wiring, e2e and isolation files passed 5 runs in a row on both backends.
- **TDD:** I wrote the tests first.
  - The wiring, e2e, commands and asks tests failed until the implementation existed. They failed on missing
    `wiring`, `NO_MODEL_SET` and the `slack=` option.
  - The isolation suite passed against the existing code from the start. Those tests check behaviour that already
    worked. To show the static scan isn't vacuous, `test_scan_flags_a_workspace_only_query` plants bad queries and
    confirms the scan flags them.

## What changed

### `hosted/slack/wiring.py` (new, 137 lines)

`build_slack(settings, target, keys, provisioner, limits, *, transport=None, api=None, client_factory=None) -> SlackApp`.

- **Services:** one `SlackServices` per `COHORT_WORKSPACES` cohort.
  - Each is `AdvisorService(ws, AdvisorStore(target), AdvisorClient(ws, ADVISOR_MAX_TIMEOUT_S, transport=...), credentials=SlackCredentials(ws, keys), sandbox_default=False, sandbox_allowed=settings.sandbox_enabled)`.
  - Each also gets `PreferenceService`, `AdvisorResults`, `advisors.files` and the client.
- **Runner:** `SlackRunner` gets `Outputs(api)`, the server's shared `Limits` (`admission`), the registry, a
  `NameCache` and `key_info=partial(current_key, transport=transport)`.
- **Other parts:** a `GuestGuard`, a `ContextBuilder` with `channel_chars=slack.context_chars`, the `Dispatcher` and
  the `Gateway` with `EventLog`. When no `api` is passed, it uses `SdkSlackApi(AsyncWebClient(bot_token))` with its
  own httpx download client.
- **`SlackApp`** has `gateway`, `dispatcher`, `services`, `api`, `recovery_task`, `drain_s`, plus `on_gain`,
  `on_lose` and `close`.
  - **`on_gain`:**
    - It calls `auth.test` through the new `SlackApi.bot_user_id()`, bounded by `AUTH_TEST_S` = 10 s.
    - On failure it logs the error type and returns, so neither the gateway nor recovery starts. The next gain
      retries.
    - It sets the bot id on the gateway and the dispatcher, and starts `recover(...)` as one held task, but only if
      none is still running. Then it awaits `gateway.start()`.
  - **`on_lose`:** stops the gateway and cancels recovery. It is idempotent.
  - **`close`:** runs `on_lose`, then `dispatcher.shutdown(10 s)`, then closes the clients it built: the
    `SdkSlackApi` download client and each cohort's `AdvisorClient`.
  - **Recovery** uses its own process id, `slack-<hex>`, and finds `AdvisorResults` by workspace.

### `hosted/app.py`

- `_build_slack` builds the bot only when `settings.slack and settings.slack.enabled`. It imports `wiring` lazily
  and appends `on_gain` and `on_lose` to `health.lock`, after Health's gate hooks.
- `server.hosted["slack"]` holds the app, or None when Slack is off.
- At shutdown the lifespan closes Slack first, before the sweeper, the workspaces and the pool.
- `build_hosted_server` has a new `slack: dict | None` option for test doubles (`api`, `client_factory`).
- `app.py` is now 191 lines.

### Ruling 4 (alias with no model)

- `preferences.py` now has the constant `NO_DEFAULT`, used by `resolve`.
- In `commands.py`, `resolve_alias` maps a `BadRequestError` whose `.message == NO_DEFAULT` on a brand alias to
  `NoModelSet`. That exception carries the escaped text `NO_MODEL_SET`, "This alias has no model set yet. Ask your
  instructor."
- In `asks.py`, `_ask_picks` posts that text and the other models still run.
- `models` lists the alias as "(no model set yet)".
- Exact model ids and other refusals behave as before.

### Other changes

- **`api.py`:** `SlackApi.bot_user_id()` (`auth.test`) is new. `SdkSlackApi` gained `bot_user_id` and `aclose()`.
  `FakeSlack` gained `bot_user_id()` and a `bot_user` attribute, and the protocol signature test still passes.
- **`dispatch.py`:** `Dispatcher.shutdown(timeout_s)` waits up to the timeout, then cancels the remaining jobs.
- **`tests/hosted_harness.py`:** `running_server(..., slack=...)` passes the doubles through.
- **Folder notes:** `hosted/CLAUDE.md` and `hosted/slack/CLAUDE.md` are updated, and `docs/index.md` is regenerated.

### Tests

- **`tests/test_hosted_slack_wiring.py`:**
  - not built when disabled (only Health's own hook on the lock);
  - built when enabled and hooked to the lock;
  - one service set per cohort;
  - built and started on gain, including real recovery posting an orphaned answer;
  - an `auth.test` failure leaves the gateway off until the next gain, and the log has no error text;
  - recovery is not doubled while it runs;
  - `on_lose` stops the gateway and cancels recovery, is idempotent, and a regain restarts it;
  - `close` drains jobs, then cancels the rest;
  - `close` closes the clients it built.
- **`tests/test_hosted_slack_wiring_e2e.py`:** an `app_mention` envelope goes through `Gateway.on_request` on the
  running server, on its own event loop. It uses the real `Dispatcher`, `SlackRunner` and `KeyProvisioner`, plus
  `FakeOpenRouter`. Before the mention, the test registers the channel, provisions the key and seeds the team's
  general and openai defaults under `acting_as`. It asserts:
  - the key is named `Sidecar · abbott · 2027`;
  - the key is assigned to the guardrail named `Sidecar team budget`;
  - the posted answer is exactly `Final advice\n\nChatGPT · openai/test-advisor · $0.0020` as `Sidecar · ChatGPT`
    in the thread;
  - the job row has `owner slack|T1:C1`, `slack_user_id U1`, `completed` and `slack_posted_at` set.
- **`tests/test_hosted_slack_isolation.py`:**
  - two registered teams: B's scope sees none of A's chats, memory, thread maps, jobs (get, running, spend, usage)
    or `mark_posted`;
  - a student in the same workspace sees no team data;
  - `@sidecar usage` posts no key, hash, key name or principal;
  - a forged `channel` in `read_channel_history` arguments goes end to end through the dispatcher and the tool loop.
    Only C1 is read, and C2's text never reaches the model or Slack;
  - the static scan of `hosted/slack/*.py`, `advisor_results.py` and `hosted/limits.py`:
    - it resolves string literals with `ast`, including f-strings and `+` with module and function constants, and
      skips docstrings;
    - it fails on SQL whose `WHERE` mentions `workspace` or `cohort` (or an unresolvable part) without `owner`;
    - the allowlist is `registry.py` and `SlackJobs.orphans`, each with a comment saying why;
  - a self-test confirms the scan flags planted bad queries.
- **Commands and asks:** `test_brand_without_a_default_says_no_model_set` and
  `test_alias_without_a_team_default_says_no_model_set`. The second uses the real `PreferenceService`, with no team
  defaults saved.

## Deviations and choices

- **The `bot_user_id` method:** `SlackApi` had no `auth.test`, so I added `bot_user_id()` to the protocol, the SDK
  wrapper and `FakeSlack`. The gateway and dispatcher are built with an empty bot id, and `on_gain` sets the real one.
- **Extra `build_slack` argument:** it also takes `client_factory`, which the tests need to swap out the Socket Mode
  client. The brief listed only `transport` and `api`.
- **The e2e test file:** it is `test_hosted_slack_wiring_e2e.py`. I didn't add it to `test_hosted_app.py`, so that
  file stays focused and under the size cap.
- **Shutdown drain:** `Dispatcher.shutdown` is a new public method, so the wiring doesn't reach into
  `_tasks`.
- **Imports in `wiring.py`:** it has more than 10, since it is the composition root. I left them in one module rather
  than add a second one that would need its own test file.

## Concerns

1. **No retry after `auth.test` fails.** Per ruling 1, a failure at startup keeps the gateway off until the next lock
   gain. A stable owner never regains, so a Slack outage at deploy time keeps the bot off until a restart. A periodic
   retry would be a small follow-up.
2. **Recovery skips this process's own rows.** It runs with this process's id, so after a lose and regain in the same
   process, any rows this process interrupted are not swept. They are already interrupted by the runner, or their
   heartbeats lapse.
3. **Startup can wait on Slack.** On SQLite, `on_gain` runs inside `health.start()` during the lifespan, so a slow
   `auth.test` delays startup by up to 10 s.

## Fix round 1

**Commit:** `9e75540` `fix(slack): closed flag, start retries, sharper isolation scan`, with the trailer. The
pre-commit hooks passed. It changes 5 files: `hosted/slack/CLAUDE.md`, `hosted/slack/wiring.py`,
`tests/test_hosted_slack_isolation.py`, `tests/test_hosted_slack_wiring.py` and `tests/test_hosted_slack_wiring_e2e.py`
(+178/-57).

### 1. Closed flag

- `SlackApp._closed` is set first thing in `close()`.
- `on_gain` returns at once when it is set.
- The start task checks it before each auth.test attempt and again after auth.test answers, before starting
  recovery or the gateway.
- New tests:
  - `test_a_gain_after_close_starts_nothing`: no client, no start task, no recovery.
  - `test_close_during_auth_test_starts_nothing_after_it`: the fake auth.test closes the app while answering, and
    no recovery or gateway starts.

### 2. Revised ruling 1: the start retries

- `on_gain` starts one held `start_task` and returns promptly. A second gain while it runs starts nothing new.
- The task retries `auth.test` until it answers:
  - each attempt is capped at `AUTH_TEST_S` = 10 s;
  - the waits go 5, 10, 20, … s, capped at 5 min (`START_RETRY_S`, `START_RETRY_MAX_S`);
  - the waits go through the injectable `SlackApp.sleep`;
  - failures are logged by type only.
- Once auth.test answers, the task starts the single recovery task, then `gateway.start()`.
- `on_lose` and `close` cancel the start task.
- New and updated tests:
  - `test_auth_test_failure_is_retried_with_backoff`: 8 failures give the delays
    `[5, 10, 20, 40, 80, 160, 300, 300]`, then the gateway and recovery start. The sleeps are injected, so there are
    no wall-clock waits.
  - `test_a_second_gain_while_retrying_starts_no_second_task`: also checks that `on_lose` cancels the waiting start.
  - The existing tests now wait for `start_task` through a `gain()` helper.

### 3. Static scan

- Matching is case-insensitive (SQL verbs and `WHERE`).
- The clause runs from `WHERE` to the first ORDER, GROUP or LIMIT, and must contain `\bowner\s*=`.
- `advisor_store.py` and `session_files.py` are now scanned too. With `advisor_results.py` and `limits.py`, the scan
  sees 11, 3, 13 and 2 SQL WHERE literals in those files, and all of them pass.
- The `SlackJobs.orphans` comment now says its SQL passes today and the entry records the documented cross-team
  exception.
- New self-test cases, all flagged:
  - `select * from t where workspace=?`
  - `WHERE workspace=? ORDER BY owner`
  - `WHERE owner_hint IS NULL AND workspace=?`
- `owner = ? … ORDER BY cohort` passes.

### 4. Cross-team test

- `_fill` now also stores a session file for team A. `SessionFiles.current(scope_a, chat)` lists it, and
  `current(scope_b, chat)` raises `AdvisorAccessError`.
- New `test_team_spend_and_running_jobs_never_block_another_team`: team A has $1.25 of spend today and a queued
  job. A gets blocked by `concurrency` (cap 1) and by `daily` (cap $1), while `admit(scope_b, ...)` with the same
  caps passes.

### 5. E2E

Every `/responses` request recorded by the fake carries `Bearer <team key>`. The team key is read from the
`StudentKeyStore` for the team principal. The check is computed as one boolean, so a failure never prints the key.

### 6. Folder note

`hosted/slack/CLAUDE.md` now covers:

- the start task, its backoff constants and the injectable sleep;
- the closed flag;
- that recovery skips every row this process created, whatever its heartbeat, because `orphans` filters
  `process_id != ours`;
- the scan's new scope and rules.

### Tests

- **Wiring, e2e, isolation, commands, asks and app tests** (`test_hosted_app.py`, `test_hosted_apps.py`), on SQLite
  and Postgres: 136 passed, twice.
- **`uv run pytest`:** 1638 passed, 599 skipped.
- **`pytest -m pg`:** 578 passed, 17 skipped.
- **`ruff check`** and **`generate.js --check`:** clean.

The first concern from the original report, no retry after an `auth.test` failure, is resolved by the retrying
start.

## Fix round 2

**Commit:** `9aaf168` `fix(slack): start task logs failures by type`, with the trailer. The pre-commit hooks
passed. It changes 3 files: `hosted/slack/CLAUDE.md`, `hosted/slack/wiring.py` and
`tests/test_hosted_slack_wiring.py` (+41/-12).

### 1. Start task failures are logged by type

- In `_start`, the part after auth.test (setting the bot id, creating recovery, `gateway.start()`) is now wrapped in
  `except Exception`.
- A failure logs `Slack start failed (<TypeName>)` with no message and no traceback, and the task ends cleanly. A
  `CancelledError` still propagates.
- `_recover` got the same guard for the same reason, since nobody awaits that task either.
- New test `test_a_failing_start_is_logged_by_type_only`:
  - the gateway's client factory raises a `ValueError` with a message;
  - `start_task.exception()` is None, so nothing is left unretrieved;
  - the log record names `ValueError` and has no `exc_info`;
  - the message text is not in the log.

### 2. The close-during-auth.test test

- The fake auth.test now waits on an event. The test waits until auth.test is in flight, then runs the real
  `close()` as a task, releases the event and awaits the close.
- It asserts that the start task is done and cleared, no client opened, and no recovery started.

### 3. Folder note

The recovery bullet now says `SlackApp`'s start task runs recovery after a successful `auth.test`, one recovery task
at a time. The wiring bullet now says failures after auth.test are logged by type only.

### Tests

- **Wiring and e2e tests**, on SQLite and Postgres: 28 passed.
- **`uv run pytest`:** 1639 passed, 600 skipped.
- **`ruff check`:** clean.
- **`generate.js`:** clean.
