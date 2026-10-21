---
title: "Slack admin page implementation plan"
description: "Task-by-task plan for the /admin web page in three parallel waves: settings, sessions and audit, Slack and catalog additions, views, Auth0 login, the channel admin service, routes and wiring"
type: "proposal"
status: "pending"
read_when: "Executing or reviewing the Slack admin page work"
---

# Slack Admin Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/admin` web page on the Sidecar server where `mpd-admin` holders register and edit Slack channels,
signed in through the shared Auth0 tenant, styled with the Interview Coach admin design system.

**Architecture:** A new `hosted/admin/` package (settings, Auth0 login, server-side sessions, audit, Jinja2
views, Starlette routes registered with `custom_route`) over a new `hosted/slack/channel_admin.py` service.
Registry writes and audit entries share one transaction; key creation and model saves run after it and are
retryable. `scripts/ops/slack_channels.py` is not changed.

**Tech Stack:** Python 3.12, Starlette (via `mcp`), Jinja2 (new), httpx, PyJWT, respx, pytest; Pico 2.1.1 and
htmx 2.0.11 from jsDelivr; CSS, fonts and brand images copied from `mpd-class-mcp` commit
`7e10e3664ce30d3b96e704bfc4e9242c9a76c384`.

**Spec:** `docs/superpowers/specs/2026-10-21-slack-admin-design.md`

## Execution order

Tasks in one wave touch disjoint files and run in parallel, each in its own worktree; the controller merges each
wave onto `feat/slack-sidecar` (regenerating `docs/index.md` on a conflict) before the next wave starts.

| Wave | Tasks | Needs |
| --- | --- | --- |
| 0 | Task 0 (controller) | — |
| A | Tasks 1, 2, 3, 4 | Task 0 |
| B | Tasks 5, 6 | Wave A |
| C | Task 7, then 8, then 9 | Wave B |
| — | Task 10 (manual, with the user) | Wave C |

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

## Review Focus

1. A double-submitted register form or Back-and-resubmit gives a plain duplicate message, no second row and no
   second key (Task 5 `test_register_twice_refuses_second`).
2. A failure after the row is written (no guardrail, model save) leaves the channel off with "Setup didn't
   finish", and Finish setup completes it without a second key (Task 5 `test_setup_failure_then_finish`).
3. Two admins editing different fields of one channel both keep their change (Task 5
   `test_change_writes_only_changed_fields`).
4. A login started in one browser can't be finished in another (Task 6 `test_callback_needs_login_cookie`), and
   repeated unauthenticated `/admin/login` requests stay bounded (Task 2 `test_pending_cap`, Task 7
   `test_login_rate_limit`).
5. Slack failing while the Channels page loads still lists the registered rows with `SLACK_DOWN` (Task 7
   `test_channels_page_when_slack_fails`); a channel name or label holding `<script>` renders escaped (Task 4
   `test_templates_escape_slack_text`).

---

### Task 0: Scaffold (controller, before Wave A)

**Files:** Create `hosted/admin/__init__.py`, `hosted/admin/copy.py` (the Global Constraints strings),
`hosted/admin/CLAUDE.md` (the folder note listing the planned modules), and add the `hosted/admin` →
`tests/test_hosted_admin_<m>*.py` mapping to `scripts/checks/missing_tests.py` with a case in
`tests/test_checks_missing_tests.py`. Commit `chore(admin): scaffold the admin package`.

### Task 1 (Wave A): Admin settings

**Files:** Create `hosted/admin/settings.py`. Modify `hosted/settings.py` (`HostedSettings.admin`). Test
`tests/test_hosted_admin_settings.py`.

**Interfaces:**
- Produces: `@dataclass(frozen=True) AdminSettings(enabled: bool, client_id: str | None, client_secret: str | None (field(repr=False)), issuer: str, resource: str, public_origin: str)` with properties `callback_url`, `logout_return`, `secure_cookies: bool`, `configured: bool`, and `missing() -> list[str]`; `load_admin_settings(env, *, issuer: str, resource: str, public_origin: str) -> AdminSettings`. `HostedSettings.admin: AdminSettings | None = None`, filled by `load_hosted_settings`.

- [ ] **Step 1: Failing tests:** `test_on_by_default_and_not_configured` (`missing() == ["SIDECAR_ADMIN_CLIENT_ID", "SIDECAR_ADMIN_CLIENT_SECRET"]`), `test_disabled_values` (`false`, `0`, `no`), `test_short_secret_stops_startup` (31 characters → `ValueError`), `test_urls_and_secure_cookies` (production origin → `https://<host>/admin/callback`, Secure; `http://localhost:8000` → not Secure), `test_secret_not_in_repr`, `test_hosted_settings_carry_admin`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS); full `uv run pytest`.
- [ ] **Step 5: Commit** `feat(admin): admin settings, on by default`.

### Task 2 (Wave A): Sessions, pending logins and audit storage

**Files:** Create `hosted/admin/sessions.py`, `hosted/admin/audit.py`. Test `tests/test_hosted_admin_sessions.py`, `tests/test_hosted_admin_audit.py`.

**Interfaces:**
- Produces:
  - `PendingLogins(target, *, clock=time.time)`: `create() -> Pending(state, nonce, verifier)` (each `secrets.token_urlsafe(32)`, stored by SHA-256 of `state`; deletes expired rows first; raises `TooManyLogins` when `MAX_PENDING` unexpired rows exist), `take(state: str) -> Pending | None` (deletes the row; None when unknown or older than `LOGIN_S`). Table `admin_logins(state_hash PRIMARY KEY, nonce, verifier, created_at REAL)`.
  - `AdminSessions(target, *, clock=time.time)`: `create(subject: str, email: str) -> tuple[str, Session]` (raw id `secrets.token_urlsafe(32)`, stored hashed; deletes expired sessions first), `get(raw_id: str) -> Session | None` (None past `IDLE_S` since `last_seen_at` or `ABSOLUTE_S` since `created_at`; otherwise moves `last_seen_at`), `delete(raw_id) -> None`. `@dataclass(frozen=True) Session(subject, email, csrf_token)`. Table `admin_sessions(id_hash PRIMARY KEY, subject, email, csrf_token, created_at REAL, last_seen_at REAL)`.
  - `audit.py`: `SCHEMA` (the `admin_audit` DDL), `insert(db, *, at: float, actor: Session, action: str, row: ChannelRow, changes: dict[str, list]) -> None` (writes on a connection the caller owns, so it joins the caller's transaction), and `AdminAudit(target, *, clock=time.time)` with `record(actor, action, row, changes) -> None` (its own transaction, for model saves) and `recent(limit: int = 200) -> list[AuditEntry]` newest first. Actions `REGISTERED = "registered"`, `CHANGED = "changed"`, `MODELS_CHANGED = "models changed"`, `TURNED_OFF = "turned off"`, `TURNED_ON = "turned on"`. `AuditEntry(at, actor_email, action, team_id, channel_id, label, changes)`. Table `admin_audit(at REAL, actor_email, actor_subject, action, team_id, channel_id, label, changes TEXT)`, no id column, read `ORDER BY at DESC, rowid DESC`; `changes` as JSON.

- [ ] **Step 1: Failing tests** (`db_target`, injected clock): `test_pending_login_single_use`, `test_pending_login_expires_after_10_minutes`, `test_create_purges_expired_logins`, `test_pending_cap` (the 501st unexpired `create` raises `TooManyLogins`; expired rows don't count), `test_session_idle_expiry`, `test_session_absolute_expiry_even_when_active`, `test_get_moves_last_seen`, `test_raw_id_not_stored`, `test_create_purges_expired_sessions`, `test_delete`, `test_audit_round_trip_newest_first`, `test_audit_limit_200`, `test_insert_joins_caller_transaction` (an exception after `insert` inside one `storage.connect` leaves no row).
- [ ] **Step 2–4:** Run, implement following `ChannelRegistry._db`'s schema pattern, run on both backends.
- [ ] **Step 5: Commit** `feat(admin): sessions, pending logins and audit tables`.

### Task 3 (Wave A): Slack, registry and catalog additions

**Files:** Modify `hosted/slack/api.py`, `tests/slack_fakes.py`, `hosted/slack/registry.py`, `preferences.py`, `advisor_client.py`, `hosted/slack/CLAUDE.md`, `src/openrouter_image_mcp/CLAUDE.md`. Test the existing `tests/test_hosted_slack_api*.py`, `tests/test_hosted_slack_registry.py`, `tests/test_preferences*.py`, `tests/test_advisor_client*.py`.

**Interfaces:**
- Produces:
  - `SlackApi`/`SdkSlackApi`: `async team_id(self) -> str` (`auth.test`'s `team_id`), `async bot_channels(self) -> list[dict]` (`users.conversations`, `types="public_channel,private_channel"`, `exclude_archived=True`, paged by cursor, at most 10 pages of 200; each `{"id", "name", "is_private"}`), both through `_call`. `FakeSlack.team = "T0"`, `FakeSlack.bot_in: list[str]`.
  - `ChannelRegistry.add(row, *, audit=None)`, `update(team_id, channel_id, *, audit=None, **changes)`, `deactivate(team_id, channel_id, reason, *, audit=None)`: `audit` is `Callable[[db, ChannelRow before | None, ChannelRow after], None]`, called on the same connection before commit; an exception from it rolls the write back. For `deactivate`, called only when it returns True.
  - `AdvisorClient.public_models(self) -> list[dict]`: `GET models` without an `Authorization` header, same parsing and text-and-tools filter as `models(key)`.
  - `preferences.filter_catalog(rows, now: datetime, *, secrets: tuple[str, ...] = ()) -> list[dict]`: the filter extracted from `PreferenceService._catalog` (id shape, batch, family, six-month window, dedupe, `MAX_MODELS`, `{id, name, pricing, context_length, created, family}`); a row is dropped when any non-empty secret appears in its id or name. `_catalog` calls it with `(key, scope.owner)`, so student behaviour is unchanged.

- [ ] **Step 1: Failing tests:** `test_team_id`, `test_bot_channels_pages_and_shapes`, `test_bot_channels_error_is_slack_call_error`, `test_fake_matches_protocol`; `test_audit_callback_commits_with_row`, `test_audit_callback_failure_rolls_back` (add, update, deactivate; `db_target`); `test_public_models_sends_no_auth_header` (respx); `test_filter_catalog_matches_student_catalog` (same input, same output as `PreferenceService.view`'s models), `test_filter_catalog_empty_secret_keeps_rows`.
- [ ] **Step 2–4:** Run, implement, run; full suite (student preference tests unchanged).
- [ ] **Step 5: Commit** `feat(slack): admin hooks in the Slack api, registry and catalog`.

### Task 4 (Wave A): Design system copy and views

**Files:**
- Create: `hosted/admin/public/` (copied from `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `src/admin/public/` and `assets/brand/`: `tokens.css`, `fonts.css`, `shell.css`, `admin.css` reduced to its generic rules — `.muted`, `.error`, `.inline`, `.htmx-error`, `button.link` — `theme-instrument.css` without the Overview-only selectors, `theme-instrument-depth.css` without the pipeline rules, `admin.js`, `fonts/*.woff2` with the two OFL files, `brand/mpd-logo.png`, `brand/favicon.ico`), `hosted/admin/templates/` (`layout.html`, `channels.html`, `channel_form.html`, `log.html`, `message.html`), `hosted/admin/views.py`, folder notes for `public/`, `public/fonts/`, `public/brand/` and `templates/` (each records the source commit and "change it in mpd-class-mcp first").
- Modify: `pyproject.toml`, `uv.lock` (`uv add jinja2`).
- Test: `tests/test_hosted_admin_views.py`.

**Interfaces:**
- Produces: `Views()` with `page(name: str, *, session, **context) -> HTMLResponse` (layout pages; `session` has `.email` and `.csrf_token`) and `message(text: str, *, session=None, status: int = 200) -> HTMLResponse` (standalone page, the Coach `message.ejs` shape); `security_headers(issuer: str) -> dict[str, str]` (the spec's CSP and headers; pinned Pico and htmx URLs with SRI hashes from the Coach's `layout.ejs`); `STATIC_FILES = re.compile(r"^(?:[\w.-]+\.(?:css|js)|fonts/[\w-]+\.woff2|brand/[\w.-]+\.(?:png|ico))$")`; `PUBLIC_DIR: Path`.
- Templates mirror `views/layout.ejs`: skip link, rail "MPD Admin · Sidecar" with Channels and Change log, email and Sign out form in `rail-foot`, `main#main.main.page`, `#htmx-error`. Every POST form has `<input type="hidden" name="_csrf">`; `<body hx-headers>` carries `x-csrf-token`. `channel_form.html` serves register and edit: label and cohort read-only when editing; model selects from a `models` list grouped by family; members as checkboxes, or a one-id-per-line textarea when `members` is None; hidden fields with the rendered values; a "Setup didn't finish" notice and Finish setup button when the row is off with `SETUP_UNFINISHED`; Turn off (reason field) and Turn on as POST forms with `hx-confirm`.

- [ ] **Step 1: Failing tests:** `test_layout_shell_and_nav`, `test_templates_escape_slack_text` (Review Focus 5), `test_security_headers_exact`, `test_static_allowlist`, `test_message_page_standalone`, `test_status_plain_text` ("On" / "Off: guest joined"; no `mark` or `.pill`), `test_form_register_vs_edit`, `test_finish_setup_shown`.
- [ ] **Step 2–4:** Run, copy and write, run. `uv run python -m scripts.checks.file_sizes --all` shows no new file over 300 lines.
- [ ] **Step 5: Commit** `feat(admin): Coach design system and page templates`.

### Task 5 (Wave B): Channel admin service

**Files:** Create `hosted/slack/channel_admin.py` (split `channel_setup.py` for register/finish if it nears 300 lines). Test `tests/test_hosted_slack_channel_admin.py`.

**Interfaces:**
- Consumes: Task 2 (`audit.insert`, `AdminAudit.record`, actions, `Session`), Task 3 (`team_id`, `bot_channels`, the registry `audit` callback, `public_models`, `filter_catalog`), `channel_problem`, `new_row`, `registry_defaults`, `team_principal`, `acting_as`, `TeamKey`, `KeyProvisioner.ensure`, `SlackServices` (`preferences`, `client`).
- Produces: `ChannelAdmin(registry, api: SlackApi | None, services: Mapping[str, SlackServices] | None, provisioner, audit: AdminAudit, *, names: NameCache | None = None)`; `class ChannelRefused(Exception)` (page-ready message); `@dataclass Overview(registered: list[tuple[ChannelRow, str]], unregistered: list[dict], slack_note: str | None)`.
  - `async overview() -> Overview`: names from `channel_info` (id on failure); unregistered = `bot_channels()` minus registry rows; `api is None` → `SLACK_OFF`; a Slack failure → registered rows only and `SLACK_DOWN`.
  - `async check(channel_id) -> str | None`: `GUEST_BLOCKED` text or None.
  - `async catalog() -> list[dict]`: `filter_catalog(await services[any].client.public_models(), now)`; failure → `ChannelRefused(CATALOG_DOWN)`.
  - `async members(channel_id) -> list[tuple[str, str]] | None`: `(user id, name)` sorted by name through `NameCache` with at most five lookups at a time; None above 50 members or on failure.
  - `async register(team_id, channel_id, fields: dict, family_defaults: dict, actor) -> ChannelRow`: validate (`new_row` + `registry._validate`; models in `catalog()`), duplicate label/channel → `ChannelRefused`, `check` → `ChannelRefused`; `registry.add(row off, SETTING_UP, audit=REGISTERED)`; then `finish_setup(team_id, channel_id, family_defaults, actor)`.
  - `async finish_setup(team_id, channel_id, family_defaults: dict, actor) -> ChannelRow`: `provisioner.ensure(team_principal(..., replace(row, active=True)), team=TeamKey(label))` (`AuthRequiredError` from a missing guardrail → reason `NO_GUARDRAIL`), save `family_defaults` (from the register form, or from the edit form's model selects when Finish setup is pressed) through `preferences.update`, audit `MODELS_CHANGED`, then `registry.update(active=True, inactive_reason=None, audit=TURNED_ON)`. Any failure → `registry.update(inactive_reason=SETUP_UNFINISHED)` and `ChannelRefused(SETUP_UNFINISHED_TEXT)`. Safe to repeat.
  - `async change(team_id, channel_id, changes: dict, family_defaults: dict, actor) -> dict[str, list]`: only differing keys; `registry.update(audit=CHANGED)` in one transaction; then changed family defaults through `preferences.update` and `AdminAudit.record(MODELS_CHANGED)`; a model failure after the registry write → `ChannelRefused(MODELS_NOT_SAVED)`; nothing differing → `ChannelRefused(NOTHING_CHANGED)`.
  - `async turn_off(team_id, channel_id, reason, actor)` and `async turn_on(team_id, channel_id, actor)` (guest check first), each audited in the registry transaction.

- [ ] **Step 1: Failing tests** (FakeSlack, `db_target`, fake provisioner and preferences like `tests/slack_dispatch_rig.py`'s `FakePreferences`, respx for the public list): `test_overview_lists_registered_and_unregistered`, `test_overview_slack_off`, `test_overview_when_slack_fails`, `test_register_refuses_guest_channel`, `test_register_twice_refuses_second` (Review Focus 1: one row, one `ensure` call), `test_register_saves_row_key_models_and_audit` (entries: registered, models changed, turned on), `test_setup_failure_then_finish` (Review Focus 2: missing guardrail and a model-save failure each leave the row off with `SETUP_UNFINISHED`; `finish_setup` completes it with one key), `test_audit_failure_rolls_back_registry_change`, `test_change_writes_only_changed_fields` (Review Focus 3), `test_change_model_failure_keeps_registry_change`, `test_change_nothing`, `test_turn_off_and_on_audited`, `test_turn_on_reruns_guest_check`, `test_members_cap_50_and_concurrency`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): channel admin service`.

### Task 6 (Wave B): Auth0 login

**Files:** Create `hosted/admin/oidc.py`. Modify `tests/jwt_helpers.py` (an ID-token helper, if needed). Test `tests/test_hosted_admin_oidc.py`.

**Interfaces:**
- Consumes: Task 1 `AdminSettings`, Task 2 `PendingLogins`/`TooManyLogins`, `hosted.verifier.Auth0Verifier`.
- Produces: `Auth0Login(settings: AdminSettings, logins: PendingLogins, *, key_resolver=None, transport: httpx.AsyncBaseTransport | None = None)` with `start() -> tuple[str, str]` (authorize URL, `state` for the login cookie), `async finish(code: str, state: str, cookie_state: str | None) -> AdminIdentity`, `logout_url() -> str`; `@dataclass(frozen=True) AdminIdentity(subject: str, email: str)`; `class LoginRefused(Exception)` with `.reason` in `"state"`, `"exchange"`, `"id_token"`, `"not_admin"`. `ADMIN_PERMISSIONS = ("sidecar:use", "sidecar:admin")`.
- `start()`: `<issuer>authorize?response_type=code&client_id=…&redirect_uri=<callback>&scope=openid&audience=<resource>&state=…&nonce=…&code_challenge=<b64url(sha256(verifier))>&code_challenge_method=S256`. `finish`: `cookie_state` must equal `state` (`hmac.compare_digest`) and `take(state)` must succeed, else `"state"`; POST `<issuer>oauth/token` (`grant_type=authorization_code, client_id, client_secret, code, redirect_uri, code_verifier`; non-200 or bad JSON → `"exchange"`, log the status only); ID token by PyJWT with `algorithms=["RS256"]`, `aud=client_id`, `iss`, matching `nonce` (else `"id_token"`); access token through `Auth0Verifier(issuer=…, resource=…, key_resolver=…, profile_claim=None)`; missing either permission or the profile email → `"not_admin"`.

- [ ] **Step 1: Failing tests** (respx for `/oauth/token`, `jwt_helpers`): `test_start_url_has_pkce_state_nonce_audience`, `test_good_login`, `test_callback_needs_login_cookie` (Review Focus 4: missing or different cookie state → `"state"`), `test_missing_admin_permission`, `test_missing_use_permission`, `test_no_profile_email`, `test_admin_without_workspace_signs_in`, `test_unknown_or_reused_state`, `test_wrong_nonce`, `test_hs256_id_token_refused`, `test_exchange_failure_logs_status_only` (caplog has the status, not the secret), `test_logout_url`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(admin): Auth0 sign-in with PKCE and a login cookie`.

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

### Task 8 (Wave C): Wiring into the hosted server

**Files:** Create `hosted/admin/wiring.py` (`register_admin(server, settings, target, slack_app, provisioner, limits, *, options: dict | None)`), so `build_hosted_server` doesn't grow. Modify `hosted/app.py`, `hosted/slack/wiring.py` (`SlackApp.api` and `SlackApp.registry` read-only properties if missing), `tests/hosted_harness.py` (`admin=` option passed through like `slack=`). Test `tests/test_hosted_admin_wiring.py`.

**Interfaces:**
- `build_hosted_server(..., admin: dict | None = None)`; `admin` may hold `key_resolver` and `transport` for the Auth0 fake. When `settings.admin.enabled`: stores on the server's target; `Auth0Login` when `configured` (else None, and one warning naming `missing()`); `ChannelAdmin` from `slack_app` (`api`, `registry`, `services`) or, when Slack is off, over a fresh `ChannelRegistry` with `api=None`; routes registered with `server.custom_route`. `server.hosted["admin"]` holds the pieces (None when disabled).

- [ ] **Step 1: Failing tests** with `running_server`: `test_disabled_is_404`, `test_not_configured_page_and_warning`, `test_configured_redirects_to_auth0`, `test_slack_off_lists_rows_read_only`, `test_mcp_unaffected`.
- [ ] **Step 2–4:** Run, implement, run; full `uv run pytest`, `-m pg`, `ruff`, `file_sizes --all`.
- [ ] **Step 5: Commit** `feat(admin): serve the admin page from the hosted server`.

### Task 9 (Wave C): Docs and the Auth0 request

**Files:** Modify `docs/HOSTED_OPERATIONS.md` (an "Admin page" section: what it is, the three variables, local runs, turning it off, and "Auth0 setup (done in mpd-auth)": the MPD Sidecar Admin regular web client, RS256 with the `auth0 api patch` line, passwordless email as its only connection, callbacks and logout URLs for production and `http://localhost:8000`, client grants to both Sidecar APIs, no change to other clients or Actions), `hosted/CLAUDE.md` (Owns gains `admin/`), `hosted/admin/CLAUDE.md` (final), the root `CLAUDE.md` (one gotcha line: `/admin` signs in with the MPD Sidecar Admin client and needs `sidecar:admin`; Auth0 changes go through `mpd-auth`).

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Run** `node scripts/self-documentation/generate.js`, then `--check` → clean.
- [ ] **Step 3: Commit** `docs(admin): admin page operations`.

### Task 10: Live check (manual, with the user)

Runs with the Slack Sidecar live acceptance. Before it: the mpd-auth change (message the Interview Coach and
mpd-auth sessions with the HOSTED_OPERATIONS "Auth0 setup" section and wait for their constraints; the user
applies each tenant step), then the two Railway variables. Then:
- sign in as an `mpd-admin`, and check that a non-admin sees `NOT_ADMIN`;
- register channels A and B through the page;
- edit one field, and turn one channel off and on;
- confirm the change log.

Record the results in the spec, set its status to `completed`, and commit `docs(admin): live check results`.
Moving `slack_channels.py` onto `ChannelAdmin` is a separate change after this.
