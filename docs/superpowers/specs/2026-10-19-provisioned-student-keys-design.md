---
title: "Provisioned student OpenRouter keys"
description: "Design for issuing each student an OpenRouter key automatically from a management key, replacing the OpenRouter linking flow"
type: "proposal"
status: "completed"
read_when: "Changing how the hosted Sidecar gets, stores, checks or removes a student's OpenRouter key, or operating student keys"
---

# Provisioned student OpenRouter keys

**Date:** 2026-10-19 · **Status:** approved by the user; built on the `provisioned student keys` branch.

## Problem

Until now, each student had to link their own OpenRouter account to the hosted Sidecar.
That meant a browser PKCE flow (`link_openrouter`, the `/link/openrouter/*` pages, the
`openrouter_links` and `link_attempts` tables). Before that, every student also needed an
OpenRouter account and an invitation to the cohort's organization workspace. That is too
much admin for a class. No student has linked yet (only the instructor, while testing), so
nothing needs migrating.

## Design

1. **Management key.** One OpenRouter management key, created by an org admin, is unscoped
   and can manage keys in every workspace. It comes from the environment variable
   `OPENROUTER_MANAGEMENT_KEY`, a Railway secret the instructor sets. The server uses it
   only to create, inspect and delete student keys. It is never used for inference, never
   logged and never returned in tool output (`logs.redact` covers the `sk-or-...` format).
   When it is unset, the server still starts and `/health` passes. Students who already
   have a key keep working. Paid tools and `account_status` tell everyone else that the
   Sidecar isn't set up to issue keys yet and to ask the instructor. The message names no
   configuration.
2. **Provision on first use.** The first time a signed-in principal needs a key, the server
   creates one with `POST /api/v1/keys`. A key is needed by any tool that reads the
   caller's credentials, and by `account_status`. The request body is:
   - `name`: `Sidecar · <email> · <cohort>`. The email comes from the verified profile
     claim; without one, `user-` plus the first 10 hex digits of SHA-256 of the principal
     key stands in.
   - `workspace_id`: the principal's cohort workspace.

   The request sets no `limit` or `limit_reset`. The plaintext key comes back only once,
   in `key`. The server also keeps OpenRouter's `data.hash`.
3. **One key per principal and workspace.** Concurrent first calls must not create two
   keys:
   - An in-process `asyncio.Lock` per (principal, workspace) means one replica creates at
     most once.
   - The table's primary key (principal, workspace) and `INSERT ... ON CONFLICT DO NOTHING`
     keep the database to one row even across replicas.
   - A key created at OpenRouter that loses the insert, or whose insert fails, is deleted
     again through the management API. This is best effort and logged by hash prefix only.
4. **Storage.** The table is
   `student_keys(principal, workspace, sealed_key, key_hash, key_name, created_at, last_used_at, rejected_at)`,
   added with `CREATE TABLE IF NOT EXISTS` and keyed by (principal, workspace).
   - `sealed_key` uses the existing vault, bound to the principal key. Rows sealed under an
     old vault key are resealed under the newest one when they are read, as linked keys
     were.
   - `key_hash` is stored in clear for admin operations; it is not a secret.
   - `last_used_at` is stamped at most hourly and drives retention.
5. **Credentials.** `HostedCredentials.resolve()` stays a fast synchronous store lookup,
   because it also runs inside background jobs. Provisioning is async HTTP, so it happens
   earlier, in the `provision_keys` MCP middleware, before each `tools/call`. Tools that
   never touch credentials are skipped: uploads, downloads, the uploader, imports and
   `account_status`, which provisions itself. When the middleware can't provide a key, it
   records why in a context variable. `resolve()` raises that reason, or "not ready", only
   if the tool actually needs the key, so free tools keep working.
   - `check(scope)` keeps its meaning: a key that is missing, deleted or rejected withholds
     in-flight results.
   - `forget()` runs when inference gets a 401. It marks the row `rejected_at` instead of
     deleting it, so nothing re-provisions in a loop.
   - On the next call with a rejected row, the server asks the management API about that
     key hash:
     - Gone (404): the row is removed and a fresh key is created. The ops `delete` command
       is how an instructor resets a student.
     - `disabled`: the call fails with "disabled by your instructor".
     - Healthy again: the mark is cleared.
6. **Linking removed.** Removed:
   - the `link_openrouter` and `unlink_openrouter` tools;
   - `hosted/links.py`, `hosted/link_routes.py` and the `/link/openrouter/*` routes;
   - the PKCE exchange, `auth.py` and `config.AUTH_URL`.

   The old `openrouter_links` and `link_attempts` tables are not dropped (schema stays
   additive), but `purge --apply` empties them, since they may hold the instructor's
   sealed test key. `LINK_SIGNING_KEY` keeps its name and meaning: it still signs upload,
   download and import URLs (`hosted/uploads.py`), so existing signed links stay valid and
   no Railway variable changes.
7. **account_status** shows the sign-in and cohort, then the key name and when it was
   provisioned. Usage and budget come from `GET /api/v1/key` called with the student's own
   key: usage this month, and `effective_budget` (spend, limit, interval, reset date, and
   which layer it comes from) when OpenRouter reports one.
8. **Retention.** `purge` (still a dry run unless `--apply`) also counts student keys
   unused for the retention window whose principal has no advisor chats left in that
   workspace. With `--apply` and `OPENROUTER_MANAGEMENT_KEY` set, it deletes each key at
   OpenRouter (404 counts as gone), then removes the row. Without the management key it
   only counts them.
9. **Ops script.** The instructor runs `scripts/ops/openrouter_keys.py` locally, with
   `OPENROUTER_MANAGEMENT_KEY` in the environment; the script never prints it. Commands:
   - `list [--cohort]`
   - `show <email>`
   - `disable <email>` and `enable <email>`
   - `delete <email> --yes`
   - `usage --csv`
   - `guardrails`
10. **One HTTP seam.** All key-management HTTP goes through `hosted/openrouter_keys.py`
    (`KeyManager`, plus `current_key` for `GET /key`). Tests stub it with `respx` or the
    harness's mock transport.

## OpenRouter API relied on

| Call | Shape used | Docs |
| --- | --- | --- |
| Create | `POST /api/v1/keys {name, workspace_id}` → 201 `{key, data:{hash, name, workspace_id, disabled, ...}}` | [create](https://openrouter.ai/docs/api/api-reference/api-keys/create-a-new-api-key.md) |
| Get | `GET /api/v1/keys/{hash}` → `{data:{hash, name, disabled, usage_monthly, ...}}`, 404 when gone | [get](https://openrouter.ai/docs/api/api-reference/api-keys/get-a-single-api-key.md) |
| Update | `PATCH /api/v1/keys/{hash} {disabled}` | [update](https://openrouter.ai/docs/api/api-reference/api-keys/update-an-api-key.md) |
| Delete | `DELETE /api/v1/keys/{hash}` → `{deleted:true}`, 404 when gone | [delete](https://openrouter.ai/docs/api/api-reference/api-keys/delete-an-api-key.md) |
| List | `GET /api/v1/keys?workspace_id=&include_disabled=true&offset=` → `{data:[...]}` (pages of 100) | [list](https://openrouter.ai/docs/api/api-reference/api-keys/list-api-keys.md) |
| Current key | `GET /api/v1/key` (student key) → `{data:{usage_monthly, limit, effective_budget, budgets}}` | [current](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key.md) |
| Workspace | `GET /api/v1/workspaces/{id}` → `{data:{default_guardrail_id, ...}}` | [workspace](https://openrouter.ai/docs/api/api-reference/workspaces/get-a-workspace.md) |
| Guardrails | `GET /api/v1/guardrails?workspace_id=` → `{data:[{id, limit_usd, reset_interval}], total_count}` | [list guardrails](https://openrouter.ai/docs/api/api-reference/guardrails/list-guardrails.md) |
| Key assignments | `GET /api/v1/guardrails/assignments/keys` → `{data:[{guardrail_id, key_hash}], total_count}` | [assignments](https://openrouter.ai/docs/api/api-reference/guardrails/list-all-key-assignments.md) |

## Guardrail model

There is no per-key `limit`. Each cohort workspace's **default guardrail** carries a
budget of $25 a month, set by the user. OpenRouter checks the workspace default
guardrail per key (budget scope `workspace_default_key`) and also per member
(`workspace_default_member`).

**Open question, to confirm live.** Keys created through the management API should fall
under the default guardrail per key. They have no `creator_user_id`, so they should not be
pooled into one member's aggregate. Two checks confirm it:

- `scripts/ops/openrouter_keys.py guardrails`, which shows each workspace's default
  guardrail budget and any explicit key assignments;
- `account_status` on a freshly provisioned key, which shows `effective_budget` and the
  scope it comes from.

If the scope turns out to be `workspace_default_member`, students would share one budget.
The fix would then be a per-key `limit` or a dedicated guardrail assigned to each key.

## Failure modes

| Situation | Behaviour |
| --- | --- |
| `OPENROUTER_MANAGEMENT_KEY` unset | Server starts and `/health` passes. Paid tools and `account_status` say the Sidecar isn't set up to issue keys yet. Existing keys keep working. |
| OpenRouter down or erroring during creation | The call fails with a retry-later message; no row is written, and the next call tries again. |
| Two first calls at once | One creation; the other waits on the lock and reuses the row. |
| Insert fails or loses a cross-replica race | The new key is deleted at OpenRouter (best effort). |
| Key deleted by an admin | Inference 401 → row marked rejected → the next call sees 404 → row removed → a fresh key is provisioned. |
| Key disabled by an admin | Inference 401 → row marked rejected → each call reports "disabled by your instructor" until the key is enabled again. |
| Budget exhausted | OpenRouter returns 403 and its message is relayed. `account_status` shows the budget. |
| Vault key rotated | Rows are resealed on read, as before. |
