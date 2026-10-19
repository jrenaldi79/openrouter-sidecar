---
title: "ui/tests"
description: "Playwright specs for the MCP Apps (preview, advisor, settings, uploader), one or more <app>*.spec.js per app, with mock MCP hosts in fixtures/; gated in CI."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in ui/tests/."
---

**Owns:** `<app>*.spec.js` specs and `fixtures/<app>-host.js` mock hosts that drive the built bundles through the ext-apps protocol.

**Run:** `npm --prefix ui run build && npm --prefix ui test`. Use Playwright's pinned Chromium (`npx playwright install chromium`). A local Chrome via `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` misses clicks near the bottom edge of the 720px iframe viewport and produces false failures.

**Invariants:** specs load the generated bundles, so build first. A new app needs a spec here (enforced by `scripts/checks/missing_tests.py`).

**Gotchas:**

- The CI `ui` job runs the full suite with `CI` set. `playwright.config.js` then ignores the `*-screenshots.spec.js` review captures.
- Race-free host messages: assert with `expect.poll` or wait for the message rather than reading `hostMessages` right after a click. `fixtures/advisor-host.js` supports `hold: name` plus `window.release(name)` to pin a poll.
- Key presses run in macOS Chromium too: `Control+End` does nothing there, so use `End`.
- `*-screenshots.spec.js` capture review images rather than assert behavior.
