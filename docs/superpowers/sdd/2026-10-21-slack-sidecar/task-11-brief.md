---
title: "2026-10-21-slack-sidecar: task 11 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 11 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 11: Channel function tools

**Files:** Create `hosted/slack/slack_tools.py`. Test `tests/test_hosted_slack_slack_tools.py`.

**Interfaces:**
- Consumes: `SlackApi`, `NameCache`, `format_message`.
- Produces:
  - `READ_LIMIT = 200`, `CHARS_CAP = 30_000`, `SEARCH_PAGES = 5`, `SEARCH_MESSAGES = 1000`.
  - `TOOL_SPECS: list[dict]`: three Responses function tools (`{"type":"function","name","description","parameters"}`),
    each with `additionalProperties: false` and no channel argument. The tools are:
    - `read_thread(thread_ts?: string, limit?: integer)`
    - `read_channel_history(before?: string, limit?: integer)`
    - `search_channel(query: string, limit?: integer)`
  - `FUNCTION_NAMES = frozenset(...)`.
  - `ChannelTools(api, names, channel_id, thread_ts)`:
    - `async call(name: str, arguments: str) -> tuple[str, str]` returns the output text and a step label such as
      "Read 60 earlier messages", "Searched the channel for 'x'" or "Read the thread".
    - It parses `arguments` as JSON, ignores unknown keys, and clamps `limit` to 1..200 (default 50 for reads, 20
      for search).
    - Any `SlackCallError` or bad JSON returns `("Error: <short reason>", "Tool error")`; it never raises.

- [ ] **Step 1: Write failing tests.**
  - `test_forged_channel_ignored` (isolation): `call("read_channel_history", '{"channel":"C_OTHER"}')` reads only `C1`.
  - `test_history_caps`: 500 messages give at most 200 and at most 30,000 characters.
  - `test_search_pages_and_filters`: 7 pages available, at most 5 read, matches are case-insensitive on all terms.
  - `test_read_thread_defaults_current`.
  - `test_tool_error_is_output`: `fail_next("history", "ratelimited")` returns an output starting with `Error:`.
  - `test_specs_have_no_channel_param`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** `feat(slack): channel-bound function tools`.

