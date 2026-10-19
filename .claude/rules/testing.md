---
globs: ['tests/**', 'ui/tests/**']
---

# Testing Patterns

## pytest (`tests/`, flat)

- One file per module: `test_<module>.py`; hosted modules `test_hosted_<module>.py`; topic splits
  `test_<module>_<topic>.py`. Shared helpers (`helpers.py`, `hosted_harness.py`, `jwt_helpers.py`,
  `conftest.py`) are not tests.
- Markers: `live` hits real OpenRouter and is deselected by default; `pg` needs `TEST_DATABASE_URL`
  (disposable Postgres). The `db_target` fixture runs storage tests on SQLite and, when the URL is set,
  Postgres too.
- Pass OpenRouter keys through the `keys` fixture (`helpers.KeyBox`, `credentials=keys.credentials(ws)`).
  `memory_keyring` swaps in an in-memory keyring for the `export-local` tests; never touch the real OS keyring.
- HTTP goes through `respx` or the fakes in `scripts/dev/`; never the real network outside `live`.
- Run one file while iterating: `uv run pytest tests/test_x.py`.

## Playwright (`ui/tests/`)

- `<app>*.spec.js` per MCP App, with host fixtures in `ui/tests/fixtures/`.
- Build first (`npm --prefix ui run build`); specs load the bundles.
- Use Playwright's pinned Chromium (`cd ui && npx playwright install chromium`); a local Chrome via
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` misses clicks near the bottom edge of the iframe viewport.
- CI (`CI=1`) runs the whole suite except the `*-screenshots.spec.js` review captures.
- Wait for host messages and polls instead of reading them right after a click; the advisor fake host's
  `hold: name` + `window.release(name)` pins a poll in place. Use `End`, not `Control+End` (a no-op on macOS).

## Fixtures and secrets

The secret scan covers tests. Build tokens, JWTs and vault keys at runtime, or use obviously synthetic
values (one repeated character, very short).

## Checklist

- [ ] Test written first and seen failing
- [ ] Error paths and limits covered (sizes, expiry, auth failures, retries)
- [ ] No inter-test dependencies, real network or wall-clock sleeps
