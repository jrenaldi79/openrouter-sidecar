---
title: "2026-10-21-slack-sidecar: task 1 brief given to the implementer subagent"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 1 brief given to the implementer subagent."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

### Task 1: Dependencies, settings, redaction and test mapping

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add "slack-sdk==3.45.0" "aiohttp==3.14.4"`)
- Create: `src/openrouter_image_mcp/hosted/slack/__init__.py`, `hosted/slack/settings.py`, `hosted/slack/CLAUDE.md`
- Modify: `hosted/settings.py` (field `slack: SlackSettings | None = None`, set from `load_slack_settings(env)`)
- Modify: `logs.py` (`_KEY_RE` also masks `xox[abp]-…` and `xapp-…`), `scripts/checks/secret_scan.py` (pattern
  "Slack token" for `xox[abp]-[0-9A-Za-z-]{10,}` and `xapp-\d-[0-9A-Za-z-]{10,}`)
- Modify: `scripts/checks/missing_tests.py` (parent `src/openrouter_image_mcp/hosted/slack` requires
  `tests/test_hosted_slack_{stem}*.py`)
- Test: `tests/test_hosted_slack_settings.py`, `tests/test_logs.py`, `tests/test_checks_secret_scan.py`,
  `tests/test_checks_missing_tests.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) SlackSettings(enabled: bool, bot_token: str | None (repr=False), app_token: str | None
  (repr=False), context_chars: int = 8000, daily_cap_usd: float = 4.0, max_concurrent: int = 3, user_limit: int = 10)`;
  `load_slack_settings(env: Mapping[str, str]) -> SlackSettings`; `HostedSettings.slack`.

- [ ] **Step 1: Write failing tests.**
  - `test_disabled_by_default`: an empty env gives `enabled is False`, defaults 8000 / 4.0 / 3 / 10.
  - `test_enabled_requires_tokens`: `SIDECAR_SLACK_ENABLED=true` without `SLACK_BOT_TOKEN` or `SLACK_APP_TOKEN` raises
    `ValueError`. A bot token not starting `xoxb-`, or an app token not starting `xapp-`, also raises.
  - `test_numbers_validated`: `SIDECAR_SLACK_CONTEXT_CHARS=0`, `SIDECAR_SLACK_MAX_CONCURRENT=0`, a negative cap, or a
    non-number raises `ValueError`.
  - `test_tokens_not_in_repr`: `repr(settings)` has no token text.
  - `test_redacts_slack_tokens`: `redact("xoxb-" + "1" * 12 + "-abc")` contains no digits run, and the same for `xapp-1-`.
  - `test_secret_scan_flags_slack_token`: a fixture built at runtime (`"xoxb-" + "9" * 24`) is reported.
  - `test_slack_subpackage_needs_test`: `expected_tests("src/openrouter_image_mcp/hosted/slack/parser.py")` returns
    `"tests/test_hosted_slack_parser*.py"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_hosted_slack_settings.py tests/test_logs.py tests/test_checks_secret_scan.py tests/test_checks_missing_tests.py`.
  Expected: FAIL (module missing, patterns missing).
- [ ] **Step 3: Implement.** `load_slack_settings` parses booleans with the `_TRUE` set from `hosted/settings.py`.
  Write the folder note `hosted/slack/CLAUDE.md` (five-field frontmatter, then Owns / Invariants / Tests / Gotchas,
  shaped like `hosted/CLAUDE.md`). Its invariants: identity from the signed event only; queries bind owner and
  workspace; Slack never calls `AdvisorService.send`.
- [ ] **Step 4: Run** the same files, then `uv run pytest -q` and `uv run ruff check`. Expected: PASS.
- [ ] **Step 5: Regenerate docs** with `node scripts/self-documentation/generate.js` and stage `docs/index.md`.
- [ ] **Step 6: Commit** `feat(slack): settings, token redaction and test mapping`.

