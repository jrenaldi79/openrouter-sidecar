---
title: "2026-10-21-slack-admin: task 4 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 4 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 4 (Wave A): Design system copy and views

**Files:**
- Create: `hosted/admin/public/` (copied from `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `src/admin/public/` and `assets/brand/`: `tokens.css`, `fonts.css`, `shell.css`, `admin.css` reduced to its generic rules — `.muted`, `.error`, `.inline`, `.htmx-error`, `button.link` — `theme-instrument.css` without the Overview-only selectors, `theme-instrument-depth.css` without the pipeline rules, `admin.js`, `fonts/*.woff2` with the two OFL files, `brand/mpd-logo.png`, `brand/favicon.ico`), `hosted/admin/templates/` (`layout.html`, `channels.html`, `channel_form.html`, `log.html`, `message.html`), `hosted/admin/views.py`, folder notes for `public/`, `public/fonts/`, `public/brand/` and `templates/` (each records the source commit and "change it in mpd-class-mcp first").
- Modify: `pyproject.toml`, `uv.lock` (`uv add jinja2`).
- Test: `tests/test_hosted_admin_views.py`.

**Interfaces:**
- Produces: `Views()` with `page(name: str, *, session, **context) -> HTMLResponse` (layout pages; `session` has `.email` and `.csrf_token`) and `message(text: str, *, session=None, status: int = 200) -> HTMLResponse` (standalone page, the Coach `message.ejs` shape); `security_headers(issuer: str) -> dict[str, str]` (the spec's CSP and headers; pinned Pico and htmx URLs with SRI hashes from the Coach's `layout.ejs`); `STATIC_FILES = re.compile(r"^(?:[\w.-]+\.(?:css|js)|fonts/[\w-]+\.woff2|brand/[\w.-]+\.(?:png|ico))$")`; `PUBLIC_DIR: Path`.
- Templates mirror `views/layout.ejs`: skip link, rail "MPD Admin · Sidecar" with Channels and Change log, email and Sign out form in `rail-foot`, `main#main.main.page`, `#htmx-error`. Every POST form has `<input type="hidden" name="_csrf">`; `<body hx-headers>` carries `x-csrf-token`. `channel_form.html` serves register and edit: label and cohort read-only when editing; model selects from a `models` list grouped by family; members as checkboxes, or a one-id-per-line textarea when `members` is None; hidden fields with the rendered values; a "Setup didn't finish" notice and Finish setup button when the row is off with `SETUP_UNFINISHED`; Turn off (reason field) and Turn on as POST forms with `hx-confirm`.

- [ ] **Step 1: Failing tests:** `test_layout_shell_and_nav`, `test_templates_escape_slack_text` (Review Focus 5), `test_security_headers_exact`, `test_static_allowlist`, `test_message_page_standalone`, `test_status_plain_text` ("On" / "Off: guest joined"; no `mark` or `.pill`), `test_form_register_vs_edit`, `test_finish_setup_shown`.
- [ ] **Step 2–4:** Run, copy and write, run. `uv run python -m scripts.checks.file_sizes --all` shows no new file over 300 lines.
- [ ] **Step 5: Commit** `feat(admin): Coach design system and page templates`.

