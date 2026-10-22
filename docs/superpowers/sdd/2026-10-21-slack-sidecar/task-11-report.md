---
title: "Task 11 report: channel-bound function tools"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 11 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 11 report: channel-bound function tools

Status: DONE.

## Built
- `hosted/slack/slack_tools.py` (249 lines): `READ_LIMIT`, `CHARS_CAP`, `SEARCH_PAGES`, `SEARCH_MESSAGES`, `TOOL_SPECS`
  (non-strict, `additionalProperties: false`, no channel argument, `search_channel` requires `query`),
  `FUNCTION_NAMES`, `ChannelTools.call -> (output, label)`. It never raises.
- `context.py`: `_is_channel_message` made public (`is_channel_message`) so the tools reuse it. Nothing else changed.
- Tests: `tests/test_hosted_slack_slack_tools.py` (isolation, specs, history, thread, errors),
  `tests/test_hosted_slack_slack_tools_search.py` (search, arguments), shared helpers in `tests/slack_tools_kit.py`.
  83 tests, written first and seen failing (module missing), then green.
- `hosted/slack/CLAUDE.md` bullet, `docs/index.md` regenerated.

## Rulings implemented
1. `read_thread` takes `before`. It reads the thread (at most 10 pages of 200), filters ts < before, drops join/leave and keeps the newest `limit`.
2. History is top-level only (`is_channel_message`). Page size is max(limit+1, 50), at most 5 pages.
3. Search scans at most 5 pages and 1000 messages. Every term must match case-insensitively on entity-decoded text. It reports how many messages were searched. An empty query is `Error: query is required`.
4. A `ts` must fullmatch `\d{1,12}\.\d{1,8}` (fullmatch, because `$` would pass a trailing newline). A null ts means absent. `limit` accepts only a non-bool int, clamped to 1..200. Non-object JSON gives `bad arguments`. Blank arguments mean `{}`.
5. Output = preface, lines oldest first, newest kept within `CHARS_CAP`, then a footer. The footer gives the count, "At least N" when pages were unread, and `before="<oldest shown ts>"` (plus `thread_ts` for a non-current thread). No results gives `No messages found.`.
6. Errors are `Error: Slack said <code>` or `Error: <TypeName>`. The exception text is logged through the module logger only.
7. Labels follow the examples. The query is collapsed to one line and clipped to 40 characters.

## Verification
`uv run ruff check` clean, `uv run pytest` 1212 passed / 384 skipped / 7 deselected, `generate.js --check` clean.

## Concerns
- `search_channel` has no `before`, so its footer says "N more matches not shown; narrow the query or raise limit" and, when older messages were not searched, points to `read_channel_history before=...`. The ruling's "oldest shown ts as before" fits only the read tools.
- A thread longer than 2000 messages loses its newest messages (the replies API pages oldest first and the SlackApi protocol has no `latest`). A note line says so.
- `FakeSlack.history`'s `latest` is inclusive and Slack's is exclusive. The tools filter ts < before themselves, so both behave the same.

## Fix round 1
1. `_TS` is now `[0-9]{1,12}\.[0-9]{1,8}`; bad-timestamp cases added for Arabic-Indic and full-width digits.
2. `WorkingMessage.start`, `step` and `finish` run their text through `replies.escape`. Before this it escaped nothing. The `step` comparison and `_text` use the escaped text. Test: a step of `<!channel> & <@U1>` and a finish of `<!here>` post escaped. Risk for the runner task: `finish` now escapes, so pass it raw text, not `clean()` output (that would double-escape). The username is a field, not message text, and is left alone.
3. `_scan` keys messages by ts (as `_thread` does). Test: a stub whose page repeats under a repeated cursor gives 3 messages, not 9, for both read and search.
4. The search footer is split. Matches cut by `limit` say "raise limit or narrow the query". Matches cut by `CHARS_CAP` say "left out to fit the size cap; narrow the query". Both are tested.
CLAUDE.md updated for 1, 2 and 4.
