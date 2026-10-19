---
title: "src/openrouter_image_mcp/ui"
description: "Generated MCP App bundles: one self-contained HTML file per app, built from ui/src by ui/build.mjs, (preview, advisor, settings, uploader), plus the Northwestern logo. Never edit by hand."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/ui/."
---

**Owns:** the `<app>.html` bundles served as MCP App resources by `apps.py` and the hosted server, and `mpd-logo.png`.

**Invariants:**

- Each bundle is the output of `npm --prefix ui run build`. Edit `ui/src/`, rebuild, and stage both.
- The pre-commit gate (`scripts/checks/ui_build.py`) and the CI `ui` job rebuild and fail on unstaged, untracked or stale bundles.
- Bundles are exempt from the line-size ratchet and the secret scan. Each is 650–780 KB.

- `uploader.html` carries a `__SIDECAR_ORIGIN__` placeholder that `apps.register_uploader` replaces with the server origin for CSP `connect-src`. Keep the placeholder in `ui/src/uploader.html`.

**Tests:** Playwright specs in `ui/tests/` load these bundles. Python tests cover how they are served (`tests/test_hosted_apps.py`, `tests/test_apps.py`).
