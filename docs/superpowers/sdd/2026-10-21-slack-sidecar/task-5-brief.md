---
title: "2026-10-21-slack-sidecar: task 5 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 5 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 5: Command parser

**Files:** Create `hosted/slack/parser.py`. Test `tests/test_hosted_slack_parser.py`.

**Interfaces:**
- Produces (frozen dataclasses):
  - `Ask(aliases: tuple[str, ...], question: str)`. Empty `aliases` means the default, or the last model that
    answered. Dispatch decides the mode with `wants_sandbox`.
  - `Compare(question)`, `Models()`, `SetDefault(alias)`, `Usage()`, `Remember(note)`, `Recall()`, `Forget(n: int)`,
    `Help()`, `ThreadCommand(name: Literal["restart", "status", "stop"])`, `Empty()`.
  - `Intent` is their union.
  - `command_text(text: str, bot_user_id: str) -> str` decodes `&amp; &lt; &gt;`, turns a link `<https://x|label>`
    into `https://x`, and keeps other `<@U…>` mentions as they are. It finds the first `<@BOT>` anywhere. If the
    words after it start with a command word (`ask`, `compare`, `models`, `set`, `usage`, `remember`, `what`,
    `forget`, `help` or `!`), it returns those words. Otherwise it returns the whole text with the mention removed,
    as a question.
  - `parse(text: str, aliases: Collection[str]) -> Intent`.
  - `wants_sandbox(question: str, has_text_file: bool) -> bool`. It's true for `use sandbox`, for any of `run `,
    `execute`, `chart`, `plot`, `graph`, `analyze`, `analyse` or `calculate` (case-insensitive), or when
    `has_text_file` is set and the question names the file's extension or says "attached".

Rules:
- `ask` takes the first word as an alias. Unknown names are handed to dispatch to validate.
- Then `and <word>` is taken as another alias only if `<word>` is in `aliases` or contains `/`.
- An exact id (contains `/`) is an alias.
- Commands match case-insensitively at the start.
- `forget <n>` needs an integer of 1 or more, or the whole text is a question.

- [ ] **Step 1: Write failing tests**, one per row of the spec's Commands table. Also:
  - `test_ask_two_models`: `"ask gemini and gpt is this right?"` gives `Ask(("gemini", "gpt"), "is this right?")`.
  - `test_and_not_alias_stays_in_question`: `"ask gemini and explain"` gives `Ask(("gemini",), "and explain")`.
  - `test_exact_id`: `"ask openai/gpt-5 hi"` gives the alias `openai/gpt-5`.
  - `test_plain_question`: `"what's wrong here?"` gives `Ask((), …)`.
  - `test_bang_commands`: `"!restart"` gives `ThreadCommand("restart")`, and likewise for `!status` and `!stop`.
  - `test_mention_mid_text` (Review Focus 1): `command_text("hey <@B> ask gemini is &lt;x&gt; ok", "B")` gives
    `"ask gemini is <x> ok"`, which parses to `Ask(("gemini",), "is <x> ok")`.
  - `test_mention_at_end_is_question`: `command_text("is this right <@B>?", "B")` gives `"is this right ?"`.
  - `test_link_kept_as_url`.
  - `test_wants_sandbox` covers each keyword, plus a negative case ("what do you think?").
- [ ] **Step 2: Run** `uv run pytest tests/test_hosted_slack_parser.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with plain string handling, as one small function per command. No regex over 1 line.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): command parser`.

