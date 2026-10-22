---
title: "Brief: Slack team keys live in one Slack workspace"
description: "Archived planning record for the Slack workspace move (one SLACK_WORKSPACE constant): implementation brief given to the subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Brief: Slack team keys live in one Slack workspace

Repo: OpenRouter Sidecar (Python 3.12, uv). Package `src/openrouter_image_mcp`. Read the root `CLAUDE.md`, `.claude/rules/*.md`
and the folder notes `hosted/CLAUDE.md`, `hosted/slack/CLAUDE.md`, `hosted/admin/CLAUDE.md`, `scripts/ops/CLAUDE.md` first.

## Decision (approved by the user, binding)

Every Slack channel's OpenRouter team key, and its `Sidecar team budget` guardrail, lives in ONE OpenRouter workspace,
`8a738968-da7d-4d55-900a-16789ba50003`, whatever the channel's class. Student keys (claude.ai, Desktop, Code) stay in
their cohort workspaces (`config.COHORT_WORKSPACES`, 2027 and 2028), unchanged.

1. **Constant:** `config.SLACK_WORKSPACE = "8a738968-da7d-4d55-900a-16789ba50003"`, next to `COHORT_WORKSPACES`, with a
   one-line comment. Not an env var.
2. **`cohort` stays on the row as the class label only.** It still must be a key of `COHORT_WORKSPACES`; it still names
   the key (`Sidecar · <label> · <cohort>`, unchanged, via `principal.cohort`), and the list, usage and ops output keep
   showing it. It no longer decides any workspace.
3. **Registry (`hosted/slack/registry.py`):** `new_row` sets `workspace_id = config.SLACK_WORKSPACE`. `_validate` also
   refuses any row whose `workspace_id != config.SLACK_WORKSPACE`. `update` no longer rewrites `workspace_id` when the
   cohort changes (delete that branch); `workspace_id` stays non-updatable. No schema change.
4. **Workspace-gated provisioning (`hosted/provisioning.py`):** `KeyProvisioner` takes a second set, the team
   workspaces (e.g. `team_workspaces: frozenset[str] = frozenset()` keyword). `ensure(principal, team=TeamKey(...))`
   accepts only a workspace in the team set; `ensure(principal)` with no team accepts only a workspace in the student set.
   Anything else gives the existing `NOT_SERVED` outcome. `key_middleware` keeps checking the student set only. Build it in
   `hosted/app.py` (~`:130`) and `scripts/ops/slack_common.py` (~`:99`) with student set = cohort workspaces and team set
   = `{config.SLACK_WORKSPACE}`. Tests first (TDD): a student principal in the Slack workspace gets NOT_SERVED and no
   create call; a team principal in a cohort workspace gets NOT_SERVED and no create call; a team principal in the Slack
   workspace is created with `guardrail=TEAM_GUARDRAIL` in that workspace and named `Sidecar · <label> · <cohort>`.
5. **One Slack services set (`hosted/slack/wiring.py`):** replace `cohort_services` (one `SlackServices` per cohort) with
   one `SlackServices` built for `config.SLACK_WORKSPACE` (its own `AdvisorClient`, `AdvisorService`,
   `SlackCredentials`, `PreferenceService`). `SlackApp.services`, `dispatch.Deps.services`, `channel_setup.TeamModels`,
   `channel_admin` and `scripts/ops/slack_common.py` take that one set instead of a dict keyed by cohort; remove every
   `services[row.cohort]` lookup. Recovery (`hosted/slack/recovery.py`, `_recover_once` in wiring): a job row whose
   workspace isn't the Slack workspace is skipped quietly (no `KeyError`, no log per sweep). Pick the clearest names; keep
   modules injectable (tests pass fakes).
6. **Admin page:** the register form field for cohort is labelled `Class` (one-word labels stay in templates); the
   channels list heading likewise. Reword in `hosted/admin/copy.py`, keeping the page's plain style:
   - `NO_GUARDRAIL` -> `"The Slack workspace in OpenRouter has no 'Sidecar team budget' guardrail, so no team key can be made."`
   - `KEY_NOT_SERVED` -> `"This server doesn't serve the Slack workspace"`
   - `LABEL_FIXED` -> `"The label names the team's key, so it doesn't change after the first save."`
   `failure_reason` in `hosted/slack/channel_setup.py` no longer formats `{cohort}` into these (remove the placeholders
   and the `.format(cohort=...)` calls that become pointless). Fix the `UNTRACKED` comment there ("workspace follows the
   cohort"). If the cohort is still editable anywhere after first save and the copy said it can't change, keep whatever
   behavior exists today; only the wording about workspaces changes.
7. **Ops key script (`scripts/ops/openrouter_keys.py`):** `list` and `usage` also cover `config.SLACK_WORKSPACE`;
   `guardrails` checks `Sidecar student budget` in each cohort workspace and `Sidecar team budget` in the Slack workspace
   (use the existing constants `BUDGET_GUARDRAIL` / `TEAM_GUARDRAIL`). Update its tests.
8. **Live spikes (`tests/live/test_slack_spikes.py`):** default workspace becomes `config.SLACK_WORKSPACE` when
   `OPENROUTER_LIVE_WORKSPACE` is unset (drop the `OPENROUTER_LIVE_COHORT` fallback and fix the docstring). Don't touch
   anything else in that file.
9. **Docs, same change:** root `CLAUDE.md` Slack gotcha ("team keys use the guardrail ... in their own workspace" ->
   in the Slack workspace, `config.SLACK_WORKSPACE`); folder notes `hosted/CLAUDE.md`, `hosted/slack/CLAUDE.md`,
   `hosted/admin/CLAUDE.md` if affected, `scripts/ops/CLAUDE.md`; `docs/HOSTED_OPERATIONS.md` (guardrail goes in the
   Slack workspace, `--cohort` now only labels the class); the specs
   `docs/superpowers/specs/2026-10-21-slack-sidecar-design.md` and `...-slack-admin-design.md` where they say team keys
   or the guardrail live in the cohort workspace. Leave `docs/superpowers/plans/` alone (history). Run
   `node scripts/self-documentation/generate.js` and stage its output.

## Known touch points (from a read-only trace; verify, it may be incomplete)

- `registry.py:64-72` new_row, `:75-91` _validate, `:164-170` update.
- `provisioning.py:87` NOT_SERVED check, `:104-105` key name and guardrail, `:168` key_middleware.
- `app.py:130` provisioner construction; `scripts/ops/slack_common.py:99,100,105`.
- `wiring.py:62,70,157-172`; `dispatch.py:71,174-177`; `channel_setup.py:46-47,105-107,149-151`;
  `channel_admin.py:110-116,178`; `recovery.py:53,100`.
- `hosted/slack/principal.py` `SlackCredentials` (one per workspace) needs no change.
- Admin: `templates/channel_form.html:32-34`, `templates/channels.html:18`, `views.py:82`, `routes_channels.py:79`, `forms.py`.
- Tests asserting the old shape: `test_hosted_slack_registry.py:23`, `test_hosted_slack_wiring.py:124-128`,
  `test_hosted_slack_principal.py:41,54-107`, `slack_runner_rig.py:90-98`, `test_hosted_slack_outputs_post.py:165`,
  `test_hosted_provisioning.py:181-201`, `test_hosted_slack_wiring_e2e.py:99`, `test_hosted_slack_channel_setup.py:66,77`,
  `test_hosted_slack_channel_admin_register.py:69`, `test_hosted_admin_copy.py:7`, plus whatever the suite turns up.
  Tests that use `WS_2027` for a team principal should use the Slack workspace.

## Constraints

- Work only in your worktree. Never `--no-verify`, never a bare `git stash`, no destructive git.
- No key/token-shaped literals anywhere (secret scan covers tests). Workspace UUIDs are fine.
- 300-line ratchet: new files <= 300 lines, files already over may not grow; functions <= 50 lines.
- `scripts/checks/` untouched. No new dependencies.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Conventional commit subjects, e.g.
  `feat(slack): team keys live in the Slack workspace`. One or a few commits.
- Before reporting: `uv run ruff check`, `uv run pytest` (all pass), and if `TEST_DATABASE_URL` is given in the dispatch,
  `TEST_DATABASE_URL=... uv run pytest -m pg`. `node scripts/self-documentation/generate.js --check` clean.
- Do not dispatch subagents.

## Report

Write the full report to `.superpowers/sdd/2026-10-21-slack-workspace/report.md` inside your worktree (git-ignored):
what changed per file, the tests added (and that you saw them fail first), suite results, anything you decided that
this brief didn't settle, and concerns. Return only: status (DONE / DONE_WITH_CONCERNS / BLOCKED), commit SHAs, a
one-line test summary, and concerns.
