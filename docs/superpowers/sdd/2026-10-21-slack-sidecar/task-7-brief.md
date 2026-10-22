---
title: "2026-10-21-slack-sidecar: task 7 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 7 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 7: Thread map and channel memory

**Files:** Create `hosted/slack/threads.py` and `hosted/slack/memory.py`. Test `tests/test_hosted_slack_threads.py`
and `tests/test_hosted_slack_memory.py`.

**Interfaces:**
- Produces (threads):
  - `ThreadChat(chat_id, alias, mode: Literal["inline", "sandbox"], last_seen_ts: str | None)`.
  - `ThreadMap(target)`. Every method takes `scope` first:
    - `get(scope, channel_id, thread_ts, alias, mode) -> ThreadChat | None`
    - `put(scope, channel_id, thread_ts, alias, mode, chat_id) -> None`
    - `seen(scope, chat_id, ts) -> None`
    - `answered(scope, chat_id) -> None`
    - `last_answered(scope, channel_id, thread_ts) -> ThreadChat | None`
    - `archive(scope, channel_id, thread_ts) -> int`
    - `chats_in(scope, channel_id, thread_ts: str | None = None) -> list[ThreadChat]`
  - Table: `slack_threads(id, owner, workspace, channel_id, thread_ts, alias, mode, chat_id, last_seen_ts, last_answered_at REAL, archived_at REAL, created_at REAL)`,
    with a unique index on `(owner, workspace, channel_id, thread_ts, alias, mode) WHERE archived_at IS NULL`.
- Produces (memory):
  - `MAX_NOTES = 50`, `MAX_CHARS = 8000`.
  - `MEMORY_PREFACE` is the spec's quoted sentence.
  - `ChannelMemory(target)`:
    - `add(scope, note, author) -> int` returns the 1-based number.
    - `list(scope) -> list[str]`
    - `forget(scope, n) -> str`
    - `instructions(scope) -> str` gives `""` when there are no notes, otherwise the preface plus numbered notes.
  - Table: `slack_memory(id, owner, workspace, note, author, created_at REAL)`.
  - When full, `add` raises `BadRequestError("Channel memory is full (50 notes, 8,000 characters). Forget a note first.")`.
    An unknown `n` raises `BadRequestError`.

- [ ] **Step 1: Write failing tests** (with `db_target`).
  - `test_mode_is_part_of_key`: inline and sandbox chats for the same alias coexist.
  - `test_last_answered_picks_latest`.
  - `test_archive_hides_and_allows_new`: after `archive`, `get` gives None and `put` with a new chat succeeds.
  - `test_threads_scoped_by_principal`: team B's scope sees none of team A's rows.
  - `test_memory_caps`: 51 notes, or a total over 8,000 characters, raise. `forget(2)` renumbers.
  - `test_memory_scoped`.
  - `test_instructions_text`: starts with the preface, and has `1. …` lines.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run**, including pg. Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): thread map and channel memory`.

