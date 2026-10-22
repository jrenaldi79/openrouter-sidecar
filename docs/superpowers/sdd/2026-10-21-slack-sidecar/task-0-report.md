---
title: "Task 0 report: live spikes"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 0 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 0 report: live spikes

Status: DONE_WITH_CONCERNS (live run not possible; no OPENROUTER_LIVE_KEY, per controller ruling).

Commit: a261e66 test(slack): live spikes for tool replay, shell files and fusion

## Implemented
- tests/live/test_slack_spikes.py (136 lines, all functions under 50): marker `live`, key from OPENROUTER_LIVE_KEY (pytest.skip when unset), cohort from OPENROUTER_LIVE_COHORT (default 2027). Uses AdvisorClient.respond; the container download uses httpx directly against API_BASE.
  - MODEL = "openai/gpt-4.1-mini", PANEL = [that, "google/gemini-2.5-flash"] as module constants.
  - test_function_call_replay: echo function tool + openrouter:datetime; second request replays every output item plus function_call_output; asserts a message item.
  - test_shell_files_shape: openrouter:shell container_auto, writes outputs/a.txt, prints each container_file_citation (filename form), downloads with and without ?workspace_id=; asserts a citation exists and at least one download returned 200.
  - test_fusion_forced: fusion with 2-model panel, analyst model, max_tool_calls, tools=[web_search], allowed_tools required tool_choice; prints whether a fusion item ran and whether analysis is present; asserts the response did not fail.
- docs/superpowers/specs/2026-10-18-hosted-decision-record.md: section "Slack Sidecar: API facts (pending live run)" with the four facts, run command, "Results: pending."

## Tests run
- `uv run pytest -m live tests/live/test_slack_spikes.py -q`: 3 skipped.
- `uv run pytest tests/live/test_slack_spikes.py -q`: 3 deselected.
- `uv run ruff check`: all checks passed.
- generate.js: refreshed, docs/index.md unchanged (tests/ not indexed).
- Full `uv run pytest -q`: 840 passed, 317 skipped, 7 deselected. Pre-commit hook passed (it ran the suite again).

## Self-review
- Nothing key-shaped in the file; the key is only used in the Authorization header and never printed. Printed output is truncated JSON of response bodies only.
- Skip is clean via the `live` fixture.

## Concerns
- Tests are unverified against the real API: model ids, the exact fusion item type name (matched loosely via "fusion" in type), and the shell file citation location (found by walking the whole body) may need adjusting on the first live run.
- The replay test replays the original user message plus output items; if a reasoning item rejects replay under store=false, that is exactly the fact being probed.
