# Decision record: hosting the OpenRouter Sidecar

**Date:** 2026-10-18 · **Status:** accepted by the user in the hosted-sidecar design review. Items marked *open* need the user before launch.

## Decisions

1. **Hosted remote MCP on Railway** for every Claude client (claude.ai, Desktop, Claude Code, Claude Tag). This supersedes the "No remote deployment is authorized" clause in `2026-10-05-northwestern-advisor-mcp-design.md`. The preconditions it named are now met by design:
   - authenticated per-user access (Auth0 RBAC, `sidecar:use`);
   - server-side transcript storage with user isolation (every row is scoped by `(iss, sub)` principal and workspace);
   - a hosted OpenRouter credential design (per-user PKCE link, AES-GCM vault).
2. **Region** (*open*): one Railway region. US East is recommended (closest to Chicago); the user confirms it at deploy.
3. **Single replica.** In-process advisor jobs, guarded by a Postgres advisory lock.
4. **Stored data:**
   - advisor transcripts (briefs and final answers);
   - session files the caller chose to send;
   - job metadata and costs;
   - preferences;
   - linked OpenRouter keys, AES-256-GCM sealed and bound to the principal;
   - link attempts (short-lived);
   - since 2026-10-19: provisioned student keys instead (`student_keys`, same sealing) with OpenRouter's key hash; the two linking tables are emptied by `purge`.
   - image assets (M3): uploads, results, unblended layers and their metadata.
   
   **Not stored:** Auth0 tokens, reasoning traces, provider container contents.
5. **Retention.**
   - Chats idle for 90 days are purged with their jobs and files. Expired link attempts go after 1 day.
   - Provider-side container files follow OpenRouter's own expiry (30 days idle).
   - Generated images and uploads (M3) are kept for 30 days from creation. The server sweeps them hourly; each account is capped at 2 GB.
6. **Sandbox mode stays disabled** (`SIDECAR_SANDBOX_ENABLED` unset). On 2026-10-05 a live test showed that another key in the shared cohort workspace could list and download uploads. Inline sessions send files in the prompt and offer no tools.
   - 2026-10-21: the live two-account test (`../plans/2026-10-20-sandbox-isolation-live-test.md`) passed every
     go criterion now that students never hold keys. The flag was set only for the test window and unset
     afterwards; enabling sandbox mode for students is a separate instructor decision.
7. **Spend.** Each student's own OpenRouter key is charged, within the cohort workspace. Server limits: 2 concurrent jobs, 20 requests/min, $5/day per principal.
   - 2026-10-19: OpenRouter linking was replaced by provisioned keys. The server creates one key per student and cohort with an org management key (`OPENROUTER_MANAGEMENT_KEY`); each cohort workspace's default guardrail budgets every key at $25/month. Students need no OpenRouter account. See `2026-10-19-provisioned-student-keys-design.md`.
8. **Local stdio server** stays until M5 parity, then it is retired.
   - Retired on 2026-10-19 (M5 task 5.3): the hosted connector is the only server; `export-local` remains for migration.
9. **Image blobs on a Railway volume** (M3), not an object-storage bucket as the design spec proposed. The single-replica constraint already exists, the volume needs no extra credentials or vendor, and there's no untested S3 code. Uploads go to the Sidecar's own `PUT /uploads/…` endpoint (single-use, size- and pixel-checked, decoded before storage) instead of presigned URLs. Downloads use 24-hour HMAC links. Moving to a bucket later only means replacing `LocalDirBlobs`.

## Student notice (*open*: the user approves wording before launch)

> The Northwestern AI Sidecar runs on a university-managed cloud server. When you
> use it, your advisor conversations and any files you choose to send are stored on
> that server so you can continue them later, and deleted after 90 days without use.
> Images you upload or generate are stored for 30 days.
> Your OpenRouter key is stored encrypted and is only used for your own requests,
> which are billed to your key. Instructors can disable access at any time. Don't
> send confidential or personal information you wouldn't put in a class assignment.
