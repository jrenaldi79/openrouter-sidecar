---
title: "2026-10-21-slack-admin: task 9 brief given to the implementer subagent"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): task 9 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 9 (Wave C): Docs and the Auth0 request

**Files:** Modify `docs/HOSTED_OPERATIONS.md` (an "Admin page" section: what it is, the three variables, local runs, turning it off, and "Auth0 setup (done in mpd-auth)": the MPD Sidecar Admin regular web client, RS256 with the `auth0 api patch` line, passwordless email as its only connection, callbacks and logout URLs for production and `http://localhost:8000`, client grants to both Sidecar APIs, no change to other clients or Actions), `hosted/CLAUDE.md` (Owns gains `admin/`), `hosted/admin/CLAUDE.md` (final), the root `CLAUDE.md` (one gotcha line: `/admin` signs in with the MPD Sidecar Admin client and needs `sidecar:admin`; Auth0 changes go through `mpd-auth`).

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Run** `node scripts/self-documentation/generate.js`, then `--check` → clean.
- [ ] **Step 3: Commit** `docs(admin): admin page operations`.

