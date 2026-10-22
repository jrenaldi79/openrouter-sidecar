---
title: "Slack Sidecar launch handoff"
description: "Where the Slack Sidecar launch stands on 2026-10-22: what is done, what is staged, the next steps in order and the backlog"
type: "status"
status: "snapshot"
read_when: "When picking up the Slack Sidecar launch, the migration framework or the admin limiter fix in a new session or harness"
---

# Slack Sidecar launch handoff (2026-10-22)

Start here when you continue this work in a new session, in Claude Code or Codex. Read `AGENTS.md` (or
`CLAUDE.md`), `docs/WORKING_AGREEMENTS.md`, and then this page. Specs, plans and build records are referenced
by path, not repeated.

## Where things stand

- **Code:** the Slack Sidecar (plan Tasks 0–23), the `/admin` UI (plan Tasks 1–9), the single Slack workspace move
  and the sandbox folders fix are all on `main`, which equals `feat/slack-sidecar`. CI is green on Linux, macOS and
  Windows. The last full run gave 2919 passed, 22 skipped, Postgres included.
- **Production is still on the old build.** Railway service `sidecar`
  (`https://sidecar-production-e110.up.railway.app`) is running deployment `cb472970` from 2026-10-21 06:27 UTC,
  which has none of this work. Railway paused builds on every plan (status page incident "Dashboard and API
  unavailable, builds paused"). `railway up` answered "Deploys have been paused temporarily" on every try. A retry
  loop ran about 6 hours, then was stopped for this handoff. **Nothing has been deployed.**
- **Staged in Railway** with `--skip-deploys`, so the next deploy picks them up and turns the bot on:
  `SIDECAR_SLACK_ENABLED=true`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SIDECAR_ADMIN_CLIENT_ID`,
  `SIDECAR_ADMIN_CLIENT_SECRET`. The maintainer entered the secrets; all five were checked present by name.
- **Slack app:** "Sidecar", App ID `A0CB1AQM8EA`, in the Northwestern MPD workspace. It uses Socket Mode, so there is
  no request URL. The bot token is `xoxb-`; the app-level token is `xapp-` with `connections:write`.
- **OpenRouter:** the guardrail `Sidecar team budget` exists in the Slack workspace
  (`config.SLACK_WORKSPACE`). Team keys are created only under it.
- **Auth0:** the client "MPD Sidecar Admin" was created through the `auth0` CLI. It is a regular web app using
  `authorization_code` only; its only connection is `email`, and its grants carry `sidecar:use` and `sidecar:admin`.
  Callbacks are prod and `localhost:8000`. The ids are in `docs/HOSTED_OPERATIONS.md` §10. The change is logged in
  `../mpd-auth/docs/ROLLOUT_LOG.md`, which is pushed.
- **Live spikes run so far** (`tests/live/test_slack_spikes.py`):
  - Only `/workspace/home` is saved, and its files are listed relative to home.
  - Fusion forcing works.
  - `web_fetch` allowed domains admit subdomains.
  - Recorded in the Slack spec and `src/openrouter_image_mcp/hosted/slack/CLAUDE.md`.
- **Open security item:** an earlier spike traceback printed a live OpenRouter key in the maintainer's terminal. The
  maintainer was asked to delete or rotate that key. Confirm it's done.

## Next steps, in order

1. **Deploy once Railway builds reopen.** Check status.railway.com. Then, from a clean checkout of `main`'s tip:
   - Check the service is quiet. `railway logs --since 35m --service sidecar` should show only `GET /health` and
     `GET /ready`; otherwise wait, since a restart interrupts in-process advisor jobs. `railway run ... active-jobs`
     can't reach the private Postgres from a laptop, and `railway ssh` is blocked from the maintainer's network.
   - Run `railway up --service sidecar --ci`. Output containing "deploys have been paused" or "not signed in" means
     nothing deployed, even on exit 0.
   - If the CLI is signed out, the maintainer runs `railway login`.
2. **Verify:**
   - `curl` `/health` and `/ready`.
   - The logs show the Slack Socket Mode connection, with no errors at boot.
   - `/admin` signs in with an admin account.
3. **Live acceptance, with the maintainer:**
   - Slack: `docs/superpowers/plans/2026-10-21-slack-sidecar.md`, "Task 24: Live acceptance", and the spec's
     "Live acceptance" items 1–6 in `docs/superpowers/specs/2026-10-21-slack-sidecar-design.md`.
   - Admin: `docs/superpowers/plans/2026-10-21-slack-admin.md`, "Task 10: Live check".
   - Register the first channel through `/admin` (or `scripts/ops/slack_channels.py add`).
   - Once provisioned team keys are live, tell the mpd-auth owner so its rollout log is updated.
4. **Sandbox persistence spike** (the maintainer runs it; it needs a live key). It checks that
   `/workspace/home/work/` survives a sleep, that `/tmp` and `/workspace/project` don't, and that a fresh container
   is empty. It waits about 8 minutes.

   ```bash
   printf "OpenRouter live key: "; read -rs OPENROUTER_LIVE_KEY; echo; export OPENROUTER_LIVE_KEY
   uv run pytest -m live tests/live/test_sandbox_spikes.py -s
   ```

   Compare the `[spike]` lines with `EXPECTED` in the test. If `work/` does not survive, change `SANDBOX_SAFETY` in
   `src/openrouter_image_mcp/advisor_payload.py` so it stops promising persistence.
5. **Migration framework**, the next hardening step. It must land before any further schema change:
   - The brainstorm stopped at the choice of approach. Astra recommends approach A, our own small runner; the reasons,
     traps and test list are in `docs/superpowers/reviews/2026-10-21-astra-open-questions.md` §2.
   - Decided already: its own branch; a `down` per migration, with destructive downs behind `--allow-destructive`; a
     separate `migrate up` step as Railway's pre-deploy command; the server refuses to start on a stale schema; the
     baseline adopts the deployed database without changing it.
   - Next: spec in `docs/superpowers/specs/`, then plan, then build.
   - When it lands, update `CLAUDE.md` ("Schema changes are additive in code"), `.claude/rules/python.md` and
     `testing.md`, and the folder notes.
6. **Admin sign-in limiter:**
   - Probe whether Railway's edge overwrites `X-Real-IP` and `X-Forwarded-For`.
   - If the edge overwrites `X-Real-IP`, key `hosted/admin/routes_auth.py`'s limiter on it, with no fallback to
     `X-Forwarded-For`.
   - The probe design is in the Astra review §3.

## Backlog

- `scripts/ops/slack_channels.py` still runs its own registry and Slack checks. Move it onto
  `hosted/slack/channel_admin.py` (`ChannelAdmin`), which `/admin` already uses, so the two paths can't drift.
- Remove the merged subagent worktrees under `.claude/worktrees/agent-*` (local only).
- Stop the throwaway local Postgres used for `pytest -m pg` (port 54999, database `sidecar_test`), if it is still
  running.

## Where the history lives

| What | Where |
| --- | --- |
| Slack Sidecar design and plan | `docs/superpowers/specs/2026-10-21-slack-sidecar-design.md`, `docs/superpowers/plans/2026-10-21-slack-sidecar.md` |
| Admin UI design and plan | `docs/superpowers/specs/2026-10-21-slack-admin-design.md`, `docs/superpowers/plans/2026-10-21-slack-admin.md` |
| Hosted design and decisions | `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md`, `docs/superpowers/specs/2026-10-18-hosted-decision-record.md` |
| Subagent briefs, reports, ledgers, rulings | `docs/superpowers/sdd/` (index in its `README.md`) |
| Astra's answers on sandbox, migrations, limiter | `docs/superpowers/reviews/2026-10-21-astra-open-questions.md` |
| Operations runbook (deploy, rotation, admin client) | `docs/HOSTED_OPERATIONS.md` |
| Maintainer's working agreements | `docs/WORKING_AGREEMENTS.md` |
