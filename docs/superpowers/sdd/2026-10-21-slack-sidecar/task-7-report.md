---
title: "Task 7 report: thread map and channel memory"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 7 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 7 report: thread map and channel memory

**Status:** DONE

## Implemented
- `hosted/slack/threads.py` (127 lines): `ThreadChat`, `ThreadMap` (get, put, seen, answered, last_answered, archive, chats_in), table `slack_threads` plus partial unique index on unarchived keys. Injectable `clock`.
- `hosted/slack/memory.py` (73 lines): `MAX_NOTES`, `MAX_CHARS`, `MEMORY_PREFACE`, `ChannelMemory` (add, list, forget, instructions), table `slack_memory`. Injectable `clock`.
- Tests: `tests/test_hosted_slack_threads.py` (9), `tests/test_hosted_slack_memory.py` (8), all with `db_target`.
- Folder note `hosted/slack/CLAUDE.md` Owns list updated; `docs/index.md` regenerated.

## TDD
- RED: both test files failed at collection, `ModuleNotFoundError: No module named 'openrouter_image_mcp.hosted.slack.memory'` (captured in `task-7-red.txt`).
- GREEN: 17 passed (SQLite), 17 pg-parametrized skipped locally (no `TEST_DATABASE_URL`; CI `postgres` job runs them). Full suite: 1037 passed, 366 skipped. `ruff check` clean.

## Decisions
- Every query binds `owner=? AND workspace=?`; tests cover a different owner and a different workspace alone.
- `put`: UPDATE the unarchived row (chat_id replaced, `last_seen_ts` and `last_answered_at` reset), else INSERT ON CONFLICT DO NOTHING, retried once if a concurrent put won.
- `seen`: compares Slack ts with `Decimal` (exact, no float loss), keeps the original string; the UPDATE is guarded on the value read so a concurrent newer ts is never overwritten.
- Ordering uses `created_at, rowid` (threads and memory) rather than `created_at, id`: ids are random hex, so ties on a coarse clock (Windows CI) would renumber notes unpredictably. `rowid` exists on both engines.
- Error texts for empty note and unknown `n` are mine (brief only fixed the "full" text).

## Concerns
- `add` checks caps then inserts in one connection without a lock; two simultaneous adds in one channel could exceed a cap by one. Low risk (one channel, human-paced); not worth a lock.
- pg paths not executed locally.

## Fix round 1

**Finding:** `ThreadMap.seen` used `last_seen_ts IS ? OR last_seen_ts=?`; Postgres rejects a bound parameter after `IS` (`SyntaxError at or near "$5"`), failing `test_put_replaces_active_row_and_resets_seen[pg]` and `test_seen_only_moves_forward[pg]`.

**Change (`threads.py`):**
- `seen` builds the guard in two cases: stored value None gives `last_seen_ts IS NULL` with no parameter; otherwise `last_seen_ts=?`. The forward-only check and the compare-and-set on the value read are unchanged.
- `put` now raises `RuntimeError` if both of its attempts (update, insert) fail, instead of returning silently.

**Verification:**
- pg + SQLite: `TEST_DATABASE_URL="postgresql://$(whoami)@127.0.0.1:54999/sidecar_test" uv run --frozen pytest -p no:cacheprovider tests/test_hosted_slack_threads.py tests/test_hosted_slack_memory.py -q` -> `34 passed` (17 SQLite + 17 Postgres, none skipped).
- `uv run pytest -q` -> `1037 passed, 366 skipped, 7 deselected`.
- `uv run ruff check` -> `All checks passed!`
- No new test for the `put` RuntimeError: it needs a concurrent insert race that cannot be forced deterministically.
