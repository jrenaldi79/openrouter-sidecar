---
title: "ui/src"
description: "MCP App sources (ESM): one <app>.html + <app>.js per app (preview, advisor, settings, uploader) with shared helpers, bundled by ui/build.mjs into src/openrouter_image_mcp/ui."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in ui/src/."
---

**Owns:**

- Apps: `preview.*` (image preview), `advisor.*` (advisor results and handoff), `settings.*` (preferences and onboarding), `uploader.*` (hosted drop-zone: gets a single-use link through `create_image_upload` and PUTs bytes straight to the Sidecar origin).
- Helpers: `markdown.js` (marked, plus DOMPurify sanitizing, with raw HTML escaped), `provider-logos.js`, and the advisor's `advisor-progress.js` (live running state, elapsed time, poll backoff) and `advisor-handoff.js` (share text framing, model-context update, copy fallback, More menu).

**Invariants:**

- Apps talk to the host only through `@modelcontextprotocol/ext-apps`. The single exception is the uploader's PUT to its single-use upload URL, which is limited by CSP `connect-src` to the Sidecar origin. Apps never see bearer tokens.
- Untrusted text renders through `renderMarkdown` or `textContent`, never `innerHTML`.
- The advisor viewer never calls `app.sendMessage` (`ui/message`): hosts ask the user to approve it. Sharing uses `app.updateModelContext`, or copy when the host lacks it.
- A new app needs `<app>.html` and `<app>.js`, its name in the list at the end of `ui/build.mjs`, a spec, and a host fixture.

**Tests:** `ui/tests/<app>*.spec.js` (Playwright). Build first.

**Gotchas:** `advisor.js` is over the 300-line ratchet. Extract helpers before adding to it.
