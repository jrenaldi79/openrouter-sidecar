---
title: "src/openrouter_image_mcp/hosted/admin"
description: "The /admin web page: MPD admins sign in with Auth0 (sidecar:admin) and register and edit Slack channels; settings, Auth0 login, sessions, audit, views and routes."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/admin/."
---

**Owns:**

- `copy.py`: every sentence the page shows (plain text; templates escape it).
- Being built (`docs/superpowers/plans/2026-10-21-slack-admin.md`): `settings.py` (admin settings, on by default),
  `sessions.py` (pending logins and sessions), `audit.py` (the change log), `oidc.py` (Auth0 sign-in),
  `views.py` with `templates/` and `public/` (the Interview Coach admin design system), `forms.py`, `routes.py` and
  `wiring.py`. The Slack side is `hosted/slack/channel_admin.py`.

**Invariants:**

- Access needs `sidecar:use` and `sidecar:admin` in the access token and a profile email; Auth0 changes are made in
  `mpd-auth`, never from this repo.
- The client secret is never logged or rendered. Sessions and pending logins are stored hashed.
- Registry writes and their audit entries share one transaction.

**Tests:** `tests/test_hosted_admin_<module>*.py` (enforced by `scripts/checks/missing_tests.py`).
