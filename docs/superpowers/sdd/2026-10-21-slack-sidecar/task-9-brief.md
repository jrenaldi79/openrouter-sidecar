---
title: "2026-10-21-slack-sidecar: task 9 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 9 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 9: Guest and shared-channel checks

**Files:** Create `hosted/slack/guests.py`. Test `tests/test_hosted_slack_guests.py`.

**Interfaces:**
- Consumes: `SlackApi`, `ChannelRegistry`, `GUEST_OFF`.
- Produces:
  - `channel_problem(api, channel_id) -> str | None` returns `"shared outside the organization"` when
    `is_ext_shared` (or `is_pending_ext_shared`), `"has a guest member"` when any member's `users.info` has
    `is_restricted` or `is_ultra_restricted`, and None otherwise.
  - `GuestGuard(registry, api, *, every_s=3600.0, clock=time.monotonic)`, all async:
    - `allowed(row) -> bool` re-checks at most once per `every_s` per channel, caching in memory.
    - `on_member_joined(team_id, channel_id, user_id) -> None`
    - `on_channel_shared(team_id, channel_id) -> None`
  - Deactivating posts `GUEST_OFF` at the channel's top level only when `registry.deactivate` returns True.

- [ ] **Step 1: Write failing tests.**
  - `test_ext_shared_refused`.
  - `test_guest_member_refused`.
  - `test_clean_channel_ok`.
  - `test_guest_join_deactivates_and_posts_once`: two joins give one post.
  - `test_channel_shared_deactivates`.
  - `test_member_join_non_guest_noop`.
  - `test_hourly_backstop_cached`: two `allowed` calls within an hour make one `members` call. Past an hour, the
    second makes another.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): guest and shared-channel guard`.

