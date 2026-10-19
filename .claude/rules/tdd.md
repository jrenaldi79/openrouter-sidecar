---
globs: ['src/**', 'ui/src/**', 'scripts/**']
---

# MANDATORY: Test-Driven Development (TDD) First

**EVERY feature request MUST start with writing tests before any implementation.**
**EVERY bug fix MUST start by reproducing the bug in a failing test.**

When receiving ANY feature request, your FIRST response should be:

1. "Following TDD - I'll write tests first to define what success looks like"
2. Write failing tests using the Red-Green-Refactor cycle
3. Only then proceed with implementation to make tests pass

When receiving ANY bug report, your FIRST response should be:

1. "Reproducing the bug first - writing a test that triggers the exact error"
2. Write a test that demonstrates the reported failure
3. Run it and confirm it FAILS (proves you captured the bug)
4. Only then write the fix

## Where the test goes

| Source | Test |
| --- | --- |
| `src/openrouter_image_mcp/<m>.py` | `tests/test_<m>.py` (or `tests/test_<m>_<topic>.py`) |
| `src/openrouter_image_mcp/hosted/<m>.py` | `tests/test_hosted_<m>.py` (or `..._<topic>.py`) |
| `ui/src/<app>.js` (with `<app>.html`) | `ui/tests/<app>.spec.js` (or `<app>-<topic>.spec.js`) |
| `scripts/checks/<m>.py` | `tests/test_checks_<m>.py` |

`scripts/dev/` has no unit tests by design; the synthetic e2e (`docs/E2E_LOCAL.md`) exercises it.

## TDD Process - ALWAYS FOLLOW

1. **Red**: write the test, run it alone and see it fail: `uv run pytest tests/test_x.py`
   (or `npm --prefix ui test -- tests/x.spec.js`).
2. **Green**: the simplest code that makes it pass; re-run the single file.
3. **Refactor**: clean up implementation and test without changing behavior; re-run.
4. **Finalize**: `uv run ruff check` and `uv run pytest`; add `uv run pytest -m pg` with
   `TEST_DATABASE_URL` set when you touched storage; `npm --prefix ui run build` + `npm --prefix ui test`
   when you touched `ui/`.
5. **Docs**: files added, removed or renamed -> `node scripts/self-documentation/generate.js`, review,
   stage. Update the folder's CLAUDE.md note when what it owns changes.

## Enforcement

`scripts/checks/missing_tests.py` (pre-commit) blocks a commit that **adds** a module above without a
matching test. Dunder modules, `scripts/`, `tests/` helpers and ui helpers without an app page are exempt.
`uv run python -m scripts.checks.missing_tests --all` lists the existing backlog.

**Red Flags — STOP immediately if:**

- Creating modules before their tests
- Editing source/config files before a reproduction test exists for a bug fix
- Theorizing about root cause without confirming it with a failing test
- Merging a fix that was never verified against the actual error

## When TDD Can Be Skipped

Only for documentation-only changes (`*.md`, skill text) and simple refactors with existing coverage.
Bug fixes are NEVER exempt.
