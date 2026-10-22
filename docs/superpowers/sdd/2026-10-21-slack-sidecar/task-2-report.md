---
title: "Task 2 report: Slack API seam and channel registry"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 2 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 2 report: Slack API seam and channel registry

Status: DONE. Commit: b58ce9c `feat(slack): Slack API seam and channel registry` (pre-commit hook ran and passed).

## Implemented
- `hosted/slack/api.py` (162 lines): `SlackFile`, `SlackMessage` (+ `from_raw`), `Page`, `SlackCallError`, `SlackApi` protocol, `SdkSlackApi`.
  `post` always sets `unfurl_links=False, unfurl_media=False`; `upload` uses `files_upload_v2`; `download` streams with a bearer
  header and a byte cap (`too_large`, `http_<status>`, transport errors become `SlackCallError`); every `SlackApiError` maps to
  `SlackCallError` with `retry_after_s` from `Retry-After`. `members` follows cursors. `SdkSlackApi(client, *, http=None)` takes an optional
  httpx client (used by tests with `MockTransport`).
- `hosted/slack/registry.py` (179 lines): `ChannelRow`, `DEFAULT_ALIASES`, `new_row`, `ChannelRegistry` (`get`, `by_label`, `all`, `add`,
  `update`, `deactivate`, `mark_welcomed`, `mark_warned`). Table created on use; JSON columns are TEXT; guarded `UPDATE ... WHERE` with
  `rowcount == 1` for the once-only transitions; `add` uses `ON CONFLICT DO NOTHING` and raises `ValueError` on a duplicate key or label.
- `tests/slack_fakes.py`: `FakeSlack` with the brief's state/records and `fail_next`.
- `CLAUDE.md` Owns list updated; `docs/index.md` regenerated and staged.

## TDD evidence
RED: `uv run pytest tests/test_hosted_slack_api.py tests/test_hosted_slack_registry.py -q` gave `ModuleNotFoundError: No module named
'openrouter_image_mcp.hosted.slack.registry'` (and `.api`), 2 collection errors.
GREEN: same command, `26 passed, 7 skipped` (the skips are the pg-param variants; `TEST_DATABASE_URL` is unset locally).
Full: `uv run pytest -q` gave `884 passed, 324 skipped, 7 deselected`; `uv run ruff check` clean.

## Self-review notes
- Tests cover every brief test plus: username omitted when absent, no Retry-After, label precedence, file mapping, cursor paging,
  pins/members/user/info, members pagination, upload, download bearer/too_large/http error, `update`, reactivate, `mark_warned`,
  and a signature-parity test that both `SdkSlackApi` and `FakeSlack` match the `SlackApi` protocol.
- No token-shaped literals (fixture built as `"xoxb-" + "1" * 12`).
- Both source files under 300 lines, functions under 50.

## Decisions and concerns
- `FakeSlack.history` and `.members` are both brief-mandated state names and protocol method names. They are dict subclasses that are
  also callable (`_CallableState`, with `__signature__` copied from the real implementation), so `fake.history["C1"] = [...]` and
  `await fake.history("C1")` both work.
- `FakeSlack.history()` returns newest first (like Slack) and pages with a numeric offset cursor; `replies` returns thread order.
- `SdkSlackApi.upload` accepts `username` for protocol parity but ignores it (`files_upload_v2` has no such parameter).
- `new_row` defaults `default_model="gpt"` (a `DEFAULT_ALIASES` key); the brief did not name a default. Limits default to $4 and 3 per the spec.
- `update` refuses key columns, `workspace_id` and `created_at`; changing `cohort` re-derives `workspace_id`. A `team_label` clash raises `ValueError`.
- `created_at` is excluded from dataclass equality so round-trip equality ignores the stored timestamp.
- Postgres paths (`-m pg`) were not run locally; the SQL uses only portable constructs from the existing stores.

## Fix round 1
Changes:
- `registry.py`: `new_row` default `default_model` is now `"gemini"` (was `"gpt"`); `test_new_row_defaults` asserts it.
- `api.py`: `SdkSlackApi.download` parses the URL with `httpx.URL` and requires scheme `https` and host exactly `files.slack.com`
  (lowercased, trailing dot stripped); otherwise raises `SlackCallError("download", "untrusted_host")` before any request or header.
  The request uses `follow_redirects=False`; any non-2xx (including 3xx) raises `http_<status>`.
Covering tests (`tests/test_hosted_slack_api.py`): `test_download_refuses_untrusted_host` (http scheme, `evil.example`,
`files.slack.com.evil.example`, host-in-path, unparseable URL; the MockTransport records no request),
`test_download_does_not_follow_redirects` (302 gives `http_302`, one request only, host `FILES.slack.com.` accepted);
the existing valid `https://files.slack.com/a` download tests still pass.
Command: `uv run pytest tests/test_hosted_slack_api.py tests/test_hosted_slack_registry.py -q` gave `32 passed, 7 skipped`.
