# OpenRouter Sidecar

Local image generation, editing, and side-advisor chats for Northwestern University AI classes. Each student signs in individually to the Northwestern OpenRouter organization. Claude remains the main assistant and deliberately supplies the brief and shareable skills for an advisor.

**Instructor testing only. Student deployment is on hold until OpenRouter increases organization capacity.**

Repository: [jrenaldi79/northwestern-image-mcp](https://github.com/jrenaldi79/northwestern-image-mcp)
(private). Adapted from [skelly-77/openrouter-image-mcp](https://github.com/skelly-77/openrouter-image-mcp),
with the original MIT license and commit history retained.

## Cohorts

| Cohort | Students | Required OpenRouter workspace |
| --- | --- | --- |
| 2027 | Capstone, full-time and second-year | Class of 2027 (`21082e84-ae02-4639-ad40-c7251b98ab10`) |
| 2028 | First-year | Class of 2028 (`8804f9c5-afd2-4de3-8d9f-f74b3d58c651`) |

Set `OPENROUTER_IMAGE_COHORT=2027` or `2028`. Missing, empty, unknown or conflicting selection fails before sign-in. `OPENROUTER_IMAGE_WORKSPACE_ID` can alternatively select either ID above. There is no personal-account or original-firm fallback.

## Local Claude Desktop setup

Use this checkout and Python 3.12 or newer. Prepare an environment with uv:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe --group dev -e .
.venv\Scripts\python.exe -m openrouter_image_mcp.cli print-config --cohort 2027
```

On macOS/Linux use `.venv/bin/python` instead. `print-config` produces one `openrouter-sidecar` entry using this interpreter and checkout. Use `--cohort 2028` for a first-year student. Use **Settings → This computer → Developer → Edit config** to locate the active file; the usual Windows location is `%APPDATA%\Claude\claude_desktop_config.json`.

**Fully exit Claude Desktop through File → Exit before editing.** Closing its window can leave it running. Back up the configuration and merge these `mcpServers` entries, preserving other settings and servers. Reopen Claude Desktop and confirm the single Northwestern entry shows **Running** in Developer settings. Manually configured local servers are managed there, separately from remote connector setup in Customize. Keep the checkout and environment in place.

Maintain one server and one installation entry. Each student receives the same server configured with their confirmed cohort; the two class workspaces remain in OpenRouter. Student release instructions will follow instructor testing and the capacity increase. The original upstream release does not contain these changes.

The MCP server is now named `openrouter-sidecar`. The Claude installer removes
the former `northwestern-images` entry and its legacy cohort entries. For an
existing customized configuration, rename its entry key (Codex: both TOML table
headers) and preserve the entry's values. Back up the file and restart the client.
Credentials and saved advisor chats use their existing storage identifiers;
the Python package and command remain `openrouter-image-mcp` for compatibility.

## Instructor test

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

## Tools

| Tool | What it does |
| --- | --- |
| `account_status` | Shows configured Northwestern cohort/workspace, sign-in state, key label, spending and available limits. |
| `auth_login` | Starts browser sign-in constrained to this workspace. |
| `auth_logout` | Removes this cohort's saved credential. |
| `list_image_models` | Discovers available image models and pricing. |
| `get_image_model` | Shows model parameters, providers and prices. |
| `generate_image` | Generates images with metadata and reported costs. |
| `edit_image` | Edits local images using references, masks and output sizing. |
| `remask_image` | Re-blends a saved masked edit locally, with no upload or model charge. |
| `list_chat_models` | Discovers text advisor models supporting tools, with pricing and context limits. |
| `start_advisor_chat` | Creates a local chat using an exact ID, a saved brand default, or the general default; no inference. |
| `send_advisor_message` | Starts an owned background advisor job and opens its result viewer; returns status only. |
| `ask_advisor` | Headless: asks an advisor and returns its answer to the caller (wrapped as untrusted output). Creates a session or continues one by `chat_id`; accepts `files`. |
| `wait_advisor_job` | Headless: keeps waiting for a long `ask_advisor` job and returns the same result shape. |
| `list_advisor_chats` | Lists local chats belonging to the active credential and workspace. |
| `get_advisor_chat` | Shows metadata and a bounded message count, without returning the transcript to the parent model. |
| `reset_advisor_sandbox` | Assigns a fresh container while preserving the local chat history. |
| `delete_advisor_chat` | Deletes the owned local transcript and mapping. |
| `retry_advisor_cleanup` | Retries exact recorded skill-upload deletions without inference. |
| `get_advisor_job` | Checks an owned job's status, cost, and cleanup without reading the answer. |
| `open_advisor_result` | Reopens the viewer for an owned job without inserting its response into the parent chat. |
| `get_advisor_result` | App-only: retrieves owned partial/final answer text as UI metadata. |
| `summarize_advisor_result` | App-only: starts a paid summary job, for editing and review before sending. |
| `export_advisor_result` | App-only: exports owned completed results to durable local Markdown. |
| `open_sidecar_settings` | Opens first-use onboarding or lets the user change defaults at any time. |
| `get_sidecar_preferences` | Reads local defaults, settings ID, save revision, and walkthrough completion. |
| `set_sidecar_preferences` | Saves validated defaults atomically; preserves omitted choices and rejects stale saves. |
| `resolve_advisor_model` | Resolves Gemini, ChatGPT, Claude or a one-off exact model ID without inference. |
| `get_sidecar_settings` | App-only: loads model choices, pricing, context limits and configured cohort. |
| `run_sidecar_demo` | App-only: starts an explicitly selected tiny advisor, history, skill, or one-image demo. |
| `get_sidecar_demo` | App-only: reads an owned image demo status and UI preview without inference. |

New images default to `~/Pictures/Northwestern AI/Class of 2027` or `Class of 2028`. Edits save beside the primary input. Existing explicit output overrides remain available. Files are not overwritten; JSON sidecars record prompts, settings and reported costs. Keep those files in mind when sharing coursework.

Prompts and input images go through OpenRouter to third-party providers. Get explicit permission before uploading other people's images, research materials or confidential project information. Start with one low-cost draft and relay reported spending. Use the live catalog for model choice and prices. Organization/workspace budgets are administered in OpenRouter; this server does not create or increase them.

## Local side-advisor testing

For first use, ask **“Use openrouter-sidecar to open onboarding.”** The app confirms
the configured cohort and sign-in, then offers a general advisor default plus
Gemini/Google, ChatGPT/OpenAI, Grok/xAI and Open Weight defaults. Open Weight choices are
limited to GLM, Qwen, Kimi, MiniMax and DeepSeek. All choices, including the general
default and `list_chat_models`, use the same approved families and a rolling six
calendar months of OpenRouter catalog age. Models with missing, invalid or future
dates are omitted. OpenRouter's `created` field is its listing date, not necessarily
the original model release date. Claude is omitted from setup. Choices show exact
OpenRouter IDs, context limits and catalog pricing. Saving choices is free.
Later ask **“Open Sidecar settings”** or **“Change my default Gemini model.”**
The advisor viewer also offers a shortcut to request settings in the parent chat.

Defaults persist locally for the active credential and workspace. They do not
sync across devices; signing in with a new API key creates a separate scope.
`start_advisor_chat(model="Gemini")` resolves the saved Google default; omit model
for the general default. An exact ID overrides only that new chat. Existing chats
retain their models. Missing, retired or aged-out defaults require a replacement
in settings. `start_advisor_chat(model="Open Weight")` uses the saved Open Weight
choice. Legacy family preferences migrate without changing existing chats.

The inline guide caps its requested height at 620px and scrolls when needed;
compact fields show input/output prices beneath each selected model in USD per
million tokens. Each selection saves automatically; a failed save offers a deliberate
retry. The general advisor is required to finish setup, and brand defaults are optional.
Batch model variants are omitted from setup and advisor model discovery.

The **How it works** visual guide explains separate advisor conversations, model
defaults, relevant skill handoffs, image generation, and controlled return of
results. The main assistant selects relevant available shareable skill instructions
and deliberately sends them with the brief. Sidecar does not automatically scan
installed plugins or synchronize local skill files. Follow-ups use the advisor's
own saved history; the main chat chooses any additional context to include.

Results stay in the viewer until the user explicitly shares a selection or full
answer. A summary is another paid advisor call and can be reviewed before sending.
Saving local Markdown does not send it to the parent. The guide has no live demo
controls: navigation, preferences, and **Finish** do not invoke inference.
Completion is saved explicitly and does not block ordinary use. The existing
app-only demo endpoints remain available for separate controlled test harnesses.

Restart Claude Desktop and start a new chat to refresh tool discovery. Ask Claude to
list chat models, choose an exact model ID, and start an advisor chat. The returned
`chat_id` identifies that advisor for subsequent messages and follow-ups. Starting
or listing chats does not run inference; sending a message incurs model and possible
sandbox compute charges. The MCP does not automatically replay a potentially billed
request after a timeout or provider error. The reported cost is whatever OpenRouter
returns, not an independent guarantee of total spending.

`send_advisor_message` promptly returns a `job_id` and status; the full response
stays in the local database. In MCP Apps hosts, the viewer polls UI-only result
metadata and displays streamed answer text, with the original user prompt collapsed.
System prompts, appended skill instructions, shell output, and reasoning traces are
not displayed. Opening, scrolling, polling, or exporting does not update the parent
model context. Preview a selection or the full response, edit it, and explicitly
click **Send to parent chat** to hand it back. **Summarize and send** uses the same
advisor model and incurs inference, then shows an editable preview before sending.
If host messaging is unsupported or rejected, use the explicit copy fallback.

### Headless advisor (subagent mode)

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
- **Inline sessions (the default):** files go into the prompt, and the advisor has no
  tools.
- **Sandbox sessions** give the advisor a shell. They're refused while the server has
  sandboxes disabled.

The `answer` is wrapped in `<advisor_output>` with a note to treat it as data to
verify, not as instructions. `send_advisor_message` also accepts `files` and keeps
its viewer contract: the answer never reaches the parent model.

### Hosted images

On the hosted server, images are asset ids, never paths:
1. `create_image_upload(filename, mime, size_bytes)` returns a one-time URL. Upload
   with `curl -T room.png "<upload_url>"` within 10 minutes; the response holds the
   `asset_id`. Files can be PNG, JPEG or WebP, at most 20 MB and 40 megapixels.
2. `edit_image(images=[asset_id], mask_id=...)` and `generate_image(...)` store their
   results as assets. The result text lists each asset id with download links, which
   are valid for 24 hours.
3. `remask_image(image_id, mask_id)` redoes a masked blend for free.
   `get_image_download(asset_id)` returns fresh links.

Assets are private to the signed-in person and kept for 30 days, up to 2 GB each.

Use `get_advisor_job` to check status, or `open_advisor_result` to reopen a viewer.
The three App-only operations are omitted for clients without MCP Apps capability.
The UI never receives API credentials or directly accesses provider files. Every
result operation checks the credential fingerprint and configured workspace.
**Save as file** writes durable Markdown to an owner/workspace export directory
beside the local SQLite database, outside OpenRouter containers. Exported files are
independent copies; deleting a chat does not delete those explicit exports.

Jobs run in the local MCP process and can continue while the viewer is closed.
Closing/restarting the host can terminate that process: unfinished jobs become
interrupted, with any retained answer text labelled partial, and are never replayed
automatically. A stale heartbeat identifies unexpectedly abandoned jobs. Background
operation is not a promise that inference survives application or computer shutdown.

Advisor inference has a 30-minute total deadline, separate from image inference's
10-minute default. Set `OPENROUTER_ADVISOR_TIMEOUT_S` to a finite positive number
up to 1800 to shorten the advisor deadline. Catalog, upload, and cleanup requests
retain 30-second limits. The exclusive chat lock lasts 40 minutes to cover inference
and cleanup. For Codex, set `tool_timeout_sec = 2100` under
`[mcp_servers.openrouter-sidecar]` to allow 35 minutes for the complete tool call;
restart the app after changing settings or server code. Other hosts may impose
different limits. This extends local waits, not provider deadlines, and does not
recover an in-flight answer after app shutdown or disconnection.

Claude selects relevant skills and supplies packets with `name`, `content`, and
optional `source` and `version`. Supply supporting Markdown/text as additional named
packets. A path or skill name alone does not share Claude's filesystem or connectors.
Only shareable, nonconfidential instructions belong in this cohort arrangement;
private skills or private task artifacts need a different provider boundary.
No local plugin directory is automatically scanned or uploaded. Up to 20 uniquely
named packets, totaling 250 KB, are serialized into a server-named JSON attachment.
The advisor receives the same instructions as text in its history, so it can follow
up after uploaded originals are deleted. Complex plugins requiring unavailable
tools, executable assets, or subagents still require main-chat assistance.

Each chat gets a random container ID bound locally to the active credential and
Northwestern workspace. Tool arguments cannot select a container, workspace, file
ID, HTTP URL, or provider tool configuration. Ownership checks run before provider
operations. There is no exposed workspace file browser, generic HTTP fetch, or
container download/promotion tool. The hosted shell engine is fixed to OpenRouter
with outbound networking explicitly disabled. This version does not enable web
search/fetch. API keys remain in the OS credential store and HTTP authorization
headers, never in skill uploads or the model/container configuration.

The local SQLite transcript lives at `%LOCALAPPDATA%\Northwestern AI\advisor.sqlite3`
on Windows and `~/.local/share/Northwestern AI/advisor.sqlite3` elsewhere. It stores
deliberate briefs, supplied skill text, and final answers, not raw provider reasoning
or shell output. Signing out or switching credentials hides the previous credential's
chats; a new OAuth key has a new scope even for the same person. This is neither
encryption nor protection against another program running as the same OS user.
History is bounded rather than silently truncated; model context limits may be
smaller than the application's size limit. Preferences and onboarding use the same
local credential/workspace scope; remote MCP hosting remains separate work.

Uploaded skill originals are explicitly deleted after each response, including
inference failures. A failed deletion leaves an owned receipt in SQLite, is reported,
and blocks another inference until cleanup succeeds. Use `retry_advisor_cleanup` to
retry without running a model. A process crash or ambiguous upload failure can leave
an upload without a confirmed receipt; the MCP never searches the shared workspace
to guess which file to delete.

Container copies are independent from uploaded originals. OpenRouter saves files
under `/workspace/home` and retains them for 30 days after last use; workspace files
do not expire automatically. The advisor is instructed to use `/tmp`, return answers
inline, and remove scratch files, but this is **not enforced deletion of every model
write**. Reset rotates the working container ID; reset/delete do not claim erasure
of old provider containers. No outputs are promoted into permanent workspace files.
See [container persistence](https://openrouter.ai/docs/guides/features/containers).

Two live test accounts previously demonstrated that ordinary workspace membership
could access another account's container files when supplied its ID. The MCP checks
prevent this through these tools; they do not change OpenRouter permissions or stop
someone modifying a local installation or using their extracted API credential
directly. Automated synthetic two-credential tests verify the MCP boundary separately
from that provider limitation. This new advisor path still needs an instructor test
through Claude Desktop before student rollout.

## Inline image previews

The existing server includes an MCP Apps gallery for `generate_image`, `edit_image`,
and `remask_image`. Hosts advertising MCP Apps HTML support receive a bundled viewer
with JPEG previews, filenames, saved paths, reported usage/cost, notes and failures.
Original files still save locally. Clients without that capability keep receiving
ordinary text and image content. SVGs and files beyond the preview limit show their
saved filenames without an inline preview. The gallery needs no network connection.

After updating the checkout, fully exit and reopen Claude Desktop, then start a new
chat to refresh tool metadata. Actual inline rendering depends on the installed
host's MCP Apps support for local servers. Claude's web app cannot reach this local
stdio server directly.

To check the gallery without paying for a model call, prepare a synthetic fixture:

```powershell
.venv\Scripts\python.exe scripts\prepare_gallery_test.py
```

Paste the printed `remask_image` request into the new Claude Desktop chat. It creates
a local preview with a $0.00 cost and uploads nothing. An existing masked edit can
also be re-masked for free; a plain generated image lacks the required mask sidecar.

## Companion skills and local plugin

`skills/openrouter-image` carries the general workflow; `skills/architectural-render-polish` remains optional. Run `python scripts/sync_plugin_skills.py` after source skill changes. The bundled Claude Code plugin contains one instructor entry configured for 2027 and uses `uv run --no-sync` against the prepared checkout; it requires an installed environment and `uv` on the client's PATH. It is not a published Northwestern distribution.

## Development

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check .
```

Live tests are excluded by default because they use OpenRouter and may cost money. Original project provenance remains in the Git remote and MIT license. Keep student roster data in the roster spreadsheet.

The gallery HTML is committed in the Python package. Node is only needed to change
or rebuild it, not to run the server:

```powershell
cd ui
npm ci
npm run build
npm test
```

Browser tests use Playwright Chromium, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an
existing Chrome executable. They exercise the bundled viewer with a synthetic
image and a mock MCP Apps host, without OpenRouter calls.
