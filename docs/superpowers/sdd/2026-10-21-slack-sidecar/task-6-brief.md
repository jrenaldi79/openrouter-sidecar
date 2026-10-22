---
title: "2026-10-21-slack-sidecar: task 6 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 6 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 6: Replies

**Files:** Create `hosted/slack/replies.py`. Test `tests/test_hosted_slack_replies.py`.

**Interfaces:**
- Consumes: `SlackApi`, `SlackCallError`, `RateLimitedError.kind`, `logs.redact`.
- Produces:
  - `SPLIT_AT = 3500`, `MAX_PARTS = 3`.
  - `escape(text) -> str`.
  - `clean(text, secrets: Collection[str]) -> str`: runs `redact`, replaces each secret with `***`, then `escape`.
  - `split_answer(text) -> list[str]`.
  - `footer(model_label: str, cost_usd: float | None, warning: str | None = None) -> str`. It gives
    `"<label> · $0.0123"`, with `"cost unknown"` when the cost is None, and the warning on its own line.
  - `limit_text(error: RateLimitedError) -> str`:
    - daily: "Your team's daily Sidecar cap ($4) is reached. It resets at midnight UTC. Ask your instructor if you need more."
    - concurrency: "Your team already has 3 answers running. Try again in a minute."
    - rate: "You've sent 10 requests in 10 minutes. Try again in about N minutes."
  - `budget_text() -> str`: "Your team's monthly Sidecar budget is used up. It resets at the start of next month. Ask your instructor."
  - `provider_error_text(label, others: list[str]) -> str` names the model and suggests another alias.
  - `WorkingMessage(api, channel, thread_ts, label, *, every_s=10.0, clock=time.monotonic)` with async methods:
    - `start() -> str` posts `"Sidecar · <label> is working…"` as username `Sidecar · <label>`.
    - `step(text) -> None` edits only when the text changed and at least `every_s` has passed. It drops intermediate
      steps, swallows `SlackCallError` and logs it.
    - `finish(text) -> None` always edits.

- [ ] **Step 1: Write failing tests.**
  - `test_escape_blocks_mentions_and_links` (Review Focus 4): `escape("<!channel> <@U1> <https://e|x> a&b")` contains
    no `<` or `>`, and `&` becomes `&amp;`.
  - `test_clean_redacts_key_and_slack_token`.
  - `test_split_on_paragraphs`: each part is at most 3,500 characters, every part but the last ends at a `\n\n`
    boundary, and joining the parts gives the original text back.
  - `test_split_long_paragraph_hard_cut`.
  - `test_more_than_three_parts_detected`: 20,000 characters gives more than 3 parts. The caller rule is tested in
    Task 14.
  - `test_footer_plain_text`.
  - `test_limit_texts`, one per kind.
  - `test_working_message_throttle`: on a fake clock, steps at t=0, 3 and 12 give one edit at t=12 with the t=12
    text. A repeat of the same text never edits.
  - `test_working_message_ignores_429`: `FakeSlack.fail_next("update", "ratelimited", 5)`, then `step` doesn't raise.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(slack): reply escaping, splitting and working messages`.

