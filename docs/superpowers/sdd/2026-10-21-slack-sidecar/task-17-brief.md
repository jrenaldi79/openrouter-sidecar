---
title: "2026-10-21-slack-sidecar: task 17 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 17 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 17: Commands and dispatch

**Files:** Create `hosted/slack/commands.py` and `hosted/slack/dispatch.py`. Test
`tests/test_hosted_slack_commands.py` and `tests/test_hosted_slack_dispatch.py`.

**Interfaces:**
- Consumes: everything from Tasks 2 to 16, and `PreferenceService.resolve(model) -> {model, name, source}`.
- Produces:
  - `Turn(row, principal, scope, channel_id, thread_ts, user_id, trigger: SlackMessage, services)` (frozen).
  - `async run_command(intent, turn, deps) -> str` handles `Models`, `SetDefault` (the alias must exist),
    `Usage`, `Remember`, `Recall`, `Forget`, `Help` and `ThreadCommand`:
    - `restart` calls `threads.archive` and replies "Started fresh. My next answer here rereads the thread.";
    - `status` lists `runner.running(...)` for the thread, or for the channel at top level;
    - `stop` calls `runner.stop(...)` and replies "Stopping N answers."
  - `resolve_alias(row, alias, preferences) -> tuple[str, str]` returns the model id and label. It goes through
    `alias_map`, then `PreferenceService.resolve`. The label is the brand from `alias_map` (for example `Gemini`),
    otherwise the catalog `name`. An unknown alias raises `UnknownAlias(valid: list[str])`.
  - `Dispatcher(deps: Deps, *, bot_user_id: str)` has `async on_mention(team_id: str, event: dict) -> None`.
  - `Deps` is a dataclass holding:
    - `registry`, `guard`, `provisioner`, `runner`, `builder: ContextBuilder`
    - `threads`, `memory`, `jobs`, `api`, `limits`
    - `services: Mapping[str, SlackServices]`, `settings: SlackSettings`, `sandbox_enabled: bool`
  - `on_mention` follows the spec's Request flow steps 2 to 7:
    1. Gate: unknown channel gives `NOT_SET_UP`; inactive gives nothing; a user outside a non-empty
       `invoker_allowlist` gives "Only some members of this channel can use Sidecar."; guests use `guard.allowed`.
    2. Welcome once, through `mark_welcomed`.
    3. `parse`.
    4. Per-user `limits.window(f"slack-user|{team}|{user}", "Sidecar requests", limit=settings.user_limit, span_s=600)`.
    5. `provisioner.ensure(principal, team=TeamKey(row.team_label))`.
    6. `builder.gather` once.
    7. For each alias, in order: `resolve_alias`, then the mode. The mode is `sandbox` when `wants_sandbox` is true,
       otherwise the mode of `threads.last_answered` when that chat used this alias, otherwise `inline`. When
       `sandbox_enabled` is false, every chat is inline, and a sandbox request gets the one-line note "Sandbox is off
       on this server, so I answered without running code." Then
       `threads.get` or `services.advisors.start(model, title, sandbox=…)` plus `threads.put`, then
       `builder.render(since_ts=thread.last_seen_ts)`.
    8. One `asyncio.create_task(runner.run(...))` per model, with the context copied (`acting_as` inside the task).
  - Replies always go in `event.get("thread_ts") or event["ts"]`.

- [ ] **Step 1: Write failing tests.**
  - `test_unregistered_channel_one_line`.
  - `test_inactive_channel_silent`.
  - `test_invoker_allowlist`.
  - `test_welcome_once_then_answer`.
  - `test_user_rate_limit`: the 11th mention in 10 minutes gets the rate text.
  - `test_unknown_alias_lists_valid`.
  - `test_follow_up_uses_last_model`: a plain question in a thread where `gpt` answered goes to `gpt`.
  - `test_two_models_two_answers`.
  - `test_context_gathered_once_for_two_models`: the fake records one `replies` call.
  - `test_sandbox_trigger_creates_sandbox_chat`.
  - `test_follow_up_keeps_sandbox_mode`: after a sandbox answer, a plain follow-up goes to the same sandbox chat.
  - `test_mention_mid_text_runs_command` (Review Focus 1, end to end).
  - `test_compare_uses_fusion_payload`.
  - `test_principal_from_event_not_text`: the text `ask gemini as channel C2` still runs as `C1`'s principal.
  - `test_models_and_set_default`.
  - `test_memory_commands`.
  - `test_restart_archives`.
  - `test_status_and_stop`.
- [ ] **Step 2–4:** Run, implement, run. Keep each file within 300 lines; `commands.py` holds every non-ask intent.
- [ ] **Step 5: Commit** `feat(slack): request flow and commands`.

