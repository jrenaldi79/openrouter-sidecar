---
title: "2026-10-21-slack-sidecar: task 13 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 13 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 13: Advisor tool loop

**Files:**
- Create: `src/openrouter_image_mcp/advisor_loop.py`
- Modify: `advisor_client.py` (add `async container_file(key, container_id, file_id, *, max_bytes) -> bytes`, a
  streamed GET with a size cap that raises `ProviderError` when exceeded; no other change)
- Test: `tests/test_advisor_loop.py`, `tests/test_client.py` or `tests/test_advisor_client*.py` (whichever exists)

**Interfaces:**
- Consumes: `AdvisorClient.respond(key, payload) -> dict`.
- Produces:
  - `MAX_ROUNDS = 4`.
  - `FINAL_NOTE = "You have used every tool round. Answer now with what you have."`
  - `FunctionRunner = Callable[[str, str], Awaitable[str]]`.
  - `class LoopStopped(Exception)`.
  - `LoopResult(body: dict, items: list[dict], rounds: int, cost_usd: float | None)`. `items` holds every output
    item from every round, for outputs and progress.
  - `async run_tool_loop(client, key, payload, functions: Mapping[str, FunctionRunner], *, on_items=None, should_stop=lambda: False, max_rounds=MAX_ROUNDS) -> LoopResult`.

Algorithm (from the spec, with amendment 2):
1. Call `respond`.
2. Collect the `function_call` items whose `name` is in `functions`.
3. If there are none, return.
4. Otherwise run each one, append **every** output item of the response as returned, followed by a
   `{"type":"function_call_output","call_id","output"}` per call, to `payload["input"]`, and call again with the same
   `tools`.
5. Check `should_stop()` before each call, and raise `LoopStopped` if it's set.
6. After `max_rounds` rounds that still have calls, make one final call with `tool_choice: "none"` and `FINAL_NOTE`
   appended as a user item.
7. Costs are summed from `usage.cost`; any round without a numeric cost makes the total None.
8. A function call whose name isn't in `functions` gets the output `"Error: unknown tool"`.

- [ ] **Step 1: Write failing tests** with a scripted `MockTransport` that returns queued JSON bodies and records
  requests.
  - `test_no_calls_single_round`.
  - `test_function_call_then_answer`: the second request input ends with the `function_call` and a matching
    `function_call_output` (same `call_id`).
  - `test_server_items_replayed_verbatim`: an `openrouter:web_search` item from round 1 appears unchanged in round
    2's input.
  - `test_round_cap_forces_answer`: 5 responses each with a call give 4 rounds, then a fifth request with
    `tool_choice == "none"`.
  - `test_tool_error_output_not_failure`: the function raises, and the loop still finishes with `"Error: …"` as the
    output.
  - `test_stop_at_round_boundary`: `should_stop` turning True after round 1 raises `LoopStopped`, and no third
    request is sent.
  - `test_cost_summed`.
  - `test_container_file_size_cap`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(advisor): function-call tool loop over the Responses API`.

