---
title: "Railway leak rotation (2026-10-22)"
description: "Record of the secret rotation after Railway's 2026-10-22 breach: what was rotated in each MPD project, how it was checked, what went wrong and what was still open"
type: "history"
status: "completed"
read_when: "When rotating secrets after another leak, or checking what was changed on 2026-10-22"
---

# Railway leak rotation (2026-10-22)

Railway disclosed a security incident that exposed project environment variables. Every secret in the MPD
projects on the account was treated as known. The procedure now lives in `docs/HOSTED_OPERATIONS.md` §7
("Railway's variables leaked") for this service, and in `docs/secret-rotation.md` in the `mpd-class-mcp` repo for
the Interview Coach. This page records the run.

## What was rotated

**`openrouter-sidecar`** (services `sidecar` and `Postgres`):

- With `scripts/ops/rotate_secrets.py` (clipboard, `--skip-deploys`): `OPENROUTER_MANAGEMENT_KEY`,
  `SLACK_BOT_TOKEN` (revoked, then the app reinstalled), `SLACK_APP_TOKEN`, `SIDECAR_ADMIN_CLIENT_SECRET`, and the
  generated `VAULT_KEYS` (a new version, with no old entry) and `LINK_SIGNING_KEY`.
- The Postgres password, regenerated in the dashboard (Database → Config → Regenerate). The `DATABASE_URL` hash
  changed, as expected.
- Deployed `main` (`62eaf29`) as `fe83ea67` at 22:03 UTC with `railway up`. This was also the first deploy since
  2026-10-21, so the Slack bot and `/admin` went live with it.
- The five stored student keys were deleted at OpenRouter (`openrouter_keys.py delete`), then
  `DELETE FROM student_keys` emptied the table. The rows were sealed with the removed vault key and could no longer
  be opened. The SQL ran through a temporary public TCP proxy, which was deleted within minutes.

**`mpd-class-mcp`** (Interview Coach): both Auth0 client secrets (MPD MCP Admin, MPD Class MCP roster reader),
`SESSION_SECRET`, the OpenRouter, Tavily, Firecrawl and Resend keys, the MPD Interview Coach Slack bot token and the
Postgres password; deployed `90162b3b` with `railway up`. Details are in that repo's `docs/secret-rotation.md`.

**`mpd-claude-cloud-mcp-bridge`** (in development, unused), rotated later the same evening:

- The `postgres` password, regenerated in the dashboard (the `DATABASE_URL` hash changed); Railway redeployed Postgres.
- The `mpd_identity_app` login used by `class-identity`. It is the bridge's own role, not Railway's, so it was
  changed with `ALTER ROLE ... PASSWORD` through a temporary TCP proxy (deleted right after). The script sent a
  SCRAM-SHA-256 verifier, so the plaintext never reached the server or its logs. The new password was checked to
  work and the old one to be refused, then written into `class-identity`'s `DATABASE_URL`.
- The migrator and backup roles' passwords were never in Railway and were left alone.
- `class-identity` is **down**. It stopped when Postgres restarted (its startup failed while the database was away),
  and every rebuild since used the repo's root probe `Dockerfile` instead of `Dockerfile.identity`. The service's
  saved builder is `RAILPACK` with `dockerfilePath` `Dockerfile.identity`, and Railway's new builder ignores the path.
  The bridge repo documents this fault and a recovery (`deploymentRedeploy` with `usePreviousImageTag: false`), which
  also built the probe image this time. The deploy is left for the bridge's owner.

## Checks

- Sidecar: `/health` and `/ready` pass; the Slack Socket Mode session connected at boot; `/admin` sign-in works; the
  operator probe passed all 6 checks and issued the operator a new key under the new vault key.
- Coach: `/healthz` reports `"db":"ok"`; the `/admin` dashboard's system cells all read OK; the roster reads Auth0
  live; the student portal signs in.
- Auth0's logs showed no sign of intrusion. Management API changes came only from the operator, roster-reader
  tokens only from Railway's address ranges and one Google Cloud address, and every `mpd-admin` member was expected.

## What went wrong

- **The runbook's Postgres step was wrong.** It said to change the password in Postgres and then edit
  `POSTGRES_PASSWORD` and `PGPASSWORD`. Railway's guidance is to use **Regenerate**, which changes the password and
  the variables together; editing them by hand makes them disagree. §5 and §7 now say so.
- **An Auth0 secret was rotated twice.** The Railway command took its flags from an unquoted zsh variable, which zsh
  passes as one argument, so it failed after the rotation had already killed the old secret. Type every flag out.
- **`railway redeploy` was refused** during Railway's incident ("cannot be redeployed"). `railway up` from a clean
  `main` worked.
- **The rotation script was run from the home folder** and its path wasn't found. Run it from the repo folder.
- **The first SQL attempt failed** because a `python -c '…'` wrapper removed the quotes from string literals.
  `ON_ERROR_STOP` rolled the transaction back. Also, `psql -c` with several statements prints only the last result,
  so check counts in a separate call.
- **Resend answered 403** to a check sent with Python's default User-Agent; with a User-Agent set it answered 401
  `restricted_api_key`, which shows the send-only key is valid.

## Open when this was written

- Done later the same day: the old provider keys and the OpenRouter key printed during an earlier spike were
  deleted, the clipboard history was cleared, and the old values were blanked in the coach's local `.env`,
  `.env.bak` and `.env.railway`.
- The coach's local env files also held a retired Google service-account key (`mpd-interview-coach@...`, key id
  starting `c797d381`), now blanked. Delete that key in Google Cloud if it still exists.
- The `Sidecar team budget` guardrail is missing from the Slack workspace (`openrouter_keys.py guardrails`), so team
  keys can't be created. Create it before the Slack launch.
- Bring `class-identity` back up with an identity image (see the bridge entry above).
- Optionally, log the three Auth0 secret rotations in `../mpd-auth/docs/ROLLOUT_LOG.md`.
