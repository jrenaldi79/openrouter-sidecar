---
title: "2026-10-21-slack-admin: task 6 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 6 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 6 (Wave B): Auth0 login

**Files:** Create `hosted/admin/oidc.py`. Modify `tests/jwt_helpers.py` (an ID-token helper, if needed). Test `tests/test_hosted_admin_oidc.py`.

**Interfaces:**
- Consumes: Task 1 `AdminSettings`, Task 2 `PendingLogins`/`TooManyLogins`, `hosted.verifier.Auth0Verifier`.
- Produces: `Auth0Login(settings: AdminSettings, logins: PendingLogins, *, key_resolver=None, transport: httpx.AsyncBaseTransport | None = None)` with `start() -> tuple[str, str]` (authorize URL, `state` for the login cookie), `async finish(code: str, state: str, cookie_state: str | None) -> AdminIdentity`, `logout_url() -> str`; `@dataclass(frozen=True) AdminIdentity(subject: str, email: str)`; `class LoginRefused(Exception)` with `.reason` in `"state"`, `"exchange"`, `"id_token"`, `"not_admin"`. `ADMIN_PERMISSIONS = ("sidecar:use", "sidecar:admin")`.
- `start()`: `<issuer>authorize?response_type=code&client_id=…&redirect_uri=<callback>&scope=openid&audience=<resource>&state=…&nonce=…&code_challenge=<b64url(sha256(verifier))>&code_challenge_method=S256`. `finish`: `cookie_state` must equal `state` (`hmac.compare_digest`) and `take(state)` must succeed, else `"state"`; POST `<issuer>oauth/token` (`grant_type=authorization_code, client_id, client_secret, code, redirect_uri, code_verifier`; non-200 or bad JSON → `"exchange"`, log the status only); ID token by PyJWT with `algorithms=["RS256"]`, `aud=client_id`, `iss`, matching `nonce` (else `"id_token"`); access token through `Auth0Verifier(issuer=…, resource=…, key_resolver=…, profile_claim=None)`; missing either permission or the profile email → `"not_admin"`.

- [ ] **Step 1: Failing tests** (respx for `/oauth/token`, `jwt_helpers`): `test_start_url_has_pkce_state_nonce_audience`, `test_good_login`, `test_callback_needs_login_cookie` (Review Focus 4: missing or different cookie state → `"state"`), `test_missing_admin_permission`, `test_missing_use_permission`, `test_no_profile_email`, `test_admin_without_workspace_signs_in`, `test_unknown_or_reused_state`, `test_wrong_nonce`, `test_hs256_id_token_refused`, `test_exchange_failure_logs_status_only` (caplog has the status, not the secret), `test_logout_url`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(admin): Auth0 sign-in with PKCE and a login cookie`.

