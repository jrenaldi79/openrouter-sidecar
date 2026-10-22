---
title: "SDD ledger \u2014 plan: docs/superpowers/plans/2026-10-21-slack-sidecar.md"
description: "Archived planning record for the Slack Sidecar build (docs/superpowers/plans/2026-10-21-slack-sidecar.md): progress ledger (task completions, fix rounds, rulings)."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# SDD ledger — plan: docs/superpowers/plans/2026-10-21-slack-sidecar.md

Spec: docs/superpowers/specs/2026-10-21-slack-sidecar-design.md (reachable).
Worktree: .claude/worktrees/slack-sidecar, branch feat/slack-sidecar, from 709c182.
Baseline: 840 passed, 317 skipped, 4 deselected.
Environment: OPENROUTER_LIVE_KEY unset; TEST_DATABASE_URL unset (pg tests run in CI).

## Pre-flight scan

### Shared files / interfaces between tasks

| Tasks | Producer -> consumer | Finding |
| --- | --- | --- |
| 1 -> 21 | SlackSettings / HostedSettings.slack -> build_slack | consistent |
| 2 -> 6, 9, 10, 11, 14, 15, 20 | SlackApi methods (replies, history, pins, channel_info, members, user, post, update, upload, download) | consistent; every consumer uses only these |
| 2 -> 3 | ChannelRow.active, ids -> team_principal | consistent |
| 3 -> 17 | KeyProvisioner.ensure(principal, team=TeamKey) | consistent |
| 4 -> 14 | Limits.admit kwargs, AdvisorResults.create slack kwargs, SlackJobs.mark_posted | consistent |
| 4 -> 16 | SlackJobs.spend_by_user / spend_since | consistent |
| 4 -> 20 | SlackJobs.orphans(process_id) | consistent |
| 5 -> 17 | parse(text, aliases), command_text, wants_sandbox | consistent |
| 6 -> 14 -> 20 | post_answer: Task 14 moves it to replies.py only "if runner passes 260 lines"; Task 20 consumes "replies.post_answer (or the runner's post step)" | CONFLICT (ruling R2) |
| 6 limit_text vs 2/17 per-row caps | limit_text strings hardcode $4 / 3 / 10 while caps are per registry row | CONFLICT (ruling R3) |
| 7 -> 14, 17 | ThreadMap / ChannelMemory methods | consistent |
| 8 -> 9 | GUEST_OFF defined in welcome.py, used by guests.py | consistent (8 before 9) |
| 10 -> 11, 12 | NameCache, format_message, Context.urls, TZ | consistent |
| 12 -> 13, 14 | payload dict; FUSION_CHOICE fallback depends on Task 0 | depends on R1 |
| 13 -> 15 | AdvisorClient.container_file | consistent |
| 14 -> 15, 16 | SlackRunner(outputs=None) wired in 15; budget_warning wired in 16 | consistent |
| 14 -> 17 | runner.running / runner.stop / SlackServices | consistent |
| 17 -> 19 | Dispatcher.on_mention(team_id, event) | consistent |
| 9 -> 19 | GuestGuard.on_member_joined / on_channel_shared | consistent |
| 18 -> 21 | Task 18 makes Health own the OwnerLock; Task 21 says "replace health.start()'s single attempt with OwnerLock hooks" but no interface lets wiring add hooks | GAP (ruling R4) |
| 20 -> 21 | recover(jobs, results_for, api, registry, process_id) | consistent |
| 2, 9, 16 -> 22 | ops scripts call registry/new_row/channel_problem/usage | consistent |
| 0 -> 12, 13, 15 | live API facts feed fusion forcing, replay, filename form | depends on R1 |

### Per-task self-consistency

| Task | Finding |
| --- | --- |
| 0 | Requires OPENROUTER_LIVE_KEY to run; not set (ruling R1) |
| 1 | Files, tests and interfaces agree; folder note + docs regen included |
| 2 | Agrees; FakeSlack in tests/slack_fakes.py is a helper (not mapped by missing_tests) |
| 3 | Agrees |
| 4 | pg run named; TEST_DATABASE_URL unset locally (ruling R5) |
| 5 | Agrees after self-review fixes (Ask has no sandbox field; command_text) |
| 6 | limit_text conflict (R3) |
| 7 | Agrees |
| 8 | Agrees |
| 9 | Agrees |
| 10 | Agrees |
| 11 | Agrees |
| 12 | Fusion fallback branch depends on Task 0 (R1) |
| 13 | Agrees |
| 14 | post_answer placement (R2) |
| 15 | Agrees; wires Outputs into runner |
| 16 | Agrees; runner must fetch key_info via current_key for budget_warning |
| 17 | Agrees |
| 18 | Hook-registration gap (R4) |
| 19 | Agrees |
| 20 | post_answer (R2) |
| 21 | Hook-registration gap (R4) |
| 22 | Agrees (no unit tests by design) |
| 23 | Agrees |
| 24 | Manual, needs the user |

### Rulings

- R1 Ruling: Task 0 writes the spike tests and verifies they collect, commits them, and records "pending live run" in the decision record; the live run waits for the user to export OPENROUTER_LIVE_KEY in their own shell before Task 12 — the controller never handles keys, and Tasks 1-11 don't depend on the results — if wrong, Tasks 12/13/15 may need small changes once the facts arrive.
- R2 Ruling: Task 14 always puts the final post step in replies.py as `async post_answer(api, channel, thread_ts, label, text, footer) -> None`, and Task 20 calls it — one post path for live and recovered answers — cost if wrong: none beyond a slightly larger replies.py.
- R3 Ruling: `limit_text(error, *, daily_cap_usd, max_concurrent, user_limit)` formats the numbers from the row/settings; the plan's sample strings are what defaults produce — the spec says each team's caps come from its registry row — cost if wrong: a signature change in Task 6 only.
- R4 Ruling: Task 18 exposes the lock as `Health.lock: OwnerLock` with `on_gain`/`on_lose` lists that callers may append to before `start()`; Task 21 appends Slack's hooks there — cost if wrong: small change in Task 21 wiring.
- R5 Ruling: pg-marked tests are written in each storage task and run in CI's postgres job; locally they skip while TEST_DATABASE_URL is unset — cost if wrong: a Postgres-only bug surfaces in CI instead of locally.

## Progress

Task 0: implemented (709c182..a261e66), DONE_WITH_CONCERNS (not run live; model ids are guesses)
Task 0: ⚠️ trailer checked — commit ends with the Opus 5.5 Co-Authored-By line. Model validity settles on the first live run (R1).
Task 0: minor (deferred): shell spike should print raw shell items before its assert (test_slack_spikes.py:151-156)
Task 0: minor (deferred): download assert requires 200; a 302 fails though it's a useful fact (:162)
Task 0: minor (deferred): replay spike passes vacuously if the model never calls echo (:123-130)
Task 0: minor (deferred): fusion spike prints only item keys, not the item (:177-178)
Task 0: minor (deferred): unused `workspace` param in _download (:138)
Task 0: complete (commits 709c182..a261e66, review clean)
Task 1: implemented (a261e66..923ea99), DONE
Task 1: ⚠️ trailer checked — Opus 5.5 Co-Authored-By present.
Task 1: minor (deferred): deferred `from ..settings import _TRUE` reaches into the parent's private name to dodge an import cycle (slack/settings.py)
Task 1: minor (deferred): a malformed SIDECAR_SLACK_* number blocks startup even with Slack off; no test pins it; note it in HOSTED_OPERATIONS (Task 23)
Task 1: minor (deferred): `inf` passes as a daily cap and silently disables it; consider math.isfinite (slack/settings.py)
Task 1: minor (deferred): `_number` lacks type hints
Task 1: fix round 1/5 (1 addressed, 0 open — `\b` in _SLACK_RE removed, test added; commits 923ea99..745277b)
Task 1: minor (deferred): Slack-prefixed lookalike words are now partly masked (accepted, fail-closed)
Task 1: complete (commits a261e66..745277b, review clean)
Task 2: implemented (745277b..b58ce9c), DONE (concerns: default_model "gpt"; upload drops username; FakeSlack callable dicts) — in review
Task 2: security note (automated): SdkSlackApi.download sends the bot token to any URL; add https + files.slack.com host check and no redirects — goes into the fix round as Important
Task 2: Ruling: the reviewer rated the download-host issue Minor; I treat it as Important because it can send the bot token to a non-Slack host — secrets handling outranks polish — cost if wrong: one small extra fix round
Task 2: minor (deferred): ChannelRegistry.update rewrites every column from a stale snapshot; racing mark_welcomed can resend the welcome — fix when Task 22 uses update (SET only changed columns)
Task 2: minor (deferred): LABEL.match with `$` accepts a trailing newline; use fullmatch (registry.py)
Task 2: minor (deferred): _call maps only SlackApiError; SlackRequestError / transport errors escape unmapped (api.py)
Task 2: minor (deferred): download passes a 200 HTML login page through as file bytes
Task 2: minor (deferred): FakeSlack history treats `latest` as inclusive (real API is exclusive); callable-dict state can be clobbered by assignment
Task 2: minor (deferred): upload `username` accepted by the protocol and FakeSlack but ignored by files_upload_v2 (no such param)
Task 2: minor (deferred): _validate doesn't check default_model is in alias_map, max_concurrent >= 1, daily_cap >= 0; no test for a label clash on update
Task 2: fix round 1/5 (2 addressed, 0 open — default gemini; download https files.slack.com only, no redirects; commits b58ce9c..af510a0)
Task 2: minor (deferred): download allows non-443 ports and userinfo on files.slack.com; restrict port to None/443 and reject userinfo
Task 2: complete (commits 745277b..af510a0, review clean)
Task 3: Ruling: KeyProvisioner.ensure refuses `team` for non-Slack principals too (not in the plan) — keeps student keys from ever getting a team name or guardrail — cost if wrong: one extra guard to remove
Task 3: dispatched (BASE af510a0)
Task 3: implemented (af510a0..b5bec3d), DONE (SLACK_ISSUER lives in hosted/principal.py; _recheck takes no team; harness FakeOpenRouter gained guardrails list)
Task 3: minor (deferred): SlackCredentials.resolve uses pending_reason() (MCP middleware state); use NOT_READY directly (slack/principal.py:396)
Task 3: minor (deferred): recheck test doesn't assert the team guardrail; no disabled-team-key recheck test
Task 3: minor (deferred): guardrail "caches_per_name" test never counts GET /guardrails calls
Task 3: minor (deferred): DISABLED/UNAVAILABLE copy and log say "student"/"Your key" for team keys — revisit in Task 6/14 replies
Task 3: minor (deferred): test pins key_name(student, team_label=...) permissiveness
Task 3: complete (commits af510a0..b5bec3d, review clean)
Task 4: Ruling: Limits.window keeps per-(key, span) windows separate from the per-minute admit window — a Slack user's 10/10min must not eat the team's per-minute budget — cost if wrong: one data-structure change
Task 4: dispatched (BASE b5bec3d)
Task 4: implemented (b5bec3d..976f827), DONE (orphans is cross-team by design; runner must act under each row's own principal — carry to Task 20)
Task 4: minor (deferred): SlackJobs spend query duplicates Limits._durable's SUM; shared helper would prevent drift
Task 4: minor (deferred): running/orphans return SELECT * (prompt/answer bodies)
Task 4: minor (deferred): window deque per (key, span) is shared across callers with different limits; _recent never pruned
Task 4: minor (deferred): test_admit_overrides lacks a case for another owner's spend being excluded
Task 4: complete (commits b5bec3d..976f827, review clean)
Task 5: Ruling: parser edge cases the plan left open — Recall only for "what do you remember"; `set default` exact; `remember for this channel:` needs the colon; empty ask/compare keep "" for dispatch; mention-only is Empty; unknown `!x` is a question — follows the spec's "anything else is a question" — cost if wrong: small parser tweaks
Task 5: dispatched (BASE 976f827)
Task 5: implemented (976f827..c250d84), DONE (concerns: models/usage/help whole-text only; substring sandbox keywords e.g. "paragraph")
Task 5: Ruling: wants_sandbox uses token matching. "use sandbox" always triggers it. With a text file attached, an action token triggers it (chart/plot/graph-not-graphic/analy/calculat/comput/visuali/run/execute), as do the file's extension or "attached". Without a file, it needs run/execute + code/script/python/program, or an action token + data/dataset/csv/spreadsheet/numbers/table. Why: the spec says "asks to run code, chart or analyze data"; the plan's substring list sent "graphic design" and "plot of this story" to a paid sandbox. Cost if wrong: some data asks without those words run inline until the student says "use sandbox".
Task 5: minor (deferred): "ask gemini and yes/no only" takes yes/no as an alias (plan-mandated `/` rule; dispatch replies unknown alias)
Task 5: minor (deferred): text before a mid-text mention is dropped when the remainder starts with a command word
Task 5: minor (deferred): trailing comma/colon stays on an alias ("ask gemini, what")
Task 5: fix round 1/5 (1 addressed, 0 open — wants_sandbox token/intent matching; commits c250d84..a375a74)
Task 5: minor (deferred): "use sandbox" is a substring test ("reuse sandbox" matches); use a word-boundary search
Task 5: minor (deferred): `log`/`md` as extension tokens make "how do I log in" True when a file is attached
Task 5: complete (commits 976f827..a375a74, review clean)
Task 6: Ruling: split_answer returns [] for blank text; a paragraph over 3,500 characters is cut at the last newline or space within the final 500 characters, else hard-cut; WorkingMessage.finish never raises; start may raise — the spec is silent on these and the runner owns a failed start — cost if wrong: small replies.py tweaks
Task 6: dispatched (BASE a375a74)
Task 6: implemented (a375a74..52ba664), DONE (singular "1 minute"; own provider-error wording; extra ts property; failed step resets throttle)
Task 6: minor (deferred): WorkingMessage.finish gives no signal on failure; only SlackCallError is caught — Task 14 posts the answer as a new message anyway, so the finish edit is cosmetic
Task 6: minor (deferred): step/finish before start passes ts=None to update
Task 6: minor (deferred): rate text hardcodes "10 minutes" window
Task 6: minor (deferred): CRLF paragraphs aren't split on \r\n\r\n
Task 6: minor (deferred): no test that step logs via redact or that a failed step retries next time
Task 6: complete (commits a375a74..52ba664, review clean)
Task 7: Ruling: ThreadMap.put replaces an unarchived row's chat_id instead of failing; seen() only moves forward; notes are ordered by created_at then id — the plan is silent on these — cost if wrong: small store changes
Task 7: dispatched (BASE 52ba664)
Task 7: implemented (52ba664..dd554dc), DONE (orders notes by created_at, rowid instead of id; add-cap race; own error wording)
Task 7: Ruling: accept `created_at, rowid` note ordering over the plan's `created_at, id` — random ids would reorder ties unpredictably and rowid exists on both engines — cost if wrong: none
Environment: throwaway Postgres 14 at 127.0.0.1:54999 db sidecar_test (data in scratchpad/pgd). TEST_DATABASE_URL="postgresql://$USER@127.0.0.1:54999/sidecar_test". R5 revised: run pg tests locally from now on. Full -m pg run at dd554dc: 342 passed, 2 failed (Task 7 seen only) — Tasks 2-4 pass on Postgres.
Task 7: minor (deferred): Decimal NaN in _ts_value would raise on compare; add is_finite
Task 7: minor (deferred): memory add cap race (single replica, human pace)
Task 7: fix round 1/5 (2 addressed, 0 open — seen branches on NULL; put raises; pg 18/18; commits dd554dc..4df048f)
Task 7: minor (deferred): ThreadMap.put can raise RuntimeError on a lost double race — dispatch (Task 17) should let it surface as a generic error
Task 7: complete (commits 52ba664..4df048f, review clean)
Task 8: Ruling: the welcome example uses `gemini` only if the channel has that alias, otherwise the default alias; help lists aliases sorted, then commands, then the data notice under "What I send and keep" — the spec fixes the copy but not the layout — cost if wrong: copy edits
Task 8: dispatched (BASE 4df048f)
Task 8: implemented (4df048f..7bf2065), DONE (help escaped once; own wording for intro/models/usage lines; test reads parser._COMMAND_WORDS)
Task 8: minor (deferred): welcome_text doesn't escape row.default_model in the example line
Task 8: minor (deferred): :g budget format gives $37.5 / exponent for huge numbers
Task 8: minor (deferred): help command-word test is weak (words appear anyway); Default split test fragile
Task 8: complete (commits 4df048f..7bf2065, review clean)
Task 9: Ruling: the hourly backstop fails open when Slack's check errors (returns True, leaves the cache stale so the next call retries) — a Slack outage shouldn't silence every team, and guest-join and shared-channel events still deactivate. Cost if wrong: during a Slack API outage, a channel that gained a guest without an event could answer until the next successful check.
Task 9: dispatched (BASE 7bf2065)
Task 9: implemented (7bf2065..3314b02), DONE (post failure after deactivate swallowed; failed users.info on join drops cache; serial users.info per member) — in review
Task 9: review Approved (minors: stale row; non-SlackCallError path; doc claims an ops caller that doesn't exist yet; post failure never retried; _checked never pruned; concurrent scans duplicated)
Task 9: Ruling: fold the stale-row re-read and the doc wording into the fix round alongside the revised fail-open ruling. The stale row is security-relevant and the doc rule is a project rule. Cost if wrong: one extra registry read per mention.
Task 9: minor (deferred): GUEST_OFF post failure is never retried; _checked never pruned; concurrent allowed() calls duplicate scans; serial users.info per member
Task 9: fix round 1/5 started — bounded fail-open, join-failure recheck mark, registry re-read, doc wording
Task 9: Ruling (revises the earlier fail-open ruling after the automated security note): on SlackCallError, allowed() returns True only if a successful check happened within 2 × every_s; otherwise False. A member_joined whose users.info fails marks the channel "needs recheck", and allowed() denies until a check succeeds. Why: a security gate shouldn't fail open without recent evidence. Cost if wrong: during a long Slack outage, teams get no answers, which they wouldn't anyway.
Task 9: fix round 1/5 (4 addressed, 0 open — bounded fail-open on any exception; join mark denies until a check succeeds; registry re-read; docs say "will call"; commits 3314b02..b2a9c44)
Task 9: minor (deferred): a check started before a join-lookup failure can finish after it and clear the mark (narrow concurrent race); a non-SlackCallError from users.info on join propagates without setting the mark; _verified/_recheck are per-process (single owner replica runs Slack)
Task 9: complete (commits 7bf2065..b2a9c44, review clean)
Task 10: Ruling: DATA_PREFACE is the spec sentence; bot label "Claude" (any case) shows Claude, other bots their label, unlabeled bots "bot"; dates in America/Chicago without %-d (Windows CI); gather gains `first_turn=True` so later turns skip pins/history; replies paged ≤10 pages; trigger files over limit, non-UTF-8 or failing download are listed, never raised; packet names are safe basenames with -2/-3 dedupe; pins capped dropping oldest; mention markup left as-is — the brief leaves these open — cost if wrong: small render changes
Task 10: dispatched (BASE b2a9c44)
Task 10: implemented (b2a9c44..9f0600c), DONE_WITH_CONCERNS (trigger files + context can exceed 200k session limit — runner caps; listed not in text — payload tells model; added tzdata dep; pins/history errors skipped, thread error raises) — in review
Task 10: review Changes requested (Important: Context.urls delta-only on later turns; files ignore 200k session limit. Minors: body/label can forge lines; html entities; 10-page cap no marker; delta-oldest treated as root; NameCache unbounded)
Task 10: Ruling: files(trigger, *, budget=None) lists files that would overflow MAX_SESSION_CHARS minus the builder's max context size; runner may pass a smaller budget — the spec names the 200k session limit and the builder knows both sizes — cost if wrong: runner double-counts
Task 10: Ruling: fold cheap framing hardening (indent continuation lines, sanitize labels/names) and html.unescape into the fix round — untrusted input is labeled data per spec — cost if wrong: none
Task 10: Ruling: listed names are NOT in Context.text; payload task (Task 12) must surface `listed` and filter Sidecar's own replies if wanted — carry to Task 12 dispatch
Task 10: minor (deferred): 10-page reply cap has no marker; later-turn delta oldest treated as root; NameCache unbounded
Task 10: fix round 1/5 started — urls from full thread, session budget, framing hardening, unescape
Task 10: fix round 1/5 (4 addressed, 0 open; re-review raised pre-existing Important: NUL text attachment passes _read_text but validate_files refuses → whole call fails; Minor: html.unescape decodes legacy entities; commits 9f0600c..e6074a5)
Task 10: note for Task 14 runner: pass files(budget=) = remaining session room after existing session files; default assumes empty session
Task 10: fix round 2/5 started — reject NUL attachments (list them), decode only &amp; &lt; &gt;
Task 10: fix round 2/5 (2 addressed, 0 open — NUL text listed, Slack-only entity decode via parser.decode_entities; 70896cd held only CLAUDE.md, code landed in 7d41ec8; controller re-reviewed the 21-line diff directly)
Task 10: Ruling: controller inspects tiny (<30 line) fix diffs directly instead of dispatching a re-review — the diff fits on one screen — cost if wrong: a missed minor that the final review catches
Task 10: complete (commits b2a9c44..7d41ec8, review clean)
Task 11: Ruling: read_thread gains optional `before` (thread_ts?, before?, limit?) so the model can page into a trimmed middle — the spec's trim note sends the model to read_thread but the 2-arg form can't reach the middle of a long thread — cost if wrong: one extra optional param in a tool schema
Task 11: Ruling: ts arguments must match ^\d{1,12}\.\d{1,8}$ else "Error: bad timestamp"; outputs start with DATA_PREFACE, lines via format_message, chronological, newest kept within CHARS_CAP, with a footer giving the oldest ts to continue from; errors carry only SlackCallError.code; step-label queries clipped to 40 chars; no `strict` on specs — brief is silent — cost if wrong: copy tweaks
Task 11: dispatched (BASE 7d41ec8)
Task 11: implemented (7d41ec8..00280e8), DONE (search footer can't carry before — hints read_channel_history; >2000-reply thread loses newest, noted in output) — in review
Task 11: review Approved (Spec ✅; minors: \d accepts non-ASCII digits; step label unescaped into WorkingMessage.step; _scan no ts dedupe; name lookups before cap; footer hint wrong when cap cut; name-or-label duplication; test gaps)
Task 11: Ruling: WorkingMessage escapes every step/status text itself (replies.escape) so no caller can post model-chosen text raw — Safe posting bullet in spec — cost if wrong: double-escape if a caller already escaped (check)
Task 11: minor (deferred): names resolved before the cap (parity with ContextBuilder); name-or-label line duplicated in context.py/slack_tools.py; test gaps (10-page note, cap on thread/search, fixtures duplicated, blank lines in kit)
Task 11: fix round 1/5 started — ascii ts, escaped steps in WorkingMessage, scan dedupe, footer wording
Task 11: fix round 1/5 (4 addressed, 0 open — [0-9] ts, WorkingMessage escapes start/step/finish, _scan dedupes by ts, footer splits limit vs cap; commits 00280e8..7bec6e2; controller re-reviewed directly)
Task 11: note for Task 14 runner: WorkingMessage.step/finish take RAW text (they escape); never pass clean()/escaped text or it double-escapes
Task 11: complete (commits 7d41ec8..7bec6e2, review clean)
- R1 revised: Ruling: Tasks 12-13 proceed on the plan's documented API facts (spec amendments: replay not previous_response_id, allowed_tools with fusion fallback, shell files shape); the live spike run moves to before Task 24 and the user is asked to run it when convenient — waiting idles the whole plan, and the facts come from the docs the plan already cites — cost if wrong: small changes to payload/advisor_loop/outputs after the live run
Task 12: Ruling: FUSION_FORCED = True module constant selects amendment 3 (allowed_tools) and False selects the openrouter/fusion model + plugins fallback; both paths built and tested; the live run flips it if forcing fails — Task 0 is pending (R1 revised) — cost if wrong: flip one constant
Task 12: Ruling: fetch_domains keeps only http(s) hosts with a dot, drops IP literals, localhost, ports, userinfo and trailing dots, lowercases (IDNA-encodes non-ASCII), caps at 20 — spec says domains of thread URLs plus allowlist; IPs/localhost add exfiltration surface for no class use — cost if wrong: a team can't fetch a bare-IP link
Task 12: Ruling: Task 10's `listed` is surfaced via `files_note(listed) -> str` here (a "Files in the thread, not attached" section the runner appends to context.md) and the instructions tell the model to ask the team to re-attach — carries the Task 10 dependency — cost if wrong: copy
Task 12: dispatched (BASE 7bec6e2)
Task 12: implemented (7bec6e2..0c166e8), DONE (tool param shapes from spec, unverified live; added build_slack_payload(listed=()) appending files_note to the brief, not context.md) — in review
Task 12: review Changes requested (Spec ❌: sandbox instructions replaced, losing upload bundle pointers + container safety; Important: thread hostnames in instructions; Minors: public-suffix hosts, scheme check bypass, thread hosts evict allowlist, compare copy, type hint, test gaps)
Task 12: Ruling: extract upload notes + container safety from advisor_payload into helpers and append to SLACK_SANDBOX — spec says sandbox instructions ADD the outputs rule — cost if wrong: none
Task 12: Ruling: instructions mention the fetch rule generically; no Slack-sourced text (hostnames, file names) in instructions — untrusted input stays in user turn — cost if wrong: model doesn't know which domains; it learns from refusals
Task 12: Ruling: drop known shared suffixes (named frozenset) and ccTLD second-level two-label hosts; allowlist fills slots first; spike records subdomain matching and plugin tools — closes the exfiltration path pending the live fact — cost if wrong: a team can't fetch a bare github.io page
Task 12: Ruling: listed note stays in the brief (not context.md) — untrusted but in user turn, easier to test — Task 14 must store the plain brief in append_turn (not the note) and must not upload bundles for compare jobs
Task 12: fix round 1/5 started — sandbox upload notes, generic fetch copy, shared suffixes, scheme check, allowlist priority, compare copy, type hint, spike facts
Task 12: fix round 1/5 (8 addressed, 0 open; commits 0c166e8..150026d)
Task 12: minor (deferred): SANDBOX_SAFETY "do not write final reports into files" sits beside the Slack outputs/ rule — revisit with Task 15 if models skip outputs/
Task 12: complete (commits 7bec6e2..150026d, review clean)
Task 13: Ruling: loop collects every `function_call` item; names in `functions` run, others get "Error: unknown tool" (brief steps 2 and 8 conflict; step 8 is the safer reading) — cost if wrong: none
Task 13: Ruling: run_tool_loop works on a copy of payload (caller's dict and input list untouched); final forced call keeps the same tools with tool_choice "none" and FINAL_NOTE as a user message item; function exceptions become "Error: <ExceptionType>" (no message text) and are logged via the redacting logger; container_file uses GET {API_BASE}/containers/{cid}/files/{fid}/content with the client's normal auth/workspace handling, test in a new tests/test_advisor_client.py — brief is silent — cost if wrong: small
Task 13: dispatched (BASE 150026d)
Task 13: implemented (150026d..f72fa2a), DONE (rounds counts every respond incl. forced final; function_call without string name/call_id ignored) — in review
Task 13: review Changes requested (Spec ❌: LoopStopped from a runner swallowed; replay can leave unmatched function_call; stream-cap branch untested; minors: unicode digits in Content-Length, DecodingError leaks, exc_info logs content, partial cost lost on stop, duplicate call_ids)
Task 13: Ruling: every replayed function_call with a string call_id gets exactly one output ("Error: malformed call" for a bad name); items without call_id and later duplicate ids are dropped from the replay; LoopStopped carries partial cost_usd and items — the API rejects unmatched calls and Task 14 must bill a stopped job — cost if wrong: none
Task 13: note for Task 14: never pass a payload whose tool_choice is "required" into the loop with functions (compare has none, so fine); bill LoopStopped.cost_usd on stop
Task 13: fix round 1/5 started
Task 13: fix round 1/5 (7 addressed, 0 open — LoopStopped re-raised and carries partial items/cost; replay keeps only matchable calls, malformed name answered; stream-cap test proven; ascii Content-Length; HTTPError mapped; no exc_info; commits f72fa2a..735ddb2; controller re-reviewed the source diff directly)
Task 13: complete (commits 150026d..735ddb2, review clean)
Task 14: Ruling: the runner trims job.context.files to the chat's remaining session room (existing session files minus the slack/context.md and same-path files being replaced) and moves overflow names into the listed note; the job's context arrives pre-gathered (Task 17), so the runner is the first place that sees the chat's stored files — cost if wrong: an extra SessionFiles read per job
Task 14: Ruling: the implementer may split runner.py into sibling modules (each with its own test file) to stay ≤300 lines; post_answer goes in replies.py per R2 whatever the size; team-facing error copy must not say "student" — fixes Task 3's deferred minor at the Slack edge — cost if wrong: none
Task 14: dispatched (BASE 735ddb2), model opus (integration across AdvisorService sandbox prep, results, files, loop)
Task 14: implemented (735ddb2..92b3720), DONE_WITH_CONCERNS (later turns lose earlier context — delta context.md replaced + plain brief history; AdvisorClient now maps 402→InsufficientCreditsError, 403→ForbiddenError; server item types/fusion failure shape loose until live run; per-owner semaphore keeps first max_concurrent until restart) — in review
Task 14: Ruling (spec gap, flagged for the user): every turn renders the WHOLE thread (pins + channel + thread, trimmed) into slack/context.md, replaced — Task 17 gathers with first_turn=True and renders with since_ts=None on every turn; threads.seen still records newest_ts. Why: with one replaced file, a delta-only context.md loses everything said before the last turn, and the spec's "history carries the rest" holds only Sidecar's own turns. Cost if wrong: up to ~92k chars of context resent per turn (bounded by the caps and the daily budget) and two extra Slack reads per turn.
Task 14: review Spec ✅ Quality Changes requested (Important: heartbeat cancels caller during stop/fail handlers; cost lost on post-loop and mid-loop failures; queue fairness/slot holding on busy chat. Minors: stop while queued; 403 moderation → budget copy; frozen semaphore size; mark_posted after outputs; full session copy; test gaps)
Task 14: Ruling: answer runs in an inner task the heartbeat may cancel; outcome captured before handlers; OpenRouterError from the loop carries partial cost/items like LoopStopped; per-chat FIFO lock before the team semaphore with its own "still answering" copy; moderation 403 → ModerationError with own copy — spec: spend stays, WorkingMessage always finishes, Review Focus 3 — cost if wrong: none
Task 14: fix round 1/5 started (8 items)
Task 14: fix round 1/5 (8 addressed, 0 open; re-review minors: lost-lease spend not recorded, mark_posted unguarded, cleanup error masks cancel, heartbeat non-OpenRouter errors unlogged, run_tool_loop 52 lines, negative max_concurrent, flagged_input in job error; commits 92b3720..95006e1)
Task 14: fix round 2/5 started — the seven minors (billing-adjacent, cheap)
Task 14: fix round 2/5 (7 addressed, 0 open — cancellation carries spend, interrupt(cost_usd=), mark_posted guarded, cleanup can't mask cancel, heartbeat logs, loop ≤50 lines, max_concurrent clamped, fixed moderation error text; commits 95006e1..38cc95f; controller re-reviewed core diff directly)
Task 14: minor (deferred): `@sidecar` same-chat follow-up waiting past QUEUE_WAIT_S gets CHAT_BUSY and is not run; server item types / fusion failure shape loose until live run
Task 14: complete (commits 735ddb2..38cc95f, review clean)
Task 15: Ruling: until the live run records the shell filename form, output_refs accepts both `/workspace/project/<rel>` and a bare relative `<rel>` (also `project/<rel>`), keeps only `outputs/...`, rejects backslashes, `..`, `.`, empty segments, control chars and absolute paths elsewhere; upload name is the path under outputs/ with `/` → `_`, bounded to 200 chars; per-file download cap is the remaining 20 MB budget; a notes message is plain team copy without ids — Task 0 pending — cost if wrong: a filename shape the parser misses (fix after live run)
Task 15: dispatched (BASE 38cc95f)
Task 15: implemented (38cc95f..5d4bb39), DONE (oversize note keys off 'larger than' wording in container_file error; fake Outputs.post signature changed in runner_errors test; rig handles container GET) — in review
Task 15: review Changes requested (Spec ❌ minor: C1 controls pass; Medium: bidi/zero-width names reach Slack; Low: unbounded notes can exceed Slack's 40k limit; oversize copy wrong with shared budget; cancel untested; fake signature loose; string-coupled oversize detection)
Task 15: Ruling: reject Cc/Cf/Zl/Zp chars and blank segments; at most 10 per-file notes + one "K more" line, stop after 3 consecutive download failures; typed ContainerFileTooLargeError(ProviderError) — model-controlled names, Slack size limit — cost if wrong: none
Task 15: minor (deferred): output file names/content not redacted (files are posted as written per spec)
Task 15: fix round 1/5 started
Task 15: fix round 1/5 (6 addressed, 0 open — Cc/Cf/Zl/Zp and blank segments rejected; 10 notes + K-more line; stop after 3 consecutive download failures; typed ContainerFileTooLargeError; cancel test; fake signature; commits 5d4bb39..136cd14; controller re-reviewed source diff directly)
Task 15: complete (commits 38cc95f..136cd14, review clean)
Task 16: Ruling: warning month is the UTC "YYYY-MM" (OpenRouter's monthly usage resets on UTC months); today_spend uses the same day boundary Limits.admit uses for the daily cap; missing/None effective_budget → no budget line and no warning; the runner fetches key info through an injectable callable (default openrouter_keys.current_key) before post_answer, and any failure there just skips the warning — brief silent — cost if wrong: one boundary off by hours
Task 16: dispatched (BASE 136cd14)
Task 16: implemented (136cd14..1fbb0bb), DONE (runner builds month-figures only; Task 17 usage command uses full report + escape; warning claimed before post, lost if post fails twice) — in review
Task 16: review Spec ✅ Changes requested (Medium: GET /key before every post can delay answers ~30-60s; Low: fetch even when already warned; usage_text unescaped names; meaningless assert; names resolved beyond top 10)
Task 16: Ruling: 4 s bound on the warning fetch; skip fetch when row.warned_month is this month; usage_report escapes names (safe by construction); claim-before-post kept (prevents duplicate warnings across concurrent answers) — cost if wrong: an occasional missed warning
Task 16: fix round 1/5 started
Task 16: fix round 1/5 (5 addressed, 0 open — 4 s bound, skip when warned, escaped names + others count, assert dropped, one_line public; commits 1fbb0bb..169d95a; controller re-reviewed directly)
Task 16: note for Task 17: usage_text is safe to post as is (names escaped); do NOT escape again
Task 16: complete (commits 136cd14..169d95a, review clean)
Task 17: Ruling: per the Task 14 context ruling, dispatch gathers with first_turn=True and renders since_ts=None every turn (overrides brief step 7's since_ts=last_seen_ts) and renders ONCE for all models; compare runs one Fusion job (not one per alias); run_command returns post-ready text (every user/model/Slack-sourced string inside it escaped exactly once; help_text and usage_text are already safe); event fields (channel, user, ts, team) must be non-empty strings or the event is ignored; posts in the event's thread; background tasks are held in a set and their exceptions logged; ThreadMap.put RuntimeError and provisioning errors become plain team copy — accumulated notes from Tasks 7/14/16 — cost if wrong: small
Task 17: dispatched (BASE 169d95a), model opus (request-flow integration, principal derivation)
Task 17: implemented (169d95a..333a23e), DONE_WITH_CONCERNS (team principal has no saved family defaults, so real PreferenceService.resolve("Gemini") fails "No advisor default selected"; registry.update rewrites all columns — race with deactivate/mark_warned accepted per ruling 8) — in review
Task 17: Ruling (flagged for the user): team model defaults are seeded at registration — Task 22's `slack_channels.py add`/`update` take the general default and per-family defaults (model ids, checked against the catalog) and write them to the team principal's preferences under acting_as; Task 21's e2e seeds them too; a brand alias with no family default answers "This alias has no model set yet. Ask your instructor." (escaped, no error text) — spec: aliases resolve through alias_map then resolve_advisor_model; teams have no settings UI — cost if wrong: instructors pick models per team at add time
Task 17: carry to Task 22: ChannelRegistry.update rewrites all columns (race with deactivate/mark_warned) — Task 22 fixes update to write only changed columns
Task 17: review Spec ✅ Changes requested (Important: test mutates shared parametrize dict → pg case fails in mixed run; Minors: !stop blocked by per-user window; concurrent first mentions create two chats; on_mention can raise on malformed events; edits/other bots not filtered; limit-order test gap)
Task 17: Ruling: ThreadCommand, Help and Empty don't count against the per-user window; per-(owner, channel, thread, alias, mode) lock around get/start/put; on_mention never raises; events with `edited`, message_changed/deleted subtypes, or another bot's bot_id are ignored (no paid work from other bots) — spec: edits/deletes ignored; stop must always work — cost if wrong: Claude Tag can't ask Sidecar (spec keeps them separate anyway)
Task 17: fix round 1/5 started
Task 17: fix round 1/5 (6 addressed, 0 open — test isolation, unlimited ThreadCommand/Help/Empty, per-key chat start lock via Shared, on_mention never raises, edits/deletes/any bot_id ignored, limit-order test; commits 333a23e..c24928f; controller re-reviewed source diff directly)
Task 17: complete (commits 169d95a..c24928f, review clean)
Task 18: Ruling: per R4, Health exposes `Health.lock: OwnerLock` whose on_gain/on_lose lists later tasks append to; hooks run in order, each awaited, an exception in one is logged by type and the rest still run; on_gain runs on every gain (also after a loss and regain), on_lose once per loss; gate behaviour for MCP traffic is unchanged — brief silent on hook errors — cost if wrong: none
Task 18: dispatched (BASE c24928f)
Task 18: implemented (c24928f..5114e79), DONE (pg_target fixture added, db_target reuses it; Health._lost keeps shutdown reason) — in review
Task 18: review Spec ❌ Changes requested (regain overwrites shutdown reason; unexpected exceptions end the loop silently; split-brain window unbounded by timeouts; concurrent close; cancel skips on_lose hooks; misleading log; dead re-export)
Task 18: Ruling: accept the session-advisory-lock split-brain window (≤ ~every_s) as inherent, bounded with connect/statement timeouts and same-tick reconnect, and documented in HOSTED_OPERATIONS; replicas stay at 1 — cost if wrong: brief overlap of background work during a DB blip
Task 18: fix round 1/5 started
Task 18: fix round 1/5 (7 addressed, 0 open — Health overwrites only its own reasons incl. STARTING; any exception drops+reconnects; connect_timeout and every_s-bounded checks with same-tick retake; close waits for in-flight call; shielded on_lose; neutral log; dead re-export removed; HOSTED_OPERATIONS documents takeover window; commits 5114e79..0d44508; controller re-reviewed source diff directly)
Task 18: complete (commits c24928f..0d44508, review clean)
Task 19: Ruling: payload shape validated (missing/non-string event_id, team_id or event dict → drop + log, never raise); dedupe before filtering as the brief says; every handler runs as a task held in a set (Socket Mode handlers must return fast), exceptions logged by type; also treat `channel_left` (the event Slack sends the bot itself when removed) as "bot removed" alongside member_left_channel with user == bot_user_id; stop() cancels purge, waits ≤5 s for in-flight handler tasks then cancels, and closes the socket — brief silent on these — cost if wrong: none
Task 19: dispatched (BASE 0d44508)
Task 19: implemented (0d44508..0c454fc), DONE (dedupe DB failure → handle anyway; stop disconnects before waiting for handlers) — in review
Task 19: review Spec ✅ Changes requested (Medium: real SDK auto-reconnects after disconnect so stop() doesn't stop intake; Low: real connect() retries forever so start() can hang the gain hook; snapshot hole; fail-open dedupe has no fallback; test gaps)
Task 19: Ruling: stop sets _stopping then close() (auto-reconnect off) then drains; start launches connect as a task and returns; fail-open dedupe kept with a bounded in-memory recent-ids set — owner-lock hooks must not hang; a redelivery after a failed ack mustn't run paid work twice — cost if wrong: none
Task 19: carry to Task 21: shutdown must also drain the dispatcher's job task set (jobs live there, not in the gateway)
Task 19: fix round 1/5 started
Task 19: fix round 1/5 (5 addressed, 0 open — _stopping flag + close() before drain; start() launches connect as a task; recent-ids LRU before DB dedupe; tests split into rig + lifecycle; renames; commits 0c454fc..078f60c; controller re-reviewed source diff directly)
Task 19: complete (commits 0d44508..078f60c, review clean)
Task 20: Ruling: recovery acts under each orphan's own team principal/scope (parsed from its owner/workspace; orphans() is cross-team); an active row counts only when its heartbeat has lapsed (fresh heartbeat = another live owner during a takeover window → leave it); interrupt passes the row's own process_id (captured ownership); the thread is found through ThreadMap by chat_id (add `ThreadMap.thread_for(scope, chat_id)` and a `threads` parameter to recover); completed-unposted rows older than 24 h are marked posted without posting; footer from the row's model and cost — brief silent; Task 7 note — cost if wrong: an old answer never posted
Task 20: dispatched (BASE 078f60c)
Task 20: implemented (078f60c..a6ffffd), DONE (recovered post labelled with model id — job row has no alias; working message left unedited on recovered post) — in review
Task 20: review Spec ✅ Approved (lows: partial multi-part post duplicates on next gain — at-least-once accepted; mark_posted failure path untested; no atomic claim of a completed row → Task 21 serializes recover; heartbeat boundary >= vs <=; recovered label is model id)
Task 20: Ruling: keep post-then-mark (at-least-once; spec prefers a duplicate over a lost answer); recovered posts use the team's alias brand label via ThreadMap alias; Task 21 must run recover as one task per gain and not await it before starting the gateway — cost if wrong: rare duplicate posts after a crash
Task 20: fix round 1/5 started (label, mark_posted-failure test, heartbeat boundary)
Task 20: fix round 1/5 (3 addressed, 0 open — thread_chat returns alias, brand/Fusion/model-id label; mark_posted-failure test; heartbeat <= boundary; commits a6ffffd..da3b516)
Task 20: complete (commits 078f60c..da3b516, review clean)
Task 21: Ruling: on_gain resolves the bot user id via auth.test (failure → log, no gateway this gain), spawns recover as one held task (a second gain waits for the first), then awaits gateway.start() (returns promptly); on_lose stops the gateway and cancels recover; close() also drains the dispatcher's job tasks (bounded wait, then cancel → runner interrupts rows); Slack hooks are appended to health.lock (R4); the "no model set yet" alias copy ruled at Task 17 lands here (resolve_alias maps the no-default BadRequestError); the e2e seeds team preferences — carried notes from Tasks 17/19/20 — cost if wrong: small
Task 21: dispatched (BASE da3b516), model opus
Task 21: implemented (da3b516..7796820), DONE (auth.test failure leaves bot off until next gain — stable owner never regains; own-process rows not swept after lose+regain; added SlackApi.bot_user_id, client_factory, Dispatcher.shutdown, slack= harness option) — in review
Task 21: review Spec ✅ Changes requested (Important: gain after close() reopens the socket; Minors: stale orphans allowlist comment, scan blind spots (case, ORDER BY owner, two unscanned stores), cross-team test lacks session files/limits, e2e key assertion met by setup; concern: auth.test failure leaves bot off until restart)
Task 21: Ruling (revises ruling 1): on_gain starts one held start task that retries auth.test with backoff 5 s → 5 min (10 s per attempt), then starts recovery and the gateway; SlackApp._closed blocks any start after close() — a deploy during a Slack blip must not leave the bot off until restart — cost if wrong: none
Task 21: fix round 1/5 started
Task 21: fix round 1 committed 9e75540 (closed flag, held start task with auth.test backoff, scan hardening, cross-team session files/limits, e2e key header, note) — re-review dispatched (opus)
Task 21: re-review (opus) Approved — items 1–6 addressed; concurrency sound. Minors: start-task exception unretrieved (full message logged by asyncio), close-during-auth test bypasses close(), note says on_gain runs recovery
Task 21: fix round 2/5 started (the three minors)
Task 21: fix round 2 committed 9aaf168 (post-auth start and recovery failures logged by type only; real close() during held auth.test; note) — controller inspected the diff: OK
Task 21: complete (da3b516..9aaf168)
Task 22: dispatched (sonnet) BASE 9aaf168 — carries registry.update changed-columns fix (with test) and team preference seeding at add/update
Task 22: implemented cf7c854, DONE_WITH_CONCERNS (general_default unused by Slack; added --default-alias; seeding provisions the team key at add time; alias merge-only; manifest checked with Ruby YAML only; usage names double-escaped in terminal) — in review (sonnet)
Task 22: Ruling: --default-model sets the channel row's default_model alias (the brief's `--default-model gemini` is an alias); drop general_default seeding and --default-alias — my dispatch misread the brief; Slack never reads general_default — cost if wrong: none
Task 22: review (sonnet) Spec ✅ Quality Approved — Minors: orphan key on failed add (doc), alias merge-only (help), usage names entity-escaped in terminal, ValueError one-liner (deferred), nargs="+" swallows label (help)
Task 22: fix round 1/5 started (default-model ruling + minors 1,2,3,5; minor 4 deferred)
Task 22: fix round 1 committed 797cef2 — controller inspected the diff: OK
Task 22: minor (deferred): run_script prints any ValueError as one line; a DB connection failure shows a raw traceback
Task 22: complete (9aaf168..797cef2)
Task 23: dispatched (sonnet) BASE 797cef2 — carries rulings for spec: FUSION_FORCED/amendment 4 pending live, whole-thread render, team defaults at registration (--family-default; --default-model is alias), start retry + recovery
Task 23: implemented 8ceb81e, DONE (concerns: since_ts delta path unused; invite-before-add and connections:write inferred; railway run reachability) — in review (sonnet)
Task 23: review (sonnet) Spec ❌ Changes requested — cap env vars parsed but unused; spec leftovers of delta design; spec :94 claims no double post; railway run can't reach private DATABASE_URL; /health line overstated; @Sidecar casing
Task 23: Ruling: SIDECAR_SLACK_DAILY_CAP_USD / SIDECAR_SLACK_MAX_CONCURRENT are the defaults `slack_channels.py add` uses when --daily-cap/--max-concurrent are omitted (fallback 4.0/3) — spec :394 calls them registry defaults; the script runs with the service env — cost if wrong: one default source to move
Task 23: Ruling: ops docs run Slack scripts with DATABASE_URL = the Postgres public URL; verified at Task 24 — the private URL is unreachable from a laptop — cost if wrong: a doc edit
Task 23: fix round 1/5 started
Task 23: fix round 1 committed 9fee6f7 (registry_defaults helper + add uses it; spec/ops corrections) — controller inspected code diff: OK
Task 23: minor (deferred): HOSTED_OPERATIONS §6 `railway run … purge` has the same private-URL reachability question — check at Task 24
Task 23: fix round 2/5 started (bot copy @sidecar → @Sidecar to match the docs and manifest)
Task 23: fix round 2 committed e7dbb57 (@Sidecar copy) — controller reviewed stat/summary: OK
Task 23: complete (797cef2..e7dbb57)
Ruling: run the final whole-branch review before Task 24 (live acceptance) — Task 24 deploys to a live Slack workspace with real keys; reviewing first means fixes land before anything goes live — cost if wrong: none (Task 24 is docs-only on the code side)
Final review: dispatched (opus) 709c182..e7dbb57 → final-review.md
Final review: With fixes — Critical 0, Important 3 (I1 Slack transport errors fail jobs + drop spend; I2 fresh-heartbeat orphans lock a team out; I3 bot replies widen web_fetch allowlist), Minor 10
Final: Ruling: one fix dispatch covers I1–I3, M1, M2, M3, M4 (30 min total deadline below the 40 min lease), M6, M7 (≤4 models per ask), M8 (neutral reconnect reason), M10 annotation, and the pre-merge deferred minors (registry _validate caps/default_model; strip ,:; from alias tokens; guest recheck mark on any lookup failure) — the reviewer's fix list, all small and testable — cost if wrong: rework visible in one commit series
Final: Ruling: M5 keep "Your team has a monthly budget" and amend the spec — the guardrail amount lives in OpenRouter and can change; fixed copy can't be wrong — cost if wrong: students don't see the dollar figure until !usage
Final: Ruling: M9 multi-tenant fetch hosts (docs.google.com forms) recorded at live acceptance step 5 and noted in the spec as a residual risk; no deny list now — a host deny list is whack-a-mole and the team's own allowlist is the control — cost if wrong: a prompt-injected GET to a form the attacker owns
Final: Ruling: M10 app.py length and orphan chat on failed put left as is — grandfathered file; orphan is harmless until purge — cost if wrong: none
Final: fix dispatch started (opus)
Final: fixes committed 4f8f216 408bf73 701070b 7ad2cfc 772ac73 (tests 1694 passed; pg 606) — scoped re-review dispatched (opus)
Final: re-review (opus) — all 15 fix items Addressed; Ready for live acceptance: Yes. New Minor: spend lost when a cancel lands in post-loop cleanup; Low: _twice docstring. Fixer concerns 1–4 judged not before merge.
Final: residual fixed by controller 4722fb4 (spend recorded in try/else before cleanup + regression test seen failing; docstring and note) — full suite 1695 passed
Final: deferred (not before merge): lapsed rows count against the cap ~75 s after start; own-process rows from an earlier lock period not swept; active_for_chat closes a lapsed row without editing its message; a test file imports another
Task 24: waiting on the user (live spike run with their key, Railway deploy and variables, Slack app from the manifest, guardrail creation)
