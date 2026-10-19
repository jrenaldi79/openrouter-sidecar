---
globs: ['src/**', 'ui/src/**', 'scripts/**']
---

# Test-Driven Development: big features only

## When to use TDD

Use TDD (test first, see it fail, then implement) **only for big features**: a new module, MCP tool,
MCP App, HTTP route or storage table, or a behaviour change that spans several files.

**Don't write new tests for small changes:** copy and wording, tool descriptions, styling, assets
(logos, icons), config, renames, small bug fixes and one-file tweaks. Make the change, then run the
existing suites (Finalize below) so nothing regresses. Update an existing test only when the change
breaks it. A fix that gets complex enough to deserve a test counts as a big feature.

## Where the test goes

| Source | Test |
| --- | --- |
| `src/openrouter_image_mcp/<m>.py` | `tests/test_<m>.py` (or `tests/test_<m>_<topic>.py`) |
| `src/openrouter_image_mcp/hosted/<m>.py` | `tests/test_hosted_<m>.py` (or `..._<topic>.py`) |
| `ui/src/<app>.js` (with `<app>.html`) | `ui/tests/<app>.spec.js` (or `<app>-<topic>.spec.js`) |
| `scripts/checks/<m>.py` | `tests/test_checks_<m>.py` |

`scripts/dev/` has no unit tests by design; the synthetic e2e (`docs/E2E_LOCAL.md`) exercises it.

## TDD process (big features)

1. **Red**: write the test, run it alone and see it fail: `uv run pytest tests/test_x.py`
   (or `npm --prefix ui test -- tests/x.spec.js`).
2. **Green**: the simplest code that makes it pass; re-run the single file.
3. **Refactor**: clean up implementation and test without changing behavior; re-run.

## Finalize (every change)

- `uv run ruff check` and `uv run pytest`; add `uv run pytest -m pg` with `TEST_DATABASE_URL` set when
  you touched storage; `npm --prefix ui run build` + `npm --prefix ui test` when you touched `ui/`.
- Files added, removed or renamed -> `node scripts/self-documentation/generate.js`, review, stage.
  Update the folder's CLAUDE.md note when what it owns changes.

## Enforcement

`scripts/checks/missing_tests.py` (pre-commit) blocks a commit that **adds** a module above without a
matching test, since a new module is a big feature. Dunder modules, `scripts/`, `tests/` helpers and ui
helpers without an app page are exempt. `uv run python -m scripts.checks.missing_tests --all` lists the
existing backlog.
