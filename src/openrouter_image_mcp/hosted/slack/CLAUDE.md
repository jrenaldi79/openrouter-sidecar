---
title: "src/openrouter_image_mcp/hosted/slack"
description: "Slack Sidecar: the @sidecar bot inside the hosted server (Socket Mode). Currently its environment settings; the bot modules land here."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in src/openrouter_image_mcp/hosted/slack/."
---

**Owns:**

- Configuration: `settings.py` (`SlackSettings`, `load_slack_settings`; off unless `SIDECAR_SLACK_ENABLED` is set, then both tokens are required and their prefixes checked). `HostedSettings.slack` carries the result.
- Slack I/O seam: `api.py` (`SlackApi` protocol, `SdkSlackApi` over slack_sdk's async client, `SlackMessage`/`SlackFile`/`Page`, `SlackCallError` with `retry_after_s`). Nothing else in the bot talks to Slack; tests use `FakeSlack` from `tests/slack_fakes.py`, which must keep the protocol's signatures.
- Channel registry: `registry.py` (`ChannelRegistry` over the `slack_channels` table, `ChannelRow`, `new_row`, `DEFAULT_ALIASES`). One row per (Slack team, channel); `deactivate`, `mark_welcomed` and `mark_warned` are guarded updates that return True only for the caller that changed the row.
- Team identity and credentials: `principal.py` (`team_principal` builds the `slack|<team>:<channel>` principal only from the signed event's ids plus an active registry row; `acting_as` sets a module-private context variable and always resets it; `SlackCredentials` is the `CredentialSource` for the team in context). Team keys are created by `KeyProvisioner.ensure(principal, team=TeamKey(label))`, named `Sidecar · <label> · <cohort>` and budgeted by the `Sidecar team budget` guardrail (no guardrail, no key). A Slack principal without `team`, or a student with one, is refused.
- Job queries: `jobs.py` (`SlackJobs` over the Slack columns that `advisor_results.py` adds to `advisor_jobs`: `spend_by_user`, `spend_since`, `running`, `mark_posted`, and `orphans` for restart recovery). Per-team caps reach `Limits.admit(scope, max_concurrent=, daily_usd=)` from the registry row; the per-Slack-user request window is `Limits.window(...)`, independent of the per-minute budget.

**Invariants:**

- Identity comes only from the signed Slack event, never from message text or a caller-chosen field.
- Every query binds both the owner and the workspace. The one exception is `SlackJobs.orphans`, a server-side restart sweep filtered by `slack_channel_id` and `process_id`; its caller must act on each row under that row's own team principal.
- Slack never calls `AdvisorService.send`.
- `SLACK_BOT_TOKEN` (`xoxb-`) and `SLACK_APP_TOKEN` (`xapp-`) come only from the environment, stay out of `repr`, and are masked by `logs.redact`. The secret scan blocks literals.

**Tests:** `tests/test_hosted_slack_<module>*.py` (enforced by `scripts/checks/missing_tests.py`). Build token fixtures at runtime (`"xoxb-" + "7" * 20`); the secret scan covers tests.

**Gotchas:** `settings.py` imports `_TRUE` from `hosted/settings.py` inside the function, because `hosted/settings.py` imports this package at module load.
