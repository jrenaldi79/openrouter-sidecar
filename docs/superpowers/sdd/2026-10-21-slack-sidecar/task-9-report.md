---
title: "Task 9 report: guest and shared-channel guard"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 9 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 9 report: guest and shared-channel guard

Status: DONE. Commit 3314b02 `feat(slack): guest and shared-channel guard`.

## Implemented
`hosted/slack/guests.py` (about 95 lines, all functions under 50):
- `channel_problem(api, channel_id)`: sharing first (`is_ext_shared` / `is_pending_ext_shared` -> "shared outside the organization", no member scan), then any member with `is_restricted` / `is_ultra_restricted` -> "has a guest member", else None. `SlackCallError` propagates to the caller (ops script / guard). `SdkSlackApi.channel_info` and `.user` return the inner object, which is what the code reads.
- `GuestGuard(registry, api, *, every_s=3600.0, clock=time.monotonic)`:
  - `allowed(row)`: inactive -> False with no Slack call; cached within `every_s` -> True; problem -> deactivate + post, False; `SlackCallError` -> log (redacted), True, no cache write.
  - `on_member_joined`: ignores unregistered or inactive rows; guest -> deactivate with reason "guest joined". A failed `users.info` lookup is logged and drops that channel's cache entry so the next `allowed` re-checks the whole channel.
  - `on_channel_shared`: ignores unregistered; deactivates with reason "shared outside the organization".
  - Post of `GUEST_OFF` at top level (`thread_ts=None`) only when `deactivate` returns True; a failed post is logged, the deactivation stands.

## TDD
RED: `ModuleNotFoundError: No module named 'openrouter_image_mcp.hosted.slack.guests'` (collection error). GREEN: `16 passed, 10 skipped`; with Postgres, `26 passed`. Full suite `1061 passed, 376 skipped`; `ruff check` clean.

## Files
- src/openrouter_image_mcp/hosted/slack/guests.py (new)
- tests/test_hosted_slack_guests.py (new, 16 tests: brief's 7 plus pending-share, shared-skips-scan, both guest flags, join lookup failure, unregistered events, post failure, backstop deactivation, inactive no-call, fail-open retry)
- src/openrouter_image_mcp/hosted/slack/CLAUDE.md (Owns entry), docs/index.md (regenerated)

## Self-review
Brief and rulings covered; nothing extra beyond the two small robustness choices below. Tests assert behavior (registry state, posts, call counts, cache).

## Concerns
- Two choices beyond the rulings: post failure is swallowed after deactivation (otherwise the event handler would crash after the state change), and a failed join lookup invalidates the cache. Both are easy to drop.
- Guard cache is per-process memory; with multiple replicas each re-checks hourly (harmless).
- `channel_problem` scans members serially with one `users.info` call each; a large channel costs N calls on each hourly re-check (add path is one-off). Fine for class team channels.

## Fix round 1
Changes (`guests.py`, its tests, `hosted/slack/CLAUDE.md`):
1. Bounded fail-open: `allowed()` records only successful checks (`_verified`). Any exception from the check (`except Exception`, noqa BLE001 with reason) is logged via `redact` and returns True only if the last success was less than `2 * every_s` ago, else False; no success time is written on failure.
2. `on_member_joined` lookup failure adds the channel to `_recheck`. `allowed()` then skips the cache, runs the check, and denies on any failure regardless of the success window; a completed check (clean or problem) clears the mark, as does deactivation.
3. `allowed()` re-reads the row via `registry.get`; missing or inactive returns False with no Slack call.
4. Docs (module docstring, CLAUDE.md) say the ops script will call `channel_problem` and does not exist yet; CLAUDE.md describes the new behavior.
New tests: failure without prior success denies; failure open within 2h, denied after (success time not refreshed); failure retried next call; non-SlackCallError (KeyError) with and without prior success; failed join lookup then failing check denies; then clean check allows and clears the mark; then guest found deactivates; stale active row after deactivation denied with no Slack call; row missing from registry denied.
Commands/output:
- `uv run pytest tests/test_hosted_slack_guests.py -q`: 24 passed, 18 skipped
- Postgres run: 42 passed
- `uv run pytest -q`: 1069 passed, 384 skipped, 7 deselected
- `uv run ruff check`: All checks passed
