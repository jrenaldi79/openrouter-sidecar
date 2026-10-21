---
title: "src/openrouter_image_mcp"
description: "Core package: the command line, the shared tool helpers, the image pipeline (catalog, client, imaging, service, outputs), the advisor, preferences and onboarding, and the SQLite/Postgres storage seam the hosted server builds on."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/."
---

**Owns:**

- Entry points: `cli.py` (`serve-http`, `purge`, `active-jobs`, `export-local`; no command prints help). The MCP server itself is `hosted/app.py`.
- Tool helpers: `tool_support.py` (server name, `tool_errors` mapping to redacted `ToolError`, progress reporting) and `catalog_tools.py` (`list_image_models` / `get_image_model` and their tables).
- Image pipeline: `service.py` (validate, prepare, call, fit, hand to an `OutputSink`), `catalog.py` (live model catalog, parameter validation), `client.py` (OpenRouter image HTTP; `advisor_client.py` is the advisor's), `imaging.py`, `outputs.py` (encoding and names; the hosted `AssetSink` stores results).
- Credentials: `credentials.py` (the `CredentialSource` protocol every OpenRouter caller is given; there is no default), `keystore.py` (read-only access to the retired local key, for `export-local`).
- Advisor: `advisor*.py` (`advisor_shares.py`: answers the user shared from the viewer, the read and wait tools and the unread-share reminder middleware; `advisor_summary.py`: share summaries on the fast `SIDECAR_SUMMARY_MODEL`, answer only; `advisor_loop.py`: the function-call tool loop that replays output items in `input`, and puts the partial `items` and `cost_usd` on a `LoopStopped` or a mid-loop `OpenRouterError`; `advisor_client.py` also holds `container_file`, the size-capped sandbox file download, and raises `InsufficientCreditsError` for 402, `ModerationError` for a moderation-flagged 403 and `ForbiddenError` for any other 403, so the Slack bot can say the budget is used up), `headless.py`, `session_files.py`, `prompt_cache.py`.
- Preferences and onboarding: `preferences.py`, `preference_tools.py`, `onboarding.py`.
- Shared seams: `storage.py` (SQLite locally, pooled Postgres when hosted), `assets.py` (asset metadata plus blob store), `apps.py` (MCP App resources and per-client progressive enhancement, including `register_uploader`), `errors.py` (typed errors and `KEY_REJECTED`), `error_bodies.py` (reading OpenRouter error responses and moderation blocks, shared by both clients), `logs.py`, `config.py`, `tool_annotations.py` (MCP read-only/destructive/paid hints for every hosted tool).
- Migration: `local_bundle.py` (the `export-local` bundle: finds the retired local database, build, strict validation, content digest, preference seeding).

**Does not own:** multi-tenant HTTP serving, Auth0 and student key provisioning (`hosted/`); bundled app HTML (`ui/`, generated).

**Invariants:**

- Every tool has an entry in `tool_annotations.ANNOTATIONS`, and every entry names a hosted tool; the server calls `annotate()` last, and an unlabelled tool stops startup.
- Every advisor and preference query binds both the credential owner and the workspace.
- Errors and logs go through `logs.redact` / `redact_log`; keys, JWTs and signed links never reach stderr or model-visible text.
- Image tool results keep `structuredContent` small and model-safe (some hosts show it to the model). Preview JPEGs go only in the result `_meta["sidecarPreviews"]` (`apps.preview_meta`, a list index-aligned with `structuredContent.images`, `null` where there is no preview). `negotiate_apps` drops both for clients without Apps.
- Storage schema changes are additive (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) in code; there are no migration files.

**Tests:** flat `tests/test_<module>*.py`. Storage tests take the `db_target` fixture (SQLite, plus Postgres when `TEST_DATABASE_URL` is set). `tests/tool_harness.py` registers the advisor and preference tools on an in-memory server for tool-level tests.

**Gotchas:** `service.py`, `imaging.py`, `client.py` and `catalog.py` are over the 300-line ratchet. Extract into a sibling before adding to them.
