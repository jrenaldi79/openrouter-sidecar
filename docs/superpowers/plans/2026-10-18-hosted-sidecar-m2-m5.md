# Hosted Sidecar M2–M5 Implementation Plan (outline)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Before starting each milestone, expand its tasks into full TDD steps (test code, commands, expected output), as in `2026-10-18-hosted-sidecar-m0-m1.md`.

**Goal:** Headless subagent-style advisor with session files (M2), hosted images (M3), MCP Apps over remote (M4), then migration and stdio retirement (M5).

**Spec:** `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md` §3, §4, §7. **Depends on:** M1 merged and deployed. Waits come from M0 Task 0.3.

---

## M2 — Headless advisor and session files

### Task 2.1: Session files repository
- Create `src/openrouter_image_mcp/session_files.py` with table `session_files(chat_id, principal, workspace, path, content, sha256, version, updated_at, PRIMARY KEY(chat_id, path))`, using the Task 1.7 adapter.
- `upsert(scope, chat_id, files, replace=False) -> FileDelta(added, updated, removed)`. Validation: UTF-8 text only (reject NUL bytes or anything that fails a strict decode); normalized relative POSIX paths (no `..`, no absolute paths, ≤300 chars); ≤100k chars per file; ≤200k chars per session; no active-credential substring (reuse the `_prepare` check).
- Test first: version bumps only on a changed sha, `replace` drops omitted paths, cross-scope access is denied, and every limit is checked.

### Task 2.2: Inline files in requests
- Modify `AdvisorService._prepare/send`. When the session has files, `input[0]` becomes a stable user item: `<session_files>` + each file as `<file path="…" version="N">…</file>`, sorted by path. History follows, then the brief plus an "Updated since last turn: …" line taken from the delta.
- Add a session flag `chats.sandbox BOOLEAN DEFAULT false`. **When false, omit the `tools` key entirely** (no `openrouter:shell`) and use the inline INSTRUCTIONS variant, which drops all container text.
- `configure_cache` then sees the large stable `input[0]` and adds the Gemini breakpoint. Test that the payload for inline sessions has no `tools`, and that the cache breakpoint lands on the files block when it's ≥16k chars.

### Task 2.3: Answer envelope
- Add `envelope(answer: str) -> str`, which returns `"<advisor_output>\n" + answer + "\n</advisor_output>"` plus the fixed note constant. Strip or escape any literal `</advisor_output>` inside the answer.
- Test: escaping works, and the envelope never contains skill or prompt text.

### Task 2.4: `ask_advisor` and `wait_advisor_job` tools
- Add them in `advisor_tools.py`: register both in `ADVISOR_ARGUMENTS` with exact argument sets, and add `wait_s` bounds validation (1..MAX_WAIT_S).
- `AdvisorJobs.wait(job_id, wait_s, progress)` waits on an `asyncio.Event` set by `_run` on completion (add a per-job event map), and calls `progress` every 10s.
- Busy chat: `_start` catches the active-job unique-index conflict and returns `{status:"busy", running_job_id}`.
- Tool docstrings must say the answer **is** returned to the caller, that it's untrusted, how the session ID works, that files must be passed explicitly, and that sandbox is unavailable while disabled.
- Tests through the in-process MCP client: completes within the wait, falls back to running, wait later returns completed, busy, progress notifications emitted (use a fake `respond_stream` with controlled delays), and principal isolation for `wait_advisor_job`.

### Task 2.5: Sandbox mode behind a flag
- `SIDECAR_SANDBOX_ENABLED` (default false). While false, `ask_advisor(sandbox=True)` raises `BadRequestError` with the isolation explanation.
- While true, build a tar from the session files (`tarfile`, in-memory) and upload it via the existing `upload_skills` path with a `nu_files/` prefix. Instructions tell the advisor to extract to `/workspace/project/inputs` and work in `/workspace/project/work`. `max_tool_calls=24`.
- Tests: flag off → refused, no upload; flag on → one upload per turn, cleanup recorded, and `tools` present.

### Task 2.6: `send_advisor_message` parity and docs
- Add `files`, `replace_files` and `sandbox` to the viewer tool, with the same validation.
- Update `README.md` tool tables and the `skills/` instructions for the headless pattern: start → ask → reuse `chat_id`, and pass files explicitly.

**M2 exit:** Claude Code CLI with an instructor account runs ask, then follow-up with the same `chat_id` from a different client (claude.ai), and context is retained. Record this in the acceptance log.

---

## M3 — Hosted images

### Task 3.1: AssetStore interface plus a bucket implementation
- `assets.py`: `AssetStore.put(scope, data, meta) -> Asset` and `get(scope, asset_id) -> (bytes, meta)`, with `image_assets` metadata in the DB.
- Two backends: `LocalDirAssets` (tests and stdio) and `BucketAssets` (S3-compatible, using `boto3`, or `httpx` with SigV4 if a dependency is undesirable; decide in the task and pin the version).
- Enforce owner-only access, a 30-day `expires_at`, and the per-principal storage cap.

### Task 3.2: Upload endpoint
- `create_image_upload` tool plus `PUT /uploads/{token}` custom route. The token is single-use, signed with HMAC, lasts 10 minutes, and is bound to the principal.
- Limits: ≤20 MB streamed, ≤40 MP (`PIL.Image.MAX_IMAGE_PIXELS`), Pillow `verify()` then reopen, mime allow-list png/jpeg/webp.
- Tests: oversize, decompression bomb, wrong mime, replay, foreign principal.

### Task 3.3: Refactor ImageService from paths to assets
- `service.py`, `outputs.py`, `remask.py`: inputs become asset ids, outputs go through `AssetStore.put` with sidecar metadata in `meta`, and `.unmasked.png` becomes an `unmasked` asset linked by `parent_ids`.
- Keep `imaging.py` pure. Delete `choose_dir`/`_probe_writable` usage in hosted mode only (stdio keeps paths until M5).
- Port the existing service tests onto `LocalDirAssets` before changing behavior.

### Task 3.4: Tool surface and downloads
- Hosted `generate_image`/`edit_image(images=[asset_id], mask_id)`/`remask_image(image_id, mask_id)`, and `get_image_download(asset_id)`, which returns a signed GET URL (24h) for the image and its JSON.
- The `GET /assets/{token}` route streams from the bucket. The `_image_result` previews are unchanged.

### Task 3.5: Retention for assets
- Extend `purge` to delete expired assets (bucket objects first, then rows).

**M3 exit:** parity checklist items 1–6 pass on claude.ai and Claude Code CLI (upload via curl, edit, remask, download).

---

## M4 — MCP Apps over remote

### Task 4.1: Viewer, settings and gallery
- Verify that `ui://` resources render from the remote server in claude.ai and Desktop. Fix CSP: `resourceDomains`/`connectDomains` include `public_origin` where the app loads downloads.
- Re-run the existing Playwright suites against the hosted resource HTML (`ui/tests`).

### Task 4.2: Uploader app
- `open_image_uploader` UI tool plus `ui/src/uploader.{html,js}` with a drop zone. The app calls the app-only `create_image_upload` and `fetch` PUTs the file. On success it adds the asset id to the conversation via the host's model-context API.
- Playwright tests with a fake host: success, oversize rejection shown, network failure retains the file.

### Task 4.3: Gallery downloads
- Download buttons use `get_image_download`. Test that no bearer token reaches the iframe.

**M4 exit:** the Desktop/claude.ai upload → edit → download flow works for an instructor account.

---

## M5 — Migration and stdio retirement

### Task 5.1: Local export
- `openrouter-image-mcp export-local --out bundle.json` writes the active cohort's chats (history only), preferences and job metadata. It never includes keys or container IDs.

### Task 5.2: Authenticated import
- `import_local_bundle` app-only tool, or an upload route reusing Task 3.2 tokens. It validates the schema and size, then creates chats under the caller's principal with new IDs and fresh containers. Imports are idempotent by bundle hash.

### Task 5.3: Parity checklist and retirement
- Walk the spec §4 parity checklist in each client and record the results.
- Then remove stdio-only code paths (`auth.py` localhost listener, `keystore.py`, `scripts/install_claude_desktop.py`, plugin `.mcp.json` stdio entry), replacing them with connector instructions, and update the README.
- One PR, which needs explicit user approval before merging.

---

## M2 implementation notes (2026-10-18, resume here)

Decisions made before work paused (no M2 code written yet):
- **Keep stdio unchanged.** Add `advisor_chats.sandbox INTEGER NOT NULL DEFAULT 1` (legacy rows stay sandbox/shell). Existing SQLite files need a migration: add a `storage` helper for SQLite `PRAGMA table_info` / Postgres `ADD COLUMN IF NOT EXISTS`. `AdvisorService(..., sandbox_default=True, sandbox_allowed=True)`. Hosted `build_workspaces` passes `sandbox_default=False, sandbox_allowed=settings.sandbox_enabled`.
- **Inline chats** (`sandbox=0`): no `tools` key, no skill upload (skills already go into the brief), and an inline INSTRUCTIONS variant with no container text.
- **Files travel in memory** to the job task, like skills. `send` upserts them inside the chat lease, and the upsert delta produces the "Updated since last turn" line.
- **Headless logic** goes in a new `headless.py` (`HeadlessAdvisor(service, jobs, preferences)` with `ask` / `wait`). It only calls service, jobs and preferences methods, so the hosted `ByWorkspace` proxies work unchanged. `AdvisorJobs` gets a per-job `asyncio.Event`; `wait` polls the DB every 1s plus the event. Progress uses `ctx.report_progress` from an MCPServer `Context` param.
- **Busy:** `AdvisorResults.create` raises `AdvisorAccessError` when a job is active. Catch it and look up the active job id for `running_job_id`.
- **`sandbox` arg** goes on `start_advisor_chat` and `ask_advisor` (create only). `send_advisor_message` gets `files` and `replace_files` only.
- `cache_usage` is not stored on job rows; either add a JSON column or leave it out of `ask_advisor` results.
