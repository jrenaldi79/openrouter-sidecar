---
title: "src/openrouter_image_mcp/hosted"
description: "Hosted remote MCP (Railway): authenticated multi-tenant Streamable HTTP server with Auth0 verification, per-principal OpenRouter key linking and vault, cohort workspaces, limits, image assets with signed uploads and downloads, health, sweeper and retention."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/."
---

**Owns:**

- Server: `app.py` (`build_hosted_server`, transport security, tool registration), `main.py` (`serve-http` entry), `settings.py` (environment config; fails closed on unsafe values), `health.py` (liveness, readiness, single-owner advisory lock).
- Identity: `verifier.py` (Auth0 RS256, exact issuer and audience), `principal.py` (stable key `issuer|subject`), `credentials.py` (principal plus linked key).
- OpenRouter linking: `links.py` (single-use, browser-bound link attempts), `link_routes.py` (browser pages), `openrouter_keys.py` (exchange and verify), `vault.py` (keys encrypted at rest; associated data binds a ciphertext to its principal).
- Images: `images.py` (the shared `ImageService` with asset ids in and out), `image_tools.py`, `image_results.py`, `uploads.py` (single-use upload URLs that answer CORS preflight, HMAC-signed download links). Hosted generate, edit and remask bind the gallery app, and Apps clients also get `open_image_uploader`.
- Operations: `workspaces.py` (one service set per cohort), `limits.py` (per-principal admission for paid work), `sweeper.py` (hourly expiry sweep), `retention.py` (`purge`), `devproxy.py` (local development only).

**Invariants:**

- Hosted tools reference images by asset id, never by a local path.
- Every query is scoped to the authenticated principal; no tool accepts a caller-chosen principal or workspace.
- One replica owns background work through the health advisory lock.
- `VAULT_KEYS`, `LINK_SIGNING_KEY`, Auth0 and `DATABASE_URL` come only from the environment (`settings.py`). The secret scan blocks literals.

**Tests:** `tests/test_hosted_<module>*.py`, using `tests/hosted_harness.py` and `tests/jwt_helpers.py`. The local end-to-end stack is in `scripts/dev/` (`docs/E2E_LOCAL.md`). Operations are in `docs/HOSTED_OPERATIONS.md`.

**Gotchas:** Auth0 tenant setup and class identity live outside this repo (`../mpd-auth`). Don't change them from here.
