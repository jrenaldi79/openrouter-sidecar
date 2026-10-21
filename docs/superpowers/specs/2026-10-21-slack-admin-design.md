---
title: "Slack admin page design"
description: "A /admin web page on the Sidecar server where MPD admins register and edit Slack channels, signed in through the shared Auth0 tenant with sidecar:admin, styled with the Interview Coach admin design system"
type: "proposal"
status: "pending"
read_when: "Building or changing the Sidecar admin page, its Auth0 login, admin sessions, or the shared Slack channel admin service"
---

# Slack admin page

## Goal

Instructors manage the Slack Sidecar from a web page instead of `scripts/ops/slack_channels.py`. An admin signs in
with the class Auth0 login, picks a channel `@Sidecar` has been invited to, fills in the team's label, cohort and
models, and saves. Later they edit a channel's models and limits, turn it off or on, and see who changed what.

Success: an `mpd-admin` registers and edits channels A and B during the Slack Sidecar live acceptance without a
terminal, and nobody without `sidecar:admin` can see or change anything.

## Scope

In: Slack channel registration and editing, which models each team uses (aliases, the model per family, default
alias, Fusion panel, analyst and helper model), caps, fetch allowlist, invoker list, turning channels off and on,
and a change log.

Out: usage reports (`slack_usage.py` and `@Sidecar usage` stay), student keys (`openrouter_keys.py` stays),
students' own model choices in claude.ai, and any Auth0 change made from this repo. `slack_channels.py` stays as a
fallback and calls the same service as the page.

Users: holders of the `mpd-admin` role (two people today). There is no instructor or TA role.

## Sign-in

### Auth0 (owned by `mpd-auth`, applied by the user)

- A new regular web client, **MPD Sidecar Admin** (confidential, authorization code), with callback
  `https://<sidecar host>/admin/callback`, logout return URL `https://<sidecar host>/admin`, and the same
  passwordless email connection the MCP clients use. A matching localhost client entry (or callback) for local
  runs.
- A client grant from that client to the existing Sidecar APIs (production and localhost). The APIs keep
  `require_client_grant`.
- Nothing else changes: not the shared "MPD MCP Clients" client, not the Interview Coach's "MPD MCP Admin" client,
  not the Actions. These changes go through the `mpd-auth` repo and its rollout log, after checking with the
  Interview Coach and mpd-auth sessions; the user applies each tenant step.

### Login flow

1. `GET /admin/login` creates a pending login (random `state`, `nonce` and PKCE verifier) in `admin_logins`, keyed
   by the hash of `state`, single use, expiring after 10 minutes, and redirects to Auth0 `/authorize` with
   `response_type=code`, `scope=openid`, `audience=<MCP_RESOURCE_URI>`, S256 PKCE, `state` and `nonce`.
2. `GET /admin/callback` consumes the pending login (unknown, expired or reused `state` is refused), exchanges the
   code at `/oauth/token` with the client secret, checks the ID token (RS256, issuer, `aud` = client id, `nonce`)
   and checks the access token with the same `TokenVerifier` the MCP endpoint uses (RS256, exact issuer and
   audience).
3. Access requires `sidecar:use` and `sidecar:admin` in the access token's `permissions` and the profile claim
   `https://mpd.northwestern.edu/profile` with an email (the Action sets it only for a verified email). Anyone else
   sees a standalone "This page is for Sidecar admins." page with a sign-out link. Tokens are not stored.
4. `POST /admin/logout` (CSRF-checked) deletes the session and redirects to Auth0 `/v2/logout` with the client id
   and the return URL.

### Sessions

- `admin_sessions`: `id_hash` (SHA-256 of a random 32-byte id), `subject`, `email`, `csrf_token`, `created_at`,
  `last_seen_at`. The cookie `sidecar_admin` holds the raw id: `HttpOnly`, `Secure` (except plain-http localhost),
  `SameSite=Lax`, `Path=/admin`.
- A session ends after 2 hours idle or 12 hours after sign-in, whichever comes first; `last_seen_at` moves on each
  request. Removing someone's admin role takes effect at their next sign-in, within 12 hours.
- The hourly sweeper deletes expired sessions and pending logins.
- Every POST carries `_csrf` (forms) or `x-csrf-token` (htmx) equal to the session's `csrf_token`, compared in
  constant time; a mismatch is 403 with no change.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIDECAR_ADMIN_ENABLED` | on | `false` removes every `/admin` route (404). |
| `SIDECAR_ADMIN_CLIENT_ID` | none | The MPD Sidecar Admin client id. |
| `SIDECAR_ADMIN_CLIENT_SECRET` | none | Its secret, at least 32 characters. Never logged; masked by `logs.redact`. |

The callback is `<public origin>/admin/callback`, from `MCP_RESOURCE_URI`'s origin. When the admin page is on but
the client id or secret is missing, the server starts normally, logs one warning naming the missing variables, and
every `/admin` page shows "Admin sign-in isn't configured on this server yet." A secret under 32 characters, or a
non-HTTPS public origin outside localhost, stops startup like other unsafe settings.

## Pages

All pages share the Coach admin shell: a left rail "MPD Admin · Sidecar" with Channels and Change log, the
admin's email and Sign out at its foot. Status is plain text, never coloured marks or pills.

### Channels (`GET /admin`)

- Registered channels in a table: label, `#channel name` (from Slack; the id when the lookup fails), cohort,
  "On" or "Off: <reason>", default alias, daily cap, concurrent limit, and an Edit link.
- "Channels Sidecar is in but not registered": channels the bot is a member of with no registry row, each with a
  Register link. Empty: "Invite @Sidecar to a channel, then register it here."
- When Slack is off on this server (`SIDECAR_SLACK_ENABLED` unset), the page says "Slack isn't turned on for this
  server." and lists the registered rows only, read-only.

### Register (`GET`/`POST /admin/channels/new?channel=<id>`)

- The team id comes from the bot (`auth.test`). The guest and external-sharing check (`channel_problem`) runs
  first; a problem shows "Sidecar can't be used in #name: it <reason>." and no form.
- Form: label, cohort (select from `COHORT_WORKSPACES`), aliases (rows of alias → brand or exact model id,
  starting from `DEFAULT_ALIASES`), the model for each family (selects from that cohort's catalog, so the class's
  existing catalog filters apply), default alias (select from the aliases), Fusion panel (1 to 8 models), Fusion
  analyst, helper model, fetch allowlist (one domain per line), daily cap and concurrent limit (prefilled from
  `SIDECAR_SLACK_DAILY_CAP_USD` and `SIDECAR_SLACK_MAX_CONCURRENT`), and "Only these people can use Sidecar"
  (checkboxes of the channel's members by name, up to 200 members; above that, a Slack user id list).
- Save: validate, run the guest check again, save the team's per-family models (which creates the team's
  OpenRouter key under the `Sidecar team budget` guardrail), then write the row and one audit entry, then redirect
  to the channel's edit page. A failure re-shows the form with the entered values and one plain sentence.

### Edit (`GET`/`POST /admin/channels/<team>/<channel>`)

- The same form, prefilled. Label and cohort are shown read-only: the label names the team key and the cohort
  decides the workspace that holds its chats and key.
- Save compares the submitted values with the values the form was rendered from (carried in hidden fields) and
  sends only changed fields to `ChannelRegistry.update`, so two admins editing different fields don't overwrite
  each other. Model changes go through the same catalog check. No change: "Nothing changed."
- Turn off (`POST .../off`, reason required) and Turn on (`POST .../on`, runs the guest check first) are buttons
  with a confirm.

### Change log (`GET /admin/log`)

The newest 200 entries: time (America/Chicago), admin email, `#channel` and label, action (registered, changed,
turned off, turned on) and each changed field as before → after.

## Shared channel service

`hosted/slack/channel_admin.py` holds what `scripts/ops/slack_common.py` and `slack_channels.py` do today, so the
script and the page can't drift: `unregistered(api, registry)`, `check(api, channel_id)`,
`register(fields, actor)`, `change(team, channel, changed_fields, actor)`, `turn_off(..., reason, actor)`,
`turn_on(..., actor)`, each returning the changed fields for the audit entry. Validation stays in
`registry._validate`; model checks and seeding use the cohort's `PreferenceService` under `acting_as` the team
principal, as `slack_channels.py` does now. `slack_channels.py` becomes a thin argument parser over it.

`SlackApi` gains `bot_channels() -> list[dict]` (`users.conversations` for the bot, public and private channels,
paged; `channels:read` and `groups:read` are already in the manifest). `members(channel)` already exists.

## Data

Additive tables, created with `IF NOT EXISTS` on SQLite and Postgres:

- `admin_logins(state_hash PRIMARY KEY, nonce, verifier, created_at)`
- `admin_sessions(id_hash PRIMARY KEY, subject, email, csrf_token, created_at, last_seen_at)`
- `admin_audit(id, at, actor_email, actor_subject, action, team_id, channel_id, label, changes)` with `changes`
  as JSON `{field: [before, after]}`; rows hold registry values only, never keys or tokens.

## Design system

The Interview Coach admin (`mpd-class-mcp/src/admin/public/`) is the source. Copied unchanged into
`hosted/admin/public/`: `tokens.css`, `fonts.css`, `shell.css`, the generic rules of `admin.css`,
`theme-instrument.css`, `theme-instrument-depth.css`, `admin.js`, the Archivo SemiCondensed and JetBrains Mono
`woff2` files with their OFL licence files, the MPD logo and the favicon. Not copied: the Overview console, the
pipeline, job status marks, the diff view and avatars. The folder note records the source commit; changes to the
system are made in `mpd-class-mcp` first and copied again.

Pages use Pico 2.1.1 and htmx 2.0.11 from jsDelivr, pinned with SRI hashes, as the Coach does. Templates are
Jinja2 with autoescaping, mirroring `layout.ejs` (skip link, rail, `main#main.main.page`, `#htmx-error`). Forms
are plain POSTs with redirect after success and work without JavaScript; htmx is used for the error banner and
the confirm on turn off and on.

Every `/admin` response sends the Coach's headers: `Content-Security-Policy` with `default-src 'none'`,
`script-src` the pinned htmx path and `/admin/public/`, `style-src 'self'` and the pinned Pico path,
`font-src 'self'`, `img-src 'self' data:`, `connect-src 'self'`, `form-action 'self'` and the Auth0 issuer,
`frame-ancestors 'none'`, `base-uri 'none'`; plus `Cache-Control: no-store`, `Referrer-Policy: same-origin` and
`X-Content-Type-Options: nosniff`. No inline scripts, styles or event handlers. `/admin/public/` serves only
`*.css`, `*.js`, `fonts/*.woff2` and the brand images, without a session, cached for one hour.

## Code layout

`hosted/admin/` (new folder, with its folder note): `settings.py`, `oidc.py`, `sessions.py`, `audit.py`,
`routes.py` (registered with `server.custom_route`, like the upload routes, by `hosted/app.py` when the page is
on), `views.py`, `templates/` and `public/`. One guard function checks the session and CSRF for every page and
POST. Every file stays within 300 lines and every function within 50. `jinja2` is added with `uv add`.

## Errors

The page shows one plain sentence and keeps the admin's input; details go to the log through `redact`, never to
the page:

- Slack unreachable or erroring: "Slack didn't answer. Try again."
- Catalog unreachable: "The model list didn't load. Try again." (model fields can't be saved).
- No `Sidecar team budget` guardrail in the cohort workspace: "The 2027 workspace has no 'Sidecar team budget'
  guardrail, so no team key can be made." Nothing is saved.
- A model the catalog doesn't offer, an invalid row (`registry._validate`), or a duplicate label: the validation
  message.
- Auth0 refusing the code exchange: the sign-in failed page with a "Try again" link.

## Testing

pytest only (server-rendered pages; no Playwright):

- `test_hosted_admin_oidc.py`: a fake Auth0 (respx and `tests/jwt_helpers.py`) for a good login, missing
  `sidecar:admin`, missing `sidecar:use`, no profile email, bad or reused `state`, wrong `nonce`, failed exchange.
- `test_hosted_admin_sessions.py`: idle and absolute expiry, logout, sweeper purge, cookie flags (`db_target`).
- `test_hosted_admin_routes.py`: CSRF refusal, security headers, static allowlist, 404 when disabled, the
  not-configured page, the Slack-off page.
- `test_hosted_slack_channel_admin.py`: register, change with only changed fields written, turn off and on, guest
  refusal, missing guardrail, audit rows; `slack_channels.py` still works through it.
- `test_hosted_admin_audit.py`: entries and the change-log view.

## Rollout

1. Build on `feat/slack-sidecar`, so the Slack Sidecar live acceptance registers channels A and B through the page.
2. In `mpd-auth`: add the MPD Sidecar Admin client and its grants, after checking with the Interview Coach and
   mpd-auth sessions; the user applies each tenant step.
3. Set `SIDECAR_ADMIN_CLIENT_ID` and `SIDECAR_ADMIN_CLIENT_SECRET` on Railway.
4. Sign in as an `mpd-admin`, register the test channels, and record the result with the live acceptance.
