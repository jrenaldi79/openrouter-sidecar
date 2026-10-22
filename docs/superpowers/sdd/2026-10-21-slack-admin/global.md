---
title: "2026-10-21-slack-admin: global constraints handed to every implementer"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): global constraints handed to every implementer."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

## Global Constraints

- Branch `feat/slack-sidecar`. No Auth0 tenant change from this repo. `scripts/ops/slack_channels.py` and
  `slack_common.py` are not modified.
- Files at most 300 lines (`.py .js .css .html` are counted, templates and copied CSS included); functions at
  most 50 lines. Regenerate docs with `node scripts/self-documentation/generate.js`; `--check` must be clean.
- Tests for `hosted/admin/<m>.py` live in `tests/test_hosted_admin_<m>*.py`; Slack modules in
  `tests/test_hosted_slack_<m>*.py`. Storage tests use `db_target` (SQLite, plus Postgres when
  `TEST_DATABASE_URL` is set).
- No token-, key- or secret-shaped literals anywhere, tests included; build fakes at runtime
  (`"s" * 40`, `"xoxb-" + "1" * 10`).
- `SIDECAR_ADMIN_CLIENT_SECRET` is `repr=False`, never logged, never rendered; Auth0 exchange failures log the
  HTTP status only.
- Settings: `SIDECAR_ADMIN_ENABLED` on unless `false`/`0`/`no`; `SIDECAR_ADMIN_CLIENT_ID`;
  `SIDECAR_ADMIN_CLIENT_SECRET` at least 32 characters. Callback `<public origin>/admin/callback`, logout return
  `<public origin>/admin`.
- Permissions required: `sidecar:use` and `sidecar:admin` in the access token's `permissions`; profile claim
  `https://mpd.northwestern.edu/profile` with a non-empty `email`. `workspace_id` is not required.
- Cookies (`Path=/admin`, `HttpOnly`, `SameSite=Lax`, `Secure` unless the public origin is `http://localhost` or
  `http://127.0.0.1`): `sidecar_admin` (session id) and `sidecar_admin_login` (login `state`, `Max-Age=600`).
  Session: `IDLE_S = 7200`, `ABSOLUTE_S = 43200`. Pending login: single use, `LOGIN_S = 600`,
  `MAX_PENDING = 500`; per address `LOGIN_LIMIT = 20` per `LOGIN_SPAN_S = 600`.
- CSP and headers exactly as the spec's Design system section; no inline script, style or event handlers.
- Jinja2 `Environment(autoescape=True)`; never `|safe` on data.
- Plain copy, no status pills, coloured marks or tags. Exact strings (defined once in
  `hosted/admin/copy.py`, Task 0):
  - `NOT_CONFIGURED = "Admin sign-in isn't configured on this server yet."`
  - `NOT_ADMIN = "This page is for Sidecar admins."`
  - `LOGIN_FAILED = "Sign-in didn't work. Try again."`
  - `TOO_MANY_LOGINS = "Too many sign-ins in progress. Try again in a few minutes."`
  - `SLACK_OFF = "Slack isn't turned on for this server."`
  - `NO_CHANNELS = "Invite @Sidecar to a channel, then register it here."`
  - `SLACK_DOWN = "Slack didn't answer. Try again."`
  - `CATALOG_DOWN = "The model list didn't load. Try again."`
  - `NO_GUARDRAIL = "The {cohort} workspace has no 'Sidecar team budget' guardrail, so no team key can be made."`
  - `GUEST_BLOCKED = "Sidecar can't be used in #{name}: it {reason}."`
  - `NOTHING_CHANGED = "Nothing changed."`
  - `SETTING_UP = "Setting up"`, `SETUP_UNFINISHED = "Setup didn't finish"`,
    `SETUP_UNFINISHED_TEXT = "Setup didn't finish: {reason}."`
  - `MODELS_NOT_SAVED = "Saved the channel settings. The team's models weren't changed: {reason}."`

