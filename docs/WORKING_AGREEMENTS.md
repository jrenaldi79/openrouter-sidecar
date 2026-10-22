---
title: "Working agreements"
description: "How the maintainer wants agents to work in this repo: secrets, git, deploys, Auth0, schema changes, tests and copy"
type: "guidance"
status: "maintained"
read_when: "At the start of any agent session in this repo, in any harness (Claude Code, Codex)"
---

# Working agreements

These are the maintainer's standing instructions, gathered from past sessions so any agent harness can follow them.
They sit alongside `CLAUDE.md` and `.claude/rules/`; where they differ, the more specific rule wins.

## Secrets

- Agents never handle real OpenRouter keys, Slack tokens, Auth0 secrets or database passwords. When a secret has to
  go somewhere (Railway variables, a live test), give the maintainer a script that reads it from the terminal with
  `printf "Name: "; read -rs VAR; echo` and passes it on stdin (`railway variable set NAME --stdin --skip-deploys`).
  The script never prints the value. Write such scripts for bash and zsh (`read -p` fails in zsh).
- No key- or token-shaped literals anywhere, tests included (`scripts/checks/secret_scan.py` enforces it).
- Live-test fixtures hide keys from `repr` (see the `Live` dataclass in `tests/live/test_slack_spikes.py`); a
  pytest traceback once printed a live key through a plain tuple.
- Never commit `.env` files, scratch files or temporary output. `.gitignore` covers `.env` and `.env.*`.

## Git

- Never `--no-verify`. Never a bare `git stash` (the stash is shared across worktrees). Force pushes, hard resets and
  other destructive commands need the maintainer's explicit approval.
- Push feature branches to origin once merged work is ready, as a backup. A push never deploys.
- Merging to `main` and opening PRs happen only when the maintainer asks.
- Planning documents (specs, plans, briefs, reports, reviews, handoffs) are committed under `docs/superpowers/` so
  the history is on GitHub.

## Deploys (Railway)

- There is no auto-deploy. A deploy is `railway up --service sidecar --ci` from a clean checkout of the commit to
  ship. Stage variable changes with `--skip-deploys` so they ride the next deploy.
- Before a deploy, check for in-flight work: advisor jobs run in-process for up to 30 minutes, and a restart cuts
  them off. With no direct database access, `railway logs --since 35m --service sidecar` showing only
  `GET /health` and `GET /ready` lines counts as quiet.
- `railway up` can exit 0 while printing "deploys have been paused"; treat that, or "not signed in", as no deploy.
- After a deploy, check `/health` and `/ready` and the logs.

## Auth0 (shared MPD tenant)

- The tenant is shared with the Interview Coach (`../mpd-class-mcp`) and class identity. Its contract and rollout
  live in `../mpd-auth`. Before any tenant or auth-contract change, check with the Interview Coach owner, and get the
  maintainer's explicit approval for each write.
- Record every applied tenant change in `../mpd-auth/docs/ROLLOUT_LOG.md` (commit and push there).
- Don't change the shared "MPD MCP Clients" public client, its APIs, the "MPD - Email in access token" Action or
  the post-login order. Read email from the profile claim (always verified), not the older `/email` claim.

## Schema changes

- The Slack and admin tables ship with the existing `CREATE TABLE IF NOT EXISTS` / `ensure_column` pattern. From the
  first production deploy of that work onward, every schema change and persistent data change is a versioned
  migration with a rollback step. Ordinary query edits are not migrations. Don't add new inline DDL; the migration
  framework (approach A in `docs/superpowers/reviews/2026-10-21-astra-open-questions.md`) comes first.

## Tests

- TDD only for big features: a new module, MCP tool, MCP App, HTTP route or table, or a behaviour change across
  several files. Small changes (copy, descriptions, styling, logos, config, small fixes) get no new tests; run the
  existing suites. `.claude/rules/tdd.md` overrides the global "mandatory TDD".

## Hosts (claude.ai, Claude Code)

- claude.ai groups tools by `readOnlyHint`, and never renders the MCP App of a read-only tool, so a tool that opens
  an app must not be read-only.
- claude.ai caches the tool list when the connector is added; after changing tool metadata, disconnect and
  reconnect.
- claude.ai delivers tool results to MCP Apps without the result `_meta`; Claude Code shows `structuredContent` to
  the model instead of the text, so keep bulky data (base64) out of it.
- Check a host behaviour in the real client before relying on it.

## Copy and design

- Student-facing copy is plain: call them MCP servers, say what each does and how to use it. A visual metaphor may
  stay in the design, never in the words.
- No decorative tags: no status pills, eyebrow labels, stamps, chip rows or coloured accent stripes on cards. Put the
  information in plain text.
