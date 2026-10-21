---
title: "Slack admin page implementation plan"
description: "Task-by-task plan for the /admin web page: admin settings, sessions and audit, Auth0 login, Slack additions, the shared channel service, views in the Coach design system, routes and wiring"
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
views, Starlette routes registered with `custom_route`) over a new shared `hosted/slack/channel_admin.py` service
that `scripts/ops/slack_channels.py` also uses. Pages are server-rendered forms; htmx only for the error banner
and turn off/on confirms.

**Tech Stack:** Python 3.12, Starlette (via `mcp`), Jinja2 (new), httpx, PyJWT, respx, pytest; Pico 2.1.1 and
htmx 2.0.11 from jsDelivr; CSS, fonts and brand images copied from `mpd-class-mcp` commit
`7e10e3664ce30d3b96e704bfc4e9242c9a76c384`.

**Spec:** `docs/superpowers/specs/2026-10-21-slack-admin-design.md`

## Global Constraints

- Branch `feat/slack-sidecar`; work only in the worktree. No Auth0 tenant change from this repo.
- Files at most 300 lines (`.py .js .css .html` are counted, templates and copied CSS included); functions at
  most 50 lines. Every new folder gets a five-field `CLAUDE.md` note; regenerate docs with
  `node scripts/self-documentation/generate.js`.
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
- Cookie `sidecar_admin`: `HttpOnly`, `SameSite=Lax`, `Path=/admin`, `Secure` unless the public origin is
  `http://localhost` or `http://127.0.0.1`. Session: 2 h idle (`IDLE_S = 7200`), 12 h absolute
  (`ABSOLUTE_S = 43200`). Pending login: single use, `LOGIN_S = 600`.
- CSP and headers exactly as the spec's Design system section; no inline script, style or event handlers.
- Jinja2 `Environment(autoescape=True)`; never `|safe` on data.
- Plain copy, no status pills, coloured marks or tags. Exact strings:
  - `NOT_CONFIGURED = "Admin sign-in isn't configured on this server yet."`
  - `NOT_ADMIN = "This page is for Sidecar admins."`
  - `SLACK_OFF = "Slack isn't turned on for this server."`
  - `NO_CHANNELS = "Invite @Sidecar to a channel, then register it here."`
  - `SLACK_DOWN = "Slack didn't answer. Try again."`
  - `CATALOG_DOWN = "The model list didn't load. Try again."`
  - `NO_GUARDRAIL = "The {cohort} workspace has no 'Sidecar team budget' guardrail, so no team key can be made."`
  - `GUEST_BLOCKED = "Sidecar can't be used in #{name}: it {reason}."`
  - `NOTHING_CHANGED = "Nothing changed."`
  - `LOGIN_FAILED = "Sign-in didn't work. Try again."`

## Decisions this plan adds to the spec

These come from the code; Task 10 writes them back into the spec.

1. **Register is two steps.** The model catalog is fetched with the team's own key, so the team key must exist
   before the model selects can be filled. Step 1 (channel, label, cohort) is a POST that runs the guest check
   and creates the team key; step 2 is the full form. An abandoned step 2 leaves a key with no row, as a failed
   `slack_channels.py add` does today.
2. **Expired sessions and pending logins are purged at each sign-in**, not by the hourly sweeper (which only
   exists when `ASSET_DIR` is set). Expiry is always checked on read.
3. **The admin page verifies tokens with its own `Auth0Verifier(profile_claim=None)`** and checks the profile
   email itself, so an admin outside a cohort (no `workspace_id`) can still sign in.
4. **The secret is kept out of logs by never logging it** (`repr=False`, status-only errors), since
   `logs.redact` has no pattern for an Auth0 client secret.
5. **`SlackApi.team_id()`** is added (`auth.test`'s `team_id`); `bot_user_id()` stays.

## Review Focus

1. An `mpd-admin` whose profile claim has an email but no `workspace_id` signs in (Task 3 test
   `test_admin_without_workspace_signs_in`).
2. A double-submitted register form or a browser Back-and-resubmit gives a plain duplicate message and no second
   row (Task 5 test `test_register_twice_refuses_second`).
3. Two admins editing different fields of one channel both keep their change (Task 5 test
   `test_change_writes_only_changed_fields`).
4. Local sign-in over `http://localhost` works (no `Secure` flag there) and production cookies are `Secure`
   (Task 8 test `test_cookie_secure_only_off_localhost`).
5. Slack failing while the Channels page loads still lists the registered rows with `SLACK_DOWN` (Task 8 test
   `test_channels_page_when_slack_fails`). A channel name or label holding `<script>` renders escaped (Task 7
   test `test_templates_escape_slack_text`).

---

### Task 1: Admin settings and test mapping

**Files:**
- Create: `src/openrouter_image_mcp/hosted/admin/__init__.py`, `hosted/admin/settings.py`, `hosted/admin/CLAUDE.md`
- Modify: `hosted/settings.py` (`HostedSettings.admin`), `scripts/checks/missing_tests.py`, `pyproject.toml`/`uv.lock` (`uv add jinja2`)
- Test: `tests/test_hosted_admin_settings.py`, `tests/test_checks_missing_tests.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) AdminSettings(enabled: bool, client_id: str | None, client_secret: str | None (repr=False), issuer: str, resource: str, public_origin: str)` with properties `callback_url`, `logout_return`, `secure_cookies: bool`, `configured: bool`, and `missing() -> list[str]` (env names unset); `load_admin_settings(env, *, issuer: str, resource: str, public_origin: str) -> AdminSettings`. `HostedSettings.admin: AdminSettings | None = None`, filled by `load_hosted_settings`.

- [ ] **Step 1: Write failing tests.**
  - `test_on_by_default_and_not_configured`: empty env → `enabled`, not `configured`, `missing() == ["SIDECAR_ADMIN_CLIENT_ID", "SIDECAR_ADMIN_CLIENT_SECRET"]`.
  - `test_disabled_values`: `SIDECAR_ADMIN_ENABLED` in `false`, `0`, `no` → not enabled.
  - `test_short_secret_stops_startup`: a 31-character secret raises `ValueError`.
  - `test_urls_and_secure_cookies`: production origin → callback `https://<host>/admin/callback`, `secure_cookies` True; `http://localhost:8000` → False.
  - `test_secret_not_in_repr`.
  - `test_hosted_settings_carry_admin`.
  - `missing_tests`: `hosted/admin/oidc.py` maps to `tests/test_hosted_admin_oidc*.py`.
- [ ] **Step 2: Run** `uv run pytest tests/test_hosted_admin_settings.py tests/test_checks_missing_tests.py` → FAIL.
- [ ] **Step 3: Implement** the settings, the `hosted/admin` mapping in `missing_tests.py` (stdlib only), and `uv add jinja2`. Folder note per Global Constraints.
- [ ] **Step 4: Run** the two files, then `uv run pytest` → PASS.
- [ ] **Step 5: Commit** `feat(admin): admin settings, on by default`.

### Task 2: Sessions, pending logins and audit storage

**Files:** Create `hosted/admin/sessions.py`, `hosted/admin/audit.py`. Test `tests/test_hosted_admin_sessions.py`, `tests/test_hosted_admin_audit.py`.

**Interfaces:**
- Produces:
  - `PendingLogins(target, *, clock=time.time)`: `create() -> Pending(state, nonce, verifier)` (each `secrets.token_urlsafe(32)`, stored by SHA-256 of `state`), `take(state: str) -> Pending | None` (deletes the row; None when unknown or older than `LOGIN_S`), `purge() -> int`. Table `admin_logins(state_hash PRIMARY KEY, nonce, verifier, created_at REAL)`.
  - `AdminSessions(target, *, clock=time.time)`: `create(subject: str, email: str) -> tuple[str, Session]` (raw id `secrets.token_urlsafe(32)`, stored hashed; purges expired sessions and pending logins first), `get(raw_id: str) -> Session | None` (None past `IDLE_S` since `last_seen_at` or `ABSOLUTE_S` since `created_at`; otherwise moves `last_seen_at`), `delete(raw_id) -> None`, `purge() -> int`. `@dataclass(frozen=True) Session(subject, email, csrf_token)`. Table `admin_sessions(id_hash PRIMARY KEY, subject, email, csrf_token, created_at REAL, last_seen_at REAL)`.
  - `AdminAudit(target, *, clock=time.time)`: `record(actor: Session, action: str, row: ChannelRow, changes: dict[str, list]) -> None`, `recent(limit: int = 200) -> list[AuditEntry]` newest first. Actions `REGISTERED = "registered"`, `CHANGED = "changed"`, `TURNED_OFF = "turned off"`, `TURNED_ON = "turned on"`. `AuditEntry(at, actor_email, action, team_id, channel_id, label, changes)`. Table `admin_audit(at REAL, actor_email, actor_subject, action, team_id, channel_id, label, changes TEXT)` with no id column (both backends have `rowid`; the storage layer adds it on Postgres), read `ORDER BY at DESC, rowid DESC`; `changes` as JSON.

- [ ] **Step 1: Write failing tests** (`db_target`, injected clock): `test_pending_login_single_use`, `test_pending_login_expires_after_10_minutes`, `test_session_idle_expiry`, `test_session_absolute_expiry_even_when_active`, `test_get_moves_last_seen`, `test_raw_id_not_stored` (no row column equals the raw id), `test_create_purges_expired`, `test_delete`, `test_audit_round_trip_newest_first`, `test_audit_limit_200`.
- [ ] **Step 2–4:** Run (FAIL), implement following `ChannelRegistry._db`'s schema pattern, run (PASS) on both backends.
- [ ] **Step 5: Commit** `feat(admin): sessions, pending logins and audit tables`.

### Task 3: Auth0 login

**Files:** Create `hosted/admin/oidc.py`. Test `tests/test_hosted_admin_oidc.py`.

**Interfaces:**
- Consumes: `AdminSettings`, `PendingLogins`, `hosted.verifier.Auth0Verifier`.
- Produces: `Auth0Login(settings: AdminSettings, logins: PendingLogins, *, key_resolver=None, transport: httpx.AsyncBaseTransport | None = None)` with `start() -> str` (authorize URL), `async finish(code: str, state: str) -> AdminIdentity`, `logout_url() -> str`; `@dataclass(frozen=True) AdminIdentity(subject: str, email: str)`; `class LoginRefused(Exception)` with `.reason` in `"state"`, `"exchange"`, `"id_token"`, `"not_admin"`. `ADMIN_PERMISSIONS = ("sidecar:use", "sidecar:admin")`.

`start()`: `<issuer>authorize?response_type=code&client_id=…&redirect_uri=<callback>&scope=openid&audience=<resource>&state=…&nonce=…&code_challenge=<b64url(sha256(verifier))>&code_challenge_method=S256`. `finish`: `take(state)` (None → `"state"`), POST `<issuer>oauth/token` form `grant_type=authorization_code, client_id, client_secret, code, redirect_uri, code_verifier` (non-200 or bad JSON → `"exchange"`, log status only), ID token checked with PyJWT (RS256, `aud=client_id`, `iss`, `nonce` equal; failure → `"id_token"`), access token through `Auth0Verifier(issuer=…, resource=…, key_resolver=…, profile_claim=None)`; missing either permission or profile email → `"not_admin"`.

- [ ] **Step 1: Write failing tests** with respx for `/oauth/token` and `tests/jwt_helpers.token`/`resolver` (add an ID-token helper there if needed): `test_start_url_has_pkce_state_nonce_audience`, `test_good_login`, `test_admin_without_workspace_signs_in` (Review Focus 1), `test_missing_admin_permission`, `test_missing_use_permission`, `test_no_profile_email`, `test_unknown_or_reused_state`, `test_wrong_nonce`, `test_exchange_failure_logs_status_only` (caplog holds the status and not the secret), `test_logout_url`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(admin): Auth0 sign-in with PKCE`.

### Task 4: Slack additions

**Files:** Modify `hosted/slack/api.py`, `tests/slack_fakes.py`. Test `tests/test_hosted_slack_api*.py` (the existing api test file).

**Interfaces:**
- Produces: on `SlackApi` and `SdkSlackApi`: `async team_id(self) -> str` (`auth.test`'s `team_id`), `async bot_channels(self) -> list[dict]` (`users.conversations`, `types="public_channel,private_channel"`, `exclude_archived=True`, paged by cursor, at most 10 pages of 200; each `{"id", "name", "is_private"}`), both through `_call`. `FakeSlack.team = "T0"` and `FakeSlack.bot_in: list[str]` (channel ids, names from `.channels[id]["name"]`).

- [ ] **Step 1: Write failing tests:** `test_team_id`, `test_bot_channels_pages_and_shapes`, `test_bot_channels_error_is_slack_call_error`, and a fake parity check that `FakeSlack` still matches the protocol's method names.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): team id and the bot's channels`.

### Task 5: Shared channel service

**Files:** Create `hosted/slack/channel_admin.py`. Test `tests/test_hosted_slack_channel_admin.py`.

**Interfaces:**
- Consumes: `ChannelRegistry`, `new_row`, `ChannelRow`, `channel_problem`, `team_principal`, `acting_as`, `TeamKey`, `KeyProvisioner.ensure`, `SlackServices.preferences` (`view`, `get`, `update(settings_id, revision, family_defaults=)`), `AdminAudit` and `Session` (Task 2), Task 4's api methods.
- Produces: `ChannelAdmin(registry, api: SlackApi | None, services: Mapping[str, SlackServices] | None, provisioner, audit: AdminAudit)`; `class ChannelRefused(Exception)` (message is page-ready plain copy); `@dataclass Overview(registered: list[tuple[ChannelRow, str]], unregistered: list[dict], slack_note: str | None)`.
  - `async overview() -> Overview` — names from `channel_info` (id when it fails); unregistered = `bot_channels()` minus registry rows; `api is None` → `slack_note = SLACK_OFF`; a Slack failure → registered rows only and `slack_note = SLACK_DOWN`.
  - `async begin(channel_id, label, cohort) -> ChannelRow` — the draft row (`new_row` with the cohort's defaults and `registry_defaults`), duplicate label/channel → `ChannelRefused`, guest check → `GUEST_BLOCKED`, `provisioner.ensure` (`AuthRequiredError` for a missing guardrail → `NO_GUARDRAIL`).
  - `async catalog(row: ChannelRow) -> dict` — `preferences.view()` under `acting_as(team_principal(…, replace(row, active=True)))`; returns `{"models": [...], "family_defaults": {...}}`; failure → `CATALOG_DOWN`.
  - `async members(channel_id) -> list[tuple[str, str]] | None` — `(user id, name)` sorted by name, None above 200.
  - `async register(row: ChannelRow, family_defaults: dict, actor: Session) -> ChannelRow` — duplicate check again, guest check, save family defaults, `registry.add`, audit `REGISTERED` with every field as `[None, value]`.
  - `async change(team_id, channel_id, changes: dict, family_defaults: dict, actor) -> dict[str, list]` — only keys whose value differs from the stored row; family defaults through `preferences.update`; `registry.update(**changes)`; audit `CHANGED`; empty → `ChannelRefused(NOTHING_CHANGED)`.
  - `async turn_off(team_id, channel_id, reason, actor)` and `async turn_on(team_id, channel_id, actor)` (guest check first), each audited.

- [ ] **Step 1: Write failing tests** (FakeSlack, `db_target`, a fake provisioner and a fake preferences stand-in like `tests/slack_dispatch_rig.py`'s `FakePreferences`): `test_overview_lists_registered_and_unregistered`, `test_channels_page_slack_off`, `test_overview_when_slack_fails`, `test_begin_refuses_guest_channel`, `test_begin_missing_guardrail`, `test_register_twice_refuses_second` (Review Focus 2), `test_register_saves_models_row_and_audit`, `test_change_writes_only_changed_fields` (Review Focus 3: two `change` calls built from the same stale row, different fields, both survive), `test_change_nothing`, `test_turn_off_and_on_audited`, `test_turn_on_reruns_guest_check`, `test_members_cap_200`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): shared channel admin service`.

### Task 6: `slack_channels.py` on the shared service

**Files:** Modify `scripts/ops/slack_channels.py`, `scripts/ops/slack_common.py`, `scripts/ops/CLAUDE.md`.

**Interfaces:** Consumes Task 5. The CLI keeps every subcommand and flag; `model_seeder` and `_check_channel` give way to `ChannelAdmin`. The script's actor is a `Session(subject="ops-script", email=os.environ.get("USER", "ops-script"), csrf_token="")`, so script changes appear in the change log.

- [ ] **Step 1: Rewire** `cmd_add` (`begin` then `register`), `cmd_update` (`change`, plus `turn_on` for `--active`), `cmd_deactivate` (`turn_off`), keeping messages and exit codes.
- [ ] **Step 2: Smoke-test:** `--help` and `list` against a temp SQLite `DATABASE_URL` exit 0; `uv run pytest`.
- [ ] **Step 3: Commit** `refactor(ops): slack_channels uses the shared channel service`.

### Task 7: Design system copy and views

**Files:**
- Create: `hosted/admin/public/` (copied: `tokens.css`, `fonts.css`, `shell.css`, `admin.css` reduced to its generic rules — `.muted`, `.error`, `.inline`, `.htmx-error`, `button.link` and their token use — `theme-instrument.css` without the Overview-only selectors, `theme-instrument-depth.css` without the pipeline rules, `admin.js`, `fonts/*.woff2` and the two OFL files, `brand/mpd-logo.png`, `brand/favicon.ico`) from `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384` (`src/admin/public/`, `assets/brand/`); `hosted/admin/templates/` (`layout.html`, `channels.html`, `begin.html`, `channel_form.html`, `log.html`, `message.html`); `hosted/admin/views.py`; folder notes for `public/`, `public/fonts/`, `public/brand/`, `templates/` (each records the source commit and "change it in mpd-class-mcp first").
- Test: `tests/test_hosted_admin_views.py`

**Interfaces:**
- Produces: `Views()` with `page(name: str, *, session: Session | None, **context) -> HTMLResponse` (layout pages) and `message(text: str, *, session: Session | None = None, status: int = 200) -> HTMLResponse` (standalone page, Coach `message.ejs` shape); `security_headers(issuer: str) -> dict[str, str]` (the spec's CSP and headers; pinned Pico and htmx URLs with SRI hashes taken from the Coach's `layout.ejs`); `STATIC_FILES = re.compile(r"^(?:[\w.-]+\.(?:css|js)|fonts/[\w-]+\.woff2|brand/[\w.-]+\.(?:png|ico))$")`.
- Templates mirror `views/layout.ejs` (skip link, rail "MPD Admin · Sidecar" with Channels and Change log, email and Sign out form in `rail-foot`, `main#main.main.page`, `#htmx-error`). Every POST form has `<input type="hidden" name="_csrf">`; `<body hx-headers>` carries `x-csrf-token`. `channel_form.html` shows label and cohort as read-only text when editing, the members picker as checkboxes (or a one-id-per-line textarea when `members` is None), and Turn off (reason field) and Turn on as plain POST forms with `hx-confirm`.

- [ ] **Step 1: Write failing tests:** `test_layout_shell_and_nav` (rail brand, two nav links, `aria-current` on the current page, sign-out form with `_csrf`), `test_templates_escape_slack_text` (Review Focus 5), `test_security_headers_exact`, `test_static_allowlist`, `test_message_page_standalone`, `test_status_plain_text` ("On" / "Off: guest joined", no `mark` or `.pill`).
- [ ] **Step 2–4:** Run, copy and write, run. `uv run python -m scripts.checks.file_sizes --all` shows no new file over 300 lines.
- [ ] **Step 5: Commit** `feat(admin): Coach design system and page templates`.

### Task 8: Routes

**Files:** Create `hosted/admin/routes.py` (split a `hosted/admin/forms.py` for form parsing if `routes.py` nears 300 lines). Test `tests/test_hosted_admin_routes.py`, `tests/test_hosted_admin_forms.py` if split.

**Interfaces:**
- Consumes: Tasks 1–3, 5, 7.
- Produces: `admin_routes(settings: AdminSettings, *, login: Auth0Login | None, sessions: AdminSessions, audit: AdminAudit, channels: ChannelAdmin, views: Views) -> list[Route]`. With `login is None` (not configured) every path except `/admin/public/…` answers `NOT_CONFIGURED`.
  - `GET /admin/login` → 302 to `login.start()`. `GET /admin/callback` → `finish`, `AdminSessions.create`, set cookie, 302 to `/admin`; `LoginRefused("not_admin")` → `NOT_ADMIN` (403); other reasons → `LOGIN_FAILED` (400). `POST /admin/logout` → delete session, clear cookie, 302 to `login.logout_url()`.
  - `GET /admin` (overview), `GET /admin/channels/new?channel=` (step 1 form), `POST /admin/channels/new` (`begin`, then 303 to `GET /admin/channels/new/details?channel=&label=&cohort=`), `GET`/`POST /admin/channels/new/details` (full form; `register`; 303 to the edit page), `GET`/`POST /admin/channels/{team}/{channel}` (edit; hidden fields carry the rendered values so only changed fields are sent to `change`), `POST /admin/channels/{team}/{channel}/off` (reason required), `POST …/on`, `GET /admin/log`, `GET /admin/public/{path}` (allowlist, 1 h cache, no session).
  - One guard: no or expired session → 302 to `/admin/login`; POST without a matching `_csrf` form field or `x-csrf-token` header (constant-time compare) → 403, no change. `ChannelRefused` → the form again with values and its message (422). Every response gets `security_headers`.

- [ ] **Step 1: Write failing tests** (Starlette app from `admin_routes`, `httpx.AsyncClient(transport=httpx.ASGITransport(app))`, a fake `Auth0Login` and `ChannelAdmin` where convenient): `test_redirects_to_login_without_session`, `test_callback_sets_cookie_and_redirects`, `test_cookie_secure_only_off_localhost` (Review Focus 4), `test_not_admin_page`, `test_login_failed_page`, `test_logout_clears_session`, `test_csrf_required_on_every_post`, `test_headers_on_every_response`, `test_not_configured_mode`, `test_channels_page_when_slack_fails` (Review Focus 5), `test_register_two_steps`, `test_edit_sends_only_changed_fields`, `test_refusal_keeps_input`, `test_turn_off_requires_reason`, `test_log_page`, `test_static_files_and_404`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(admin): admin routes`.

### Task 9: Wiring into the hosted server

**Files:** Modify `hosted/app.py` (extract `hosted/admin/wiring.py` with `register_admin(server, settings, target, slack_app, provisioner, *, options: dict | None)` so `build_hosted_server` doesn't grow), `hosted/slack/wiring.py` (`SlackApp.api` and `SlackApp.registry` read-only properties if missing), `tests/hosted_harness.py` (`admin=` option passed through like `slack=`). Test `tests/test_hosted_admin_wiring.py`.

**Interfaces:**
- `build_hosted_server(..., admin: dict | None = None)`; `admin` may hold `key_resolver` and `transport` for the Auth0 fake. When `settings.admin.enabled`: build the stores on the server's target, `Auth0Login` when `configured` (else None and one warning naming `missing()`), `ChannelAdmin` from `slack_app` (`api`, `registry`, `services`) or read-only from a fresh `ChannelRegistry` when Slack is off, and register each route with `server.custom_route`. `server.hosted["admin"]` holds the pieces (None when disabled).

- [ ] **Step 1: Write failing tests** with `running_server`: `test_disabled_is_404`, `test_not_configured_page_and_warning`, `test_configured_redirects_to_auth0`, `test_slack_off_lists_rows_read_only`, `test_mcp_unaffected` (an MCP tool call still works with admin on).
- [ ] **Step 2–4:** Run, implement, run; full `uv run pytest`, `-m pg`, `ruff`, `file_sizes --all`.
- [ ] **Step 5: Commit** `feat(admin): serve the admin page from the hosted server`.

### Task 10: Docs and the Auth0 request

**Files:** Modify the spec (the five decisions above; status stays `pending` until the live check), `docs/HOSTED_OPERATIONS.md` (new "Admin page" section: what it is, the three variables, local runs, turning it off, and "Auth0 setup (done in mpd-auth)": the MPD Sidecar Admin regular web client, callbacks `https://<host>/admin/callback` and `http://localhost:8000/admin/callback`, logout URLs, passwordless email connection, client grants to both Sidecar APIs, no change to other clients or Actions), `hosted/CLAUDE.md` (Owns gains `admin/`), root `CLAUDE.md` (one gotcha line: `/admin` signs in with the MPD Sidecar Admin client and needs `sidecar:admin`; Auth0 changes go through `mpd-auth`), `scripts/ops/CLAUDE.md`.

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Run** `node scripts/self-documentation/generate.js`, then `--check` → clean.
- [ ] **Step 3: Commit** `docs(admin): admin page operations and spec decisions`.

### Task 11: Live check (manual, with the user)

Runs with the Slack Sidecar live acceptance. Before it: the mpd-auth change (message the Interview Coach and
mpd-auth sessions with the HOSTED_OPERATIONS "Auth0 setup" section and wait for their constraints; the user
applies each tenant step), then the two Railway variables. Then sign in as an `mpd-admin`, check that a
non-admin sees `NOT_ADMIN`, register channels A and B through the page, edit one field, turn one off and on,
and confirm the change log. Record the results in the spec and set its status to `completed`; commit
`docs(admin): live check results`.
