---
title: "SDD ledger \u2014 plan: docs/superpowers/plans/2026-10-21-slack-admin.md"
description: "Archived planning record for the Slack admin UI build (docs/superpowers/plans/2026-10-21-slack-admin.md): progress ledger (task completions, fix rounds, rulings)."
type: "history"
status: "completed"
read_when: "When tracing why this work was built the way it was"
---

# SDD ledger — plan: docs/superpowers/plans/2026-10-21-slack-admin.md
Spec: docs/superpowers/specs/2026-10-21-slack-admin-design.md (revised after the Codex and Fable reviews, 6e50c97)
Ruling: tasks in one wave run in parallel, each implementer in its own worktree (Agent isolation), and the controller cherry-picks each wave onto feat/slack-sidecar before the next wave — the user asked for parallel execution; disjoint files per wave plus separate worktrees avoid edit and commit races — cost if wrong: a merge conflict the controller resolves (docs/index.md is regenerated)
Ruling: implementers run on Sonnet; reviews on Sonnet, except Task 6 (Auth0) and Task 7 (routes) on Opus — the user asked for Sonnet for code writing; sign-in and routes are the security boundary — cost if wrong: an extra fix round
Task 0: complete (d10507a) — scaffold by the controller (package, copy.py with its test, folder note, missing_tests mapping)
Wave A: BASE d10507a — Tasks 1, 2, 3, 4 dispatched in parallel (worktrees)
Wave A: first dispatch failed — agent worktrees are cut from main (753406e), not feat/slack-sidecar, and agents can't write outside their worktree. Ruling: each agent resets its fresh worktree-agent branch to the base sha (authorised in the contract) and writes its report under its own worktree's git-ignored .superpowers/ — cost if wrong: none
Wave A: second dispatch blocked — the harness denies git reset --hard in agent worktrees. Ruling: agents create a new branch at the base with git switch -c admin-task-N <base> (non-destructive) — cost if wrong: fall back to running in the controller's worktree
Wave A: third dispatch — Tasks 1-4 on sonnet in worktrees, base d10507a, git switch -c admin-task-N
Task 1: implementer DONE 893c605 (branch admin-task-1, worktree agent-a4308189e6eaece46); review dispatched (sonnet)
Ruling: accept the Sonnet 5.5 co-author trailer on implementer commits — it is the accurate attribution — cost if wrong: none
Task 1: complete (c5eb1de, cherry-picked from 893c605) — review approved, no Critical/Important
Parked (Task 1 minor): a short secret stops startup even when SIDECAR_ADMIN_ENABLED=false; settings imports private _LOCAL_HOSTS via a deferred import — revisit in the final review
Task 2: implementer DONE 50220e0 (branch admin-task-2, worktree agent-a55116e59f2065382); review dispatched (sonnet)
Task 2: complete (ae8b881, cherry-picked from 50220e0; folder note and index conflicts merged; controller added field(repr=False) on Session.csrf_token per review minor) — review approved
Parked (Task 2 minor): pending-login cap is soft under concurrent create (count then insert); recent(limit) not clamped — final review
Task 3: implementer DONE_WITH_CONCERNS d788b27 (branch admin-task-3, worktree agent-a43166b5adc55866d). Ruling: a secret inside a model name or price is redacted, not dropped — the brief says student behaviour must not change and the existing _catalog redacts — cost if wrong: none for students; admin catalog shows redacted text
Task 3: complete (f0879df, cherry-picked from d788b27) — review approved
Ruling: drop registry.AUDIT_SCHEMA (a module global the wiring would set); audit.insert runs CREATE TABLE IF NOT EXISTS on the caller's connection itself (589ebd6) — python rules ask for injectable code over module globals; DDL in the write transaction is fine on SQLite and Postgres — cost if wrong: one extra IF NOT EXISTS per audited write
Parked (Task 3 minor): test_fake_matches_protocol checks hasattr only; Audit alias vs _audit after-type mismatch; bot_channels truncation at 10 pages is silent; public_models 401 maps to KEY_REJECTED copy — final review
Ruling: Wave B starts before Task 4 merges — Tasks 5 and 6 consume only Tasks 1-3, and views are disjoint — cost if wrong: an index/folder-note conflict at merge
Wave B: BASE 589ebd6 — Task 5 (sonnet) and Task 6 (sonnet; review on opus) dispatched in parallel (worktrees)
Task 4: implementer DONE_WITH_CONCERNS 3e04f29 (branch admin-task-4, worktree agent-a71749ded78cad199); review dispatched (sonnet). Ruling: security_headers takes public_origin so script-src names <origin>/admin/public/ (CSP has no path-only source) — Task 7/8 pass settings.public_origin — cost if wrong: CSP tweak
Task 6: implementer DONE 4f970df (branch admin-task-6, worktree agent-aa6a3361abdfacccf); review dispatched (opus)
Task 4: complete (4ea0ca7, cherry-picked from 3e04f29) — review approved; controller applied minors: security_headers(issuer, public_origin) now required and script-src never 'self' (upload/download routes share the origin), dropped unused .error-box stripe rule, STATIC_FILES re.ASCII with a test case
Parked (Task 4 minor): fieldset without legend, empty action th, identical Edit link text; escaping test lacks a quote payload; no_channels_text duplicates copy.NO_CHANNELS — final review
Task 6: review approved (opus). Fix round 1 sent to implementer: HS256 test isolates algorithm; profile_claim on AdminSettings from HostedSettings; azp check for multi-aud ID tokens; tests for the extra refusal branches; info log on sub mismatch / not_admin
Task 6: complete (e2bcfc2 + 40402ed, cherry-picked from 4f970df + e0198af) — review approved (opus), fix round 1 re-review approved
Parked (Task 6 minor): no test for the no-profile-email log line
Task 5: implementer DONE_WITH_CONCERNS 0911c44 (branch admin-task-5, worktree agent-ad04d7587ec7503de); review dispatched (sonnet)
Ruling: a missing team guardrail gets a distinct ProviderError subclass (same UNAVAILABLE text for students) so ChannelAdmin can show NO_GUARDRAIL — the brief assumed AuthRequiredError, which provisioning never raises for it — cost if wrong: small error-type change
Ruling: the nine sentences in channel_setup.py move to hosted/admin/copy.py — global constraint: page copy lives in copy.py — cost if wrong: none
Task 5: review Needs fixes (2 Important: double-submit on finish_setup/turn_on; model-audit failure gives false message + log gap). Fix round 1 sent to implementer with rulings A (guardrail subclass) and B (copy move) plus all minors
Task 5: complete (b65f9c1 + 44f8dbe, cherry-picked from 0911c44 + b7dc225) — fix round 1 re-review approved; full -m pg suite 688 passed
Parked (Task 5 residual): ChannelSetup._unfinished returns success for a row that was already active at entry (stale Finish setup with a failing save says done) — return current only when the snapshot was off; set_inactive_reason guarded only on active=0 can overwrite another admin's off reason — guard on the setup reasons. Fix in the final fix wave
Wave B: complete
Ruling: Task 9 (docs) runs in parallel with Task 7 — docs describe the spec's behaviour; Task 7 doesn't touch hosted/admin/CLAUDE.md so Task 9 owns its final version — cost if wrong: a doc touch-up after Task 8
Wave C: BASE 44f8dbe — Task 7 (sonnet; review opus) and Task 9 (sonnet) dispatched; Task 7 may add ChannelAdmin.channel(team, channel) -> (row, saved models)
Task 9: implementer DONE_WITH_CONCERNS d06de69 (branch admin-task-9, worktree agent-a79d1fedc01489441). Ruling: review Task 9 after Tasks 7 and 8 merge, so the reviewer checks the docs against real routes and wiring — cost if wrong: none
Task 7: implementer DONE_WITH_CONCERNS ece63f3 (branch admin-task-7, worktree agent-a63313097914e88ab); review dispatched (opus)
Ruling: Task 7's local copy constants in routes_base.py move to copy.py in the final fix wave — cost if wrong: none
Ruling: Task 8 starts on base ece63f3 (Task 7's commit) during Task 7's review — admin_routes' signature is settled and Task 8's files are disjoint — cost if wrong: re-run Task 8 tests after Task 7 fixes
Task 8: dispatched (sonnet) on base ece63f3
Task 8: implementer DONE_WITH_CONCERNS bc05e0e (branch admin-task-8, worktree agent-a2e356222185957d2); review dispatched (sonnet)
Task 7: review (opus) Needs fixes — family selects silently write team models; not-admin page lacks sign-out link (spec); router-level 404/405/307 lack security headers. Fix round 1 sent with minors M1 (orig= not FormValues), M2 (bounded number parsing), M4, M5, M6, M7, M8, M9, M10 and copy move
Open for user (Task 7 M3): per-address login limit keys on uvicorn's client address; with FORWARDED_ALLOW_IPS=* the leftmost X-Forwarded-For entry is client-controlled if Railway appends — availability only (MAX_PENDING=500 lock). Raise at wrap-up / live check
Task 8: review approved — merge after Task 7's fix round lands
Parked (Task 8 minor): pin Slack-off edit test to 422 + SLACK_OFF; disabled test should assert no admin_* tables; (4.0, 3) fallback repeated in three places; Limits._recent never prunes empty deques for unique admin-login keys — final fix wave
Task 7: fix round 1 DONE b6dfe7d; re-review dispatched (opus — hand-rolled body reader is security-sensitive)
Task 7: complete (deca299 + 9ce1bf8, from ece63f3 + b6dfe7d) — fix round 1 re-review approved (opus)
Task 8: complete (9b49e93, from bc05e0e)
Task 9: merged (04cd783, from d06de69); doc review pending
Controller: 640d978 — wiring test uses realistic Slack ids (M8 id check) and pins Slack-off edit page to 422; folder notes match the split routes and the template contract
Parked (Task 7 re-review minors): cross-site POST /admin/logout with no session (SameSite=Lax) clears cookie and ends the Auth0 session — require Sec-Fetch-Site same-origin or Origin match when sessionless; methods outside ANY get bare 405 without headers; family select blank based on values not orig after refusal; %FF decodes to U+FFFD; catch-all shadows any later /admin route
Parked (controller): Slack-off edit page refuses via a catalog StopIteration (services None) — should refuse with SLACK_OFF explicitly
Full suite 1977 passed; -m pg was 786 passed before the id fix
Wave C: complete
Final review: whole-branch review dispatched (opus) on 6e50c97..640d978; Task 9 docs review (sonnet) in parallel
Task 9 docs review: Needs fixes — local-run section points at the fake-issuer E2E stack (needs real AUTH0_ISSUER + localhost resource); Slack-off bullet says only SLACK_BOT_TOKEN (startup needs both tokens); 'any replica' vs replicas=1; add login-limit + FORWARDED_ALLOW_IPS bullet, Turn on vs Finish setup, quote guardrail sentence — into the final fix wave
Final review (opus): With fixes — I1 Finish setup after a missing guardrail activates with no models (disabled selects post nothing; fake prefs never need a key); I2 htmx responseHandling swaps 422/409 text into body; I3 edit page dead-ends while the catalog is down; 10 minors
Ruling: row turns on only when default_model's alias resolves (covers I1 + M1); M3 and M5 are spec deviations recorded in the spec (generic notice on later visits; log shows label + channel id)
Final fix wave: one dispatch (sonnet, worktree, base 640d978) with final-fix-brief.md — review importants, review minors, all parked minors, docs fixes
Final fix wave: DONE_WITH_CONCERNS 5af73e4, 976ef3d, ca8ed36 (branch admin-final-fix, worktree agent-a3c2845f259ae24cd); SQLite 2044 passed, pg 832 passed; scoped re-review dispatched (opus)
User: commit and push feat/slack-sidecar after merging branches (push doesn't trigger a build; remote snapshot). Pushed 640d978 to origin/feat/slack-sidecar (new branch, upstream set). Push again after the final fixes merge
Final fix wave: complete (97596a5, f65ef98, 96ca381 from 5af73e4, 976ef3d, ca8ed36) — re-review approved (opus); controller 8ae056b narrows the registry update comment to Postgres. Suites: SQLite 2044, pg 832. Pushed 8ae056b to origin/feat/slack-sidecar
Admin plan: all tasks complete except Task 10 (manual live check, with the user)
Next (user, 2026-10-21): migration framework with rollback, done in this session
