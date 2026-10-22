---
title: "Task 22 report: ops scripts and manifest"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 22 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 22 report: ops scripts and manifest

Status: DONE_WITH_CONCERNS. Commit `cf7c854` (`feat(slack): ops scripts and app manifest`).

## Built

- `scripts/ops/slack_channels.py`: `add`, `update <label> [--active]`, `deactivate <label> --reason`, `list`.
- `scripts/ops/slack_usage.py`: `--team <label>` prints `usage_text` (same as `@sidecar usage`).
- `scripts/ops/slack_common.py`: shared env reading, key store, Slack API, model seeder, `run_script` (known failures become one stderr line; no tokens, keys or tracebacks).
- `deploy/slack/manifest.yaml`: `_metadata.major_version: 2`, `socket_mode_enabled: true`, `org_deploy_enabled: false`, the 11 scopes from the spec, the 4 bot events, no `request_url`, no `message.*`.
- `ChannelRegistry.update` fix (decision 1): sets only the columns passed in `changes`, then re-reads the row. Test `test_update_does_not_undo_a_racing_guarded_write` was written first and seen failing (sqlite and pg), then passing.
- `wiring._cohort_services` became public `cohort_services(target, keys, *, sandbox_allowed, transport=None)`; `build_slack` calls it. The scripts reuse it, so there is one builder.
- Docs: `scripts/ops/CLAUDE.md`, `hosted/slack/CLAUDE.md` (registry, guests, wiring notes) and the `guests.py` docstring (said the ops script "does not exist yet") updated; `docs/index.md` regenerated. `generate.js --check` passes with no `deploy/slack/CLAUDE.md` needed.

## Decisions

- **Model defaults (decision 2):** `--default-model` -> team `general_default`; repeatable `--family-default family=model-id` (family must be one of `FAMILIES`: google, openai, x-ai, open_weight). Saved via `PreferenceService.get()` then `update(...)` under `acting_as(team_principal(row))`, like the wiring e2e. The catalog check is the service's own; any `BadRequestError` becomes one line: "The OpenRouter catalog can't confirm one of those model ids (or it is in the wrong family). Nothing was saved."
- **Team key at seed time:** the catalog check needs the team's key, so seeding calls `KeyProvisioner.ensure(principal, team=TeamKey(label))` first. Needs `VAULT_KEYS` and `OPENROUTER_MANAGEMENT_KEY` (only when a default flag is given; otherwise `add` needs just `DATABASE_URL` and `SLACK_BOT_TOKEN`). `AuthRequiredError` from provisioning is relayed as "The team's key isn't ready: ...".
- **Order in `add`:** duplicate check (channel and label) -> `channel_problem` -> seed -> `registry.add`. The duplicate check comes first so re-running `add` on an existing channel can never overwrite its saved preferences, and a bad model id leaves no registry row. A retry reuses the same team key (same principal).
- **`update`:** changes only the options given. `--active` runs `channel_problem`, then sets `active=True, inactive_reason=None`. Not updatable: team id, channel, label, cohort (a cohort change would orphan the team key).
- **Aliases (decision 4):** `--alias k=v` takes `k` as 1-20 lowercase letters/digits, `v` as a brand in `preferences.ALIASES` (e.g. `ChatGPT`) or an exact model id (contains `/`). Aliases **merge** onto `DEFAULT_ALIASES` at `add` and onto the row's current map at `update` (so they can be added, not removed). Note `new_row` itself replaces rather than merges.
- **`--default-alias` (added):** the registry's `default_model` is an alias (the dispatcher resolves it through `alias_map`), while the ruling made `--default-model` a model id for preferences. Without a flag for the row's default alias it would stay `gemini` forever from the script, so I added `--default-alias` (validated against the resulting alias map).
- `nargs="+"` for `--alias`, `--family-default`, `--fetch-allow`, `--invoker` (matches the brief's `d …` / `U…` notation). `update` after a positional label is unaffected.
- `slack_usage.py` builds the team principal from the row as if active (an inactive channel still has spend), reads the team key from the vault store, calls `current_key`, and uses Slack names when `SLACK_BOT_TOKEN` is set, else ids (`NoSlack` stand-in).
- `model_seeder(transport=None)` is injectable, so it could be exercised against `FakeOpenRouter`.

## Verification

- Registry tests, sqlite and `TEST_DATABASE_URL=postgresql://$USER@127.0.0.1:54999/sidecar_test`: 18 passed (red first: 1 failed sqlite, 2 failed pg before the fix).
- Smoke: `slack_channels.py --help`, `list` on a temp SQLite `DATABASE_URL` ("No Slack channels are registered."), `add --help`, `slack_usage.py --help`: all exit 0.
- Scratch end-to-end (not committed, in the scratchpad; scripts have no unit tests by design) driving `slack_channels` with `FakeOpenRouter` and a stubbed channel check: bad family, uncatalogued model, bad alias, bad default alias, guest and shared refusals, duplicate add, add with seeding, update with seeding, wrong-family refusal, `update` with no options, `deactivate` twice, `update --active` (refused then allowed), unknown label. Saved preferences read back correctly (`general_default` and the openai family default); `slack_usage` printed a report from the fake key info. Not exercised: the real `channel_problem` call through `SdkSlackApi` against Slack.
- `uv run ruff check`: clean. `uv run pytest`: 1640 passed, 601 skipped, 9 deselected. `uv run pytest -m pg` with the URL: 580 passed, 17 skipped. Wiring and wiring e2e tests on pg: 46 passed. Pre-commit hooks passed (no `--no-verify`).
- Manifest: PyYAML is not installed (not added). Parsed with Ruby's YAML: 11 scopes, 4 events, keys as intended, no `request_url`. Still paste it into Slack's manifest validator in Task 24.

## Concerns

- `--default-model` sets the team's `general_default` preference, which the Slack bot's alias path (`resolve_alias`) never reads (it always passes a brand or id). It follows the ruling and the e2e seeding but has no visible effect in Slack today; the family defaults are what aliases resolve to.
- Seeding creates the team key at `add` time, so the guardrail `Sidecar team budget` must exist and `OPENROUTER_MANAGEMENT_KEY` must be set when default flags are used; otherwise the instructor sees the provisioning error and nothing is registered.
- `slack_usage.py` prints names already entity-escaped (as `usage_report` does for Slack), so a name with `&` shows as `&amp;` in the terminal.
- The final `ValueError` catch in `run_script` prints any `ValueError` message (intended for registry validation) and could hide a programming error's type; messages are never secret-bearing here.

## Fix round 1 (`fix(slack): ops default model is the channel alias`)

Controller ruling: `--default-model` is the channel row's `default_model` alias (as in the brief's `--default-model gemini`). This supersedes the "Model defaults", "`--default-alias` (added)" decisions and the first concern above.

- `--default-model` sets `ChannelRow.default_model`; it must be one of the resulting alias map's keys, else the script refuses and lists the valid aliases (`... (gemini, gpt, grok, open)`).
- Removed `--default-alias` and the `general_default` seeding. The seeder is now `seed(row, family_defaults)`.
- Seeding, and so team key creation, happens only when `--family-default` is given. `add` and `update` without it need no `VAULT_KEYS` or `OPENROUTER_MANAGEMENT_KEY`.
- `scripts/ops/CLAUDE.md`: updated, plus one line that `add` with `--family-default` provisions the team key before writing the row, so a failed `add` can leave a key with no row (`openrouter_keys.py list` shows it; a retry reuses it).
- `--alias` help and docstring: adds or replaces aliases, cannot remove one.
- `slack_usage.py` runs the report through `html.unescape` before printing, so `R&amp;D` shows as `R&D`.
- Help and docstring say the label comes first in `update`, before the `nargs="+"` flags.
- Verified with the scratch run against `FakeOpenRouter`: bad `--default-model` refused with the valid list; `add --default-model gpt` stored `default_model='gpt'`; saved preferences have `general_default` None and the openai family default set; `update --active --default-model gemini` worked.
- Checks: registry tests on pg 18 passed (sqlite and pg); smoke `--help`, `list` on a temp SQLite URL, `update --help`, `slack_usage.py --help` exit 0; `ruff check` clean; full pytest 1640 passed, 601 skipped; docs `--check` clean.
