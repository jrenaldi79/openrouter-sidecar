---
title: "2026-10-21-slack-admin: task 7 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 7 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 7 (Wave C): Routes

**Files:** Create `hosted/admin/routes.py` and `hosted/admin/forms.py` (form parsing and the rendered-vs-submitted diff). Test `tests/test_hosted_admin_routes.py`, `tests/test_hosted_admin_forms.py`.

**Interfaces:**
- Consumes: Tasks 1, 2, 4, 5, 6; `Limits.window(key, label, *, limit, span_s)` from `hosted/limits.py`.
- Produces: `admin_routes(settings: AdminSettings, *, login: Auth0Login | None, sessions: AdminSessions, audit: AdminAudit, channels: ChannelAdmin, views: Views, limits) -> list[Route]`. With `login is None` every path except `/admin/public/…` answers `NOT_CONFIGURED`.
  - `GET /admin/login` → `limits.window(f"admin-login|{client host}", …, limit=LOGIN_LIMIT, span_s=LOGIN_SPAN_S)` and `TooManyLogins` → 429 `TOO_MANY_LOGINS`; else set `sidecar_admin_login` and 302 to `start()`'s URL. `GET /admin/callback` → `finish(code, state, cookie)`, clear the login cookie, `AdminSessions.create`, set `sidecar_admin`, 302 to `/admin`; `"not_admin"` → `NOT_ADMIN` (403); other reasons → `LOGIN_FAILED` (400). `POST /admin/logout` → delete the session, clear the cookie, 302 to `logout_url()`.
  - `GET /admin`; `GET`/`POST /admin/channels/new?channel=` (one form, `register`, 303 to the edit page); `GET`/`POST /admin/channels/{team}/{channel}` (edit; only changed fields to `change`); `POST …/finish`, `POST …/off` (reason required), `POST …/on`; `GET /admin/log`; `GET /admin/public/{path}` (`STATIC_FILES`, one-hour cache, no session).
  - One guard: no or expired session → 302 to `/admin/login`; a POST without a matching `_csrf` field or `x-csrf-token` header (`hmac.compare_digest`) → 403, no change. `ChannelRefused` → the form again with the values and its message (422). Every response gets `security_headers`.

- [ ] **Step 1: Failing tests** (Starlette app from `admin_routes`, `httpx.AsyncClient(transport=httpx.ASGITransport(app))`, fakes for `Auth0Login` and `ChannelAdmin`): `test_redirects_to_login_without_session`, `test_login_sets_login_cookie`, `test_callback_sets_session_and_clears_login_cookie`, `test_cookie_secure_only_off_localhost`, `test_login_rate_limit` (Review Focus 4), `test_not_admin_page`, `test_login_failed_page`, `test_logout_clears_session`, `test_csrf_required_on_every_post`, `test_headers_on_every_response`, `test_not_configured_mode`, `test_channels_page_when_slack_fails` (Review Focus 5), `test_register_one_form`, `test_edit_sends_only_changed_fields`, `test_refusal_keeps_input`, `test_finish_setup_route`, `test_turn_off_requires_reason`, `test_log_page`, `test_static_files_and_404`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(admin): admin routes`.

