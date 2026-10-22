---
title: "Task 13 report: advisor tool loop"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 13 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 13 report: advisor tool loop

Status: DONE.

- `src/openrouter_image_mcp/advisor_loop.py` (new, ~115 lines): `MAX_ROUNDS`, `FINAL_NOTE`, `FunctionRunner`,
  `LoopStopped`, `LoopResult`, `run_tool_loop`. `on_items` is async. `rounds` counts every respond call,
  including the forced final one.
- `advisor_client.py`: `container_file` (streamed GET, id regex `^[A-Za-z0-9_-]{1,128}$` -> `BadRequestError`,
  401 -> `AuthRequiredError`, non-2xx / Content-Length or body over cap -> redacted `ProviderError`).
- Tests: `tests/test_advisor_loop.py` (brief's tests plus unknown tool, string input, payload untouched,
  cancellation, stop before first/final call, cost None cases, on_items ordering) and `tests/test_advisor_client.py`.
  Red first (ModuleNotFoundError), then green.
- Folder note and `docs/index.md` refreshed.
- ruff clean; full suite 1312 passed, 384 skipped.

Rulings applied: 1-8 as specified. Additional choices: function_call items lacking a string `name` or `call_id`
are ignored (not run, do not continue the loop); a response whose `output` is not a list of dicts raises
`ProviderError`; a tool failure is logged with `exc_info` (handler redacts) and returns `Error: <Type>`.

## Fix round 1

1. `_run_one` re-raises `LoopStopped` ahead of the generic handler; test added.
2. Replay consistency via `_plan_round`: a `function_call` without a string `call_id` is dropped from the replay;
   a repeated `call_id` runs and replays once (first wins); a non-string name with a valid id gets
   `"Error: malformed call"`. A response whose only calls were dropped ends the loop. Three tests plus one for
   the dropped-only case.
3. Stream cap test uses `httpx.Response(200, stream=...)` with no Content-Length; verified it fails when the
   `total > max_bytes` check is disabled.
4. Content-Length check uses `declared.isascii() and declared.isdigit()`.
5. `container_file` catches `httpx.HTTPError` and raises a redacted generic `ProviderError`; test with a
   `DecodingError`. `_request` untouched.
6. Function-failure log carries tool name and exception type only (no `exc_info`); caplog test.
7. `LoopStopped(items, cost_usd)` is filled by `run_tool_loop` on the way out for both should_stop and runner
   raises. The docstring notes that a mid-loop `ProviderError` discards partial cost and that callers see items
   through `on_items`. Two tests.

ruff clean; full suite 1321 passed, 384 skipped.
