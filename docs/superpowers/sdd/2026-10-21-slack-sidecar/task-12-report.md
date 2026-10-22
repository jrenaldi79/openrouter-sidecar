---
title: "Task 12 report: Slack payload"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): task 12 implementer report."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# Task 12 report: Slack payload

Status: done. Code: `src/openrouter_image_mcp/hosted/slack/payload.py` (197 lines). Tests: `tests/test_hosted_slack_payload.py` (42 tests, written first and seen failing on import).

- `build_slack_payload` starts from `build_payload`, forces `store: False`, replaces instructions (SLACK_INLINE / SLACK_SANDBOX / SLACK_COMPARE + memory after a blank line), sets tools and `max_tool_calls` (12 / 24).
- `compare` swaps the chat to inline first (no shell, no function tools). `FUSION_FORCED = True`: `tools=[fusion_tool]` + `FUSION_CHOICE`; False: model `openrouter/fusion` + `plugins=[{"id":"fusion", panel, analyst, max_tool_calls, tools}]`. Both tested via monkeypatch.
- `fetch_domains` and one `_worker_tools` helper enforce the web_fetch rule at the top level, in the helper subagent and in Fusion tools.
- Extra beyond the brief: optional `listed=()` kwarg on `build_slack_payload` appends `files_note(listed)` to the brief (not instructions, since the names come from Slack). Task 14 should pass `Context.listed`.
- `fetch_domains` also drops URLs with a backslash in the netloc (browser/urlparse host mismatch), all-digit TLDs (shorthand IPs like 127.1) and labels failing a DNS label regex (wildcards, hyphen edges, >63 chars).

Verification: ruff clean; full pytest 1263 passed, 384 skipped, 7 deselected.

Concerns: the exact parameter shapes for `openrouter:web_search`/`web_fetch`/`subagent` and the fusion `plugins` entry are inferred from the spec and the spike (only fusion and datetime shapes are exercised there); FUSION_FORCED must be set from the live spike result.

## Fix round 1

1. Sandbox payloads keep the upload pointers and container rules. `advisor_payload.py` now exposes `SANDBOX_SAFETY` and `upload_notes(uploads)`; `INSTRUCTIONS` is rebuilt from the same pieces and was checked byte-identical to the old string, as was `_sandbox_instructions` for files and skills uploads. `SLACK_SANDBOX` adds the outputs rule, `SANDBOX_SAFETY` and the upload notes. Test added: bundle path, `/workspace/project/inputs/`, skill path, safety text and shell `file_ids`. `advisor_payload.py` is 113 lines before and about 125 after (under 300).
2. No Slack-sourced string in instructions: generic web_fetch line; domains only in tool parameters. Tests cover a URL host, an allowlist host and a listed file name.
3. `SHARED_SUFFIXES` frozenset plus the two-label registry rule; `alice.github.io` and `bbc.co.uk` stay allowed. Tested.
4. `urls` need an http(s) scheme (`//evil.com`, `evil.com/x`, `mailto:`, `data:` dropped); allowlist entries may be bare (including `host:port`, `user@host/x`) but an explicit other scheme is rejected. Tested.
5. Allowlist entries take slots first, thread hosts fill up to 20, output sorted. Tested with 25 thread hosts.
6. `SLACK_COMPARE` describes the panel (search always; fetch only on linked or allowed domains) and says the outer model has no other tools.
7. `listed: Sequence[str]`, and `fetch_domains` takes `Sequence[str]`.
8. Two new live spikes, one request each: `test_fetch_subdomain_matching` and `test_fusion_plugin_accepts_tools`. They print their facts and don't gate. The decision record's pending list now has items 4 and 5.

Verification: ruff clean; full pytest 1283 passed, 384 skipped, 9 deselected; the advisor payload tests pass unchanged.
