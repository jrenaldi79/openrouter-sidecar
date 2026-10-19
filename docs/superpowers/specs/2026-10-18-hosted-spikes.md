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

### 2026-10-19: claude.ai connector, real account

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

- **Paid flows, run by the agent after the user approved spending ($0.0407 in total):**
  - **Advisor follow-up.** `ask_advisor` started a chat with one file (`notes/project.md`, a synthetic codename) for $0.0008. A follow-up on the same `chat_id`, sent without the file, named the file and its owner correctly for $0.0011.
  - **Masked edit.** `edit_image` with `google/gemini-2.5-flash-image` used an uploaded 256x256 PNG and an uploaded mask, for $0.0388. The change stayed inside the mask.
  - **Remask.** `remask_image` with a second uploaded mask re-blended the result for $0.00.
  - **Downloads.** Both results downloaded as 256x256 PNGs. The edit's metadata records the model, prompt, cost and the input's sha256, which matches the uploaded file.
- **Finding, fixed:** image results carried the preview JPEG as base64 in `structuredContent`, and Claude Code showed that JSON to the model. Previews now travel in `_meta.sidecarPreviews` (`367017c`, deployed in `dfa2e3f`).
- **After deploying `dfa2e3f`** (previews in `_meta`, throttled partial writes, pooled Postgres):
  - a free remask returned only ids and links;
  - a third advisor turn on the same chat, with nothing resent, recalled the codename and launch week ($0.0041).

- **Gallery regression, fixed (`47aeccc`):** claude.ai delivers tool results to the app without `_meta`, so after `dfa2e3f` every preview read "Preview unavailable". The gallery now falls back to the result's JPEG image content blocks.
- **claude.ai, checked by the agent in the user's browser after `47aeccc`:**
  - a free `remask_image` rendered the gallery with its preview, filename, asset id and Download button;
  - `open_image_uploader` rendered the drop zone.

- **Stdio retirement gate (parity checklist), 2026-10-19, through the live connector:**
  - **Generate:** `google/gemini-3.1-flash-lite-image` returned two 1264x848 images (3:2) for $0.0672.
  - **n above the model's max:** asking for n=2 from a max-1 model split into two billed calls, with the note "model max n is 1; will make 2 calls".
  - **Edit with references and `fit=preserve`:** two input assets gave a 1264x848 result from 1264x848 inputs ($0.0342). The metadata records the fit (`crop_back`, no padding or upscale).
  - **Masked edit, remask and cost reporting:** passed earlier (above). Every result reports its cost.
  - **Previews:** claude.ai renders the gallery (above), and Claude Code shows them inline as images, with no base64 in the model-visible result.
  - **Advisor in claude.ai (after `1ad1c0f`):** "show it to me" used `start_advisor_chat` and `send_advisor_message`, and the viewer streamed. `open_advisor_result` and `open_sidecar_settings` now render as apps (labelling them read-only had hidden them). Share with Claude sent the full answer, and Claude then read it via "Check the app on screen". "Quietly check" used `ask_advisor`.
  - **Skills:** the user's `no-ai-slop` plugin skill was passed in full as a skill packet, and the output followed it. A canary skill's arbitrary rule was followed, while the same prompt without the skill ignored it.
  - **Result:** the gate passed, and stdio was retired.

**Still open:**

- **Uploader drop and gallery Download in claude.ai:** needs a person. The app's sandboxed iframe isn't reachable by browser automation, and downloads need the user's OK. The same upload path (link, PUT, asset id) passes live through the connector.
- **Two-account isolation, live:** needs a second rostered identity, which is an Auth0 tenant change awaiting approval. The synthetic two-principal and cross-cohort checks pass; live, there is only the unknown-id refusal above.
