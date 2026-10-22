---
title: "Task 10 report: channel context builder"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 10 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 10 report: channel context builder

## Built
- `hosted/slack/context.py` (~246 lines): constants, `NameCache`, `format_message`, `Gathered`, `Context`, `ContextBuilder` (`gather`, `render`, `files`), `newest_within`, `trim_thread`.
- `hosted/slack/context_files.py` (~82 lines): `trigger_files(api, files)`, `safe_name`, text-file eligibility, duplicate suffixes.
- Tests: `test_hosted_slack_context.py` (builder), `..._context_format.py` (format, NameCache), `..._context_files.py` (attachments). 48 tests, written first and seen failing (collection errors) before implementing.
- `tzdata>=2026.5` added with `uv add` (Windows CI has no system tz database for `ZoneInfo`). Folder CLAUDE.md and docs/index.md updated.

## Decisions beyond the rulings
- Pins and channel history reads are best effort: a `SlackCallError` is logged (code only) and that part is left out. The thread (`replies`) read still raises.
- Pins are ordered and capped by message ts (pin time is not in `SlackMessage`); oldest dropped first. An oversized single newest line is clipped, not dropped.
- Failed name lookups are cached 60 s (`FAILED_NAME_TTL_S`), successes for ttl. Unknown user (`user_not_found`) is treated as a failure.
- Channel read: up to 3 history pages of 50, strict `ts < root`, thread replies (thread_ts != ts) and join/leave excluded, newest 20 kept.
- `render` always includes the trigger even if its ts is not newer than `since_ts`. `newest_ts` covers all gathered thread messages, including ones trimmed from the text.
- `Gathered.urls` holds links of the whole thread; `Context.urls` holds links of the messages actually rendered (delta on later turns). URLs are html-unescaped, `<url|label>` handled, trailing punctuation stripped.
- `Context.listed` = unreadable/non-text trigger files, then files on other rendered thread messages. It is NOT written into `Context.text`; the payload task should mention listed files if the model should know.
- Files declared larger than `MAX_FILE_CHARS * 4` bytes are listed without downloading. Download exceptions other than `SlackCallError` are also caught and logged (type name only), per "never raises".
- `trim_thread` clips an oversized root to half the cap; the note is omitted when nothing was trimmed.

## Results
`uv run ruff check` clean; `uv run pytest`: 1117 passed, 384 skipped, 7 deselected; `generate.js --check` clean.

## Concerns
- Several trigger files can total more than the 200k-per-session limit (`MAX_SESSION_CHARS`) together with `slack/context.md`; the runner should cap or the packets will be refused.
- On later turns the delta can include Sidecar's own earlier answer (labeled `Sidecar · X`); the caller may want to filter, per its own chat history.

## Fix round 1
- `Context.urls` is now `g.urls` (all http(s) links in the gathered thread and trigger), also on later turns. Test: URL only in the root, later-turn delta.
- Session budget: `ContextBuilder.files(trigger, *, budget=None)` and `trigger_files(api, files, budget)`. Default budget is `MAX_SESSION_CHARS - max_context_chars()` where `max_context_chars() = THREAD_CHARS + PINS_CHARS + channel_chars + CONTEXT_OVERHEAD_CHARS` (2,000). Default is 106,000 with `channel_chars=8000`. A readable text file that would push the running total of attached characters over the budget is listed, never raises; listed files do not consume budget. Tests: two 100k CSVs (second listed), explicit small budget, zero budget, render integration.
- `format_message`: entities are unescaped first (`html.unescape`), then the body is split with `splitlines()` and continuation lines are joined with a two-space indent (covers `\r\n`, unicode line separators and `&#10;`). Labels and names have control characters and line breaks collapsed to single spaces; an empty result falls back to `bot`. `<@U…>` is untouched. Tests for each.
- Concern: an unescaped `&lt;@U1&gt;` in a body now reads as `<@U1>` in the model's data (Slack only turns real mentions into `<@U…>`, so it is indistinguishable); data-framed, so left as the ruling asked.

## Fix round 2
- `_read_text` treats text containing `\x00` like non-UTF-8: logged, file listed, not attached (session `validate_files` would refuse it and fail the whole call). Test added.
- `format_message` and URL extraction now use Slack's three entities only, via `parser.decode_entities` (renamed from the private `_decode_entities`; `&amp;` is decoded last). `&region=us` and `&#10;` stay literal, `&amp;lt;` gives `&lt;`. The `&#10;` forging test became `test_only_slack_entities_are_decoded`; real-newline continuation indenting is still covered by `test_body_continuation_lines_are_indented`.
- Results: context and parser tests 139 passed; full suite and ruff below in the final run.

### Fix round 2, correction
Commit 70896cd contained only the `hosted/slack/CLAUDE.md` edit; the code and test changes described above were not in it (the edit script did not apply them, and I reported without checking `git diff --stat`). They are applied in the follow-up commit `fix(slack): reject NUL attachments, decode only Slack entities (code)`: NUL check in `context_files._read_text` with `test_nul_text_listed`; `parser.decode_entities` (renamed, public) used by `context._body` and `_urls`; `test_only_slack_entities_are_decoded` replaces the `&#10;` forging test. Verified with `git diff --stat` before committing and by grep (no `html.unescape` left in context.py).
