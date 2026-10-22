---
title: "2026-10-21-slack-sidecar: task 15 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 15 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 15: Sandbox outputs

**Files:** Create `hosted/slack/outputs.py`. Test `tests/test_hosted_slack_outputs.py`.

**Interfaces:**
- Consumes: `LoopResult.items`, `AdvisorClient.container_file`, `SlackApi.upload`.
- Produces:
  - `MAX_FILES = 5`, `MAX_BYTES = 20 * 1024 * 1024`.
  - `OutputRef(file_id, path)`.
  - `output_refs(items: list[dict], container_id: str) -> list[OutputRef]`. It takes `files` from each shell item
    (`openrouter:shell`, or `shell_call_output`) whose `container_id` matches, and normalizes `filename` to a path
    relative to `/workspace/project/`, using the form recorded in Task 0. It keeps paths under `outputs/`, with no
    `..`, and deduplicates by `file_id`, keeping the latest.
  - `Outputs(api)` has `async post(client, key, channel, thread_ts, label, container_id, refs) -> list[str]`. It
    returns one-line notes for skipped or failed files, which are posted as a single message. It never raises. Ids
    never appear in the notes.

- [ ] **Step 1: Write failing tests.**
  - `test_only_outputs_folder`.
  - `test_other_container_ignored`.
  - `test_traversal_rejected`.
  - `test_caps_five_files_twenty_mb`: the sixth file and any byte beyond 20 MB in total are skipped with a note.
  - `test_download_failure_noted_not_raised`.
  - `test_notes_have_no_ids`.
- [ ] **Step 2–4:** Run, implement, run. Then wire `Outputs.post` into the runner's step 12 and add
  `test_sandbox_chart_posted` to `tests/test_hosted_slack_runner.py`.
- [ ] **Step 5: Commit** `feat(slack): post sandbox outputs to the thread`.

