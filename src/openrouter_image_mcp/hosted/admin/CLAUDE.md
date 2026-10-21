---
title: "src/openrouter_image_mcp/hosted/admin"
description: "The /admin web page: MPD admins sign in with Auth0 (sidecar:admin) and register and edit Slack channels; settings, Auth0 login, sessions, audit, views and routes."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/admin/."
---

**Owns:**

- `copy.py`: every sentence the page shows (plain text; templates escape it).
- `settings.py`: `AdminSettings` and `load_admin_settings` (page on unless `SIDECAR_ADMIN_ENABLED` is false, 0 or no;
  Auth0 client id and secret of at least 32 characters; callback and logout URLs; Secure cookies unless the origin is
  local http). Unset client values mean "not configured", not an error. `jwks_uri` is `AUTH0_JWKS_URI` as the MCP verifier reads it
  (`signing_keys_url` falls back to the issuer's own). `HostedSettings.admin` carries it.
- `sessions.py`: pending Auth0 logins (single use, 10 minutes, at most 500) and signed-in sessions (2 hours idle,
  12 hours absolute), both stored by the SHA-256 of the cookie or `state`; the clock is injected.
- `audit.py`: the `admin_audit` change log. `insert(db, ...)` writes on the caller's connection so a registry write
  and its entry share one transaction (it creates its table there); `AdminAudit` records on its own
  transaction and reads the newest entries.
- `views.py`: renders the pages (`Views.page`, `Views.message`), builds the CSP and other response headers
  (`security_headers`) and holds the static-file allowlist (`STATIC_FILES`, `PUBLIC_DIR`). Its `templates/` are Jinja2
  with autoescaping; `public/` is the Interview Coach admin design system, copied from `mpd-class-mcp` (change it
  there first).
- `oidc.py`: `Auth0Login` (authorization code with PKCE, `state` and `nonce`; `start`, `finish`, `logout_url`),
  `AdminIdentity` and `LoginRefused` (reason `state`, `exchange`, `id_token` or `not_admin`). Signing keys come from
  `AdminSettings.signing_keys_url`. `finish` needs the login cookie to equal `state`, checks the RS256 ID token, then the access token through `Auth0Verifier(profile_claim=None)`
  for both permissions and a profile email. A failed exchange logs the HTTP status only.
- `forms.py`: parses the register and edit forms and compares the submitted values with the values the form was
  rendered from (carried in hidden fields), so only changed fields are saved. A blank family model is never a
  change. It parses numbers at the boundary: `daily_cap_usd` in (0, 1000], `max_concurrent` a whole number from 1 to
  20, each checked only when the admin changed it.
- `routes.py`: `admin_routes`, the route table (login, callback, logout, channels, register, edit, finish setup, turn
  off and on, change log, static files, and last a catch-all for `/admin` and `/admin/{rest:path}` that takes every
  HTTP method, so an unknown path or a method like TRACE or PROPFIND still gets a 404 with the headers instead of
  Starlette's bare 405). `routes_base.py` holds the one guard every route goes through: it checks the session, the
  Slack id format (8 to 21 capitals or digits) and the CSRF token (`_csrf` field or `x-csrf-token` header) for every
  POST, reads urlencoded bodies with a byte and field cap, adds `security_headers` to every response and answers
  unexpected errors with a generic page; `REGISTER_DEFAULTS` (4.0 dollars, 3 chats) is the one fallback for the
  register form's limits. `routes_auth.py` has sign-in and sign-out (the login route is limited per client address;
  the not-admin page links to Auth0 sign-out; a sign-out with no session is only honoured when it comes from our own
  page, `Sec-Fetch-Site: same-origin` or our `Origin`, else it redirects to `/admin`), `routes_channels.py` the
  pages (Channels, the register and edit forms, the change log), `routes_actions.py` the POSTs: register, save,
  finish setup, turn off and turn on. Without an `Auth0Login` (client values unset) every page except the static files shows
  `NOT_CONFIGURED`. A `ChannelRefused` shows the form again with the entered values and its message. The edit page opens while
  OpenRouter's model list is down (no choices, `CATALOG_DOWN` shown, current values kept) so Turn off stays reachable;
  with Slack off it is a plain `SLACK_OFF` refusal.
- `wiring.py`: `register_admin`, called from `build_hosted_server` when the page is on. It builds the sessions, audit,
  `Auth0Login` and `ChannelAdmin` (over the Slack app, or over a fresh registry with no Slack api when Slack is off)
  and registers the routes with `server.custom_route`. It logs one warning naming the missing client variables.
  `server.hosted["admin"]` holds the pieces, or None when the page is off.
- The Slack side is `hosted/slack/channel_admin.py` (the page's service: overview, register, finish setup, change,
  turn off and on) with `channel_setup.py`. Routes call it and stay thin.

**Invariants:**

- Access needs `sidecar:use` and `sidecar:admin` in the access token and a profile email; Auth0 changes are made in
  `mpd-auth`, never from this repo.
- The client secret is never logged or rendered. Sessions and pending logins are stored hashed.
- Registry writes and their audit entries share one transaction.
- A missing `Sidecar team budget` guardrail is `errors.NoGuardrailError`; the page shows the `NO_GUARDRAIL` sentence and
  leaves the row off with Finish setup. Until the team has a key its saved models read as nothing saved, so the model
  selects stay on and Finish setup posts the typed ones.
- A row turns on only when its `default_model` alias resolves to a model: a brand alias needs its family's model, an
  exact model id needs none. `register` and `finish` refuse with `CHOOSE_DEFAULT_MODEL`; register also needs an analyst
  and a helper model (`CHOOSE_ANALYST`, `CHOOSE_HELPER`).
- Setup failure reasons are admin sentences from `copy.py` (`KEY_*`, `MODEL_NOT_ACCEPTED`, `OPENROUTER_DOWN`,
  `UNEXPECTED`), never the student-facing key text or raw error text.
- Every sentence the page shows (notices, help lines, confirm texts, empty states, errors) comes from `copy.py`; only
  one-word labels, headings and button names stay in the templates. Details go to the log through `logs.redact`, never
  to the page. The change log shows the label and channel id, not the channel name.
- Operations (variables, the Auth0 request, turning the page off): `docs/HOSTED_OPERATIONS.md` §10.

**Tests:** `tests/test_hosted_admin_<module>*.py` (enforced by `scripts/checks/missing_tests.py`).
