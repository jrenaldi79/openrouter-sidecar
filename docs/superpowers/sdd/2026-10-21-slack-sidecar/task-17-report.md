---
title: "Task 17 report: request flow and commands"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 17 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 17 report: request flow and commands

**Status:** DONE_WITH_CONCERNS
**Commit:** 333a23e `feat(slack): request flow and commands` (base 169d95a)

## What was built

- `hosted/slack/dispatch.py` (187 lines): `Deps` (the brief's 13 fields, `services` keyed by cohort),
  `Dispatcher(deps, *, bot_user_id)` with `on_mention(team_id, event)` and `drain()`.
  Flow: event check (ruling 4) → gate → welcome once → `parse(command_text(...))` → per-user window →
  help/empty from the row alone → `provisioner.ensure(principal, team=TeamKey(label))` → inside
  `acting_as(principal)`: scope from the cohort credentials → `run_command` or `prepare_jobs` + one task per job.
- `hosted/slack/commands.py` (195 lines): `Turn` (frozen; the brief's fields plus a `top_level` property:
  trigger ts == reply ts), `run_command`, `static_text`, `resolve_alias`, `UnknownAlias`, `unknown_alias_text`.
- `hosted/slack/asks.py` (142 lines, a sibling extracted to keep both files ≤300): `prepare_jobs`, `Pick`,
  `COMPARE_ALIAS = ":fusion"`, `COMPARE_LABEL`, `SANDBOX_OFF`, `START_FAILED`, `READ_FAILED`, `DEFAULT_BRIEF`.
- Tests: `tests/test_hosted_slack_dispatch.py` (gate, welcome, limit, identity, event checks, key copy,
  thread-map failure, background failure), `tests/test_hosted_slack_asks.py` (models, modes, context, Fusion,
  start failures), `tests/test_hosted_slack_commands.py` (every command and `resolve_alias`), helper
  `tests/slack_dispatch_rig.py` (`DispatchRig` on top of the runner rig: the real runner, stores and
  ContextBuilder, `GuestGuard`, `FakePreferences`, `FakeProvisioner`). `tests/slack_runner_rig.py`
  `Model` now serves `GET /models` from `model.catalog` (needed by the real `AdvisorService.start`).
- `hosted/slack/CLAUDE.md` documents the three modules and the rig; `docs/index.md` regenerated.

All 18 tests from the brief exist (`test_status_and_stop` and `test_models_and_set_default` are in the
commands file; the ask-path ones are in `test_hosted_slack_asks.py`), plus tests for rulings 1–6:
`test_every_turn_rereads_the_whole_thread`, `test_context_gathered_once_for_two_models` (one `replies` and
one `pins` read, identical `context.md` for both chats), `test_compare_uses_fusion_payload` /
`test_compare_never_sandbox`, `test_memory_commands` / `test_unknown_alias_escaped_once` (escaped exactly once),
`test_event_without_ids_ignored` (8 cases), `test_background_failure_logged_by_type` (runs as the team principal
in the task, logs `ValueError` not its text), `test_key_problem_team_copy`, `test_thread_map_failure_plain_copy`,
`test_help_needs_no_key`, `test_sandbox_off_answers_inline_with_note`, `test_start_failure_names_another_model`,
`test_thread_links_become_fetch_domains`, `test_usage_posts_report`.

RED: all three test modules failed at collection (`No module named ...dispatch`), recorded in `task-17-red.txt`.
GREEN: 46 new tests pass on SQLite and 46 on Postgres. Full suite: 1535 passed, 507 skipped, 9 deselected.
`pytest -m pg` (local Postgres 54999): 485 passed, 17 skipped. `ruff check` clean, docs `--check` clean,
pre-commit hooks passed.

## Choices where the brief was silent or could not work as written

1. **Order:** aliases are resolved before the thread is gathered (an unknown alias is answered without
   reading Slack); the context is still gathered and rendered once for all models. Ruling 1 applied:
   `first_turn=True`, `since_ts=None` every turn.
2. **Help and an empty mention need no key:** they are answered after the per-user window but before
   provisioning (`static_text`), so a disabled team key doesn't hide `help`. Every mention, commands included,
   counts toward the per-user window.
3. **Welcome budget:** the welcome comes before provisioning, so it uses `welcome_text(row, None)`
   ("Your team has a monthly budget (`@sidecar usage`).") instead of a dollar figure.
4. **Compare:** one Fusion job, label `Fusion`, thread-map alias `:fusion` (no parsed alias can start with `:`
   and reach the thread map), outer chat started on `row.fusion_analyst`, always inline. A plain follow-up in a
   thread whose last answer was Fusion runs Fusion again (spec: "continue with the last model that answered").
5. **Key copy (ruling 6):** `replies.key_text` for `NOT_READY`, `DISABLED`, `UNAVAILABLE` and `KEY_REJECTED`;
   anything else (`NOT_SET_UP`, `NOT_SERVED`, a non-OpenRouter exception) gives
   `KEY_PROBLEM` = "Sidecar can't reach this team's key right now. Ask your instructor." I did not use
   "Your instructor has been told": nothing tells them.
6. **Resolution/start failures:** a mapped alias whose brand can't be resolved, or a model `advisors.start`
   rejects, posts `provider_error_text(label, other aliases)` and the other models still run. An exact id the
   catalog refuses is an `UnknownAlias`. Other unexpected errors in a request post `FAILED` (logged by type).
7. **`!stop` with nothing running** says "Nothing is running in this thread."; one job says "Stopping 1 answer."
8. **The bot's own user id** is ignored like a malformed event (no reply loops).
9. **`!stop` semantics (runner, Task 14):** a job whose in-flight request returns the final answer still
   posts it; stop takes effect before the next model round. The test drives a tool-call round to show it.

## Concerns

- **Team preferences have no family defaults.** `PreferenceService.resolve("Gemini")` for a fresh team principal
  raises "No advisor default selected" (family defaults start `None`). Tests use `FakePreferences`; with the real
  service every brand alias would fail (the reply would be "Gemini didn't answer this time…"). Task 21 (or a
  small follow-up) must seed or derive team family defaults before the end-to-end test can pass.
- **`ChannelRegistry.update` rewrites every column** from its own fresh read, so a `set default` racing a
  `deactivate` or `mark_warned` in the same instant can revert that change (window: one SELECT→UPDATE).
- `Deps.provisioner` is typed `object` to accept test fakes; Task 21 passes the real `KeyProvisioner`.

## Fix round 1

**Commit:** c24928f `fix(slack): dispatch test isolation, stop exempt, chat start lock` (on 333a23e).

1. `test_event_without_ids_ignored` copies the parametrize dict before popping `team`; the dispatch, asks and
   commands files now pass with sqlite and pg in one session (108 passed).
2. `ThreadCommand`, `Help` and `Empty` skip the per-user window (`dispatch._UNLIMITED`); asks and the other
   commands still count. `test_thread_commands_and_help_skip_user_window`: after 10 asks, `!stop`, `!status`,
   `!restart`, help and an empty mention answer, and `models` gets the rate text.
3. `asks._chat_id` holds a per-(owner, workspace, channel, thread_ts, alias, mode) `asyncio.Lock` around
   get → start → put, from `Deps.chat_locks` (a `runner_queue.Shared(asyncio.Lock)`, default factory, dropped when
   idle). `test_concurrent_first_mentions_share_one_chat` reproduced the race first (two chats; `start` patched
   to yield, as the real catalog call does), then passes with one chat, one thread row and no lock left.
4. `on_mention` wraps `_on_mention` in one outer try that logs the type and never raises. Tests: a malformed
   `files: ["x"]` (AttributeError) and a registry `OperationalError` (message not logged).
5. Events with `edited`, a `message_changed`/`message_deleted` subtype, or any `bot_id` are ignored. The
   Dispatcher knows only our bot *user* id, and our own posts are already dropped by it, so every `bot_id` is
   ignored (equivalent to "other than our own"). Parametrized test, four cases.
6. `test_user_rate_limit` now makes ten asks; the 11th ask gets the rate text with no provisioning call, no
   `replies` read and no model request, and another user still gets an answer.

Rig: `DispatchRig.count(name)` records calls to a FakeSlack method (moved from the asks tests).
Results: full suite 1543 passed, 515 skipped, 9 deselected; `pytest -m pg` 493 passed, 17 skipped; ruff clean.
