# OpenRouter Sidecar

Image generation, image editing, and side-advisor chats for Northwestern MPD AI classes, through [OpenRouter](https://openrouter.ai). Claude stays the main assistant; the Sidecar adds image models and lets Claude consult other models (Gemini, ChatGPT and others) with a deliberately chosen brief. Each person uses their own OpenRouter account, inside their cohort's Northwestern workspace.

There are two ways to run it:

- **Hosted connector (recommended).** An authenticated remote MCP server on Railway that works in claude.ai, Claude Desktop and Claude Code. Nothing to install.
- **Local stdio server (legacy).** The original Python server that runs on your own computer with Claude Desktop. It still works and stays until the hosted server reaches parity (milestone M5), then it is retired.

**Status: instructor testing only.** Only the instructor account has access to the hosted server, for acceptance testing. Students are not onboarded yet, and student rollout is on hold until OpenRouter increases organization capacity. Do not point students at either path yet.

Repository: [jrenaldi79/openrouter-sidecar](https://github.com/jrenaldi79/openrouter-sidecar)
(private). Adapted from [skelly-77/openrouter-image-mcp](https://github.com/skelly-77/openrouter-image-mcp),
with the original MIT license and commit history retained.

## Hosted connector (recommended)

The server runs on Railway (US East, single replica, Postgres for chats and settings, a volume for images):

```text
https://sidecar-production-e110.up.railway.app/mcp
```

**Access.** Sign-in is through Auth0. An account needs the `sidecar:use` permission and a profile claim carrying its cohort's `workspace_id`. Without them the server answers 403. Auth0 and class membership are managed outside this repository (`../mpd-auth`).

**1. Add it as a connector.**
- **claude.ai and Claude Desktop:** add a custom connector in the Connectors settings, paste the URL above, and sign in when prompted.
- **Claude Code:** add it as a remote HTTP MCP server, then run `/mcp` inside Claude Code to sign in:

  ```bash
  claude mcp add --transport http openrouter-sidecar https://sidecar-production-e110.up.railway.app/mcp
  ```

**2. Link your OpenRouter account (once).** Ask Claude for `account_status`. If OpenRouter is not linked, call `link_openrouter`: you get a one-time link to open in your own browser within 10 minutes. It runs OpenRouter sign-in for your cohort's workspace, then asks you to confirm. Only the browser that opens the link can finish it. The resulting key is stored encrypted on the server and used only for your requests, so every paid call bills your own OpenRouter key. `unlink_openrouter` stops the server using it; delete the key in OpenRouter to revoke it fully. Tools that cost money fail with a link-required message until you link.

**3. Try it.**
- Ask a side advisor a question with `ask_advisor` (see [Headless advisor](#headless-advisor-subagent-mode)).
- Generate or edit an image (see [Hosted images](#hosted-images)).
- In claude.ai and Desktop, ask Claude to "open Sidecar settings" to pick default advisor models.

**What is stored on the server:** advisor transcripts and any files you chose to send, job metadata and costs, preferences, your encrypted OpenRouter key, and image uploads and results. Auth0 tokens and model reasoning traces are not stored. Chats idle for 90 days are purged, and images are kept for 30 days. See [the decision record](docs/superpowers/specs/2026-10-18-hosted-decision-record.md). The student-facing notice wording is not yet approved.

**Per-person limits** (server defaults): 2 concurrent advisor jobs, 20 requests per minute and $5.00 per day of reported advisor cost; image calls are limited to 2 at a time and 20 per minute. The advisor sandbox (a shell for the advisor) is disabled on the hosted server, so hosted advisor sessions are inline: files go into the prompt and the advisor has no tools.

The connector brings its own workflow guide (server instructions) and the class skills, adapted for hosted use, as prompts and `skill://` resources: `openrouter-image` and `architectural-render-polish`. Nothing needs installing in claude.ai or Claude Desktop, and Claude can pass these skills to an advisor.

Operators: see [docs/HOSTED_OPERATIONS.md](docs/HOSTED_OPERATIONS.md) for configuration, Auth0, Railway setup, deployment, key rotation and retention.

## Tools

### Hosted connector only

| Tool | What it does |
| --- | --- |
| `account_status` | Shows your sign-in, cohort and whether OpenRouter is linked. No cost. |
| `link_openrouter` | Returns a one-time browser link that links your own OpenRouter account. |
| `unlink_openrouter` | Stops the server using your linked key; chats are kept. |
| `create_image_upload` | Returns a one-time PUT URL for uploading an image (`curl -T file URL`); the response holds the asset id. |
| `generate_image` | Generates images; results are stored as assets with 24-hour download links and reported cost. |
| `edit_image` | Edits images given as asset ids (`images=[asset_id]`, optional `mask_id`); results are stored as assets. |
| `remask_image` | Re-blends a masked edit with a new mask by asset id. Free: no model call. |
| `get_image_download` | Returns fresh 24-hour download links for one of your assets. |
| `open_image_uploader` | MCP Apps clients only: opens a drop zone that uploads from your computer and adds the asset ids to the conversation. |
| `create_local_import` | Returns a one-time link and the two commands that move your local Sidecar chats and settings here (see below). |

### Hosted and local

| Tool | What it does |
| --- | --- |
| `list_image_models` | Discovers available image models and pricing. |
| `get_image_model` | Shows model parameters, providers and prices. |
| `list_chat_models` | Discovers text advisor models supporting tools, with pricing and context limits. |
| `start_advisor_chat` | Creates a chat using an exact ID, a saved brand default, or the general default; no inference. |
| `send_advisor_message` | Starts a background advisor job and opens its result viewer; returns status only. Accepts `files`. |
| `ask_advisor` | Headless: asks an advisor and returns its answer to the caller (wrapped as untrusted output). Creates a session or continues one by `chat_id`; accepts `files`. |
| `wait_advisor_job` | Headless: keeps waiting for a long `ask_advisor` job and returns the same result shape. |
| `list_advisor_chats` | Lists your chats. |
| `get_advisor_chat` | Shows metadata and a bounded message count, without returning the transcript to the parent model. |
| `reset_advisor_sandbox` | Assigns a fresh container while preserving chat history. Only relevant to sandbox chats. |
| `delete_advisor_chat` | Deletes the owned transcript and mapping. |
| `retry_advisor_cleanup` | Retries exact recorded skill-upload deletions without inference. |
| `get_advisor_job` | Checks an owned job's status, cost, and cleanup without reading the answer. |
| `open_advisor_result` | Reopens the viewer for an owned job without inserting its response into the parent chat. |
| `open_sidecar_settings` | Opens first-use onboarding or lets the user change defaults at any time. |
| `get_sidecar_preferences` | Reads defaults, settings ID, save revision, and walkthrough completion. |
| `set_sidecar_preferences` | Saves validated defaults atomically; preserves omitted choices and rejects stale saves. |
| `resolve_advisor_model` | Resolves Gemini, ChatGPT, Claude or a one-off exact model ID without inference. |
| `get_advisor_result` | App-only: retrieves owned partial or final answer text for the viewer. |
| `summarize_advisor_result` | App-only: starts a paid summary job, for editing and review before sending. |
| `get_sidecar_settings` | App-only: loads model choices, pricing, context limits and the cohort. |
| `run_sidecar_demo` | App-only: starts an explicitly selected tiny advisor, history, skill, or one-image demo. |
| `get_sidecar_demo` | App-only: reads an owned image demo status and preview without inference. |

The app-only operations are omitted for clients without MCP Apps capability.

### Local stdio server only

| Tool | What it does |
| --- | --- |
| `account_status` | Shows the configured cohort/workspace, sign-in state, key label, spending and available limits. |
| `auth_login` | Starts browser sign-in constrained to this workspace. |
| `auth_logout` | Removes this cohort's saved credential. |
| `generate_image` | Generates images, saving them to local files with metadata and reported costs. |
| `edit_image` | Edits local image files using references, masks and output sizing. |
| `remask_image` | Re-blends a saved masked edit locally, with no upload or model charge. |
| `export_advisor_result` | App-only: exports owned completed results to durable local Markdown. Not available on the hosted server. |

## Headless advisor (subagent mode)

`ask_advisor` behaves like a subagent: the parent model gets the answer back.
1. Call `ask_advisor(prompt, files=[{path, content}, ...])` without a `chat_id`. It
   creates a session, waits up to `wait_s` seconds (default 25, max 45, with progress
   notifications), and returns `{status: "completed", chat_id, job_id, answer, cost_usd}`.
2. If the advisor needs longer, it returns `{status: "running", job_id}`. Call
   `wait_advisor_job(job_id)` until it completes.
3. Reuse `chat_id` for follow-ups. The session keeps its transcript and files, so a
   later call from any client signed in as the same person continues where it left off.
   If a job is still running, the result is `{status: "busy", running_job_id}`.

The server can't read your machine or repository, so pass every relevant source
file explicitly:
- **Limits:** UTF-8 text only, 100k characters per file, 200k per session.
- **Updates:** send only changed files on later calls. `replace_files: true` drops
  files you leave out.
- **Inline sessions (the default, and the only kind on the hosted server):** files go into
  the prompt, and the advisor has no tools.
- **Sandbox sessions** give the advisor a shell. They're refused while the server has
  sandboxes disabled (always, on the hosted server).

The `answer` is wrapped in `<advisor_output>` with a note to treat it as data to
verify, not as instructions. `send_advisor_message` also accepts `files` and keeps
its viewer contract: the answer never reaches the parent model.

## Hosted images

On the hosted server, images are asset ids, never paths:
1. `create_image_upload(filename, mime, size_bytes)` returns a one-time URL. Upload
   with `curl -T room.png "<upload_url>"` within 10 minutes; the response holds the
   `asset_id`. Files can be PNG, JPEG or WebP, at most 20 MB and 40 megapixels.
2. `edit_image(images=[asset_id], mask_id=...)` and `generate_image(...)` store their
   results as assets. The result text lists each asset id with download links, which
   are valid for 24 hours.
3. `remask_image(image_id, mask_id)` redoes a masked blend for free.
   `get_image_download(asset_id)` returns fresh links.

Assets are private to the signed-in person and kept for 30 days, with a 2 GB storage cap per account.

In Claude Desktop and claude.ai (clients with MCP Apps), `open_image_uploader` shows a
drop zone instead: it uploads straight to the Sidecar and adds the new asset ids to the
conversation. The image gallery gets a **Download** button that fetches a fresh link
and lets the host save the file; the app never sees a sign-in token.

Hosts advertising MCP Apps HTML support receive a bundled gallery for `generate_image`,
`edit_image` and `remask_image`, with JPEG previews, filenames, reported usage/cost, notes
and failures. Clients without that capability keep receiving ordinary text and image
content. SVGs and files beyond the preview limit show their filenames without an inline preview.

## Moving from the local Sidecar

If you used the local (Claude Desktop) Sidecar, you can bring its advisor chats and
settings to the hosted connector:

1. In the hosted connector, ask Claude to import your local Sidecar chats. `create_local_import` returns a one-time link, valid for 10 minutes.
2. On your computer, run the two commands it gives you:

   ```bash
   openrouter-image-mcp export-local --cohort 2027 --out sidecar-export.json
   curl -T sidecar-export.json "<link>"
   ```

The export holds that cohort's chats (titles, models, transcripts, session files) and
your default models. It never holds your OpenRouter key; link OpenRouter on the
hosted connector separately. Imported chats get new chat ids and continue where they
left off. Exports from another class year are refused, and the same export imports
only once. Your hosted settings are only filled in if you haven't chosen any yet.
Delete the export file afterwards, since it contains your transcripts.

## Cost and privacy

Prompts and input images go through OpenRouter to third-party providers. Get explicit permission before uploading other people's images, research materials or confidential project information. Start with one low-cost draft and relay reported spending. Use the live catalog for model choice and prices. The reported cost is whatever OpenRouter returns, not an independent guarantee of total spending. Organization/workspace budgets are administered in OpenRouter; this server does not create or increase them. Nothing replays a potentially billed request automatically after a timeout or provider error.

## Advisor settings and viewer

For first use, ask **“Use openrouter-sidecar to open onboarding.”** The app confirms
the cohort and sign-in, then offers a general advisor default plus
Gemini/Google, ChatGPT/OpenAI, Grok/xAI and Open Weight defaults. Open Weight choices are
limited to GLM, Qwen, Kimi, MiniMax and DeepSeek. All choices, including the general
default and `list_chat_models`, use the same approved families and a rolling six
calendar months of OpenRouter catalog age. Models with missing, invalid or future
dates are omitted. OpenRouter's `created` field is its listing date, not necessarily
the original model release date. Claude is omitted from setup. Choices show exact
OpenRouter IDs, context limits and catalog pricing in USD per million tokens. Batch
model variants are omitted. Saving choices is free and automatic; a failed save offers
a deliberate retry. The general advisor is required to finish setup, and brand defaults
are optional. Later ask **“Open Sidecar settings”** or **“Change my default Gemini model.”**

`start_advisor_chat(model="Gemini")` resolves the saved Google default; omit model
for the general default; `model="Open Weight"` uses the saved Open Weight choice. An
exact ID overrides only that new chat. Existing chats retain their models. Missing,
retired or aged-out defaults require a replacement in settings. Starting or listing
chats does not run inference; sending a message incurs model and possible sandbox
compute charges. Defaults are scoped to your credential and workspace. Locally they
live in the local database and do not sync across devices; on the hosted server they
are stored per signed-in person.

The **How it works** visual guide explains separate advisor conversations, model
defaults, relevant skill handoffs, image generation, and controlled return of
results. The main assistant selects relevant available shareable skill instructions
and deliberately sends them with the brief. Sidecar does not automatically scan
installed plugins or synchronize local skill files. Follow-ups use the advisor's
own saved history; the main chat chooses any additional context to include. The guide
has no live demo controls: navigation, preferences, and **Finish** do not invoke inference.

`send_advisor_message` promptly returns a `job_id` and status; the full response
stays in the database. In MCP Apps hosts, the viewer polls UI-only result
metadata and displays streamed answer text, with the original user prompt collapsed.
System prompts, appended skill instructions, shell output, and reasoning traces are
not displayed. Opening, scrolling, polling, or exporting does not update the parent
model context. Preview a selection or the full response, edit it, and explicitly
click **Send to parent chat** to hand it back. **Summarize and send** uses the same
advisor model and incurs inference, then shows an editable preview before sending.
If host messaging is unsupported or rejected, use the explicit copy fallback. Use
`get_advisor_job` to check status, or `open_advisor_result` to reopen a viewer.

Jobs run inside the server process and continue while the viewer is closed. A restart
(Claude Desktop closing the local server, or a hosted deploy) interrupts unfinished
jobs: they are marked interrupted, any retained answer text is labelled partial, and
they are never replayed automatically. Advisor inference has a 30-minute total
deadline. The chat lock lasts 40 minutes to cover inference and cleanup.

### Skills sent with a brief

Claude selects relevant skills and supplies packets with `name`, `content`, and
optional `source` and `version`. Supply supporting Markdown/text as additional named
packets. A path or skill name alone does not share Claude's filesystem or connectors.
Only shareable, nonconfidential instructions belong in this cohort arrangement;
private skills or private task artifacts need a different provider boundary.
Up to 20 uniquely named packets, totaling 250 KB, are sent. Complex plugins requiring
unavailable tools, executable assets, or subagents still require main-chat assistance.

## Local stdio server (legacy)

Use this only for local testing until the hosted server retires it. It runs on your computer, signs in to OpenRouter directly, and stores its key, transcripts and images locally. Claude's web app cannot reach a local stdio server.

### Cohorts

| Cohort | Students | Required OpenRouter workspace |
| --- | --- | --- |
| 2027 | Capstone, full-time and second-year | Class of 2027 (`21082e84-ae02-4639-ad40-c7251b98ab10`) |
| 2028 | First-year | Class of 2028 (`8804f9c5-afd2-4de3-8d9f-f74b3d58c651`) |

Set `OPENROUTER_IMAGE_COHORT=2027` or `2028`. Missing, empty, unknown or conflicting selection fails before sign-in. `OPENROUTER_IMAGE_WORKSPACE_ID` can alternatively select either ID above. There is no personal-account or original-firm fallback. (The hosted server takes the workspace from the signed-in profile instead.)

### Claude Desktop setup

Use this checkout and Python 3.12 or newer. Prepare an environment with uv:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe --group dev -e .
.venv\Scripts\python.exe -m openrouter_image_mcp.cli print-config --cohort 2027
```

On macOS/Linux use `.venv/bin/python` instead. `print-config` produces one `openrouter-sidecar` entry using this interpreter and checkout. Use `--cohort 2028` for a first-year student. Use **Settings → This computer → Developer → Edit config** to locate the active file; the usual Windows location is `%APPDATA%\Claude\claude_desktop_config.json`.

**Fully exit Claude Desktop through File → Exit before editing.** Closing its window can leave it running. Back up the configuration and merge these `mcpServers` entries, preserving other settings and servers. Reopen Claude Desktop and confirm the single Northwestern entry shows **Running** in Developer settings. Manually configured local servers are managed there, separately from remote connector setup in Customize. Keep the checkout and environment in place. Restart Claude Desktop and start a new chat after updating the checkout to refresh tool discovery.

Maintain one server and one installation entry. The original upstream release does not contain these changes.

The MCP server is named `openrouter-sidecar`. The Claude installer removes
the former `northwestern-images` entry and its legacy cohort entries. For an
existing customized configuration, rename its entry key (Codex: both TOML table
headers) and preserve the entry's values. Back up the file and restart the client.
Credentials and saved advisor chats use their existing storage identifiers;
the Python package and command remain `openrouter-image-mcp` for compatibility.

### Instructor test (local)

1. Select the 2027 tools in Claude Desktop and ask for `account_status`. Confirm the configured workspace above.
2. Ask for `auth_login` on that server and finish browser consent with your Northwestern organization account. Organization/workspace membership must be granted separately; this server does not enroll users.
3. Ask for `account_status` again and inspect the created key in the corresponding OpenRouter workspace. The target is configuration; a key label alone does not independently prove billing ownership.
4. To test 2028 later, fully exit Claude, rerun the installer with --cohort 2028, and reopen it. This replaces the same entry. Saved credentials remain isolated by workspace.
5. List image models and inspect pricing. Before a paid test, agree on one image (`n=1`), the model and likely cost. Startup and tool discovery do not require paid generation.

Terminal testing selects a cohort first:

```powershell
$env:OPENROUTER_IMAGE_COHORT = "2027"
.venv\Scripts\python.exe -m openrouter_image_mcp.cli status
.venv\Scripts\python.exe -m openrouter_image_mcp.cli login
.venv\Scripts\python.exe -m openrouter_image_mcp.cli logout
```

Change the cohort to `2028` for that profile. If `OPENROUTER_IMAGE_WORKSPACE_ID` is also set, it must match. `login --switch` replaces only this cohort's credential. Keys use the OS credential store under `northwestern-openrouter-image-mcp`, indexed by workspace ID. Legacy credentials are neither copied nor reused. Logout removes the local key; revoke it separately in OpenRouter if needed.

### Local files and storage

New images default to `~/Pictures/Northwestern AI/Class of 2027` or `Class of 2028`. Edits save beside the primary input. Existing explicit output overrides remain available. Files are not overwritten; JSON sidecars record prompts, settings and reported costs. Keep those files in mind when sharing coursework.

The local SQLite transcript lives at `%LOCALAPPDATA%\Northwestern AI\advisor.sqlite3`
on Windows and `~/.local/share/Northwestern AI/advisor.sqlite3` elsewhere. It stores
deliberate briefs, supplied skill text, and final answers, not raw provider reasoning
or shell output. Signing out or switching credentials hides the previous credential's
chats; a new OAuth key has a new scope even for the same person. This is neither
encryption nor protection against another program running as the same OS user.
History is bounded rather than silently truncated; model context limits may be
smaller than the application's size limit. API keys remain in the OS credential
store and HTTP authorization headers, never in skill uploads or the model/container
configuration. **Save as file** in the viewer writes durable Markdown to an
owner/workspace export directory beside the database; exports are independent copies
that deleting a chat does not remove.

Set `OPENROUTER_ADVISOR_TIMEOUT_S` to a finite positive number up to 1800 to shorten
the 30-minute advisor deadline (image inference defaults to 10 minutes). Catalog, upload
and cleanup requests keep 30-second limits. For Codex, set `tool_timeout_sec = 2100`
under `[mcp_servers.openrouter-sidecar]` to allow 35 minutes for the complete tool call;
other hosts may impose different limits.

### Local sandbox sessions

Local advisor chats can run in OpenRouter's shell sandbox (the hosted server cannot). Each
chat gets a random container ID bound locally to the active credential and Northwestern
workspace. Tool arguments cannot select a container, workspace, file ID, HTTP URL, or
provider tool configuration, and ownership checks run before provider operations. There
is no workspace file browser, generic HTTP fetch, or container download/promotion tool.
The shell engine is fixed to OpenRouter with outbound networking disabled, and this
version does not enable web search/fetch.

Uploaded skill originals are explicitly deleted after each response, including
inference failures. A failed deletion leaves an owned receipt in SQLite, is reported,
and blocks another inference until cleanup succeeds; use `retry_advisor_cleanup` to
retry without running a model. A process crash or ambiguous upload failure can leave
an upload without a confirmed receipt; the MCP never searches the shared workspace
to guess which file to delete.

Container copies are independent from uploaded originals. OpenRouter saves files
under `/workspace/home` and retains them for 30 days after last use; workspace files
do not expire automatically. The advisor is instructed to use `/tmp`, return answers
inline, and remove scratch files, but this is **not enforced deletion of every model
write**. Reset rotates the working container ID; reset/delete do not claim erasure
of old provider containers. See [container persistence](https://openrouter.ai/docs/guides/features/containers).

Two live test accounts previously demonstrated that ordinary workspace membership
could access another account's container files when supplied its ID. The MCP checks
prevent this through these tools; they do not change OpenRouter permissions or stop
someone modifying a local installation or using their extracted API credential
directly. This is why the hosted server keeps sandboxes disabled.

### Checking the gallery without a model call

Prepare a synthetic fixture:

```powershell
.venv\Scripts\python.exe scripts\prepare_gallery_test.py
```

Paste the printed `remask_image` request into a new Claude Desktop chat. It creates
a local preview with a $0.00 cost and uploads nothing. An existing masked edit can
also be re-masked for free; a plain generated image lacks the required mask sidecar.

## Companion skills and local plugin

`skills/openrouter-image` carries the general workflow; `skills/architectural-render-polish` remains optional. Run `python scripts/sync_plugin_skills.py` after source skill changes. The bundled Claude Code plugin contains one instructor entry configured for 2027 and uses `uv run --no-sync` against the prepared checkout; it requires an installed environment and `uv` on the client's PATH. It is a local stdio setup, not a published Northwestern distribution.

## Development and contributing

Docs for each audience:

| Topic | File |
| --- | --- |
| Operating the hosted server (config, Auth0, Railway, deploys, key rotation, retention) | [docs/HOSTED_OPERATIONS.md](docs/HOSTED_OPERATIONS.md) |
| Synthetic local end-to-end stack with a fake identity provider and fake OpenRouter | [docs/E2E_LOCAL.md](docs/E2E_LOCAL.md) |
| Hosted design and decisions | [design](docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md), [decision record](docs/superpowers/specs/2026-10-18-hosted-decision-record.md) |
| Every document, classified | [docs/index.md](docs/index.md) |
| Contributor and agent guidance | [CLAUDE.md](CLAUDE.md), plus a `CLAUDE.md` note in each source folder and path-scoped rules in `.claude/rules/` |

The project is Python 3.12+ managed with `uv`. Run the tests and lint with:

```bash
uv sync
uv run pytest -q
uv run ruff check
```

Live tests are excluded by default because they use OpenRouter and may cost money. Postgres-only tests run with `TEST_DATABASE_URL=... uv run pytest -m pg`.

Enable the contributor harness once per clone: `git config core.hooksPath .githooks`. The pre-commit hook runs the secret scan, missing-test and file-size checks, the documentation check, the MCP App bundle check, ruff and the unit suite. Documentation is generated by `node scripts/self-documentation/generate.js` (add `--check` to validate); see [CLAUDE.md](CLAUDE.md) for the full list of gates and gotchas.

The MCP App HTML in `src/openrouter_image_mcp/ui/` is generated and committed. Node is only needed to change or rebuild it, not to run the server:

```bash
npm --prefix ui ci
npm --prefix ui run build
npm --prefix ui test
```

Browser tests use Playwright Chromium, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an existing Chrome executable. They exercise the bundled viewers with a synthetic image and a mock MCP Apps host, without OpenRouter calls.

Original project provenance remains in the Git remote and MIT license. Keep student roster data in the roster spreadsheet.
