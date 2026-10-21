---
title: "src/openrouter_image_mcp/hosted/slack"
description: "Slack Sidecar: the @sidecar bot inside the hosted server (Socket Mode). Currently its environment settings; the bot modules land here."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/slack/."
---

**Owns:**

- Configuration: `settings.py` (`SlackSettings`, `load_slack_settings`; off unless `SIDECAR_SLACK_ENABLED` is set, then both tokens are required and their prefixes checked). `HostedSettings.slack` carries the result.

**Invariants:**

- Identity comes only from the signed Slack event, never from message text or a caller-chosen field.
- Every query binds both the owner and the workspace.
- Slack never calls `AdvisorService.send`.
- `SLACK_BOT_TOKEN` (`xoxb-`) and `SLACK_APP_TOKEN` (`xapp-`) come only from the environment, stay out of `repr`, and are masked by `logs.redact`. The secret scan blocks literals.

**Tests:** `tests/test_hosted_slack_<module>*.py` (enforced by `scripts/checks/missing_tests.py`). Build token fixtures at runtime (`"xoxb-" + "7" * 20`); the secret scan covers tests.

**Gotchas:** `settings.py` imports `_TRUE` from `hosted/settings.py` inside the function, because `hosted/settings.py` imports this package at module load.
