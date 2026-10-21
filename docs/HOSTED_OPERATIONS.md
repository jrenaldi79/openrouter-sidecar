# Hosted Sidecar operations

How to run the hosted (Streamable HTTP) OpenRouter Sidecar on Railway.
- Design: `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md`.
- Decisions: `docs/superpowers/specs/2026-10-18-hosted-decision-record.md`.
- Student keys: `docs/superpowers/specs/2026-10-19-provisioned-student-keys-design.md`.
- Local synthetic e2e: `docs/E2E_LOCAL.md`.

**Every step that touches Railway, Auth0 or OpenRouter is outward-facing.** Do each
one deliberately. Never paste secrets into chat, issues or logs.

## 1. Configuration

| Variable | Required | Value |
|---|---|---|
| `AUTH0_ISSUER` | yes | `https://dev-exz4k4zrf6whdd7s.us.auth0.com/` (trailing slash) |
| `MCP_RESOURCE_URI` | yes | `https://<service-domain>/mcp`. Must equal the Auth0 API identifier exactly |
| `DATABASE_URL` | yes | `${{Postgres.DATABASE_URL}}` (Railway reference to the private Postgres URL) |
| `VAULT_KEYS` | yes | `v1:<base64 32 bytes>`, newest first. See §5 |
| `LINK_SIGNING_KEY` | yes | ≥ 32 random characters. Signs single-use upload, import and download URLs (the name predates the retired OpenRouter linking) |
| `OPENROUTER_MANAGEMENT_KEY` | for new students | An OpenRouter **management** key (org admin). The server creates one key per student with it, and `purge` deletes idle ones. Unset: the server runs, existing keys keep working, and new students are told the Sidecar isn't set up to issue keys yet. See §8 |
| `FORWARDED_ALLOW_IPS` | yes on Railway | `*`. Railway's edge proxy is the only ingress, so trust its `X-Forwarded-*` |
| `AUTH0_JWKS_URI` | no | Defaults to `<issuer>.well-known/jwks.json` |
| `MCP_PERMISSION` | no | Default `sidecar:use` |
| `PROFILE_CLAIM` | no | Default `https://mpd.northwestern.edu/profile` |
| `SIDECAR_MAINTENANCE` | no | `true` keeps the server closed: tool calls are refused and `/ready` reports maintenance. See §4 |
| `SIDECAR_SANDBOX_ENABLED` | no | `true` in production since 2026-10-21, after the live isolation test (`docs/superpowers/plans/2026-10-20-sandbox-isolation-live-test.md`). Chats stay inline unless started with `sandbox: true`. Unset it, and repeat the test, if students or teams are ever given raw keys |
| `SIDECAR_SUMMARY_MODEL` | no | Model for share summaries of long advisor answers. Default `google/gemini-3.5-flash-lite` |
| `ASSET_DIR` | for images | Absolute path on the mounted Railway volume, e.g. `/data/assets`. Unset keeps the image tools off |
| `RETENTION_DAYS` | no | Default 90 (`purge`) |
| `PORT` / `HOST` | no | Railway sets `PORT`. `HOST` defaults to `0.0.0.0` |

`SIDECAR_DEV_INSECURE_ISSUER` and `SIDECAR_DEV_OPENROUTER_URL` are for local development only. The server refuses them unless the resource is on localhost. Never set them on Railway.

Generate secrets locally, and paste them straight into Railway's variable editor:

```bash
python3 -c 'import os,base64;print("v1:"+base64.b64encode(os.urandom(32)).decode())'
```

```bash
python3 -c 'import secrets;print(secrets.token_urlsafe(48))'
```

**Per-principal limits** (code defaults in `hosted/limits.py`):
- 2 concurrent advisor jobs;
- 20 requests per minute;
- $5.00 per day, counted from the job costs OpenRouter reports;
- image calls (`generate_image`, `edit_image`): 2 at a time and 20 per minute, counted separately. The daily dollar cap doesn't include image spend yet, because image calls are inline and record no job row.

## 2. Auth0 (shared MPD tenant)

The sidecar consumes the MPD unified RBAC contract:
- Contract: `mpd-auth/docs/CONTRACT.md`.
- Rollout steps: `mpd-auth/docs/superpowers/specs/2026-10-18-unified-auth0-rbac-design.md` §7.

Tell the "MPD MCP Interview Coach tool" session before any tenant change. Each rollout step needs the user's approval. The sidecar needs:

1. **APIs.** API **MPD OpenRouter Sidecar** with identifier = `MCP_RESOURCE_URI`:
   - RS256, RBAC on, permissions in the access token;
   - 600 s access tokens, offline access;
   - permissions `sidecar:use`/`sidecar:admin`.
   
   Also **MPD OpenRouter Sidecar (local)** with identifier `http://localhost:8000/mcp`.
2. **Client grants.** The shared static client **MPD MCP Clients** is authorized for these two APIs only.
3. **Roles.** `mpd-student` (with `sidecar:use`) is assigned to the roster by `mpd-auth sync`.
4. **Action.** The post-login Action **MPD - Profile claim** emits `workspace_id` and `cohort`. Tokens without `workspace_id` get 403.

## 3. Railway setup (one time)

1. Create the project `openrouter-sidecar` and add a **Postgres** service.
2. Add a service from this repo. Railway builds the `Dockerfile`.
3. **Settings:**
   - one replica in **one region** (the current plan allows only one; production runs in `iad`, US East). If a deploy fails with "can only deploy to a single region", the service lists more than one region: set `multiRegionConfig` back to a single entry;
   - a **volume** mounted at `/data` (image assets live under `ASSET_DIR=/data/assets`). The volume ties the service to one replica. Railway mounts it root-owned; `docker-entrypoint.sh` gives `ASSET_DIR` to the `sidecar` user (uid 10001) and then drops root;
   - health check path `/health`;
   - **autodeploy off**, because deploys go through §4;
   - private networking to Postgres.
4. Set the variables from §1. Generate a domain, then set `MCP_RESOURCE_URI` to it.
5. Deploy. Check that `GET /health` returns 200 and `GET /ready` returns `{"ready": true}`; `/ready` also fails with "asset storage is not writable" when the volume isn't usable. Check that `GET /.well-known/oauth-protected-resource/mcp` names the Auth0 issuer.
6. **Operator probe (no cost).** Sign in as a real account and check the live server without claude.ai:

   ```bash
   uv run python scripts/ops/real_login.py --prompt-login --audience https://<domain>/mcp
   uv run python scripts/ops/acceptance_probe.py --url https://<domain>/mcp
   rm .dev-state/real-token
   ```

   The login uses the passwordless **email** connection (a code by email). The shared client also has the password database and Google enabled, and the identifier-first page sends an email address to the password form. Don't set a password there: that creates a separate account without the role. The probe checks 401 without a token, admission, the hosted tool list, `account_status` (profile, cohort, and the account's own provisioned key, which this first call issues), and an upload and signed download through the volume.
7. **Acceptance (instructor account):**
   - add the connector in claude.ai (`https://<domain>/mcp`) and sign in;
   - run `account_status`: it issues your own key (`Sidecar · <email> · <cohort>`) and shows the budget. Check the budget line says `$25.00` and `monthly` (§8);
   - run `ask_advisor` with a file, then a follow-up with the same `chat_id`;
   - confirm a second test account can't see the first account's chats;
   - images: `create_image_upload`, `curl -T` the file, `edit_image` with a mask, `remask_image`, then open the download link. A second account must get "unavailable" for the first account's asset id.
   - MCP Apps (claude.ai and Desktop): `open_image_uploader`, drop a PNG, confirm the asset id reaches the chat, `edit_image` it, then use the gallery's **Download** button.
   
   Record the results under "M1 acceptance" in `docs/superpowers/specs/2026-10-18-hosted-spikes.md` (create it on first use).

**One owner at a time.** `/ready` holds a Postgres advisory lock, so a second replica reports not ready. Advisor jobs run in-process. Replicas must stay at 1.

**Database connections.** Store calls share one pool per `DATABASE_URL` (`storage.POOL_MAX` = 20 sessions; idle extras close after 5 minutes). The owner lock holds one more dedicated session, so the service needs at most 21 of Railway Postgres's 100. Each pooled session is pinged before reuse, so a Postgres restart costs a reconnect, not failed calls. If the pool is exhausted, a call waits up to 10 s and then fails as a database error. There is no environment knob; change `POOL_MAX` in code if replicas or load change. Advisor result polls take the write lock only when a job's heartbeat has lapsed.

## 4. Deploy procedure

Railway restarts the service on every deploy, **and on every variable change**. A restart interrupts advisor jobs that are running in-process. They are marked "interrupted" and never retried automatically. So:

1. Pick a quiet time and check that nothing is running:

   ```bash
   railway run openrouter-image-mcp active-jobs
   ```

   Deploy only when it prints `{"active_jobs": 0}`. Jobs run at most 30 minutes (`ADVISOR_MAX_TIMEOUT_S`).
2. Deploy (`railway up`, or a manual deploy of the commit).
3. Verify `/health` and `/ready`.

`SIDECAR_MAINTENANCE=true` is not a drain switch, because setting it restarts the service. Use it to keep a broken or mid-migration deployment closed: tool calls are refused and `/ready` reports maintenance. Clear it to reopen.

Rollback: redeploy the previous image. Schema changes are additive (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`), so older code ignores new columns.

## 5. Key rotation

- **Vault key** (encrypts the provisioned student OpenRouter keys):
  1. Prepend a new key: `VAULT_KEYS=v2:<new>,v1:<old>`.
  2. Deploy. New keys are sealed with v2, and existing keys are re-sealed with v2 the next time they're used.
  3. After a full term, remove `v1`. A key not used since then can't be opened: delete it with `scripts/ops/openrouter_keys.py delete <email> --yes` and remove its `student_keys` row, and the student gets a fresh key on their next call.
- **Link signing key** (`LINK_SIGNING_KEY`). Replace it and deploy. Outstanding upload and import URLs (10 minutes) and download links (24 hours) stop working; ask for fresh ones.
- **Management key** (`OPENROUTER_MANAGEMENT_KEY`). Create a new management key in OpenRouter, set it yourself with `railway variables set OPENROUTER_MANAGEMENT_KEY=…` (at a quiet time, see §4), then delete the old one in OpenRouter. Student keys are unaffected.
- **A student's OpenRouter key.** `scripts/ops/openrouter_keys.py delete <email> --yes`. Their next call gets a fresh key.

## 6. Retention purge

Advisor chats idle for more than `RETENTION_DAYS` (default 90) are deleted along with their jobs and session files, in batches of 500.
- Chats that are busy, or still owe a provider upload cleanup, are kept.
- Rows from before activity stamping are stamped on first sight.
- Student keys unused for the window, whose student has no chats left in that cohort, are deleted at OpenRouter and then forgotten (needs `OPENROUTER_MANAGEMENT_KEY`, which `railway run` provides; without it they are only counted). A key OpenRouter fails to delete is kept and retried next time.
- The retired OpenRouter linking tables (`openrouter_links`, `link_attempts`) are emptied. They are not dropped.
- The report contains counts only.

```bash
railway run openrouter-image-mcp purge
```

```bash
railway run openrouter-image-mcp purge --apply
```

The first command is a dry run. Review its counts before running `--apply`. To automate it, add a Railway cron service with the same image and the command `openrouter-image-mcp purge --apply` (weekly).

**Images are swept by the server itself.** Assets expire 30 days after creation and become unreadable at once. Each hour the serving instance deletes expired blobs from the volume and then their rows, and drops upload tickets a day after they expire. `purge` can't do this, because `railway run` executes on your machine, which has no access to the volume. Each account has a 2 GB storage cap, enforced on every upload and result.

## 7. Incidents

- **A student's access must end now.** Block the user in Auth0, which takes effect at the next token. Removing `mpd-student` takes effect within 600 s.
- **A student key may have leaked.** `scripts/ops/openrouter_keys.py delete <email> --yes`; the student gets a new key on their next call.
- **The management key may have leaked.** Delete it in OpenRouter at once (new students then can't get keys), create a new one and set it on Railway (§5). Check `openrouter_keys.py list` for keys the Sidecar didn't create.
- **The database is exposed.** Student keys are AES-GCM sealed and bound to their principal. Rotate the vault key (§5) and delete every student key (`openrouter_keys.py delete` each, or remove them in OpenRouter); the Sidecar issues new ones on next use.
- **Logs** never contain bearer tokens, OpenRouter keys (management or student) or signed upload/download tokens. Key hashes may appear; they are not secrets. Anything that looks like a key is a bug to report.

## 8. Student OpenRouter keys

Students don't need OpenRouter accounts. The first time a signed-in student uses a tool that needs OpenRouter (or `account_status`), the server creates a key named `Sidecar · <email> · <cohort>` in their cohort workspace with the management key, stores it sealed, and uses it only for their requests. One key per student and cohort, even when two calls race.

**Set up the management key (once, as an org admin).** In OpenRouter, open the organization's settings, create a **management key** (it is unscoped and can manage keys in every workspace), and copy it once. Set it yourself; never paste it into chat:

```bash
railway variables set OPENROUTER_MANAGEMENT_KEY=…
```

That restarts the service (§4). It is used only to create, inspect and delete student keys, never for inference.

**Budgets.** Each cohort workspace has a guardrail named **`Sidecar student budget`** ($25 per month), and the server assigns it to every key it creates. Keys the management key creates don't inherit the workspace default guardrail, so this assignment is what budgets them. If the guardrail is missing, or the assignment fails, the server deletes the new key and the student sees a retry-later message: there is never an unbudgeted key. To change the budget, edit that guardrail in the OpenRouter dashboard (keep its name). Check it, and that every Sidecar key is on it:

```bash
OPENROUTER_MANAGEMENT_KEY=… uv run python scripts/ops/openrouter_keys.py guardrails
```

`account_status` on a student's key shows `Budget: $… of $25.00 used (monthly …)`.

**Managing students** (locally, with the management key in your environment; the script never prints it):

| Command | Effect |
|---|---|
| `list [--cohort 2027]` | Every Sidecar key: created date, spend this month, disabled flag |
| `show <email>` | One student's key details |
| `disable <email>` / `enable <email>` | Block or unblock. A blocked student is told their key was disabled by the instructor |
| `delete <email> --yes` | Remove the key (dry run without `--yes`). The student gets a fresh key on the next call, so use `disable` to block |
| `usage [--csv]` | Spend per cohort, or a CSV per student |
| `guardrails` | Each workspace's `Sidecar student budget` guardrail, and any Sidecar key not on it |

**Removing a student** for good: remove them from the roster in Auth0 (§7), then `delete <email> --yes`. Without Auth0 access they can't get a new key.
