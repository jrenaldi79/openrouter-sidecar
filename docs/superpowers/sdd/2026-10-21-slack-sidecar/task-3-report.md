---
title: "Task 3 report: team principal, Slack credentials and team keys"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 3 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 3 report: team principal, Slack credentials and team keys

Status: DONE. Commit `b5bec3d` on `feat/slack-sidecar` ("feat(slack): team principal, Slack credentials and team keys"), pre-commit hook passed (not bypassed).

## Implemented
- `hosted/slack/principal.py`: `team_principal(team_id, channel_id, row)` builds `Principal("slack", "T:C", row.workspace_id, row.cohort)` only from the ids and row; `ValueError` on mismatched ids or inactive row. `acting_as` sets a module-private `ContextVar` and resets by token in `finally`. `SlackCredentials(workspace_id, keys)` implements resolve (no context -> `AuthRequiredError("No Slack channel in context.")`, other workspace -> `AdvisorAccessError`, no key -> `AuthRequiredError(pending_reason() or NOT_READY)`), check (`workspace` match and `keys.is_active`, never reads the context) and forget (`mark_rejected`, raises `AuthRequiredError` outside a context). It imports neither `current_principal` nor `mcp` auth context directly.
- `hosted/principal.py`: `SLACK_ISSUER = "slack"` defined here and re-exported by `slack/principal.py`. Reason: `provisioning.py` needs it, and `slack/principal.py` imports `provisioning`, so defining it only in the Slack module would be circular.
- `hosted/openrouter_keys.py`: `TEAM_GUARDRAIL = "Sidecar team budget"` (beside `BUDGET_GUARDRAIL`, re-imported by `provisioning`, so `provisioning.TEAM_GUARDRAIL` works). `create(name, ws, *, guardrail=BUDGET_GUARDRAIL)`; `_budget_guardrail` became `_guardrail(workspace_id, name)`, cache keyed `(workspace_id, name)`, error names the missing guardrail, still fail closed.
- `hosted/provisioning.py`: frozen `TeamKey(label)`; `key_name(..., team_label=None)`; `ensure(principal, *, team=None)` raises `BadRequestError` when `(issuer == "slack") != (team is not None)` (checked before anything else, so no key is created and no store access happens); `_create(principal, team)` passes `guardrail=TEAM_GUARDRAIL` only for teams (student calls are unchanged). `prepare` untouched (student-only).
- Slack `CLAUDE.md` Owns list updated; `docs/index.md` regenerated and staged.

## TDD
RED (before any implementation), three collection errors:
```
ModuleNotFoundError: No module named 'openrouter_image_mcp.hosted.slack.principal'
ImportError: cannot import name 'TEAM_GUARDRAIL' from 'openrouter_image_mcp.hosted.provisioning'
ImportError: cannot import name 'TEAM_GUARDRAIL' from 'openrouter_image_mcp.hosted.openrouter_keys'
```
GREEN: the three files give 45 passed, 24 skipped (Postgres variants). Full: `uv run pytest -q` 910 passed, 338 skipped, 7 deselected; `uv run ruff check` clean.

## Files
Created: `src/openrouter_image_mcp/hosted/slack/principal.py`, `tests/test_hosted_slack_principal.py`.
Modified: `hosted/openrouter_keys.py`, `hosted/principal.py`, `hosted/provisioning.py`, `hosted/slack/CLAUDE.md`, `docs/index.md`, `tests/test_hosted_provisioning.py`, `tests/test_hosted_openrouter_keys.py`, `tests/hosted_harness.py`.
All touched code files are well under 300 lines; no function over 50 lines.

## Tests added (beyond the brief's list)
Inactive row refused; forget outside context; context reset after exception; other workspace refused; check/forget follow the store; student with `team` refused; team creation passes the team guardrail and student creation passes no kwarg; students still assigned `g-budget`; `KeyManager` team guardrail assigned and cached per name; missing team guardrail error names it and creates no key.

## Self-review
Brief and rulings covered; no token or key-shaped literals (only `sk-or-v1-...` style fakes already used in the repo). Test-support change: `FakeOpenRouter` now has a `guardrails` list (default holds only the student guardrail, so existing behaviour is unchanged) and records `assignments`, so tests can add the team guardrail and assert which guardrail a key got.

## Concerns
- `_recheck` does not take `team`: it only removes the row and returns `None`, and `ensure` then calls `_create(principal, team)`, so the recreated key keeps the team name (covered by `test_recheck_recreates_with_team_name`). Adding an unused parameter seemed worse than deviating from the brief wording.
- `ruff --fix` rewrapped the `errors` import in `provisioning.py` into a multi-line form (the repo's isort settings force it).
