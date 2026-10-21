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
- Being built (`docs/superpowers/plans/2026-10-21-slack-admin.md`): `oidc.py` (Auth0 sign-in), `forms.py`, `routes.py`
  and `wiring.py`. The Slack side is `hosted/slack/channel_admin.py`.

**Invariants:**

- Access needs `sidecar:use` and `sidecar:admin` in the access token and a profile email; Auth0 changes are made in
  `mpd-auth`, never from this repo.
- The client secret is never logged or rendered. Sessions and pending logins are stored hashed.
- Registry writes and their audit entries share one transaction.

**Tests:** `tests/test_hosted_admin_<module>*.py` (enforced by `scripts/checks/missing_tests.py`).
