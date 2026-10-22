---
title: "Astra review: three open questions before launch"
description: "Questions put to Astra (gpt-6-astra via Codex) on sandbox folders, migrations and the admin sign-in limit, with its answers and what we did"
type: "reference"
status: "snapshot"
read_when: "Before starting the migration framework or the admin limiter fix, or when changing sandbox folder instructions"
---

# Astra review: three open questions before launch

On 2026-10-21 the user asked for a second opinion from Astra (`gpt-6-astra`, run read-only through the Codex
companion) on three open questions. The questions and Astra's answers are recorded below, with the outcome so far.

| Question | Astra's recommendation | Outcome |
| --- | --- | --- |
| 1. Sandbox folders | Inputs in `/tmp/sidecar/inputs/`, work in `/workspace/home/work/`, scratch in `/tmp/sidecar/scratch/`; reset inputs every turn; move the "no final report files" rule out of the shared text | Done in `3f14e1a`. The persistence spike (`tests/live/test_sandbox_spikes.py`) has not been run yet |
| 2. Migrations | Approach A, our own small runner, with a frozen baseline that validates the live schema | Not started; the brainstorm stopped at the choice of approach. Resume with A |
| 3. Admin sign-in limit | Key the limiter on Railway's `X-Real-IP` once a probe shows the edge overwrites it; never fall back to `X-Forwarded-For` | Not started |

## The questions

The prompt asked for a recommendation, the main reason, the main risk and the smallest concrete change for each,
in under about 900 words.

1. **Student sandbox working folder.** `advisor_payload.py` told the model to write session files to
   `/workspace/project/inputs/<path>` and work in `/workspace/project/work`, "which persists between turns". A live
   spike showed OpenRouter saves and lists only files under `/workspace/home`. Where should inputs, work and scratch
   live, how should "remove scratch under /workspace/home" interact with work that must persist and with uploaded
   bundles at `/workspace/home/<id>-<name>`, and what should a persistence spike check?
2. **Migration framework with rollback.** Schema is created in code today (`CREATE TABLE IF NOT EXISTS`,
   `ensure_column` through `storage.py`). Decided already: its own branch after this deploy, a `down` step per
   migration with destructive downs behind `--allow-destructive`, a separate `migrate up` step (Railway pre-deploy
   command), the server refusing to start on a stale schema, and a baseline that is a no-op on the deployed database.
   Options: (A) our own small runner over `storage.py`, (B) Alembic, (C) yoyo-migrations.
3. **Proxy headers and the admin sign-in limit.** `FORWARDED_ALLOW_IPS=*` makes uvicorn trust `X-Forwarded-For`.
   `/admin` login allows 20 sign-ins per client address per 10 minutes. If Railway's edge appends to a client-sent
   header, the address may be attacker-controlled. Trust a proxy range, read a Railway header, take the right-most
   hop, key the limiter differently, or accept it?

## Astra's answers

### 1. Sandbox folders: persist work deliberately; rebuild inputs

Use `/tmp/sidecar/inputs/<path>` for the current input snapshot, `/workspace/home/work/` for reusable work,
`/tmp/sidecar/scratch/` for disposable files, and keep `/workspace/home/outputs/` for Slack deliverables. Inputs
already come from the server each turn; they need no persistent duplicate.

Replace "persists between turns" with wording that says files under `work/` are saved for reuse in this chat,
including after a sandbox sleep, subject to provider retention; they are not permanent storage; processes,
environment variables and system installations do not survive a sleep. OpenRouter documents a five-minute sleep and
deletion after 30 days without use.

Replace blanket home cleanup with explicit ownership: keep `work/` and `outputs/`; remove only known temporary files
and the exact uploaded bundle copies after use. Those home copies are independent of the Files API originals, so
`advisor.py` deleting an upload does not establish deletion of its container copy. Keep cleanup claims
evidence-based.

- **Main risk:** persistent work retains student content, while prompt-directed cleanup can fail. Moving everything
  into home would retain inputs too, for no reason.
- **Smallest change:** update `SANDBOX_SAFETY` and `upload_notes` in `advisor_payload.py`, their payload tests and
  Slack's instructions. Make the input reset unconditional, including turns with no files; reject path traversal
  when extracting. Move the advisor-only rule against final-report files out of the shared safety text, since it
  conflicts with Slack deliverables.
- **Spike:** yes, before advertising verified persistence, but correct the existing promise now. Use the production
  engine, workspace and `nu_` id shape. Write unique markers under home `work/` and `outputs/`, project and `/tmp`;
  record hashes, listings and downloaded bytes; delete one home marker; leave the container idle 7–10 minutes; resume
  the same id without re-attaching files; check retained bytes, that the deletion held, that markers outside home are
  gone, and home-relative listing and download. Confirm a fresh id lacks the markers.

### 2. Migrations: choose A, with a migration-specific execution path

A narrowly scoped linear runner fits this repository's existing SQL seam. Yoyo is the closest alternative; Alembic
gets more attractive if frequent SQLite table rebuilds call for its batch operations. Neither removes the baseline
and adoption work.

- **Main risk:** underestimating the runner's correctness obligations. In `storage.py` every Postgres `CREATE`
  enters a per-process cache, so an up/down/up sequence can skip recreation. Migration DDL must bypass that cache
  and use explicit dialect handling; freeze the baseline SQL rather than importing evolving store definitions.
- **The baseline** creates the complete frozen schema on an empty database. On an existing database it validates the
  16 tables, columns, defaults, constraints, partial-index predicates and Postgres `rowid` sequences, then records
  adoption (the migration bookkeeping is the only write). It rejects partial or mismatched schemas instead of
  blessing them with `IF NOT EXISTS`. Its `down` is an explicitly destructive teardown; production rollback normally
  stops at the baseline.
- **Smallest complete change:** `migrations/0001_baseline.py`, a runner and a CLI command; remove runtime DDL and
  `ensure_column` from all stores; check schema compatibility before building the server; migrate application-test
  databases in fixtures while keeping empty-database fixtures for the migration tests.
- **Requirements:** immutable checksums, ordered history validation, serialized runners, and the migration plus its
  ledger row committed atomically. Hold the Postgres lock for the whole command on one dedicated connection. SQLite
  needs explicit `BEGIN`: today's connection wrapper does not open transactions for DDL. SQLite alterations may need
  table copies; forbid non-transactional operations such as `CREATE INDEX CONCURRENTLY` at first.
- **Tests:** fresh creation, adoption of the deployed schema, drift rejection, rollback on failure, and populated
  up/down/up on both engines. Pre-deploy migrations must stay compatible with the old server still running; a
  destructive rollback requires draining writers, and a `down` cannot recover discarded data.
- **Disagreement with the framing:** not "every SQL change". Schema changes and persistent data transformations
  need migrations; ordinary query edits belong in normal version control.

### 3. Admin limiting: use a verified ingress identity contract

Prefer Railway's documented `X-Real-IP` for the admin limiter, after verifying that the edge overwrites
caller-supplied values. Railway names that header as the remote client IP but does not explicitly document
sanitizing it. Pinned uvicorn 0.54.0 picks the leftmost `X-Forwarded-For` address when trusting `*`.

- **Main risk:** confusing an observed header with a trusted boundary. Don't guess Railway's address ranges or
  blindly take the right-most hop. Cookies are resettable, and no authenticated subject exists when login starts.
- **Smallest change:** a Railway-specific client-address resolver used by `routes_auth.py`, with tests and an
  operations note. Require one valid, normalized IP; a missing or malformed header must not fall back to
  attacker-controlled `X-Forwarded-For`. Keep the pending-login cap, knowing it is soft and the per-IP limits are
  per process.
- **Probe:** a disposable Railway deployment, hit through every production ingress with absent, forged, chained and
  duplicate `X-Forwarded-For` and `X-Real-IP` headers over IPv4 and IPv6. Capture the raw headers and socket peer
  before uvicorn rewrites them and check forged values cannot move a request between limiter buckets. Ask Railway
  for a sanitization guarantee; without one, put a controlled sanitizing proxy in front.
