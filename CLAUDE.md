# CLAUDE.md

Guidance for Claude Code in **OpenRouter Sidecar** (`openrouter-image-mcp`): image generation, editing
and side-advisor chats for Northwestern AI classes. It runs as a hosted multi-tenant remote MCP on
Railway (`serve-http`) for claude.ai, Claude Desktop and Claude Code. The local stdio server was retired on
2026-10-19; only `export-local` remains, to move a student's old local chats to the hosted server.
Folder-level `CLAUDE.md` notes load automatically when you work in a folder; read them before editing.

## Stack

- **Server:** Python 3.12+ with `uv`, one root `pyproject.toml` (`mcp`, `httpx`, `pillow`, `pyjwt`, `psycopg`,
  `uvicorn`; `keyring` only so `export-local` can read the retired local key). SQLite in tests and the local
  e2e stack, Postgres in production. The Dockerfile builds the hosted image.
- **MCP Apps:** ESM in `ui/` with its own `package.json` (esbuild, `@modelcontextprotocol/ext-apps`,
  Playwright). `ui/build.mjs` bundles each app into `src/openrouter_image_mcp/ui/<app>.html` (generated).
- **Skills:** `src/openrouter_image_mcp/hosted/skills/` is served to connector clients; `skills/` (the plugin's
  source) holds byte-identical copies, and `plugin/skills/` is kept in step by `scripts/sync_plugin_skills.py`.
- `reference/` holds vendored upstream code and is excluded from ruff and the checks.

## Essential commands

```bash
uv sync                                    # install / update the environment
uv run pytest                              # unit suite (live tests deselected)
uv run pytest tests/test_x.py              # one file while iterating
TEST_DATABASE_URL=postgresql://... uv run pytest -m pg   # Postgres-only tests
uv run ruff check                          # lint
npm --prefix ui ci                         # ui dependencies (once)
npm --prefix ui run build                  # rebuild MCP App bundles; stage the output
npm --prefix ui test                       # Playwright; install its pinned Chromium, not local Chrome
node scripts/self-documentation/generate.js          # refresh docs/index.md after adding/moving files
uv run python -m scripts.checks.missing_tests --all  # modules still lacking a test
uv run python -m scripts.checks.file_sizes --all     # files over the 300-line limit
```

One-time per clone, enable the git hooks: `git config core.hooksPath .githooks`.

## Quality gates

| Gate | Rule | Enforced by |
| --- | --- | --- |
| Secrets | No keys, tokens, JWTs, signed links or non-local DB passwords, tests included | `scripts/checks/secret_scan.py` (pre-commit) |
| TDD | Big features only (`.claude/rules/tdd.md`); a new module ships with its test (`tests/test_<m>.py`, `test_hosted_<m>.py`, `ui/tests/<app>*.spec.js`). Small changes just run the existing suites | `scripts/checks/missing_tests.py` (pre-commit) |
| File size | 300-line ratchet: new files capped, oversized files may not grow | `scripts/checks/file_sizes.py` (pre-commit) |
| Docs | Index and AUTO block current; every source folder has a note | `generate.js --staged` (pre-commit), CI `docs` |
| Bundles | Generated MCP App HTML matches `ui/src` | `scripts/checks/ui_build.py` (pre-commit), CI `ui` |
| UI behavior | Playwright suites pass (review captures skipped) | CI `ui` |
| Lint and tests | `ruff check`, `pytest` | pre-commit; CI on Windows, macOS and Linux |
| Postgres | `pytest -m pg` | pre-push when `TEST_DATABASE_URL` is set; CI `postgres` |

Path-scoped rules live in `.claude/rules/` (TDD, code quality, testing, Python, ui).

## Critical gotchas

- **Never edit `src/openrouter_image_mcp/ui/*.html`.** They are generated; edit `ui/src` and rebuild.
- **OpenRouter HTTP lives in a few seams:** `client.py` (images), `advisor_client.py` (advisor) and
  `hosted/openrouter_keys.py` (management API for student keys and guardrails, plus `GET /key`). Tests stub them
  with `respx` or the harness fake; don't add httpx calls elsewhere.
- **Student keys are provisioned, not linked:** `OPENROUTER_MANAGEMENT_KEY` lets the server create one key per
  principal and cohort on first use (`hosted/provisioning.py`, middleware before tool calls), sealed per principal in
  `student_keys`. `HostedCredentials.resolve()` stays a synchronous lookup; never provision inside it. The management
  key is never used for inference, logged or returned. Instructors manage keys with `scripts/ops/openrouter_keys.py`.
- **Keys never leak:** keys are vault-encrypted per principal; nothing writes to the OS keyring (`keystore.py` only
  reads the retired local key for `export-local`). Errors and logs pass through `logs.redact`. Tests pass keys through
  the `keys` fixture (`helpers.KeyBox`); `memory_keyring` exists only for the export tests.
- **Schema changes are additive in code** (`IF NOT EXISTS`); there is no migrations folder. Storage tests run on
  SQLite and, with `TEST_DATABASE_URL`, Postgres via the `db_target` fixture.
- **Auth0 and class identity are managed outside this repo** (`../mpd-auth`); don't change them from here.
- **Hosted single owner:** one replica holds the advisory lock (`hosted/health.py`) for sweeps and background work.
- **Oversized files are grandfathered, not blessed:** before adding to `service.py`, `imaging.py`, `client.py` or
  `catalog.py`, extract a sibling module.

## Key docs

| Topic | File |
| --- | --- |
| Hosted operations (Railway, Auth0, deploy, rotation, incidents) | `docs/HOSTED_OPERATIONS.md` |
| Local synthetic end-to-end run | `docs/E2E_LOCAL.md` |
| Hosted design and decisions | `docs/superpowers/specs/2026-10-18-hosted-*.md` |
| Every doc, classified | `docs/index.md` (metadata in `docs/catalog.json`) |

## Documentation maintenance

Classify Markdown with flat frontmatter at the start of the document. All five fields are required one-line strings; use JSON-quoted values. Example:

```yaml
---
title: "Guide"
description: "Purpose of this document"
type: "guidance"
status: "maintained"
read_when: "When making related changes"
---
```

Types: guidance/status/reference/proposal/generated/history/temporary.
Statuses: maintained/snapshot/pending/completed/abandoned/superseded/temporary/generated.
Catalog `docs/catalog.json` records use the same fields for attachments or Markdown without frontmatter. Use the configured docs root when different.
Update authored prose in the same change as related behavior. Refresh after source or documentation changes, and check before committing.

Run `node scripts/self-documentation/generate.js` to refresh and add `--check` to validate.

<!-- AUTO:docs -->
<!-- prettier-ignore-start -->
[Documentation index](docs/index.md)
<!-- prettier-ignore-end -->
<!-- /AUTO:docs -->
