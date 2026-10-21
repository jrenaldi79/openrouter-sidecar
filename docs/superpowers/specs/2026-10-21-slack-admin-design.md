---
title: "Slack admin page design"
description: "A /admin web page on the Sidecar server where MPD admins register and edit Slack channels, signed in through the shared Auth0 tenant with sidecar:admin, styled with the Interview Coach admin design system"
type: "proposal"
status: "pending"
read_when: "Building or changing the Sidecar admin page, its Auth0 login, admin sessions, or the channel admin service"
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
students' own model choices in claude.ai, and any Auth0 change made from this repo.

`slack_channels.py` is not changed on this branch. It stays the working fallback through the live acceptance and
shares only the low-level pieces with the page (`ChannelRegistry` and its validation, `channel_problem`, the team
key provisioner and `PreferenceService.update`). Moving it onto the page's service is a follow-up after the live
check. Until then, changes made with the script don't appear in the change log.

Users: holders of the `mpd-admin` role (two people today). There is no instructor or TA role.

## Sign-in

### Auth0 (owned by `mpd-auth`, applied by the user)

- A new regular web client, **MPD Sidecar Admin** (confidential, authorization code), with callback
  `https://<sidecar host>/admin/callback`, logout return URL `https://<sidecar host>/admin`, and a matching
  `http://localhost:8000` callback and logout URL for local runs.
- The client signs ID tokens with RS256. A client created by API or CLI can be left without
  `jwt_configuration.alg` and sign HS256, which this page refuses; set it explicitly
  (`auth0 api patch "clients/<id>" --data '{"jwt_configuration":{"alg":"RS256"}}'`), as the Coach admin did.
- Only the passwordless email connection is enabled for the client. With `Username-Password-Authentication` also
  enabled, identifier-first login asks for a password.
- A client grant from that client to the existing Sidecar APIs (production and localhost). The APIs keep
  `require_client_grant`. The "MPD - Profile claim" Action already sets the profile claim for any client.
- Nothing else changes: not the shared "MPD MCP Clients" client, not the Interview Coach's "MPD MCP Admin" client,
  not the Actions. These changes go through the `mpd-auth` repo and its rollout log, after checking with the
  Interview Coach and mpd-auth sessions; the user applies each tenant step.

### Login flow

1. `GET /admin/login`:
   - Deletes expired pending logins.
   - Refuses with 429 "Too many sign-ins in progress. Try again in a few minutes." in either case:
     - 500 unexpired pending logins already exist;
     - the caller's address has started 20 in the last 10 minutes (`Limits.window`, keyed by the client address
       uvicorn reports behind Railway's proxy).
   - Otherwise creates a pending login: random `state`, `nonce` and PKCE verifier in `admin_logins`, keyed by the
     hash of `state`, single use, expiring after 10 minutes.
   - Sets the cookie `sidecar_admin_login`, holding `state`, with `HttpOnly`, `SameSite=Lax`, `Path=/admin`,
     `Max-Age=600`, and `Secure` as for the session cookie.
   - Redirects to Auth0 `/authorize` with `response_type=code`, `scope=openid`,
     `audience=<MCP_RESOURCE_URI>`, S256 PKCE, `state` and `nonce`.
2. `GET /admin/callback`:
   - Requires the `sidecar_admin_login` cookie to equal the `state` query value (constant-time). This binds the
     login to the browser that started it, so another admin's login can't be finished in this browser.
   - Consumes the pending login. An unknown, expired or reused `state` is refused.
   - Clears the cookie.
   - Exchanges the code at `/oauth/token` with the client secret.
   - Checks the ID token: RS256, issuer, `aud` = client id, `nonce`.
   - Checks the access token with its own `Auth0Verifier(profile_claim=None)`, which applies the MCP endpoint's
     checks (RS256, exact issuer and audience) but doesn't require a `workspace_id`.
3. Access requires `sidecar:use` and `sidecar:admin` in the access token's `permissions` and the profile claim
   `https://mpd.northwestern.edu/profile` with an email (the Action sets it only for a verified email). Anyone else
   sees a standalone "This page is for Sidecar admins." page with a sign-out link. Tokens are not stored.
4. `POST /admin/logout` (CSRF-checked) deletes the session and redirects to Auth0 `/v2/logout` with the client id
   and the return URL.

### Sessions

- `admin_sessions`:
  - Columns: `id_hash` (SHA-256 of a random 32-byte id), `subject`, `email`, `csrf_token`, `created_at`,
    `last_seen_at`.
  - The cookie `sidecar_admin` holds the raw id, with `HttpOnly`, `SameSite=Lax` and `Path=/admin`.
  - It is `Secure` except when the public origin is plain-http localhost.
- A session ends after 2 hours idle or 12 hours after sign-in, whichever comes first; `last_seen_at` moves on each
  request. Removing someone's admin role takes effect at their next sign-in, within 12 hours.
- Expiry is checked on every read. Expired sessions are deleted whenever a session is created, and expired pending
  logins whenever a login starts. This needs no sweeper, because the hourly sweeper exists only when `ASSET_DIR`
  is set.
- Every POST carries `_csrf` (forms) or `x-csrf-token` (htmx) equal to the session's `csrf_token`, compared in
  constant time; a mismatch is 403 with no change.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIDECAR_ADMIN_ENABLED` | on | `false` removes every `/admin` route (404). |
| `SIDECAR_ADMIN_CLIENT_ID` | none | The MPD Sidecar Admin client id. |
| `SIDECAR_ADMIN_CLIENT_SECRET` | none | Its secret, at least 32 characters. Kept out of `repr`, never logged or rendered; a failed code exchange logs the HTTP status only. |

The callback is `<public origin>/admin/callback`, from `MCP_RESOURCE_URI`'s origin.
- When the admin page is on but the client id or secret is missing, the server starts normally and logs one
  warning naming the missing variables. Every `/admin` page then shows "Admin sign-in isn't configured on this
  server yet."
- A secret under 32 characters, or a non-HTTPS public origin outside localhost, stops startup like other unsafe
  settings.

## Pages

All pages share the Coach admin shell. A left rail reads "MPD Admin · Sidecar" and holds Channels and Change log,
with the admin's email and Sign out at its foot. Status is plain text, never coloured marks or pills.

### Channels (`GET /admin`)

- Registered channels in a table: label, `#channel name` (from Slack; the id when the lookup fails), cohort,
  "On" or "Off: <reason>", default alias, daily cap, concurrent limit, and an Edit link.
- "Channels Sidecar is in but not registered": channels the bot is a member of with no registry row, each with a
  Register link. Empty: "Invite @Sidecar to a channel, then register it here."
- When Slack is off on this server (`SIDECAR_SLACK_ENABLED` unset), the page says "Slack isn't turned on for this
  server." and lists the registered rows only, read-only.

### Register (`GET`/`POST /admin/channels/new?channel=<id>`)

One form; nothing is created until Save.

- **Before the form:**
  - The team id comes from the bot (`auth.test`).
  - The guest and external-sharing check (`channel_problem`) runs first. A problem shows "Sidecar can't be used
    in #name: it <reason>." and no form.
- **Model choices** come from OpenRouter's public model list (`GET /api/v1/models` needs no key), filtered by the
  same rules as students' model lists: tool-capable text models of a known family from the last six months. No
  team key is needed to show the form.
- **Fields:**
  - label, and cohort (select from `COHORT_WORKSPACES`);
  - aliases: rows of alias → brand or exact model id, starting from `DEFAULT_ALIASES`;
  - the model for each family, and the default alias (select from the aliases);
  - Fusion panel (1 to 8 models), Fusion analyst and helper model;
  - fetch allowlist, one domain per line;
  - daily cap and concurrent limit, prefilled from `SIDECAR_SLACK_DAILY_CAP_USD` and
    `SIDECAR_SLACK_MAX_CONCURRENT`;
  - "Only these people can use Sidecar": checkboxes of the channel's members by name, up to 50 members. Above 50,
    or when the names don't load, it's a Slack user id list, one per line. Names load through the bot's name cache,
    at most five Slack lookups at a time.
- **Save**, in order:
  1. Validate the fields, including each model against the public list, and run the guest check again.
  2. In one transaction, write the row turned off with reason "Setting up", plus its `registered` audit entry.
  3. Create the team's OpenRouter key under the `Sidecar team budget` guardrail.
  4. Save the team's per-family models through the team's own key.
  5. Turn the row on, with a `turned on` audit entry in the same transaction.

  A key is therefore only ever made for a saved row, and the label and cohort can't change after step 2.
- **When it fails:**
  - A failure before step 2 re-shows the form with the entered values and one plain sentence.
  - A failure in steps 3 to 5 leaves the row off with reason "Setup didn't finish". The edit page then says
    "Setup didn't finish: <plain reason>." and shows a Finish setup button that retries steps 3 to 5, which are
    safe to repeat.

### Edit (`GET`/`POST /admin/channels/<team>/<channel>`)

- The same form, prefilled. Label and cohort are shown read-only: the label names the team key and the cohort
  decides the workspace that holds its chats and key.
- **Save** compares the submitted values with the values the form was rendered from, which it carries in hidden
  fields. It sends only the changed registry fields to `ChannelRegistry.update`, in one transaction with their
  `changed` audit entry. So two admins editing different fields don't overwrite each other.
- **Changed per-family models** are saved next, through the team's key. Their audit entry is written once they
  succeed.
- **If the model save fails**, the page says "Saved the channel settings. The team's models weren't changed:
  <plain reason>." and the change log shows only what was saved. With no change at all, the page says "Nothing
  changed."
- Turn off (`POST .../off`, reason required) and Turn on (`POST .../on`, runs the guest check first) are buttons
  with a confirm, each written with its audit entry in one transaction.

### Change log (`GET /admin/log`)

The newest 200 entries. Each shows:
- the time (America/Chicago) and the admin's email;
- the `#channel` and label;
- the action: registered, changed, models changed, turned off or turned on;
- each changed field as before → after.

## Channel admin service

`hosted/slack/channel_admin.py` is the page's service, so routes stay thin. Its operations are:
- `overview()` and `check(channel_id)`;
- `catalog()`, the filtered public list;
- `members(channel_id)`;
- `register(fields, actor)` and `finish_setup(team, channel, actor)`;
- `change(team, channel, changed_fields, actor)`;
- `turn_off(..., reason, actor)` and `turn_on(..., actor)`.

**How it works:**
- Each operation returns the changed fields.
- Validation stays in `registry._validate`. Model saves use the cohort's `PreferenceService.update` under
  `acting_as` the team principal, the same call `slack_channels.py` makes.
- The student catalog filter is extracted from `PreferenceService._catalog` into a function both paths use, so
  the public list and the students' list apply the same rules.

**Atomicity.** Registry writes and their audit entries share one database transaction:
- `ChannelRegistry.add`, `update` and `deactivate` take an optional `audit` callback that inserts the entry on the
  same connection before commit.
- An audit insert failure rolls the registry change back.
- Key creation and model saves can't share that transaction. They run after it, are safe to repeat, and are
  audited only once they succeed, as described under Register and Edit.

**Slack additions.**
- `SlackApi` gains `team_id() -> str` (`auth.test`'s `team_id`) and `bot_channels() -> list[dict]`
  (`users.conversations` for the bot, public and private channels, paged). `channels:read` and `groups:read` are
  already in the manifest. `members(channel)` already exists.
- These Slack calls work from any replica. The page doesn't need the owner lock or the running gateway, only the
  bot token.

## Data

Additive tables, created with `IF NOT EXISTS` on SQLite and Postgres:

- `admin_logins(state_hash PRIMARY KEY, nonce, verifier, created_at)`. At most 500 unexpired rows.
- `admin_sessions(id_hash PRIMARY KEY, subject, email, csrf_token, created_at, last_seen_at)`.
- `admin_audit(at, actor_email, actor_subject, action, team_id, channel_id, label, changes)`.
  - `changes` is JSON `{field: [before, after]}`.
  - Rows hold registry values only, never keys or tokens.
  - Rows are ordered by `at` and `rowid`.

## Design system

The Interview Coach admin (`mpd-class-mcp/src/admin/public/`) is the source.

**Copied unchanged into `hosted/admin/public/`:**
- `tokens.css`, `fonts.css`, `shell.css`;
- the generic rules of `admin.css`;
- `theme-instrument.css`, `theme-instrument-depth.css`;
- `admin.js`;
- the Archivo SemiCondensed and JetBrains Mono `woff2` files, with their OFL licence files;
- the MPD logo and the favicon.

**Not copied:** the Overview console, the pipeline, job status marks, the diff view and avatars.

The folder note records the source commit. Changes to the system are made in `mpd-class-mcp` first, then copied
again.

**Pages:**
- They use Pico 2.1.1 and htmx 2.0.11 from jsDelivr, pinned with SRI hashes, as the Coach does.
- Templates are Jinja2 with autoescaping, mirroring `layout.ejs`: skip link, rail, `main#main.main.page` and
  `#htmx-error`.
- Forms are plain POSTs with a redirect after success, and work without JavaScript. htmx is used only for the error
  banner and the confirm on turn off and on.

**Headers.** Every `/admin` response sends the Coach's headers.
- `Content-Security-Policy`:
  - `default-src 'none'`;
  - `script-src` the pinned htmx path and `/admin/public/`;
  - `style-src 'self'` and the pinned Pico path;
  - `font-src 'self'`, `img-src 'self' data:`, `connect-src 'self'`;
  - `form-action 'self'` and the Auth0 issuer;
  - `frame-ancestors 'none'`, `base-uri 'none'`.
- `Cache-Control: no-store`, `Referrer-Policy: same-origin` and `X-Content-Type-Options: nosniff`.
- No inline scripts, styles or event handlers.

**Static files.** `/admin/public/` serves only `*.css`, `*.js`, `fonts/*.woff2` and the brand images, without a
session, cached for one hour.

**Route placement.** `custom_route` routes sit outside the MCP endpoint's transport security, which wraps `/mcp`
only. So no Host or Origin check applies to `/admin`. Cookie flags and the callback come from settings, never from
the request URL.

## Code layout

**`hosted/admin/`** is a new folder with its own folder note. It holds:
- `settings.py`, `oidc.py`, `sessions.py`, `audit.py` and `views.py`;
- `routes.py`, whose routes `hosted/app.py` registers with `server.custom_route` when the page is on, like the
  upload routes;
- `templates/` and `public/`.

One guard function checks the session and CSRF for every page and POST. Every file stays within 300 lines and
every function within 50. `jinja2` is added with `uv add`.

## Errors

The page shows one plain sentence and keeps the admin's input. Details go to the log through `redact`, never to the
page.

| Condition | What the page says |
| --- | --- |
| Slack unreachable or erroring | "Slack didn't answer. Try again." |
| Model list unreachable | "The model list didn't load. Try again." The form can't be saved. |
| No `Sidecar team budget` guardrail in the cohort workspace | "The 2027 workspace has no 'Sidecar team budget' guardrail, so no team key can be made." This happens at setup step 3, so the row stays off with Finish setup. |
| A model the list doesn't offer, an invalid row (`registry._validate`), or a duplicate label or channel | The validation message. A double-submitted register form gets the duplicate message and no second row. |
| Auth0 refusing the code exchange | The sign-in failed page with a "Try again" link. |
| Too many pending sign-ins | 429 with the text above. |

## Testing

pytest only (server-rendered pages; no Playwright):

- **`test_hosted_admin_oidc.py`**, with a fake Auth0 (respx and `tests/jwt_helpers.py`):
  - a good login;
  - missing `sidecar:admin` or `sidecar:use`, and no profile email;
  - bad or reused `state`, and a missing or mismatched login cookie;
  - wrong `nonce`, an HS256 ID token, and a failed exchange.
- **`test_hosted_admin_sessions.py`** (`db_target`):
  - idle and absolute expiry, and logout;
  - purge on create;
  - the 500 pending-login cap;
  - cookie flags.
- **`test_hosted_admin_routes.py`:**
  - CSRF refusal, security headers and the static allowlist;
  - 404 when disabled, the not-configured page and the Slack-off page;
  - the per-address sign-in limit.
- **`test_hosted_slack_channel_admin.py`:**
  - register, and change with only the changed fields written;
  - turn off and on, guest refusal and a duplicate submit;
  - audit rows;
  - failure injection at each boundary:
    - an audit insert failure rolls the registry change back;
    - a key failure or a model-save failure after the row is written leaves it off with "Setup didn't finish",
      and Finish setup completes it;
    - a model-save failure on edit keeps the registry change and its audit entry only.
- **`test_hosted_admin_audit.py`:** entries and the change-log view.
- **`test_hosted_slack_registry.py`:** the `audit` callback commits with the row and rolls back with it.

## Rollout

1. Build on `feat/slack-sidecar`, so the Slack Sidecar live acceptance registers channels A and B through the page.
   `slack_channels.py` stays as the fallback.
2. In `mpd-auth`, add the MPD Sidecar Admin client (RS256, passwordless email only) and its grants. Check with the
   Interview Coach and mpd-auth sessions first; the user applies each tenant step.
3. Set `SIDECAR_ADMIN_CLIENT_ID` and `SIDECAR_ADMIN_CLIENT_SECRET` on Railway.
4. Sign in as an `mpd-admin`, register the test channels, and record the result with the live acceptance.
5. After the live check, move `slack_channels.py` onto the channel admin service, with tests, so script changes
   join the change log. This is its own change.
