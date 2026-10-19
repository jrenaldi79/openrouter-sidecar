---
title: "src/openrouter_image_mcp"
description: "Core package: the local stdio MCP server, the shared image pipeline (catalog, client, imaging, service, outputs), the advisor, preferences and onboarding, and the SQLite/Postgres storage seam the hosted mode reuses."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/."
---

**Owns:**

- Entry points: `cli.py` (`serve` default stdio, `login`, `logout`, `status`, `serve-http`, `purge`, `active-jobs`, `export-local`, `print-config`) and `server.py` (MCP tools as thin wrappers).
- Image pipeline: `service.py` (validate, prepare, call, fit, save), `catalog.py` (live model catalog, parameter validation), `client.py` (OpenRouter image HTTP; `advisor_client.py` is the advisor's), `imaging.py`, `remask.py`, `outputs.py`.
- Credentials: `auth.py` (OAuth PKCE with a one-shot localhost listener), `keystore.py` (OS keyring, never plaintext), `credentials.py` (where the key and ownership scope come from for this call).
- Advisor: `advisor*.py`, `headless.py`, `session_files.py`, `prompt_cache.py`.
- Preferences and onboarding: `preferences.py`, `preference_tools.py`, `onboarding.py`, `image_onboarding.py`.
- Shared seams: `storage.py` (SQLite locally, pooled Postgres when hosted), `assets.py` (asset metadata plus blob store), `apps.py` (MCP App resources and per-client progressive enhancement, including `register_uploader`), `errors.py`, `logs.py`, `config.py`, `tool_annotations.py` (MCP read-only/destructive/paid hints for every tool, local and hosted).
- Migration: `local_bundle.py` (the `export-local` bundle: build, strict validation, content digest, preference seeding).

**Does not own:** multi-tenant HTTP serving, Auth0 and key linking (`hosted/`); bundled app HTML (`ui/`, generated).

**Invariants:**

- Every tool has an entry in `tool_annotations.ANNOTATIONS`; both servers call `annotate()` last, and an unlabelled tool stops startup.
- Every advisor and preference query binds both the credential owner and the workspace.
- Errors and logs go through `logs.redact` / `redact_log`; keys, JWTs and signed links never reach stderr or model-visible text.
- Image tool results keep `structuredContent` small and model-safe (some hosts show it to the model). Preview JPEGs go only in the result `_meta["sidecarPreviews"]` (`apps.preview_meta`, a list index-aligned with `structuredContent.images`, `null` where there is no preview). Local and hosted results share this shape, and `negotiate_apps` drops both for clients without Apps.
- Storage schema changes are additive (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) in code; there are no migration files.

**Tests:** flat `tests/test_<module>*.py`. Storage tests take the `db_target` fixture (SQLite, plus Postgres when `TEST_DATABASE_URL` is set).

**Gotchas:** `server.py`, `service.py`, `auth.py`, `imaging.py`, `client.py` and `catalog.py` are over the 300-line ratchet. Extract into a sibling before adding to them.
