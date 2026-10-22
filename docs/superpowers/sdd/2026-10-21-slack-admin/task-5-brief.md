---
title: "2026-10-21-slack-admin: task 5 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 5 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 5 (Wave B): Channel admin service

**Files:** Create `hosted/slack/channel_admin.py` (split `channel_setup.py` for register/finish if it nears 300 lines). Test `tests/test_hosted_slack_channel_admin.py`.

**Interfaces:**
- Consumes: Task 2 (`audit.insert`, `AdminAudit.record`, actions, `Session`), Task 3 (`team_id`, `bot_channels`, the registry `audit` callback, `public_models`, `filter_catalog`), `channel_problem`, `new_row`, `registry_defaults`, `team_principal`, `acting_as`, `TeamKey`, `KeyProvisioner.ensure`, `SlackServices` (`preferences`, `client`).
- Produces: `ChannelAdmin(registry, api: SlackApi | None, services: Mapping[str, SlackServices] | None, provisioner, audit: AdminAudit, *, names: NameCache | None = None)`; `class ChannelRefused(Exception)` (page-ready message); `@dataclass Overview(registered: list[tuple[ChannelRow, str]], unregistered: list[dict], slack_note: str | None)`.
  - `async overview() -> Overview`: names from `channel_info` (id on failure); unregistered = `bot_channels()` minus registry rows; `api is None` → `SLACK_OFF`; a Slack failure → registered rows only and `SLACK_DOWN`.
  - `async check(channel_id) -> str | None`: `GUEST_BLOCKED` text or None.
  - `async catalog() -> list[dict]`: `filter_catalog(await services[any].client.public_models(), now)`; failure → `ChannelRefused(CATALOG_DOWN)`.
  - `async members(channel_id) -> list[tuple[str, str]] | None`: `(user id, name)` sorted by name through `NameCache` with at most five lookups at a time; None above 50 members or on failure.
  - `async register(team_id, channel_id, fields: dict, family_defaults: dict, actor) -> ChannelRow`: validate (`new_row` + `registry._validate`; models in `catalog()`), duplicate label/channel → `ChannelRefused`, `check` → `ChannelRefused`; `registry.add(row off, SETTING_UP, audit=REGISTERED)`; then `finish_setup(team_id, channel_id, family_defaults, actor)`.
  - `async finish_setup(team_id, channel_id, family_defaults: dict, actor) -> ChannelRow`: `provisioner.ensure(team_principal(..., replace(row, active=True)), team=TeamKey(label))` (`AuthRequiredError` from a missing guardrail → reason `NO_GUARDRAIL`), save `family_defaults` (from the register form, or from the edit form's model selects when Finish setup is pressed) through `preferences.update`, audit `MODELS_CHANGED`, then `registry.update(active=True, inactive_reason=None, audit=TURNED_ON)`. Any failure → `registry.update(inactive_reason=SETUP_UNFINISHED)` and `ChannelRefused(SETUP_UNFINISHED_TEXT)`. Safe to repeat.
  - `async change(team_id, channel_id, changes: dict, family_defaults: dict, actor) -> dict[str, list]`: only differing keys; `registry.update(audit=CHANGED)` in one transaction; then changed family defaults through `preferences.update` and `AdminAudit.record(MODELS_CHANGED)`; a model failure after the registry write → `ChannelRefused(MODELS_NOT_SAVED)`; nothing differing → `ChannelRefused(NOTHING_CHANGED)`.
  - `async turn_off(team_id, channel_id, reason, actor)` and `async turn_on(team_id, channel_id, actor)` (guest check first), each audited in the registry transaction.

- [ ] **Step 1: Failing tests** (FakeSlack, `db_target`, fake provisioner and preferences like `tests/slack_dispatch_rig.py`'s `FakePreferences`, respx for the public list): `test_overview_lists_registered_and_unregistered`, `test_overview_slack_off`, `test_overview_when_slack_fails`, `test_register_refuses_guest_channel`, `test_register_twice_refuses_second` (Review Focus 1: one row, one `ensure` call), `test_register_saves_row_key_models_and_audit` (entries: registered, models changed, turned on), `test_setup_failure_then_finish` (Review Focus 2: missing guardrail and a model-save failure each leave the row off with `SETUP_UNFINISHED`; `finish_setup` completes it with one key), `test_audit_failure_rolls_back_registry_change`, `test_change_writes_only_changed_fields` (Review Focus 3), `test_change_model_failure_keeps_registry_change`, `test_change_nothing`, `test_turn_off_and_on_audited`, `test_turn_on_reruns_guest_check`, `test_members_cap_50_and_concurrency`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): channel admin service`.

