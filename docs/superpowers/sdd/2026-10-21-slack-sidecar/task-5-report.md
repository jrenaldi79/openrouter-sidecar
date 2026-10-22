---
title: "Task 5 report: command parser"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 5 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 5 report: command parser

Status: DONE

## Implemented
`hosted/slack/parser.py` (~190 lines, every function under 50): frozen dataclasses, `Intent` union, `command_text`, `parse`, `wants_sandbox`. Tests in `tests/test_hosted_slack_parser.py` (65).

## TDD
RED: `uv run pytest tests/test_hosted_slack_parser.py -q` -> `ModuleNotFoundError: No module named 'openrouter_image_mcp.hosted.slack.parser'`, 1 collection error.
GREEN: same command -> `65 passed`. Full: `986 passed, 349 skipped, 7 deselected`; `ruff check` clean.

## Files
- src/openrouter_image_mcp/hosted/slack/parser.py (new)
- tests/test_hosted_slack_parser.py (new)
- src/openrouter_image_mcp/hosted/slack/CLAUDE.md (Owns list)
- docs/index.md (regenerated)

## Decisions beyond the brief/rulings
- `command_text`: links are unwrapped before entities are decoded, so an escaped `&lt;https://x|y&gt;` stays literal text; `&amp;lt;` decodes once. Non-command mentions: text before and after the mention is joined with one space ("hey <@B> what's up" -> "hey what's up"; "is this right <@B>?" -> "is this right ?").
- `models`, `usage`, `help` are commands only as the whole text ("help me write this" is a question). `compare`/`ask` take the rest.
- `!restart|!status|!stop` match on the first word (case-insensitive); trailing words are ignored.
- Aliases: non-id names lowercased, ids containing `/` kept as typed; `and <word>` membership is case-insensitive; duplicates are collapsed; chains (`a and b and c`) work.
- `wants_sandbox` has no filename parameter, so "names the file's extension" is a fixed list of text extensions (.csv .tsv .txt .json .md .log .xml .yaml .yml).

## Self-review
Diff covers each Commands-table row, each ruling and each brief test. No regex, no extra modules.

## Concerns
- Keyword matching is literal substring per the brief, so "paragraph" matches `graph`, and "brun " matches `run `. Word boundaries would be a one-line change if wanted.

## Fix round 1
Finding: substring matching in `wants_sandbox` sent ordinary questions to sandbox mode. Fixed per the coordinator's ruling (token matching).

Changes (`parser.py` only, plus its test file): `wants_sandbox` now lowercases, tokenizes with `re.findall(r"[a-z]+", ...)` (one-line regex), and matches whole tokens or stems.
- `use sandbox` phrase -> True.
- With a text file: "attached", a text extension as a whole token, or an action token -> True.
- Without a file: (`run`/`execute` + `code`/`script`/`python`/`program`) or (action token + `data`/`dataset`/`csv`/`spreadsheet`/`numbers`/`table`) -> True; otherwise False.
- Action token: starts with `chart plot graph analy calculat comput visuali` (not `graphic`), or is exactly `run`/`execute`.

Tests: replaced the old keyword test with positive (10), negative-without-file (11, all nine reviewer sentences plus two), file-with-action, file hint and file-without-intent tests. Old substring-encoded cases ("execute it", "graph this", "calculate the total") now expect False without a file. RED was not captured separately for this round: the new negatives are exactly the reviewer's reproduced failures of the old substring rule.

Command: `uv run pytest tests/test_hosted_slack_parser.py -q` -> `80 passed`. Full `uv run pytest -q` -> `1001 passed, 349 skipped, 7 deselected`. `uv run ruff check` -> `All checks passed!`. Docs check clean. `parser.py` is 233 lines.
