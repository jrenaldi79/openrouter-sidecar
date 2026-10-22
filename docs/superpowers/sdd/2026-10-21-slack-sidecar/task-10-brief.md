---
title: "2026-10-21-slack-sidecar: task 10 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 10 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 10: Context builder

**Files:** Create `hosted/slack/context.py`. Test `tests/test_hosted_slack_context.py`.

**Interfaces:**
- Consumes: `SlackApi`, `SlackMessage`, `FilePacket` (`session_files.py`), `MAX_FILE_CHARS`.
- Produces:
  - `TZ = "America/Chicago"`, `THREAD_CHARS = 80_000`, `PINS_CHARS = 4_000`, `CHANNEL_MESSAGES = 20`.
  - `CONTEXT_PATH = "slack/context.md"`.
  - `DATA_PREFACE` is the spec's sentence.
  - `NameCache(api, *, ttl_s=3600.0)` has `async name(user_id) -> str`. It uses `profile.display_name`, else
    `real_name`, else the id.
  - `format_message(msg: SlackMessage, name: str) -> str` gives `"[Oct 20 10:14] Name: text"`. Bot posts use
    `msg.label`; a Claude Tag post shows as `Claude`.
  - `Gathered(thread, pins, channel, trigger, urls)` holds the raw messages fetched once per request.
  - `ContextBuilder(api, names, *, channel_chars=8000)`, async:
    - `gather(channel, thread_ts, trigger: SlackMessage) -> Gathered`
    - `render(g: Gathered, since_ts: str | None) -> Context`
    - `files(trigger) -> tuple[list[FilePacket], list[str]]`
  - `Context(text: str, newest_ts: str, files: list[FilePacket], listed: list[str], urls: list[str])`.

Rules from the spec:
- First turn (`since_ts` None): pins, then up to 20 channel messages before the root (newest kept within
  `channel_chars`), then the thread.
- Later turns: only thread messages with `ts > since_ts`.
- `channel_join` and `channel_leave` are dropped.
- A thread over `THREAD_CHARS` keeps the root plus the newest messages, with
  `"[… N earlier messages trimmed; use read_thread …]"`.
- Text files on the trigger only, when `mimetype` starts with `text/` or the extension is in
  `{csv, md, txt, py, js, ts, json, yaml, yml, sql, html, css}`, and within `MAX_FILE_CHARS`. They become
  `slack/files/<name>`. Other files in the thread are listed by name.
- `urls` are the `http(s)` URLs in the thread and trigger.

- [ ] **Step 1: Write failing tests** (with `FakeSlack`).
  - `test_first_turn_includes_pins_channel_thread`.
  - `test_channel_part_capped`: 20 long channel messages stay within `channel_chars`, newest kept.
  - `test_later_turn_delta_only`.
  - `test_labels_claude_and_sidecar`.
  - `test_join_leave_dropped`.
  - `test_long_thread_trimmed_keeps_root_and_newest`.
  - `test_trigger_text_files_only`: a CSV on the trigger becomes a packet; a PDF and a CSV elsewhere are listed only.
  - `test_top_level_mention` (Review Focus 2): `thread_ts` equal to the trigger ts, with a thread holding only the
    trigger, gives a context with the trigger.
  - `test_deleted_root`: `replies` without the root message still renders.
  - `test_preface_present`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): channel context builder`.

