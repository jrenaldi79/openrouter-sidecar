---
title: "Brief: sandbox folders match what OpenRouter actually saves"
description: "Archived planning record for the sandbox folders fix (advisor_payload.py): implementation brief given to the subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Brief: sandbox folders match what OpenRouter actually saves

Repo: OpenRouter Sidecar (Python 3.12, uv). Read the root `CLAUDE.md`, `.claude/rules/*.md` and the folder notes
`src/openrouter_image_mcp/CLAUDE.md` and `src/openrouter_image_mcp/hosted/slack/CLAUDE.md` first.

## Why (facts, verified)

- A live spike on 2026-10-21 showed OpenRouter's shell sandbox (`engine: openrouter`, `container_reference` with our own
  `nu_` + 32 hex id) saves and lists only files under `/workspace/home` (the model's `$HOME` and working directory),
  naming them relative to it. A file written under `/workspace/project` was not listed.
- OpenRouter's containers guide: a container sleeps after 5 idle minutes; only files under `/workspace/home` survive;
  processes, environment variables and installed system state don't; saved files are deleted 30 days after the
  container was last used.
- So the student instructions over-promise: `advisor_payload.upload_notes` says to work in `/workspace/project/work`,
  "which persists between turns". It doesn't survive a sleep.
- `SANDBOX_SAFETY` (shared by the student advisor and the Slack bot) says "Do not write conversation transcripts, task
  briefs, or final reports into files", which contradicts the Slack bot asking for files in `/workspace/home/outputs/`.
  It also says "Remove scratch files you wrote under /workspace/home before answering", which, read literally, deletes
  work meant to persist and the Slack outputs.

## Decision (approved by the user, binding)

Folder layout told to the model, for every sandbox chat (student and Slack):

| Folder | Use | Survives a sleep |
| --- | --- | --- |
| `/tmp/sidecar/inputs/<path>` | the current turn's session files, rebuilt every turn from the JSON bundle | no (by design: inputs aren't kept) |
| `/workspace/home/work/` | work worth reusing later in this chat | yes, up to 30 days after last use |
| `/tmp/sidecar/scratch/` | throwaway scripts and intermediate results | no |
| `/workspace/home/outputs/` | Slack only: files to post to the thread (already in `hosted/slack/payload.py`) | yes |

Uploaded bundles still land at `/workspace/home/<id[-8:]>-<name>` (that path comes from OpenRouter's `file_ids`
attachment; `_home_path` stays as is).

## Changes

1. **`src/openrouter_image_mcp/advisor_payload.py`**
   - `SANDBOX_SAFETY` (shared): keep the container rules (assigned container, no internet, can't select another
     container, fetch other users' files, invoke local Claude tools or retrieve keys) and the "cleanup is best effort;
     don't claim provider files were deleted without evidence" rule. Replace the scratch and cleanup sentences with:
     - scratch goes in `/tmp/sidecar/scratch/`;
     - only files under `/workspace/home` are saved for this chat, up to 30 days after its last use; everything else,
       including `/tmp`, installed packages, environment variables and running processes, is lost when the sandbox
       sleeps after 5 idle minutes;
     - before answering, delete files you created under `/workspace/home` that aren't in `work/` or `outputs/`, and the
       uploaded bundle copies once you've extracted them; leave `work/` and `outputs/` alone.
     Remove the transcripts/briefs/final-reports sentence from `SANDBOX_SAFETY`.
   - Move that sentence into the student advisor's own text (`_ADVISOR_HEAD` or `_ADVISOR_TAIL`, so it is in
     `INSTRUCTIONS` and therefore in every student sandbox chat) — reword to: "Do not write conversation transcripts,
     task briefs or your final answer into files; return the answer as text." The Slack bot must not get it.
   - `upload_notes` for `files` bundles: write each entry to `/tmp/sidecar/inputs/<path>`, replacing that directory
     every turn; skip any entry whose path is absolute or contains a `..` segment; keep work to reuse in
     `/workspace/home/work/`. Drop "which persists between turns".
   - Unconditional reset: in every sandbox chat's instructions (put it in `SANDBOX_SAFETY` or the sandbox instructions,
     your call, but it must reach students and Slack), say that only this turn's session files count: when no session
     files are attached this turn, remove `/tmp/sidecar/inputs` if it exists.
   - Keep the wording plain (the project's copy rule: plain words, no metaphors). Keep it short; these are model
     instructions sent on every sandbox turn.
2. **Slack (`src/openrouter_image_mcp/hosted/slack/payload.py`)**: `_SANDBOX_TOOLS` already says files in
   `/workspace/home/outputs/` are not scratch. Adjust only if the new shared wording makes it redundant or
   contradictory; it must still contain `/workspace/home/outputs/`, "only that folder is posted" and "not scratch"
   (existing tests assert those).
3. **Tests** (small change rules apply: update the existing tests the change breaks, plus targeted assertions):
   - `tests/test_advisor_files.py` (~:137) and `tests/test_hosted_slack_payload.py` (~:175, :254, :260): assert the new
     folders (`/tmp/sidecar/inputs/`, `/workspace/home/work/`, `/tmp/sidecar/scratch/`), that "persists between
     turns" and `/workspace/project` appear nowhere in any payload's instructions, that the final-report sentence is
     in student sandbox `INSTRUCTIONS` but not in a Slack sandbox payload, and that the no-files reset sentence is
     present in both. `tests/test_hosted_slack_payload.py` is at 290 lines: if it would pass 300, put the new
     assertions in a new `tests/test_hosted_slack_payload_sandbox.py`.
4. **Live spike, new file `tests/live/test_sandbox_spikes.py`** (marker `live`, deselected by default; copy the
   fixture style of `tests/live/test_slack_spikes.py`: the `Live` dataclass with `key` hidden from repr, key from
   `OPENROUTER_LIVE_KEY`, workspace from `OPENROUTER_LIVE_WORKSPACE` else `config.SLACK_WORKSPACE`; `AdvisorClient`).
   One test, `test_home_survives_a_sleep`, using the exact shell setup the server sends (`engine: openrouter`,
   `container_reference`, `"nu_" + secrets.token_hex(16)`, `file_ids: []`, `network_policy: disabled`), model
   `openai/gpt-4.1-mini`, `store: False`:
   - Turn 1: tell the model to run exactly one shell command that writes unique random markers (generate them in the
     test with `secrets.token_hex(8)`) to `/workspace/home/work/keep.txt`, `/workspace/home/outputs/out.txt`,
     `/workspace/home/gone.txt`, `/workspace/project/p.txt` and `/tmp/sidecar/scratch/t.txt`, then deletes
     `/workspace/home/gone.txt`, then lists them. Print the container file list
     (`GET {API_BASE}/containers/{id}/files?limit=100`).
   - Wait `SPIKE_SLEEP_S` seconds (env var, default 480) so the container sleeps; print a line before waiting.
   - Turn 2, same container id, no files re-attached: tell the model to `cat` all five paths (printing MISSING for
     absent ones) and run `ls -la /workspace/home /workspace/home/work /tmp/sidecar 2>&1`. Print the shell item's
     output and the container file list again, and download `work/keep.txt` through
     `GET {API_BASE}/containers/{id}/files/{file_id}/content` and print whether its bytes equal the marker.
   - Turn 3, a fresh `nu_` id: `cat /workspace/home/work/keep.txt` and print the result (expect missing).
   - Print a `[spike]` summary line per path: survived / missing / deleted as expected.
   - Assert only that the requests succeeded (as the Slack spikes do); the printed facts are the result.
   - Never print the key. Never print anything but the shell items, file lists, statuses and byte comparisons.
5. **Docs**: `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md` (~:104 and ~:122-123): replace the
   `/workspace/project/...` claims with the new layout and the 30-day/home-only rule, citing the 2026-10-21 spike. The
   `src/openrouter_image_mcp/CLAUDE.md` note if it describes the sandbox folders. Run
   `node scripts/self-documentation/generate.js` and stage its output if it changes.

## Constraints

- First: `git switch -c fix/sandbox-folders 9df2fc4fe9c6717e6c40c827c18772d324251b5f` in your worktree, then `uv sync`,
  `git config core.hooksPath .githooks`.
- Never `--no-verify`, never a bare `git stash`, no destructive git, no push. Work only in your worktree.
- No key-shaped literals. 300-line file ratchet; functions under 50 lines.
- The worktree guard refuses complex inline shell; write helper scripts to files and keep commands simple.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; subject like
  `fix(advisor): sandbox folders match what OpenRouter saves`.
- Before reporting: `uv run ruff check`, `uv run pytest` (all pass),
  `TEST_DATABASE_URL=postgresql://john_renaldi@127.0.0.1:54999/sidecar_test uv run pytest -m pg`,
  `node scripts/self-documentation/generate.js --check`, and `uv run pytest -m live tests/live/test_sandbox_spikes.py`
  with no key set (must skip cleanly).
- Do not dispatch subagents.

## Report

Return only: status (DONE / DONE_WITH_CONCERNS / BLOCKED), commit SHA(s), your worktree path, a one-line test summary,
the final text of `SANDBOX_SAFETY` and the files part of `upload_notes` (verbatim), and concerns.
