# Local end-to-end run (synthetic)

This runs the hosted sidecar (`serve-http`) on your machine, using local stand-ins for
Auth0 and OpenRouter, and drives it as test users with the real MCP SDK client.

**It is synthetic.** It does not cover:
- a real Auth0 login;
- a real OpenRouter link or spend;
- a hosted Claude client (claude.ai, Desktop, Claude Code against Railway).

Those remain acceptance steps for you.

## Pieces

| Piece | What it is |
|---|---|
| `scripts/dev/fake_identity.py` | Local issuer. It serves a JWKS on `127.0.0.1:9400` and mints contract-shaped RS256 tokens (`permissions`, profile claim). |
| `scripts/dev/fake_openrouter.py` | Local OpenRouter on `127.0.0.1:9500`. It covers the management API for student keys (behind the fake management key that `run_sidecar.sh` passes), `/key`, `/models` and `/responses`. Replies describe the files and turns the advisor saw. |
| `scripts/dev/run_sidecar.sh` | `serve-http` with dev flags. `SIDECAR_DEV_INSECURE_ISSUER` and `SIDECAR_DEV_OPENROUTER_URL` are accepted only for localhost. |
| `scripts/dev/e2e_local.py` | Driver that runs 37 checks and prints PASS/FAIL. It never prints tokens or keys. |
| `scripts/dev/e2e_migration.py` | The M5 leg: builds a local advisor database, exports it, imports it as a new student and continues the chat. |
| `.claude/launch.json` | Starts the three servers (`fake-identity`, `fake-openrouter`, `sidecar-http`). |

## Run it

```bash
docker run -d --name sidecar-pg -e POSTGRES_PASSWORD=test -p 55433:5432 postgres:16
```

```bash
docker exec sidecar-pg psql -U postgres -c "CREATE DATABASE sidecar_e2e"
```

Then start `fake-identity`, `fake-openrouter` and `sidecar-http` from `.claude/launch.json` (or run each command by hand), and run the driver:

```bash
uv run python scripts/dev/e2e_local.py --state .dev-state
```

## Result: 2026-10-18 (commit after 7dd8937)

33/33 checks passed against Postgres 16. Coverage:

- **Auth:**
  - no token → 401 with resource metadata naming the issuer;
  - a token without `sidecar:use` → 403;
  - a token without `workspace_id` → 403 (fixed during this run; it used to fail only at tool time);
  - `/health` and `/ready`.
- **Link flow over HTTP:**
  - an unlinked user gets `openrouter_link_required` with a one-time URL;
  - start → fake sign-in (with `required_workspace_id`) → callback → a confirm page naming the target account;
  - confirm succeeds, and a replayed confirm is refused;
  - unlink, then relink, keeps the user's chats.
- **Headless advisor:**
  - `ask_advisor` with two files completes in one call;
  - the answer is wrapped in `<advisor_output>`;
  - the advisor saw both files and was offered no tools;
  - a follow-up from a **new connection** with the same `chat_id` restores the transcript (turn 2) and sees the updated file (v2);
  - `wait_s=0` → `wait_advisor_job` completes (turn 3);
  - `sandbox=true` is refused while disabled.
- **Isolation:**
  - another student can't continue, read or list the first student's session or job;
  - a 2028 student is routed to the 2028 workspace.
- **Logs:** no bearer tokens or `sk-or` keys appear. Single-use link tokens and OAuth codes are withheld from logs (fixed during this run).

## Result: 2026-10-18, M3 hosted images

38/38 checks passed against Postgres 16, with `ASSET_DIR` under `.dev-state/assets`. The five new checks:
- `create_image_upload` + `PUT` creates image assets;
- `edit_image` with a mask returns an asset id and a download link;
- `remask_image` re-blends for $0.00;
- the signed download link returns the PNG;
- another student gets "unavailable" for the first student's asset.

Access logs show `/uploads/[withheld]` and `/assets/[withheld]` in place of the signed tokens.

A manual pass in the in-app browser of the link pages (fake sign-in → confirm page → "Linked") also succeeded, and the browser-linked user then completed `ask_advisor`.

## Result: 2026-10-19, M5 local-to-hosted migration

46/46 checks passed against Postgres 16, on a freshly created `sidecar_e2e` database. The new checks:
- the export holds no local chat or container ids;
- a new 2027 student links OpenRouter, gets a `create_local_import` link and uploads the export, which creates one chat;
- uploading the same export again reports `already_imported`;
- `ask_advisor` on the imported chat answers as turn 2 and sees `notes.md v1`, so the transcript and session files carried over;
- a 2028 student's upload of the 2027 export is refused with 403.

This run found that access logs printed `/imports/` link tokens. The fix withholds them like `/uploads/` and `/assets/`.

## Result: 2026-10-19, provisioned student keys

37/37 checks passed against SQLite (`DATABASE_URL=sqlite:///…`; no Postgres container was available for this run). Linking was replaced by provisioned keys, so the link checks are gone. The new checks:

- the first call issues a key named `Sidecar · <email> · <cohort>` in the student's cohort workspace, and `account_status` shows it with the $25 monthly budget;
- each student (2027, 2028 and the migration user) gets their own key on first use;
- a key disabled through the management API is refused, and `account_status` reports "disabled by your instructor"; re-enabled, it works again;
- a deleted key is replaced on the next call and the student's chats are kept.

The server log showed no `sk-or` keys. OpenRouter key hashes (not secrets) appear in its outgoing request lines.
