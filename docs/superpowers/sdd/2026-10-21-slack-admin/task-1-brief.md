---
title: "2026-10-21-slack-admin: task 1 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 1 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 1 (Wave A): Admin settings

**Files:** Create `hosted/admin/settings.py`. Modify `hosted/settings.py` (`HostedSettings.admin`). Test
`tests/test_hosted_admin_settings.py`.

**Interfaces:**
- Produces: `@dataclass(frozen=True) AdminSettings(enabled: bool, client_id: str | None, client_secret: str | None (field(repr=False)), issuer: str, resource: str, public_origin: str)` with properties `callback_url`, `logout_return`, `secure_cookies: bool`, `configured: bool`, and `missing() -> list[str]`; `load_admin_settings(env, *, issuer: str, resource: str, public_origin: str) -> AdminSettings`. `HostedSettings.admin: AdminSettings | None = None`, filled by `load_hosted_settings`.

- [ ] **Step 1: Failing tests:** `test_on_by_default_and_not_configured` (`missing() == ["SIDECAR_ADMIN_CLIENT_ID", "SIDECAR_ADMIN_CLIENT_SECRET"]`), `test_disabled_values` (`false`, `0`, `no`), `test_short_secret_stops_startup` (31 characters → `ValueError`), `test_urls_and_secure_cookies` (production origin → `https://<host>/admin/callback`, Secure; `http://localhost:8000` → not Secure), `test_secret_not_in_repr`, `test_hosted_settings_carry_admin`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS); full `uv run pytest`.
- [ ] **Step 5: Commit** `feat(admin): admin settings, on by default`.

