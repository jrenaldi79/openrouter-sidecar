---
title: "2026-10-21-slack-sidecar: task 3 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 3 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 3: Team principal, credentials and team keys

**Files:**
- Create: `hosted/slack/principal.py`
- Modify: `hosted/provisioning.py`, `hosted/openrouter_keys.py`
- Test: `tests/test_hosted_slack_principal.py`, `tests/test_hosted_provisioning.py`, `tests/test_hosted_openrouter_keys.py`

**Interfaces:**
- Consumes: `ChannelRow` (Task 2), `Principal`, `StudentKeyStore`, `KeyProvisioner`, `KeyManager`.
- Produces:
  - `SLACK_ISSUER = "slack"`
  - `team_principal(team_id: str, channel_id: str, row: ChannelRow) -> Principal`. It raises `ValueError` when the
    ids don't match the row, or the row is inactive.
  - `@contextmanager acting_as(principal: Principal)` sets a module `ContextVar`.
  - `SlackCredentials(workspace_id: str, keys: StudentKeyStore)` implements `CredentialSource`:
    - `resolve()` reads the ContextVar. With none set it raises `AuthRequiredError("No Slack channel in context.")`.
      With a different workspace it raises `AdvisorAccessError`. With no key it raises `AuthRequiredError(NOT_READY)`.
    - `check(scope)` is `keys.is_active`.
    - `forget()` calls `keys.mark_rejected` for the current team.
  - Provisioning:
    - `TeamKey(label: str)` (frozen).
    - `KeyProvisioner.ensure(principal, *, team: TeamKey | None = None) -> StudentKey`.
    - `key_name(principal, cohort=None, team_label=None)` returns `"Sidecar · <label> · <cohort>"` when
      `team_label` is given.
    - `TEAM_GUARDRAIL = "Sidecar team budget"`.
    - `KeyManager.create(name, workspace_id, *, guardrail: str = BUDGET_GUARDRAIL) -> CreatedKey`.

- [ ] **Step 1: Write failing tests.**
  - `test_principal_from_ids_and_row_only`: the subject is `"T1:C1"`, the issuer is `"slack"`, and the workspace
    and cohort come from the row. Mismatched ids raise.
  - `test_team_key_never_matches_student`: `team_principal(...).key != Principal("https://issuer/", "T1:C1", ws).key`.
  - `test_resolve_outside_context_refused`: `SlackCredentials.resolve()` with no `acting_as` raises `AuthRequiredError`.
  - `test_resolve_inside_context` (with `keys` and `db_target`): inside `acting_as(p)` it returns
    `(key, Scope("slack|T1:C1", ws))`.
  - `test_context_isolated_across_tasks`: two `asyncio` tasks acting as different teams each resolve their own scope.
  - `test_team_key_name_and_guardrail` (provisioning, with the harness `FakeOpenRouter`): a guardrail named
    `Sidecar team budget` exists; `ensure(p, team=TeamKey("abbott"))` creates key `Sidecar · abbott · 2027` and
    assigns that guardrail's id.
  - `test_no_team_guardrail_no_key`: with only the student guardrail present, `ensure(..., team=...)` raises and
    creates no key.
  - `test_slack_principal_requires_team`: `ensure(team_principal(...))` with `team=None` raises `BadRequestError`.
  - `test_recheck_recreates_with_team_name`: after a 401 and the key deleted at OpenRouter, the recreated key keeps
    the team name.
- [ ] **Step 2: Run** the three test files. Expected: FAIL.
- [ ] **Step 3: Implement.** In `KeyManager`, `_budget_guardrail(workspace_id)` becomes
  `_guardrail(workspace_id, name)`. Its cache is keyed `(workspace_id, name)`, and the error message names the
  missing guardrail. `KeyProvisioner` passes `team` down through `_create` and `_recheck`.
- [ ] **Step 4: Run** again, plus the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): team principal, Slack credentials and team keys`.

