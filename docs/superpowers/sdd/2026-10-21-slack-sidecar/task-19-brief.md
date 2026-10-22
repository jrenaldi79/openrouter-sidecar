---
title: "2026-10-21-slack-sidecar: task 19 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 19 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 19: Gateway

**Files:** Create `hosted/slack/gateway.py`. Test `tests/test_hosted_slack_gateway.py`.

**Interfaces:**
- Consumes: `Dispatcher.on_mention`, `GuestGuard.on_member_joined`, `on_channel_shared`,
  `ChannelRegistry.deactivate`.
- Produces:
  - `EventLog(target)` has `first_time(event_id) -> bool` (`INSERT … ON CONFLICT DO NOTHING`, `rowcount == 1`) and
    `purge(now: float, older_than_s=86400.0) -> int`. Table: `slack_events(event_id PRIMARY KEY, received_at REAL)`.
  - `Gateway(app_token, web_client, dispatcher, guard, registry, events, *, bot_user_id, client_factory=SocketModeClient)`.
    It has async `start()` and `stop()`, and `async on_request(client, req) -> None`.

`on_request`:
1. Ack first with `SocketModeResponse(envelope_id)`.
2. Ignore anything that isn't `events_api`.
3. If `first_time(payload["event_id"])` is False, drop the event.
4. Drop events with `bot_id` or `subtype == "bot_message"`, and edits.
5. Route:
   - `app_mention` goes to `dispatcher.on_mention(payload["team_id"], event)` as a task;
   - `member_joined_channel` goes to `guard.on_member_joined`;
   - `channel_shared` goes to `guard.on_channel_shared`;
   - `member_left_channel` where `user == bot_user_id` goes to `registry.deactivate(…, "bot removed")`.
6. `start()` also runs an hourly `events.purge`. Exceptions in handlers are logged through `redact_log` and never
   escape.

- [ ] **Step 1: Write failing tests** with a fake socket client that records responses.
  - `test_ack_before_handling`: the ack is recorded even when the handler raises.
  - `test_dedupe_across_two_gateways` (with `db_target`): two `Gateway`s on one database and one envelope give
    exactly one `on_mention`.
  - `test_bot_messages_dropped`.
  - `test_routes_guest_and_shared_and_removed`.
  - `test_purge_expires_after_24h`.
  - `test_stop_closes_socket`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): Socket Mode gateway with Postgres dedupe`.

