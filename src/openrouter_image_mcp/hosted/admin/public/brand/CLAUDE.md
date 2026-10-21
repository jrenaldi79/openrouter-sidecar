---
title: "src/openrouter_image_mcp/hosted/admin/public/brand"
description: "The MPD logo and favicon the admin rail and browser tab show, copied from mpd-class-mcp and served at /admin/public/brand."
type: "guidance"
status: "maintained"
read_when: "Replacing the admin logo or favicon."
---

**Source:** `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `assets/brand/` (`mpd-logo.png`, `favicon.ico`). **Change them in `mpd-class-mcp` first**, then copy them again here.

**Owns:** `mpd-logo.png` (the rail's brand mark, drawn at 36px) and `favicon.ico` (linked from every page).

**Invariants:** served without a session only as `brand/<name>.png|ico` (`views.STATIC_FILES`); the CSP's `img-src 'self'` lets them load. The Coach's `apple-touch-icon.png` is not copied.
