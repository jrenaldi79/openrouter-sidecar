---
title: "src/openrouter_image_mcp/hosted/admin/public"
description: "Static assets for the /admin page: the Interview Coach admin design system (fonts.css, tokens.css, shell.css, admin.css, theme-instrument*.css, admin.js), copied from mpd-class-mcp and served without a session at /admin/public."
type: "guidance"
status: "maintained"
read_when: "Changing admin styles or copying a newer version of the design system."
---

**Source:** `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `src/admin/public/`. **Change the design system in `mpd-class-mcp` first**, then copy it again here and update the commit above. Don't restyle it from this repo.

**Owns:**

- `fonts.css`, `tokens.css`, `shell.css` and `admin.js`: byte-identical to the source. `tokens.css` is the one place a colour, font, size, radius, border or shadow is defined; every later stylesheet reads it through a custom property, and class names say what a thing is, never how it looks.
- `admin.css`: only the generic rules (`.muted`, `.error`, `.warning`, `.inline`, the table-cell form reset, `form.logout`, `button.link`, `.htmx-error`).
- `theme-instrument.css` and `theme-instrument-depth.css`: the source files with the Coach-only rules removed, every other line unchanged. Removed: the Overview page head, panels, roster table, pipeline band and route, failed-today strip, job status marks, `.tbl` and jobs tables, limit pips, and the student-portal link in the rail. A few selectors in the shared "figures are mono" list name dropped classes; they match nothing and stay so the line is unchanged.
- `fonts/` (the typefaces and their licences) and `brand/` (the logo and favicon), each with its own note.

**Not copied:** `console.css`, `console-tables.css`, the Overview, the pipeline, job status marks, the diff view and avatars.

**Invariants:**

- Served by the admin routes at `/admin/public/<path>` with no session, only for paths matching `views.STATIC_FILES` (`*.css`, `*.js`, `fonts/*.woff2`, `brand/*.png|ico`). This note and the font licences are never served, so never put data or secrets in this folder.
- The CSP (`views.security_headers`) allows `style-src 'self'`, so every style lives here. Templates carry no inline `style`, `<style>`, `<script>` or `on*` handlers; `admin.js` hangs behaviour off data attributes (`[data-menu]`).
- `admin.js` shows failed htmx requests in the layout's `#htmx-error` with `textContent`, adds the `js` class to `<html>` and opens the rail's menu under 900px.
- Files are cached for an hour: hard-reload after a CSS change.

**Tests:** `tests/test_hosted_admin_views.py` (the allowlist, and that every asset the layout links exists).
