---
title: "2026-10-21-slack-admin: task 3 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 3 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 3 (Wave A): Slack, registry and catalog additions

**Files:** Modify `hosted/slack/api.py`, `tests/slack_fakes.py`, `hosted/slack/registry.py`, `preferences.py`, `advisor_client.py`, `hosted/slack/CLAUDE.md`, `src/openrouter_image_mcp/CLAUDE.md`. Test the existing `tests/test_hosted_slack_api*.py`, `tests/test_hosted_slack_registry.py`, `tests/test_preferences*.py`, `tests/test_advisor_client*.py`.

**Interfaces:**
- Produces:
  - `SlackApi`/`SdkSlackApi`: `async team_id(self) -> str` (`auth.test`'s `team_id`), `async bot_channels(self) -> list[dict]` (`users.conversations`, `types="public_channel,private_channel"`, `exclude_archived=True`, paged by cursor, at most 10 pages of 200; each `{"id", "name", "is_private"}`), both through `_call`. `FakeSlack.team = "T0"`, `FakeSlack.bot_in: list[str]`.
  - `ChannelRegistry.add(row, *, audit=None)`, `update(team_id, channel_id, *, audit=None, **changes)`, `deactivate(team_id, channel_id, reason, *, audit=None)`: `audit` is `Callable[[db, ChannelRow before | None, ChannelRow after], None]`, called on the same connection before commit; an exception from it rolls the write back. For `deactivate`, called only when it returns True.
  - `AdvisorClient.public_models(self) -> list[dict]`: `GET models` without an `Authorization` header, same parsing and text-and-tools filter as `models(key)`.
  - `preferences.filter_catalog(rows, now: datetime, *, secrets: tuple[str, ...] = ()) -> list[dict]`: the filter extracted from `PreferenceService._catalog` (id shape, batch, family, six-month window, dedupe, `MAX_MODELS`, `{id, name, pricing, context_length, created, family}`); a row is dropped when any non-empty secret appears in its id or name. `_catalog` calls it with `(key, scope.owner)`, so student behaviour is unchanged.

- [ ] **Step 1: Failing tests:** `test_team_id`, `test_bot_channels_pages_and_shapes`, `test_bot_channels_error_is_slack_call_error`, `test_fake_matches_protocol`; `test_audit_callback_commits_with_row`, `test_audit_callback_failure_rolls_back` (add, update, deactivate; `db_target`); `test_public_models_sends_no_auth_header` (respx); `test_filter_catalog_matches_student_catalog` (same input, same output as `PreferenceService.view`'s models), `test_filter_catalog_empty_secret_keeps_rows`.
- [ ] **Step 2–4:** Run, implement, run; full suite (student preference tests unchanged).
- [ ] **Step 5: Commit** `feat(slack): admin hooks in the Slack api, registry and catalog`.

