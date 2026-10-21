# Hosted Sidecar operations

How to run the hosted (Streamable HTTP) OpenRouter Sidecar on Railway.
- Design: `docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md`.
- Decisions: `docs/superpowers/specs/2026-10-18-hosted-decision-record.md`.
- Student keys: `docs/superpowers/specs/2026-10-19-provisioned-student-keys-design.md`.
- Slack bot: `docs/superpowers/specs/2026-10-21-slack-sidecar-design.md` (setup in §9).
- Admin page: `docs/superpowers/specs/2026-10-21-slack-admin-design.md` (setup in §10).
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
| `SIDECAR_SLACK_ENABLED`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | no | Turn on the Slack bot and give it its two tokens. Unset keeps it off. See §9 |
| `SIDECAR_ADMIN_ENABLED` | no | The `/admin` page is on unless this is `false`, `0` or `no`. See §10 |
| `SIDECAR_ADMIN_CLIENT_ID`, `SIDECAR_ADMIN_CLIENT_SECRET` | for sign-in | The MPD Sidecar Admin Auth0 client. The secret must be at least 32 characters. Unset, the page says sign-in isn't configured. See §10 |
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

**One owner at a time.** `/ready` holds a Postgres advisory lock, so a second replica reports not ready. Advisor jobs run in-process. Replicas must stay at 1. The owner checks its lock connection every 15 s and a waiting replica retries every 15 s, so after the owner's connection drops (network cut, Postgres restart) there is a window of up to about 15 s before it notices and stops its background work. Postgres frees the lock when it drops the session, and the owner reconnects and retakes the lock in the same check, so recovery needs no restart. Checks and connects are bounded by the same interval.

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
| `list [--cohort 2027]` | Every Sidecar key, students' and Slack teams': created date, spend this month, disabled flag (`--cohort slack` for the Slack workspace only) |
| `show <email-or-team-label>` | One student's or team's key details |
| `disable <email-or-team-label>` / `enable <email-or-team-label>` | Block or unblock. A blocked student or team is told the key was disabled by the instructor |
| `delete <email-or-team-label> --yes` | Remove the key (dry run without `--yes`). The student or team gets a fresh key on the next call, so use `disable` to block |
| `usage [--csv]` | Spend per cohort, and per class for the Slack workspace (lines like `slack (2027): 3 keys, $12.00 this month`), or a CSV per key: `cohort` is the class (a team key's is the last part of its name), `group` the workspace (a cohort year or `slack`), and `email` holds a team's label |
| `guardrails` | Each cohort workspace's `Sidecar student budget` guardrail and the Slack workspace's `Sidecar team budget` one, and any Sidecar key not on it |

**Removing a student** for good: remove them from the roster in Auth0 (§7), then `delete <email> --yes`. Without Auth0 access they can't get a new key.

## 9. Slack Sidecar

The `@Sidecar` bot answers in each capstone team's Slack channel with models other than Claude. It runs inside the hosted service over Socket Mode, so it adds no public endpoint. Design: `docs/superpowers/specs/2026-10-21-slack-sidecar-design.md`. It is off until `SIDECAR_SLACK_ENABLED` is set. Run the live acceptance in the spec before a pilot channel.

**One-time setup, in this order:**

1. **Team budget guardrail.** In OpenRouter, create a guardrail named exactly **`Sidecar team budget`** ($75 per month) in the **Slack workspace** in OpenRouter (`8a738968-da7d-4d55-900a-16789ba50003`, `config.SLACK_WORKSPACE`). Every team key lives there, whatever the channel's class, so that one guardrail budgets them all. Guardrails belong to one workspace, and the server finds the guardrail by name in the workspace of the key it creates, as it does for `Sidecar student budget` (§8). If that workspace has no guardrail with that name, the server creates no team key, so the first mention in that team's channel is refused. There is no fallback to the student guardrail.

2. **Create the Slack app.** In the class Slack workspace, go to <https://api.slack.com/apps>, choose **Create New App**, then **From a manifest**, and paste `deploy/slack/manifest.yaml`. The manifest sets the bot scopes, the four events (`app_mention`, `member_joined_channel`, `member_left_channel`, `channel_shared`) and Socket Mode. Then:
   - **Install to Workspace.** The **Bot User OAuth Token** (`xoxb-…`) is on the OAuth & Permissions page.
   - **Basic Information, App-Level Tokens, Generate Token** with the `connections:write` scope. That is the Socket Mode token (`xapp-…`).

3. **Set the variables in Railway.** Set the values yourself in Railway's variable editor or with the CLI, and never paste them into chat or a log. Setting a variable restarts the service, so follow §4 first.

   | Variable | Required | Value |
   |---|---|---|
   | `SIDECAR_SLACK_ENABLED` | yes | `true` starts the bot. Unset by default |
   | `SLACK_BOT_TOKEN` | when enabled | `xoxb-…`. The server refuses to start with it missing or without that prefix |
   | `SLACK_APP_TOKEN` | when enabled | `xapp-…`, with the same check |
   | `SIDECAR_SLACK_CONTEXT_CHARS` | no | Channel part of each turn's context. Default 8000 |
   | `SIDECAR_SLACK_DAILY_CAP_USD` | no | The daily cap `slack_channels.py add` gives a new channel when `--daily-cap` is omitted. Default 4. Read from the environment the script runs in, not by the server |
   | `SIDECAR_SLACK_MAX_CONCURRENT` | no | The same for `--max-concurrent`. Default 3 |
   | `SIDECAR_SLACK_USER_LIMIT` | no | Requests per Slack user per 10 minutes. Default 10 |

   ```bash
   railway variables set SLACK_BOT_TOKEN=… SLACK_APP_TOKEN=… SIDECAR_SLACK_ENABLED=true
   ```

   A malformed `SIDECAR_SLACK_*` number (not a number, zero or negative) stops the server from starting even while `SIDECAR_SLACK_ENABLED` is unset, so fix or remove it before deploying.

   The bot also needs `DATABASE_URL`, `VAULT_KEYS` and `OPENROUTER_MANAGEMENT_KEY` (§1), because team keys are created and sealed the way student keys are. A missing token, or one without its `xoxb-` or `xapp-` prefix, stops the server from starting. A well-formed token that Slack rejects or has revoked, or a Slack outage, doesn't: the owning replica starts Slack in the background, the server keeps serving `/health` and `/ready`, and the bot retries `auth.test` every 5 seconds, backing off to 5 minutes. Check the logs for the failure type. Once `auth.test` works, the bot posts any answers a restart left unposted and opens the connection.

4. **Register each channel.** Invite `@Sidecar` to the channel first (the guest check reads its members). Then register it with `scripts/ops/slack_channels.py`. It prints no tokens or keys.

   **Run the ops scripts from your machine with the environment set by hand.** `${{Postgres.DATABASE_URL}}` (§1) is Railway's private URL, and a laptop can't reach it, so don't rely on `railway run` to supply it. Set `DATABASE_URL` to the Postgres service's public URL (Railway shows it as `DATABASE_PUBLIC_URL` on the Postgres service), and set the rest from your password manager, not from a file or your shell history:

   | Variable | Needed by |
   |---|---|
   | `DATABASE_URL` | every script: the public Postgres URL |
   | `VAULT_KEYS` | `add` or `update` with `--family-default`, and `slack_usage.py`. The same value the server uses |
   | `OPENROUTER_MANAGEMENT_KEY` | `add` or `update` with `--family-default` (creates the team key) |
   | `SLACK_BOT_TOKEN` | `add` and `update --active` (the guest check). `slack_usage.py` uses it for names, and falls back to Slack ids without it |
   | `SIDECAR_SLACK_DAILY_CAP_USD`, `SIDECAR_SLACK_MAX_CONCURRENT` | optional: the defaults `add` uses when `--daily-cap` or `--max-concurrent` is omitted (4 and 3 when unset). `update` never uses them |

   ```bash
   read -rs DATABASE_URL && export DATABASE_URL        # the public URL
   read -rs VAULT_KEYS && export VAULT_KEYS
   read -rs OPENROUTER_MANAGEMENT_KEY && export OPENROUTER_MANAGEMENT_KEY
   read -rs SLACK_BOT_TOKEN && export SLACK_BOT_TOKEN
   ```

   Whether this setup reaches the database and the team key as written is checked during the live acceptance (step 5); fix this section if it doesn't.

   ```bash
   uv run python scripts/ops/slack_channels.py add \
       --team-id T… --channel C… --label abbott --cohort 2027 \
       --fusion-panel provider/model-a,provider/model-b,provider/model-c \
       --fusion-analyst provider/model-a --subagent-model provider/model-c \
       --default-model gemini \
       --family-default google=provider/model-a openai=provider/model-b
   ```

   - `--team-id` is the Slack workspace id and `--channel` the channel id. `--label` is the team's name in key names and usage reports, `--cohort` is the class label (it names the key, `Sidecar · <label> · <cohort>`, and shows in lists; every team key is in the Slack workspace whatever it is), and `--fusion-panel` takes 1 to 8 model ids.
   - `add` refuses a channel that is shared outside the organization or has a guest member, and says why.
   - **`--default-model`** is the channel's default alias, the one used when a mention names no model. It must be one of the channel's aliases (the defaults, plus any added with `--alias`). It is not a model id.
   - **`--family-default family=model-id`** (repeatable) sets which model each brand alias means for this team: `google`, `openai`, `x-ai` or `open_weight`. Each id is checked against the OpenRouter catalog, and the result is saved as the team's model preference. This creates the team's OpenRouter key if it doesn't exist yet. A brand alias whose family has no default answers "This alias has no model set yet. Ask your instructor.", so set every family the team's aliases use. If `add` fails after the key was created, the key stays with no channel row; a retry reuses it.
   - Other options: `--alias name=Brand` or `name=model-id` (adds or replaces, never removes), `--fetch-allow domain` (domains `web_fetch` may always reach), `--daily-cap` (USD) and `--max-concurrent`, which default to the two environment variables above (4 and 3), and `--invoker U…` (limit who can ask). The registry refuses a daily cap that isn't a positive, finite number of dollars and a `--max-concurrent` below 1.

   Change a channel later with `update`, which takes the label first and then only the options to change:

   ```bash
   uv run python scripts/ops/slack_channels.py update abbott --default-model gpt
   uv run python scripts/ops/slack_channels.py list
   ```

   The first mention in a registered channel posts the welcome and creates the team key. A channel that went inactive (the bot was removed, or a guest joined or the channel was shared) comes back with `update <label> --active`, which re-runs the guest check.

5. **Check one channel** with the live acceptance steps in the design spec before registering the rest. Record the results in the decision record.

**Reading usage.** `@Sidecar usage` in a channel posts the team's spend for everyone there. For the same report in a terminal, which also works for an inactive channel:

```bash
uv run python scripts/ops/slack_usage.py --team abbott
```

It needs `DATABASE_URL` (the public URL) and `VAULT_KEYS`, set as in step 4, shows names when `SLACK_BOT_TOKEN` is set and Slack ids otherwise, and never prints a key. A team with no key yet (no family default was set and nobody has mentioned the bot) has nothing to report, and the script says so. Team keys show in `scripts/ops/openrouter_keys.py list` and in the OpenRouter dashboard as `Sidecar · <label> · <cohort>`.

**Turning it off.**
- **One channel:** `slack_channels.py deactivate <label> --reason "..."`. Mentions there are then ignored. Removing the bot from the channel does the same on its own.
- **The whole bot:** unset `SIDECAR_SLACK_ENABLED` and deploy as in §4. The restart interrupts jobs in progress, so check `active-jobs` first. Chats, notes and the registry stay in the database until the retention purge.
- **Tokens that may have leaked:** revoke them in the Slack app settings at once (the bot stops working), then reinstall the app or generate a new app-level token and set the new values in Railway. Nothing in logs should contain a token; one that does is a bug to report.

## 10. Admin page

`/admin` is a web page on the hosted server where MPD admins register and edit the Slack bot's channels, instead of running `scripts/ops/slack_channels.py`. Design: `docs/superpowers/specs/2026-10-21-slack-admin-design.md`.

**What it does:**
- **Channels** lists the registered channels with their label, channel name, class, on or off, default alias and limits. It also lists channels `@Sidecar` is in but that aren't registered, each with a Register link.
- **Register and edit** use one form: label, class, aliases, the model for each family, the default alias, the Fusion panel, analyst and helper model, fetch allowlist, daily cap, concurrent limit and who may use Sidecar. Saving a new channel writes the row turned off with the reason "Setting up", creates the team's OpenRouter key, saves the team's models and then turns the row on. A row turns on only when its default alias resolves to a model: an alias for a brand (Gemini, ChatGPT, Grok, open weight) needs that family's model, an alias for an exact model id needs none; otherwise the page says "Choose a model for <family>, the family the default alias uses." and the row stays off. If a step after the first fails, the row stays off with "Setup didn't finish", and the edit page offers a Finish setup button that repeats those steps. Label and class can't be changed after the row is saved.
- **Turn off and Turn on** are buttons on the edit page. Turning a channel on runs the guest and external-sharing check again. A row still in setup is refused with "Finish setting up this channel first."; use Finish setup for it. The edit page still opens while OpenRouter's model list is down (it says "The model list didn't load. Try again."), and Turn off, Turn on and edits that don't touch a model keep working.
- **Change log** shows the newest 200 changes: when, who, which channel, what was done, and each changed field as before and after.

`slack_channels.py` still works and stays the fallback. Changes made with the script do not appear in the change log.

**Who can sign in.** An admin signs in with the class Auth0 login (passwordless email). The access token must carry both `sidecar:use` and `sidecar:admin`, and it must have the profile claim (`PROFILE_CLAIM`) with an email. A `workspace_id` is not required. Anyone else sees "This page is for Sidecar admins." A session ends after 2 hours idle or 12 hours after sign-in, so removing someone's admin role takes effect at their next sign-in, within 12 hours.

**Variables** (also in §1):

| Variable | Default | Value |
|---|---|---|
| `SIDECAR_ADMIN_ENABLED` | on | `false`, `0` or `no` removes every `/admin` route, and they answer 404 |
| `SIDECAR_ADMIN_CLIENT_ID` | none | The client id of **MPD Sidecar Admin** (below) |
| `SIDECAR_ADMIN_CLIENT_SECRET` | none | That client's secret, at least 32 characters. The server never logs or shows it. A shorter value stops startup |

- The page is on by default. With the client id or secret missing, the server starts normally and logs one warning naming the missing variables. Every `/admin` page then says "Admin sign-in isn't configured on this server yet."
- The callback is `<origin of MCP_RESOURCE_URI>/admin/callback` and the logout return is `<origin>/admin`. A public origin that isn't HTTPS stops startup, except `http://localhost` and `http://127.0.0.1`.
- With `SIDECAR_SLACK_ENABLED` unset, the page says "Slack isn't turned on for this server." and lists the registered channels read-only. With it set, the page needs only `SLACK_BOT_TOKEN` at runtime, not the running gateway or the owner lock. Starting the server with `SIDECAR_SLACK_ENABLED` set still needs both Slack tokens (§9), so a missing `SLACK_APP_TOKEN` stops startup even though the page itself would not use it.
- The page doesn't depend on the owner lock, so every replica serves it. The sign-in limiter below is per process.
- **Sign-in limiting.** Each client address may start 20 sign-ins per 10 minutes; the 21st gets a 429 ("Too many sign-ins in progress. Try again in a few minutes."), and so does anyone once 500 sign-ins are waiting for their callback.
  - The address comes from uvicorn's proxy headers, which are trusted only from the addresses in `FORWARDED_ALLOW_IPS`. Without it every admin shares the proxy's address and one bucket.
  - The counter is in memory and starts again from zero on every restart or deploy.
  - Open question for the user: with `FORWARDED_ALLOW_IPS=*`, the leftmost `X-Forwarded-For` entry is chosen by the client if Railway appends to that header instead of replacing it. A client could then spread sign-ins over made-up addresses to get around the limit, or fill one bucket to block a real address. That affects availability only: signing in still needs a valid Auth0 login. Check how Railway sets the header before relying on the limit.
- Registering a channel also needs `OPENROUTER_MANAGEMENT_KEY`, `VAULT_KEYS` and the `Sidecar team budget` guardrail in the Slack workspace (§9 step 1). Without the guardrail the page says "The Slack workspace in OpenRouter has no 'Sidecar team budget' guardrail, so no team key can be made.", and the row stays off with a Finish setup button. The edit page keeps its model selects on in that state, so Finish setup saves the models typed there once the guardrail exists.
- Setting a variable restarts the service, so follow §4 first.

**Auth0 setup (done in mpd-auth).** The Auth0 tenant is shared and managed in the `mpd-auth` repo, so nothing in this repo changes it. This section is the request for `mpd-auth`. Before any step, tell the Interview Coach and mpd-auth sessions and wait for their constraints. The user applies each tenant step and records it in the `mpd-auth` rollout log.

1. **Client.** Create a regular web application named **MPD Sidecar Admin** (confidential, authorization code).
2. **Signing algorithm.** Set RS256 explicitly. A client created through the API or CLI can be left without `jwt_configuration.alg` and sign HS256, which the page refuses:

   ```bash
   auth0 api patch "clients/<client id>" --data '{"jwt_configuration":{"alg":"RS256"}}'
   ```

3. **Connection.** Enable only the passwordless email connection for this client. With `Username-Password-Authentication` also enabled, identifier-first login asks for a password.
4. **URLs.**
   - Allowed callback URLs: `https://<service-domain>/admin/callback` and `http://localhost:8000/admin/callback`.
   - Allowed logout URLs: `https://<service-domain>/admin` and `http://localhost:8000/admin`.
5. **Client grants.** Authorize the client for both Sidecar APIs: **MPD OpenRouter Sidecar** and **MPD OpenRouter Sidecar (local)** (§2). The APIs keep `require_client_grant`. The **MPD - Profile claim** Action already sets the profile claim for any client.
6. **Nothing else changes.** Leave the shared **MPD MCP Clients** client, the Interview Coach's **MPD MCP Admin** client and the Actions as they are.

Then set `SIDECAR_ADMIN_CLIENT_ID` and `SIDECAR_ADMIN_CLIENT_SECRET` in Railway as in §9 step 3, and sign in as an `mpd-admin`.

**Running it locally.** Signing in to `/admin` locally needs the real tenant: set `AUTH0_ISSUER` to the real issuer (§1) and `MCP_RESOURCE_URI=http://localhost:8000/mcp`, set the two client variables to the MPD Sidecar Admin client's values, start the server on `http://localhost:8000` and open `http://localhost:8000/admin`. Sign-in returns only if the localhost callback and logout URLs from step 4 are registered. Cookies are not marked Secure on a localhost origin. `scripts/dev/run_sidecar.sh` and the stack in `docs/E2E_LOCAL.md` use a fake issuer and `127.0.0.1`, so the page loads there but nobody can sign in.

**Turning it off.** Set `SIDECAR_ADMIN_ENABLED=false` and deploy as in §4. The `/admin` routes then answer 404. Registered channels, the change log and the admin tables stay in the database. To stop one person, remove `sidecar:admin` from their role in `mpd-auth`; their session ends within 12 hours.

**Data.** The page creates three tables when needed: `admin_logins` (pending sign-ins, single use, 10 minutes), `admin_sessions` and `admin_audit` (the change log). They hold registry values only, never keys or tokens. Expired rows are removed when a sign-in starts or a session is created, so no sweeper is needed.
