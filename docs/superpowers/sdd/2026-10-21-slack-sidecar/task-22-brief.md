---
title: "2026-10-21-slack-sidecar: task 22 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 22 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 22: Ops scripts and manifest

**Files:**
- Create: `scripts/ops/slack_channels.py`, `scripts/ops/slack_usage.py`, `deploy/slack/manifest.yaml`,
  `deploy/slack/CLAUDE.md` (only if `generate.js` requires a note for `deploy/`; it isn't a source root, so check
  with `--check`)
- Modify: `scripts/ops/CLAUDE.md` (Owns gains both scripts)

**Interfaces:**
- Consumes: `ChannelRegistry`, `new_row`, `channel_problem`, `SdkSlackApi`, `usage_report`, `usage_text`.
- `slack_channels.py` subcommands:
  - `add --team-id --channel --label --cohort --fusion-panel a,b,c --fusion-analyst --subagent-model [--default-model gemini] [--alias k=v …] [--fetch-allow d …] [--daily-cap 4] [--max-concurrent 3] [--invoker U…]`
  - `update <label> [same options] [--active]`
  - `deactivate <label> --reason`
  - `list`
- `add` and `update --active` run `channel_problem` first and refuse with its reason.
- The scripts read `DATABASE_URL` and `SLACK_BOT_TOKEN` from the environment and print no secrets.
- `slack_usage.py --team <label>` prints `usage_text`.
- Manifest (YAML, `_metadata.major_version: 2`):
  - `socket_mode_enabled: true`, `org_deploy_enabled: false`
  - the spec's 11 bot scopes, exactly
  - bot events `app_mention`, `member_joined_channel`, `member_left_channel`, `channel_shared`
  - no `request_url` and no `message.*` events

No unit tests by design (`scripts/ops/CLAUDE.md`). The logic they call is tested in Tasks 2, 9 and 16.

- [ ] **Step 1: Write the scripts and the manifest.**
- [ ] **Step 2: Smoke-test.** `uv run python scripts/ops/slack_channels.py --help` and `list` against a temp SQLite
  `DATABASE_URL` both exit 0.
- [ ] **Step 3: Validate the manifest** with `uv run python -c "import yaml,sys; yaml.safe_load(open('deploy/slack/manifest.yaml'))"`.
  If PyYAML is absent, paste it into Slack's manifest validator during Task 24 instead.
- [ ] **Step 4: Regenerate docs, then commit** `feat(slack): ops scripts and app manifest`.

