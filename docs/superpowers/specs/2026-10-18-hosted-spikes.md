# Hosted Sidecar acceptance results

Live results only. The synthetic multi-user run (fakes for Auth0 and OpenRouter) is recorded in `docs/E2E_LOCAL.md`.

## M1 acceptance

### 2026-10-19: operator probe, real Auth0 (no paid calls)

- **Deployment:**
  - Railway project `openrouter-sidecar`, service `sidecar`, region `iad` (US East), one replica;
  - volume at `/data`, Postgres in the same region;
  - origin `https://sidecar-production-e110.up.railway.app`, built from commit `98484b8`.
- **Account:**
  - instructor `jrenaldi@northwestern.edu` on the passwordless `email` connection, signed in through the shared client **MPD MCP Clients** with `connection=email`;
  - the token carried `sidecar:use`, `sidecar:admin` and the profile claim.
- **Result:** `scripts/ops/acceptance_probe.py` passed 5 of 5 checks:
  - no token is refused with 401;
  - the tool list is the hosted one;
  - `account_status` shows the profile and cohort 2027;
  - an upload is stored as an asset;
  - the signed download returns the same bytes.

**Findings, fixed:**

- **The first upload failed with a 500.** Railway mounts the volume root-owned, so uid 10001 couldn't create `/data/assets`, while `/ready` still said ready.
  - Fixed in `4f74f6b`: the entrypoint gives `ASSET_DIR` to the `sidecar` user and then drops root.
  - `/ready` now fails when the asset folder isn't writable.
- **A Google sign-in (`google-oauth2`) for the same person got a token without permissions**, and it was refused with 403 as designed. Whether to accept Google sign-ins for students is still an open decision.
- **The identifier-first login page sends an email address to the password form**, because the shared client also has the database connection enabled. The operator login requests `connection=email` directly. No tenant change was made.

**Still open (needs a person in claude.ai or Claude Desktop):**

- add the connector and run `link_openrouter` with your own key;
- `ask_advisor` with a follow-up;
- the image edit, remask and download flow;
- the MCP Apps uploader and gallery Download;
- the two-account isolation check.
