---
title: "ui/src"
description: "MCP App sources (ESM): one <app>.html + <app>.js per app (preview, advisor, settings, uploader) with shared helpers, bundled by ui/build.mjs into src/openrouter_image_mcp/ui."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in ui/src/."
---

**Owns:**

- Apps: `preview.*` (image preview), `advisor.*` (advisor results and handoff), `settings.*` (preferences and onboarding), `uploader.*` (hosted drop-zone: gets a single-use link through `create_image_upload` and PUTs bytes straight to the Sidecar origin).
- Styling: `accents.css` (Northwestern bright accents, the top brand strip and status pills), which `ui/build.mjs` injects at each app's `/* ACCENTS */` marker. Use the accents for status and highlights only, with `--ink-*` shades for text.
- Helpers: `markdown.js` (marked, plus DOMPurify sanitizing, with raw HTML escaped), `provider-logos.js`, and the advisor's `advisor-progress.js` (live running state, elapsed time, poll backoff), `advisor-handoff.js` (share text framing, status strings, model-context update, copy fallback, More menu) and `advisor-share.js` (one-click share flow: summary for answers over `FULL_ANSWER_LIMIT`, ownership recheck, delivery).

**Invariants:**

- Apps talk to the host only through `@modelcontextprotocol/ext-apps`. The single exception is the uploader's PUT to its single-use upload URL, which is limited by CSP `connect-src` to the Sidecar origin. Apps never see bearer tokens.
- The image preview reads its JPEGs from the tool result's `_meta.sidecarPreviews` (index-aligned with `structuredContent.images`), never from `structuredContent`, and accepts only strict `data:image/jpeg;base64,` URIs. claude.ai delivers results without `_meta`; then it uses the result's JPEG image content blocks, but only when there is exactly one per image.
- Untrusted text renders through `renderMarkdown` or `textContent`, never `innerHTML`.
- The advisor viewer never calls `app.sendMessage` (`ui/message`): hosts ask the user to approve it. Sharing saves the share on the server (`share_advisor_result`; the server builds the text from stored results) and Claude reads it with `read_shared_advisor_result`; `app.updateModelContext` is added best-effort, and copy is the fallback when saving fails. The viewer tells the user to tell Claude they shared it. The uploader still hands off through model context: claude.ai doesn't inject it, so the uploader tells the user to ask Claude to check the app.
- A new app needs `<app>.html` and `<app>.js`, its name in the list at the end of `ui/build.mjs`, a spec, and a host fixture.

**Tests:** `ui/tests/<app>*.spec.js` (Playwright). Build first.

**Gotchas:** `advisor.js` is close to the 300-line limit. Put new advisor behavior in a sibling helper (as `advisor-share.js` does).
