---
title: "scripts/ops"
description: "Operator acceptance tools for a deployed sidecar: a real Auth0 PKCE login that saves a short-lived token, and a no-cost probe of the live server."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in scripts/ops/, or running acceptance against a deployment."
---

**Owns:**

- `real_login.py`: the authorization-code + PKCE flow an MCP client runs, using the shared public client "MPD MCP Clients" and its registered `http://localhost:33418/callback`. Saves the access token to `.dev-state/real-token` (mode 600) and prints only the audience, permissions and whether the profile claim is present. `--prompt-login` forces the account chooser.
- `acceptance_probe.py`: checks a deployment with that token: 401 without a token, admission (403 means the account lacks `sidecar:use` or the profile claim), the hosted tool surface, `account_status`, and an image upload and download (proves the asset volume is writable). `--expect-forbidden` checks only that an account without access is refused.

**Invariants:** these reach the real Auth0 tenant and the deployed server, so a person must sign in themselves. They never print or log tokens, never call a paid model and never create OpenRouter keys. Nothing here ships in the package.

**Tests:** none by design. They are operator tools run by hand during acceptance (`docs/HOSTED_OPERATIONS.md`).

**Gotchas:** the token lives 10 minutes; delete `.dev-state/real-token` after use. If the browser already has an Auth0 session for another account (e.g. Google), run `real_login.py --prompt-login`.
