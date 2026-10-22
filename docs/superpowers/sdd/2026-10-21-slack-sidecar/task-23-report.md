---
title: "Task 23 report: documentation"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 23 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 23 report: documentation

Status: DONE. Commit 8ceb81e `docs(slack): operations, folder notes and spec amendments`. Docs only; no code changed.
`node scripts/self-documentation/generate.js --check` reports "Documentation checked: 2 outputs, 0 unclassified." The pre-commit hook passed (ruff clean, 1640 passed, 601 skipped).

## What changed

- **Spec** (`docs/superpowers/specs/2026-10-21-slack-sidecar-design.md`, `status: "pending"` kept):
  - Amendment 1: `SIDECAR_TEAM_GUARDRAIL_ID` is removed from Settings and Keys and budget. Team keys use the guardrail `Sidecar team budget` in their own workspace, found by name.
  - Amendment 2: the tool loop replays output items; every request sets `store: false` and never `previous_response_id`.
  - Amendments 3 and 4: written as design, and marked "Pending the live check" in the Tools table, in Isolation and security (Exfiltration) and in the new "To confirm" list.
  - Amendment 5: `files` shape (`container_file_citation`, at most 10 per result, `filename` is the path) in Sandbox outputs.
  - Every turn re-renders pins, channel history and the trimmed thread into the replaced `slack/context.md`.
  - Team model defaults set at registration: `--family-default`, `--default-model`, the "no model set yet" copy, and team key creation.
  - Start retry and restart recovery described under Errors and edge cases.
  - `slack_posted_at` and `warned_month` added.
  - Components table gained `wiring.py`, `recovery.py` and `payload.py`.
  - Rollout step 2 updated.
  - "To confirm during implementation" split into settled and pending.
- **`docs/HOSTED_OPERATIONS.md`:**
  - New section 9, "Slack Sidecar", in the brief's order: guardrail in each cohort workspace, app from the manifest (bot token and `connections:write` app token), the variables, channel registration, usage, turning it off.
  - Also: three rows in the section 1 table, a design link at the top, and a leaked-token line.
  - Real commands and flags only, taken from the script docstrings and argparse. Tokens appear only as `xoxb-…` and `xapp-…`; a grep for token-like strings finds none.
- **Folder notes:**
  - `hosted/slack/CLAUDE.md`: the Owns list was already complete from earlier tasks. I corrected the context line (the request flow never passes `since_ts`, so every turn re-renders) and marked `FUSION_FORCED` as unconfirmed.
  - `scripts/ops/CLAUDE.md` was already accurate and is unchanged.
- **Root `CLAUDE.md`:** one gotcha line (Slack identity from the signed event and the registry; team keys use `Sidecar team budget`).
- `docs/catalog.json` and `docs/index.md`: the HOSTED_OPERATIONS description now mentions the Slack bot (index regenerated).

## Concerns

1. **Spec and code, context.** `ContextBuilder.render` still supports a `since_ts` delta, which no caller uses. I recorded that in the spec and the folder note instead of calling it a bug. If the delta path is dead code, a later cleanup could remove it.
2. **Recovery double posts.** Recovery posts first and marks second, so a crash between the two can post an answer twice. The spec now says at-least-once explicitly.
3. **Two statements are not tested or confirmed live.**
   - The ops section says to invite `@Sidecar` to the channel before `add`, because the guest check reads members. I inferred this from `channel_problem` and Slack's rules for private channels.
   - The Slack-side app token steps (`connections:write`) come from Slack's standard Socket Mode setup, not from this repo. The manifest doesn't create the app token.
4. **`railway run` and Postgres.** The ops commands use `railway run uv run python scripts/ops/...`, matching how section 6 runs `purge`. If the private `DATABASE_URL` isn't reachable from a laptop, the operator needs the public URL. The existing docs already have this ambiguity, and I did not add a new claim about it.
5. **Spec intro text.** The "Settings" table prose still mentions the Slack app manifest as before. No disagreement found.

## Fix round 1

Commit message: `fix(slack): registry default caps from settings, doc corrections`.

1. **Code (controller ruling).** Added `registry_defaults(env)` to `hosted/slack/settings.py`. It returns (daily cap, max concurrent) using the existing `_number` parsing and validation (4.0 and 3 when unset). `load_slack_settings` now calls it too, so the parsing is not duplicated. `slack_channels.py add` fills `daily_cap_usd` and `max_concurrent` from `os.environ` when `--daily-cap` or `--max-concurrent` is omitted; `update` is unchanged. A bad value surfaces as one line through `run_script`. Test-first: two tests in `tests/test_hosted_slack_settings.py` (defaults and fallback; validation) failed on the missing import, then passed. A manual check confirmed `add` picks up `SIDECAR_SLACK_DAILY_CAP_USD=2.5` and an explicit `--daily-cap 9` wins.
2. **Spec, dropped delta design.** Fixed "first-turn" wording in Long threads, the `SIDECAR_SLACK_CONTEXT_CHARS` row and the Testing bullet, and added "(later changed: every turn re-renders the whole thread)" to the Review paragraph.
3. **Spec, job rows.** The runner marks `slack_posted_at` right after the post, so recovery normally skips it; a crash between post and mark can post twice.
4. **HOSTED_OPERATIONS.** The ops scripts now run from the operator's machine with `DATABASE_URL` set to the Postgres service's public URL (`DATABASE_PUBLIC_URL`), plus `VAULT_KEYS`, `OPENROUTER_MANAGEMENT_KEY` and `SLACK_BOT_TOKEN` as each command needs them (a table says which). The docs no longer say `railway run` supplies these, and they note this is checked during live acceptance.
5. **Startup wording** (spec Errors and edge cases, HOSTED_OPERATIONS step 3): a missing or malformed token stops startup; only a well-formed token Slack rejects or revokes, or an outage, enters the retry loop while `/health` keeps serving.
6. **Bot name.** `@sidecar` became `@Sidecar` in the spec, HOSTED_OPERATIONS and both folder notes.
7. **Docs for the caps.** Spec Settings and registry tables, HOSTED_OPERATIONS and both folder notes now say the two variables are the defaults `add` uses, read from the environment the script runs in, and that the server does not read them.

Verification: `generate.js --check` clean, ruff clean, full suite 1644 passed (601 skipped, 9 deselected).

Concerns:
- Code strings the bot posts still say `@sidecar` (`welcome.py`, `commands.py` EMPTY, `runner_steps.py`), and the spec's welcome quote now says `@Sidecar`, so the spec no longer matches those strings verbatim. I left code alone beyond the ruling; a follow-up should change them (and their tests).
- HOSTED_OPERATIONS section 6 (`railway run ... purge`) has the same private-URL problem for the laptop; I did not touch it.

## Fix round 2

Commit e7dbb57 `fix(slack): bot copy names @Sidecar`. User-facing `@sidecar` became `@Sidecar` in `welcome.py` (welcome and help), `commands.py` EMPTY and `runner_steps.py` (chat-full text), with the asserting tests updated. Docstrings and the context test input (a message body) are unchanged. The spec welcome quote matches `welcome.py` word for word (only line wrapping differs). Full suite 1644 passed, ruff and `generate.js --check` clean.
