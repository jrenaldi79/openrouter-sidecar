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
| `scripts/dev/fake_openrouter.py` | Local OpenRouter on `127.0.0.1:9500`. It covers the PKCE page, code exchange, `/key`, `/models` and `/responses`. Replies describe the files and turns the advisor saw. |
| `scripts/dev/run_sidecar.sh` | `serve-http` with dev flags. `SIDECAR_DEV_INSECURE_ISSUER` and `SIDECAR_DEV_OPENROUTER_URL` are accepted only for localhost. |
| `scripts/dev/e2e_local.py` | Driver that runs 33 checks and prints PASS/FAIL. It never prints tokens or keys. |
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

A manual pass in the in-app browser of the link pages (fake sign-in → confirm page → "Linked") also succeeded, and the browser-linked user then completed `ask_advisor`.
