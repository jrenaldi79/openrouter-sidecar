---
title: "Task 19 report: Socket Mode gateway"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 19 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 19 report: Socket Mode gateway

Status: DONE.

## What was built
- `src/openrouter_image_mcp/hosted/slack/gateway.py` (180 lines): `EventLog` and `Gateway`.
- `tests/test_hosted_slack_gateway.py` (282 lines, 29 tests plus pg variants), written first and seen failing (collection error: no module), then green.
- `hosted/slack/CLAUDE.md` updated (Owns, Tests, description); `docs/index.md` regenerated.

## Behaviour
- `EventLog.first_time`: `INSERT ... ON CONFLICT DO NOTHING`, `rowcount == 1`; `purge(now, older_than_s=86400)`. Table `slack_events(event_id PRIMARY KEY, received_at REAL NOT NULL)`; the storage layer adds `rowid` on Postgres.
- `on_request`: ack first always (even invalid or non-events_api; ack failure logged, handling continues); validate `event_id`/`team_id` non-empty strings and `event` dict; dedupe (via `asyncio.to_thread`); filter `bot_id`, `edited`, `bot_message`/`message_changed`/`message_deleted`; route to a task held in `_tasks`, logged by type via `redact_log`.
- Routing: `app_mention`, `member_joined_channel` (needs channel and user), `channel_shared`, `member_left_channel` by the bot, and `channel_left`, both calling `registry.deactivate(team, channel, "bot removed")`.
- `start()` creates the client with `app_token=`/`web_client=`, appends the listener, starts the purge loop (runs at start, then every `purge_interval_s`), and connects. `stop()` is idempotent and safe before start: cancel purge, disconnect, wait up to `grace_s` (5 s) for tasks then cancel, close.
- Fake client matches slack_sdk 3.45.0 `socket_mode.aiohttp.SocketModeClient` (`socket_mode_request_listeners`, `send_socket_mode_response`, `connect`, `disconnect`, `close`) and `SocketModeRequest`/`SocketModeResponse` are the real classes.

## Decisions to note
- Dedupe DB failure fails open (event handled, logged); a lost mention seemed worse than a rare repeat on the single owner replica.
- `stop()` disconnects before draining so no new events arrive during the drain.
- `# noqa: BLE001` on four deliberate broad catches (ack, dedupe, purge, socket step).

## Verification
- ruff: clean. Full `uv run pytest`: 1581 passed, 550 skipped. With `TEST_DATABASE_URL`: full run 2109 passed, 22 skipped; `-m pg`: 529 passed.
- docs generator ran; no unclassified docs.

## Fix round 1
- `stop()` now sets `_stopping` first (on_request still acks, drops everything, re-checked after the dedupe await), calls `client.close()` (disables auto-reconnect and disconnects; no separate `disconnect()`), cancels the connect and purge tasks, then drains handlers (5 s, then cancel). Regression tests: late event during drain and event delivered inside `close()` are acked, never handled, and no task survives `stop()`.
- `start()` runs `connect()` as a held task and returns promptly; connect errors are logged by type; a second `start()` is a logged no-op; restart after stop works. Documented in the module docstring and the folder note (the owner-lock gain hook can await `start()`).
- Dedupe: bounded in-memory set of the last 1,000 ids is checked before the database; a DB error still fails open, but a repeat is dropped from memory.
- Tests added: threaded `first_time` race (one winner), fail-open with `first_time` raising, same envelope twice with DB failing handled once, recent-id bound, `caplog` type-only handler logging, meaningful malformed-routed-event test, real `SocketModeClient` built via `Gateway._new_client()` without connecting, never-returning connect.
- Nits: `_NOT_EDITS` -> `_DROPPED_SUBTYPES`, `create_task`. Tests split to keep files under 300 lines: `tests/slack_gateway_rig.py` (fakes), `test_hosted_slack_gateway.py`, `test_hosted_slack_gateway_lifecycle.py`.
- Verification: ruff clean; full pytest 1593 passed, 558 skipped; with `TEST_DATABASE_URL` full 2129 passed, 22 skipped; `-m pg` 537 passed; gateway tests both ways green.
