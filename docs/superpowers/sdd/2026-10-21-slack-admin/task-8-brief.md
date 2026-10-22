---
title: "2026-10-21-slack-admin: task 8 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 8 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 8 (Wave C): Wiring into the hosted server

**Files:** Create `hosted/admin/wiring.py` (`register_admin(server, settings, target, slack_app, provisioner, limits, *, options: dict | None)`), so `build_hosted_server` doesn't grow. Modify `hosted/app.py`, `hosted/slack/wiring.py` (`SlackApp.api` and `SlackApp.registry` read-only properties if missing), `tests/hosted_harness.py` (`admin=` option passed through like `slack=`). Test `tests/test_hosted_admin_wiring.py`.

**Interfaces:**
- `build_hosted_server(..., admin: dict | None = None)`; `admin` may hold `key_resolver` and `transport` for the Auth0 fake. When `settings.admin.enabled`: stores on the server's target; `Auth0Login` when `configured` (else None, and one warning naming `missing()`); `ChannelAdmin` from `slack_app` (`api`, `registry`, `services`) or, when Slack is off, over a fresh `ChannelRegistry` with `api=None`; routes registered with `server.custom_route`. `server.hosted["admin"]` holds the pieces (None when disabled).

- [ ] **Step 1: Failing tests** with `running_server`: `test_disabled_is_404`, `test_not_configured_page_and_warning`, `test_configured_redirects_to_auth0`, `test_slack_off_lists_rows_read_only`, `test_mcp_unaffected`.
- [ ] **Step 2–4:** Run, implement, run; full `uv run pytest`, `-m pg`, `ruff`, `file_sizes --all`.
- [ ] **Step 5: Commit** `feat(admin): serve the admin page from the hosted server`.

