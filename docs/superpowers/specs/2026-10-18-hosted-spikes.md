---
title: "Hosted Sidecar acceptance results"
description: "Live acceptance results for the deployed hosted Sidecar (real Auth0, Railway), kept apart from synthetic runs."
type: "status"
status: "maintained"
read_when: "Recording or checking live acceptance of the hosted server."
---

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
- **The identifier-first login page sends an email address to the password form**, because the shared client also has the database connection enabled. The operator login requests `connection=email` directly. For claude.ai, the database connection was later removed from **MPD MCP Clients** with the user's approval (recorded in `../mpd-auth`).

### 2026-10-19: claude.ai connector, real account (no paid calls by the agent)

- **Deployment:** commit `1527802`, same service and region.
- **Done by the user in claude.ai:**
  - added the connector with the shared client;
  - signed in with an email code;
  - ran `link_openrouter` with their own key;
  - ran two advisor chats (`google/gemini-3.8-flash`, about $0.0014 each);
  - completed onboarding.
- **Checked by the agent through the same connector, all at $0:**
  - `account_status` shows the profile, cohort 2027 and the linked key;
  - the two chats are listed, and `get_advisor_chat` shows a completed job with its cost;
  - preferences, the chat catalog and the image catalog load;
  - `create_image_upload`, then a `curl -T` PUT of a 256x256 PNG, returns an asset id;
  - replaying the same upload link is refused with 400;
  - `get_image_download` returns signed links, and the image matches the upload byte for byte;
  - a changed signature, or the metadata signature reused on the image path, returns 404;
  - an unknown asset id is refused as "unavailable for this sign-in".
- **Findings, fixed:**
  - Hosted tool descriptions called chats "locally saved" and preferences "on this computer" (`1527802`). A hosted test now rejects that wording.
  - The connector reported no skills. Server instructions, prompts and `skill://` resources were added (`cf0ae68`).
  - The advisor viewer's Settings button triggered a host "send a message" prompt. The viewer now shares through model context only (`c56b87f`).

**Still open:**

- **Paid flows through the connector:**
  - an `ask_advisor` follow-up on an existing `chat_id`;
  - an image edit;
  - a remask.

  The agent's no-spend rule stands (delegate ruling, 2026-10-19). The user runs these or sets a spending cap. The synthetic run covers the logic (`docs/E2E_LOCAL.md`).
- **The MCP Apps uploader and gallery Download in claude.ai:** needs a person to click.
- **Two-account isolation, live:** needs a second rostered identity, which is an Auth0 tenant change awaiting approval. The synthetic two-principal and cross-cohort checks pass; live, there is only the unknown-id refusal above.
