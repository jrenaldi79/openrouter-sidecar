---
title: "scripts/ops"
description: "Operator tools for a deployed sidecar: a real Auth0 PKCE login, a no-cost probe of the live server, the instructor's student OpenRouter key manager, and the Slack channel and usage tools."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in scripts/ops/, or running acceptance against a deployment."
---

**Owns:**

- `real_login.py`: the authorization-code + PKCE flow an MCP client runs, using the shared public client "MPD MCP Clients" and its registered `http://localhost:33418/callback`. Saves the access token to `.dev-state/real-token` (mode 600) and prints only the audience, permissions and whether the profile claim is present. `--prompt-login` forces the account chooser.
- `acceptance_probe.py`: checks a deployment with that token: 401 without a token, admission (403 means the account lacks `sidecar:use` or the profile claim), the hosted tool surface, `account_status` (which issues the signed-in account's own key on first use), and an image upload and download (proves the asset volume is writable). `--expect-forbidden` checks only that an account without access is refused.
- `openrouter_keys.py`: the instructor's student-key manager (`list`, `show`, `disable`, `enable`, `delete --yes`, `usage --csv`, `guardrails`) over the server's own seam (`hosted/openrouter_keys.py`). Reads `OPENROUTER_MANAGEMENT_KEY` from the environment. `delete` is a reset (the student gets a fresh key on the next call); `disable` blocks.
- `slack_channels.py`: the instructor's Slack channel registry (`add`, `update <label> [--active]`, `deactivate <label> --reason`, `list`) over `ChannelRegistry`. `add` and `update --active` call `channel_problem` with an `SdkSlackApi` on `SLACK_BOT_TOKEN` and refuse with its reason (a guest member or an outside-shared channel). `--default-model` and repeatable `--family-default family=model-id` are the team's model preferences: they are checked against the catalog and saved through the server's own `PreferenceService` (`wiring.cohort_services`) acting as the team principal, which creates the team key first when it is missing (so they need `VAULT_KEYS` and `OPENROUTER_MANAGEMENT_KEY`). `--alias k=v` adds to the channel's aliases (`v` is a brand or an exact model id), `--default-alias` sets the alias used when a mention names none. `update` changes only the options given.
- `slack_usage.py`: `--team <label>` prints the same text as `@sidecar usage` (`usage_report`, `usage_text`) from the team's key (`VAULT_KEYS`) and the job rows; names come from Slack when `SLACK_BOT_TOKEN` is set, else ids.
- `slack_common.py`: what both Slack scripts share (environment, the key store, the Slack API, the model-default seeder, and `run_script`, which turns a known failure into one line on stderr).

**Invariants:** these reach the real Auth0 tenant, the deployed server or the OpenRouter management API, so a person runs them deliberately. They never print or log tokens or keys (management or student) and never call a paid model. Only `account_status` (probe) and the server create student keys, and `slack_channels.py` creates a team's key when it seeds model defaults. Nothing here ships in the package.

**Tests:** none by design. They are operator tools run by hand during acceptance (`docs/HOSTED_OPERATIONS.md`).

**Gotchas:** the token lives 10 minutes; delete `.dev-state/real-token` after use. If the browser already has an Auth0 session for another account (e.g. Google), run `real_login.py --prompt-login`.
