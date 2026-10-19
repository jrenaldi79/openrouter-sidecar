---
globs: ['ui/**', 'src/openrouter_image_mcp/ui/**']
---

# MCP App UI (ui/)

- Sources are ESM in `ui/src/` (`<app>.html` + `<app>.js`, shared helpers alongside). `ui/build.mjs`
  (esbuild) bundles each app into one self-contained `src/openrouter_image_mcp/ui/<app>.html`.
- **Never edit the generated bundles.** Edit `ui/src`, run `npm --prefix ui run build`, stage both.
  The pre-commit gate (`scripts/checks/ui_build.py`) rebuilds when ui files are staged and blocks
  unstaged or untracked bundle output.
- A new app needs its name in the bundle list at the end of `ui/build.mjs`, a Playwright spec and a host fixture in `ui/tests/fixtures/`.
- Sanitize any rendered Markdown through DOMPurify (`markdown.js`); never assign untrusted HTML.
- Dependencies: `npm --prefix ui ci` from the lockfile; pin versions you add.
