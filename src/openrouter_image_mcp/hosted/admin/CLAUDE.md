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
  local http). Unset client values mean "not configured", not an error. `HostedSettings.admin` carries it.
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
  `AdminIdentity` and `LoginRefused` (reason `state`, `exchange`, `id_token` or `not_admin`). `finish` needs the login
  cookie to equal `state`, checks the RS256 ID token, then the access token through `Auth0Verifier(profile_claim=None)`
  for both permissions and a profile email. A failed exchange logs the HTTP status only.
- `forms.py`: parses the register and edit forms and compares the submitted values with the values the form was
  rendered from (carried in hidden fields), so only changed fields are saved. A blank family model is never a
  change. It parses numbers at the boundary: `daily_cap_usd` in (0, 1000], `max_concurrent` a whole number from 1 to
  20, each checked only when the admin changed it.
- `routes.py`: `admin_routes`, the route table (login, callback, logout, channels, register, edit, finish setup, turn
  off and on, change log, static files, and a last `/admin/{rest:path}` catch-all so unknown paths still carry the
  headers). `routes_base.py` holds the one guard every route goes through: it checks the session, the Slack id format
  and the CSRF token (`_csrf` field or `x-csrf-token` header) for every POST, reads urlencoded bodies with a byte and
  field cap, adds `security_headers` to every response and answers unexpected errors with a generic page.
  `routes_auth.py` has sign-in and sign-out (the login route is limited per client address; the not-admin page links
  to Auth0 sign-out), `routes_channels.py` the pages and register and edit, `routes_actions.py` finish setup and
  turn off and on. Without an `Auth0Login` (client values unset) every page except the static files shows
  `NOT_CONFIGURED`. A `ChannelRefused` shows the form again with the entered values and its message.
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
  leaves the row off with Finish setup.
- Every sentence the page shows comes from `copy.py`. Details go to the log through `logs.redact`, never to the page.
- Operations (variables, the Auth0 request, turning the page off): `docs/HOSTED_OPERATIONS.md` §10.

**Tests:** `tests/test_hosted_admin_<module>*.py` (enforced by `scripts/checks/missing_tests.py`).
