---
title: "Task 8 report: welcome, help and data notice"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 8 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 8 report: welcome, help and data notice

Status: DONE. Commit 7bf2065 `feat(slack): welcome, help and data notice`.

## Implemented
`hosted/slack/welcome.py` (87 lines): `NOT_SET_UP`, `GUEST_OFF`, `DATA_NOTICE` (four `•` bullets), `welcome_text(row, budget_usd)`, `help_text(row)`.
- Welcome is the spec text verbatim; example alias is `gemini` if in `alias_map`, else `row.default_model`. Budget formatted with `:g`; None gives the brief's fallback sentence.
- Help: aliases as `alias → target` (sorted, one per line), default model, Fusion panel and analyst, commands (ask, ask a and b, compare, models, set default, usage, remember/recall/forget, help, !restart/!status/!stop), then "What I send and keep" and `DATA_NOTICE`.
- `help_text` runs through `replies.escape` once at the end, so `<question>` placeholders render as `&lt;question&gt;` and cannot form Slack links. The welcome has no angle brackets.

## TDD
RED: `ModuleNotFoundError: No module named ...hosted.slack.welcome` (collection error). A first GREEN run exposed a wrong cohort in my fixture (`mpd-2026`, real cohorts are `2027`/`2028`); fixed the test. GREEN: 8 passed. Full suite 1045 passed, 366 skipped; `ruff check` clean (initial FLY002/ISC004 findings fixed by restructuring `DATA_NOTICE`).

## Files
src/openrouter_image_mcp/hosted/slack/welcome.py, tests/test_hosted_slack_welcome.py, src/openrouter_image_mcp/hosted/slack/CLAUDE.md (Owns entry), docs/index.md (regenerated).

## Self-review
`test_welcome_matches_spec` compares the full text to a literal copied from the spec, including `•` and backticks. Pre-commit hook ran and passed, not bypassed.

## Concerns
- Help copy beyond the spec's command list (e.g. the one-line intro, `models` and `usage` descriptions) is my wording, kept plain.
- `help_text` test reads `parser._COMMAND_WORDS` (private); fine for a same-package test but will need updating if renamed.
