---
title: "2026-10-21-slack-sidecar: task 0 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 0 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 0: Live spike for the tool loop, shell files and Fusion

**Files:**
- Create: `tests/live/test_slack_spikes.py` (marker `live`, same `EnvCredentials` pattern as `tests/live/test_live.py`)
- Modify: `docs/superpowers/specs/2026-10-18-hosted-decision-record.md` (new "Slack Sidecar" section)

Each spike prints the facts it needs, and asserts only that the request succeeded. Use the cheapest tool-capable
model, and run each spike once.

- [ ] **Step 1: Write `test_function_call_replay`.** One request with a function tool `echo(text)` plus
  `openrouter:datetime`, then a second request whose `input` replays every output item followed by a
  `function_call_output`. Pass: both return 200 and the second has an assistant message.
- [ ] **Step 2: Write `test_shell_files_shape`.** Sandbox request that writes `/workspace/project/outputs/a.txt`.
  Print each shell item's `files` entries and the exact `filename` form (absolute or relative). Then
  `GET containers/{container_id}/files/{file_id}/content` with and without `?workspace_id=`.
- [ ] **Step 3: Write `test_fusion_forced`.** Send `tools=[openrouter:fusion]` with the `allowed_tools`
  `tool_choice` from amendment 3, a two-model panel, and fusion `tools=[web_search]`. Print whether a fusion item ran,
  and whether `analysis` is present.
- [ ] **Step 4: Run** `OPENROUTER_LIVE_KEY=... uv run pytest -m live tests/live/test_slack_spikes.py -s`.
- [ ] **Step 5: Record results** in the decision record under "Slack Sidecar: API facts (2026-10-2x)": the replay
  outcome, the `filename` form, the download query parameter, the fusion forcing outcome, and mid-inference
  cancellation (out of scope; `!stop` stays at round boundaries). If replay fails, stop and re-plan Task 13. If
  forcing fails, Task 12 uses the `openrouter/fusion` fallback.
- [ ] **Step 6: Commit** `test(slack): live spikes for tool replay, shell files and fusion`.

