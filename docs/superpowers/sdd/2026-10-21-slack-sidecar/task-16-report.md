---
title: "Task 16 report: usage report and 80% budget warning"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 16 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 16 report: usage report and 80% budget warning

Status: DONE. Commit 1fbb0bb `feat(slack): usage report and 80% budget warning` on feat/slack-sidecar.

## What was built
- `hosted/slack/usage.py` (125 lines): `UsageReport`, `usage_report`, `usage_text`, `budget_warning`, `WARN_AT = 0.8`,
  plus `month_key`, `budget_figures`, `budget_report` (month figures only, no job queries or Slack lookups) and
  `answer_warning` (fetch, report, warning, with any exception logged by type).
- Runner wiring: `SlackRunner(..., registry=None, key_info=current_key)`; `Turn._deliver` calls `answer_warning`
  right before `post_answer` (so only for answers about to post, never failed/stopped jobs) and passes the result
  to the existing `footer(..., warning)` parameter, which appends it as a line after the footer.
- Rig: `tests/slack_runner_rig.py` now has a `ChannelRegistry`, and a stubbed `key_info` so no runner test reaches
  the real `current_key`.
- Docs: hosted/slack/CLAUDE.md, docs/index.md regenerated.

## Rulings applied
1. Month = UTC `YYYY-MM` (`month_key`); warning needs ratio >= 0.8 and then `mark_warned` True; percent is an int floor.
2. Today uses `now.date().isoformat()`, the same boundary `Limits.admit` uses (`created_at` is an ISO string); by_user is
   since the first of the UTC month, names via NameCache, one-line, largest first, 10 shown then "...and K others".
3. Missing/None/non-numeric/non-positive limit gives budget and remaining None, "no monthly limit set", no warning;
   non-numeric spend counts as 0; booleans and NaN count as non-numeric; never raises.
4. Text has no key, hash, label or other key_info field.
5. Injectable `key_info` on SlackRunner; failure logged by exception type only, answer still posts.
6. Every jobs query uses the team scope (`test_usage_scoped_to_team`).

## Deviations and notes
- The runner uses the cheap `budget_report` (month figures only) rather than the full `usage_report`, because the full
  report does three job queries and a Slack name lookup per person on every answer; the full one is for `@sidecar usage`.
- The claim (`mark_warned`) comes before the post, so a post that fails twice loses that month's warning (per ruling 1).
- `usage_text` is not entity-escaped; the Task 17 dispatcher must `escape` it (names come from Slack). Noted in CLAUDE.md.
- Imports `_one_line` from `context.py` rather than duplicating the control-character stripper.

## Verification
- Red first: both new test files failed on import/construction before implementation.
- `uv run ruff check`: clean. Full `uv run pytest`: 1485 passed, 458 skipped. pg: 436 passed, 17 skipped.
- `generate.js --check` clean; pre-commit hook passed.

## Fix round 1
Commit 169d95a `fix(slack): bounded key fetch, skip when warned, safe usage names`.
1. `answer_warning` wraps the key fetch in `asyncio.wait_for(..., WARN_FETCH_S)` (`WARN_FETCH_S = 4.0`); a timeout is
   caught with the other failures (logged by type) and means no warning. Test patches the bound to 0.01 s, uses a fetch
   that sleeps 30 s, and checks the answer posts, the fetch was cancelled and the month stays unclaimed.
2. `answer_warning` returns None before any fetch when `row.warned_month == month_key(now)`; test asserts no fetch.
3. `usage_report` slices to the top `MAX_USERS` before resolving names and runs `replies.escape` after `one_line`
   (docstring says so); `usage_text` is safe to post as is. Added `UsageReport.others` (default 0) for the "...and K
   others" count, since by_user is now at most 10. Test: a `<!channel> & <@U1>` name is escaped, 10 lookups only.
4. Removed the meaningless `"monthly" not in ...` assertion.
5. Renamed `context._one_line` to `one_line` (all uses were in context.py plus usage.py).
Rig: `rig.key_info` may now be an async function (awaited), to delay or gate the fetch.
Verification: ruff clean; full suite 1489 passed, 461 skipped; pg 439 passed, 17 skipped.
