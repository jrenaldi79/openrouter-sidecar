---
title: "src/openrouter_image_mcp/hosted"
description: "Hosted remote MCP (Railway): authenticated multi-tenant Streamable HTTP server with Auth0 verification, per-principal provisioned OpenRouter keys and vault, cohort workspaces, limits, image assets with signed uploads and downloads, health, sweeper, retention, and the Slack bot wired to the owner lock."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/."
---

**Owns:**

- Server: `app.py` (`build_hosted_server`, transport security, tool registration; builds the Slack bot with `slack/wiring.py` only when `SIDECAR_SLACK_ENABLED`, hooks it to the owner lock and closes it first at shutdown, `server.hosted["slack"]`), `main.py` (`serve-http` entry), `settings.py` (environment config; fails closed on unsafe values), `health.py` (liveness, readiness, the gate that follows the owner lock), `ownership.py` (`OwnerLock`: the single-owner Postgres advisory lock with `on_gain` and `on_lose` hooks).
- Identity: `verifier.py` (Auth0 RS256, exact issuer and audience), `principal.py` (stable key `issuer|subject`), `credentials.py` (principal plus provisioned key; resolve() is a synchronous lookup, forget() marks a key rejected after a 401).
- Student OpenRouter keys: `openrouter_keys.py` (the management API seam, `KeyManager`, plus `current_key` for `GET /key`), `provisioning.py` (`KeyProvisioner.ensure`: one key per principal and cohort on first use, orphan cleanup, recheck of rejected keys; `key_middleware` runs it before tool calls), `student_keys.py` (the `student_keys` table), `vault.py` (keys encrypted at rest; associated data binds a ciphertext to its principal), `account.py` (`account_status`: key, spend and budget).
- Images: `images.py` (the shared `ImageService` with asset ids in and out), `image_tools.py`, `image_results.py`, `uploads.py` (single-use upload URLs that answer CORS preflight, HMAC-signed download links). Hosted generate, edit and remask bind the gallery app, and Apps clients also get `open_image_uploader`.
- Operations: `workspaces.py` (one service set per cohort), `limits.py` (per-principal admission for paid work), `sweeper.py` (hourly expiry sweep), `retention.py` (`purge`, including idle student keys), `devproxy.py` (local development only).
- Guidance: `guidance.py` (server `INSTRUCTIONS`; the hosted class skills in `skills/` as MCP prompts and `skill://` resources).
- Migration: `imports.py` (`create_local_import`, `PUT /imports/{token}` on the shared upload tickets; cohort-bound, imports once per content digest).

**Invariants:**

- Hosted tools reference images by asset id, never by a local path.
- Every query is scoped to the authenticated principal; no tool accepts a caller-chosen principal or workspace.
- One replica owns background work through `OwnerLock` (`Health.lock`); start and stop background work from its `on_gain` and `on_lose` hooks, never by polling the gate. Hooks run in order, a failing hook is logged by type and skipped, and `on_gain` runs again on a regain. SQLite is always the owner.
- `VAULT_KEYS`, `LINK_SIGNING_KEY` (signs upload, import and download URLs), `OPENROUTER_MANAGEMENT_KEY`, Auth0 and `DATABASE_URL` come only from the environment (`settings.py`). The secret scan blocks literals.
- The management key only creates, reads, updates and deletes student keys and assigns each its class budget guardrail (`Sidecar student budget`; no guardrail, no key); it never runs inference and never appears in logs or tool output. Free tools keep working when no key can be provisioned.

**Tests:** `tests/test_hosted_<module>*.py`, using `tests/hosted_harness.py` and `tests/jwt_helpers.py`. The local end-to-end stack is in `scripts/dev/` (`docs/E2E_LOCAL.md`). Operations are in `docs/HOSTED_OPERATIONS.md`.

**Gotchas:** Auth0 tenant setup and class identity live outside this repo (`../mpd-auth`). Don't change them from here.
