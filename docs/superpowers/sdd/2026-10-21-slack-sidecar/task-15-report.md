---
title: "Task 15 report: sandbox outputs"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 15 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 15 report: sandbox outputs

Status: done. Commit message `feat(slack): post sandbox outputs to the thread`.

## What changed
- `hosted/slack/outputs.py` (new): `MAX_FILES = 5`, `MAX_BYTES = 20 MB`, `OutputRef`, `normalize`, `output_refs`, `upload_name`, `Outputs.post`.
  - Rulings 1 to 6 implemented as written: shell item types, entry validation and container match, path forms and rejections, dedupe by `file_id` (latest path, first-seen order), flattened name capped at 200 characters with the extension kept, file and byte caps with a shared remaining budget, notes without ids or error text, `username=Sidecar · <label>`.
  - Notes are escaped (`replies.escape`) and posted as one message; `post` never raises (per-file download and upload errors, and the notes post, are caught and logged by type).
  - Over-budget wording keys off the client's "larger than" message; a test pins that against the real `AdvisorClient`. Other download failures say it could not be fetched.
- `runner_turn.py`: `Turn.container_id` (set from the chat), `Turn._post_outputs` calls `output_refs(self.items, container_id)` then `Outputs.post(client, key, channel, thread_ts, label, container_id, refs)`. Still after `post_answer` and `mark_posted`, sandbox non-compare chats only, errors logged.
- Tests: `tests/test_hosted_slack_outputs.py` (36 cases), three runner tests in `tests/test_hosted_slack_runner_files.py` (`test_sandbox_chart_posted`, failure leaves the answer completed, inline chat posts nothing), rig serves container files, one existing test's fake `Outputs.post` signature updated.
- Docs: `hosted/slack/CLAUDE.md`, `docs/index.md` regenerated.

## Checks
ruff clean; full pytest 1446 passed; pg suite 416 passed.

## Fix round 1
Commit message `fix(slack): output names, bounded notes, typed oversize`.

1. `normalize` rejects any character of Unicode category Cc, Cf, Zl or Zp (C1 controls, U+202E, zero-width, BOM, U+2028/2029, DEL) and any segment empty after `strip()`. Parametrized cases added, plus a check that ordinary non-ASCII names are kept.
2. Notes are bounded: at most 10 per-file notes, then one line "…and K more files weren't posted." (singular "…and 1 more file wasn't posted." for K=1). Three consecutive download failures stop further downloads and the rest count toward K. Oversize skips and upload failures do not count toward the streak; any download that succeeds or is oversize resets it. Both limits tested.
3. Oversize note: "<name> wasn't posted: it would take this answer past 20 MB of files."
4. `errors.ContainerFileTooLargeError(ProviderError)`, raised by `AdvisorClient.container_file` for both the Content-Length and stream-cap cases (message unchanged). `outputs.py` catches it by type; the "larger than" matching is gone. Client tests now assert the type for body, stream and header cases. Folder notes updated.
5. A `CancelledError` from `container_file` propagates out of `post` (test added; it already did, since the catches are `except Exception`).
6. The fake `Outputs.post` in `test_hosted_slack_runner_errors.py` has the real signature.

Checks: ruff clean; outputs, runner and client tests 146 passed; full suite 1464 passed; pg suite 416 passed.

Note: the commit's pre-commit hook required splitting the outputs tests (file-size limit): refs tests stay in `tests/test_hosted_slack_outputs.py`, posting tests moved to `tests/test_hosted_slack_outputs_post.py`. Commit: 136cd14.
