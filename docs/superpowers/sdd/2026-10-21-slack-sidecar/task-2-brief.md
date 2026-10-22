---
title: "2026-10-21-slack-sidecar: task 2 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 2 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 2: Slack API seam and channel registry

**Files:**
- Create: `hosted/slack/api.py`, `hosted/slack/registry.py`, `tests/slack_fakes.py`
- Test: `tests/test_hosted_slack_api.py`, `tests/test_hosted_slack_registry.py`

**Interfaces:**
- Produces (api):
  - `SlackFile(id: str, name: str, mimetype: str, size: int, url: str)`
  - `SlackMessage(ts: str, user: str | None, text: str, label: str | None, subtype: str | None, thread_ts: str | None,
    files: tuple[SlackFile, ...] = ())`. `label` is `username`, else `bot_profile.name`, for bot posts.
  - `Page(messages: list[SlackMessage], next_cursor: str | None)`
  - `class SlackCallError(Exception)(method: str, code: str, retry_after_s: float | None = None)`
  - `class SlackApi(Protocol)` with these methods:
    - `replies(channel, thread_ts, *, cursor=None, limit=200) -> Page`
    - `history(channel, *, latest=None, cursor=None, limit=200) -> Page`
    - `pins(channel) -> list[SlackMessage]`
    - `channel_info(channel) -> dict`
    - `members(channel) -> list[str]`
    - `user(user_id) -> dict`
    - `post(channel, thread_ts, text, *, username=None) -> str` (returns the ts)
    - `update(channel, ts, text) -> None`
    - `upload(channel, thread_ts, filename, content: bytes, *, title, username=None) -> None`
    - `download(url: str, max_bytes: int) -> bytes`

    All methods are async.
  - `SdkSlackApi(client: AsyncWebClient)` implements it. `post` always passes `unfurl_links=False,
    unfurl_media=False`. `upload` uses `files_upload_v2`. `download` sends the bot token as a bearer header, streams,
    and raises `SlackCallError("download", "too_large")` past `max_bytes`. Every `SlackApiError` becomes a
    `SlackCallError`, with `retry_after_s` taken from the `Retry-After` header.
- Produces (registry):
  - `ChannelRow` (frozen) with these fields:
    - `slack_team_id`, `channel_id`, `team_label`, `cohort`, `workspace_id`, `default_model`
    - `alias_map: dict[str, str]`
    - `fusion_panel: tuple[str, ...]`, `fusion_analyst: str`, `subagent_model: str`
    - `fetch_allowlist: tuple[str, ...]`
    - `daily_cap_usd: float`, `max_concurrent: int`
    - `invoker_allowlist: tuple[str, ...]`
    - `active: bool`, `inactive_reason: str | None`, `welcomed_at: float | None`, `warned_month: str | None`
  - `DEFAULT_ALIASES = {"gpt": "ChatGPT", "gemini": "Gemini", "grok": "Grok", "open": "open weight"}`. Values are
    brands that `preferences.ALIASES` resolves to a family, or exact model ids.
  - `ChannelRegistry(target)` methods:
    - `get(team_id, channel_id) -> ChannelRow | None`
    - `by_label(label) -> ChannelRow | None`
    - `all() -> list[ChannelRow]`
    - `add(row) -> None`
    - `update(team_id, channel_id, **fields) -> ChannelRow`
    - `deactivate(team_id, channel_id, reason) -> bool` (True only on an active-to-inactive change)
    - `mark_welcomed(team_id, channel_id) -> bool` (True only for the first caller)
    - `mark_warned(team_id, channel_id, month) -> bool`
  - `new_row(team_id, channel_id, label, cohort, *, fusion_panel, fusion_analyst, subagent_model, **optional) -> ChannelRow`
    fills `workspace_id` from `config.COHORT_WORKSPACES` and fills the defaults.
- Produces (fakes): `FakeSlack` implements `SlackApi` in memory:
  - state: `channels`, `threads`, `history`, `pinned`, `users`, `members`, `files`
  - records: `posts`, `updates`, `uploads`
  - `fail_next(method, code, retry_after_s=None)` makes the next call to that method fail.

- [ ] **Step 1: Write failing tests.**
  - `test_post_disables_unfurl`: `SdkSlackApi` over a stub client records `unfurl_links is False and unfurl_media is False`.
  - `test_slack_error_mapped`: a stub raising `SlackApiError` with `error="ratelimited"` and `Retry-After: 7` gives
    `SlackCallError(code="ratelimited", retry_after_s=7.0)`.
  - `test_message_label_from_bot_profile`: a raw message with `bot_profile.name="Claude"` gives `label == "Claude"`.
  - `test_add_get_roundtrip` (with `db_target`): every field round-trips, including the JSON columns.
  - `test_add_rejects_bad_rows`: a duplicate key, a fusion panel of 0 or 9 models, a label not matching
    `^[a-z0-9-]{1,40}$`, and an unknown cohort each raise `ValueError`.
  - `test_deactivate_once`: the first call returns True, the second False, and `inactive_reason` is stored.
  - `test_mark_welcomed_once`: two calls give `[True, False]`.
- [ ] **Step 2: Run** `uv run pytest tests/test_hosted_slack_api.py tests/test_hosted_slack_registry.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Table
  `slack_channels(slack_team_id, channel_id, team_label UNIQUE, cohort, workspace_id, default_model, alias_map, fusion_panel, fusion_analyst, subagent_model, fetch_allowlist, daily_cap_usd REAL, max_concurrent INTEGER, invoker_allowlist, active INTEGER, inactive_reason, welcomed_at REAL, warned_month, created_at REAL, PRIMARY KEY (slack_team_id, channel_id))`.
  JSON columns are TEXT. `mark_welcomed` is `UPDATE … WHERE welcomed_at IS NULL` with `rowcount == 1`.
- [ ] **Step 4: Run** again, plus the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): Slack API seam and channel registry`.

