# OpenRouter Sidecar

Image generation, image editing, and side-advisor chats for Northwestern MPD AI classes, through [OpenRouter](https://openrouter.ai). Claude stays the main assistant; the Sidecar adds image models and lets Claude consult other models (Gemini, ChatGPT and others) with a deliberately chosen brief. Each person uses their own OpenRouter account, inside their cohort's Northwestern workspace.

The Sidecar is a hosted connector: an authenticated remote MCP server on Railway that works in claude.ai, Claude Desktop and Claude Code. There is nothing to install. The earlier local version that ran on each student's computer is retired; see [Moving from the local Sidecar](#moving-from-the-local-sidecar) to bring its chats across.

**Status: instructor testing only.** Only the instructor account has access to the hosted server, for acceptance testing. Students are not onboarded yet, and student rollout is on hold until OpenRouter increases organization capacity. Do not point students at it yet.

Repository: [jrenaldi79/openrouter-sidecar](https://github.com/jrenaldi79/openrouter-sidecar)
(private). Adapted from [skelly-77/openrouter-image-mcp](https://github.com/skelly-77/openrouter-image-mcp),
with the original MIT license and commit history retained.

## Connect

The server runs on Railway (US East, single replica, Postgres for chats and settings, a volume for images):

```text
https://sidecar-production-e110.up.railway.app/mcp
```

**Access.** Sign-in is through Auth0. An account needs the `sidecar:use` permission and a profile claim carrying its cohort's `workspace_id`. Without them the server answers 403. Auth0 and class membership are managed outside this repository (`../mpd-auth`).

| Cohort | Students | OpenRouter workspace |
| --- | --- | --- |
| 2027 | Capstone, full-time and second-year | Class of 2027 (`21082e84-ae02-4639-ad40-c7251b98ab10`) |
| 2028 | First-year | Class of 2028 (`8804f9c5-afd2-4de3-8d9f-f74b3d58c651`) |

The server takes the workspace from the signed-in profile; there is no personal-account fallback.

**1. Add the connector.**
- **claude.ai and Claude Desktop:** add a custom connector in the Connectors settings, paste the URL above, and sign in when prompted.
- **Claude Code:** install the plugin (see [Claude Code plugin and skills](#claude-code-plugin-and-skills)), or add the server directly. Then run `/mcp` inside Claude Code to sign in:

  ```bash
  claude mcp add --transport http openrouter-sidecar https://sidecar-production-e110.up.railway.app/mcp
  ```

**2. Link your OpenRouter account (once).** Ask Claude for `account_status`. If OpenRouter is not linked, call `link_openrouter`: you get a one-time link to open in your own browser within 10 minutes. It runs OpenRouter sign-in for your cohort's workspace, then asks you to confirm. Only the browser that opens the link can finish it. The resulting key is stored encrypted on the server and used only for your requests, so every paid call bills your own OpenRouter key. `unlink_openrouter` stops the server using it; delete the key in OpenRouter to revoke it fully. Tools that cost money fail with a link-required message until you link.

**3. Try it.**
- Ask a side advisor a question with `ask_advisor` (see [Headless advisor](#headless-advisor-subagent-mode)).
- Generate or edit an image (see [Images](#images)).
- In claude.ai and Desktop, ask Claude to "open Sidecar settings" to pick default advisor models.

**What is stored on the server:** advisor transcripts and any files you chose to send, job metadata and costs, preferences, your encrypted OpenRouter key, and image uploads and results. Auth0 tokens and model reasoning traces are not stored. Chats idle for 90 days are purged, and images are kept for 30 days. See [the decision record](docs/superpowers/specs/2026-10-18-hosted-decision-record.md). The student-facing notice wording is not yet approved.

**Per-person limits** (server defaults): 2 concurrent advisor jobs, 20 requests per minute and $5.00 per day of reported advisor cost; image calls are limited to 2 at a time and 20 per minute. The advisor sandbox (a shell for the advisor) is disabled, so advisor sessions are inline: files go into the prompt and the advisor has no tools.

The connector brings its own workflow guide (server instructions) and the class skills, adapted for hosted use, as prompts and `skill://` resources: `openrouter-image` and `architectural-render-polish`. Nothing needs installing in claude.ai or Claude Desktop, and Claude can pass these skills to an advisor.

**Tool permissions.** claude.ai asks before each connector tool call; a server can't turn that off. It does group the Sidecar's tools under Connectors → OpenRouter Sidecar → Tool permissions, using the tools' MCP annotations (`src/openrouter_image_mcp/tool_annotations.py`):
- **Read-only tools** are free lookups; setting the whole group to *Always allow* is safe.
- **Write/delete tools** create or change something, or open an in-chat app. `ask_advisor`, `send_advisor_message`, `generate_image` and `edit_image` cost money; `delete_advisor_chat` and `unlink_openrouter` delete things. The `Open…` viewers, `remask_image`, `create_image_upload`, `start_advisor_chat`, `set_sidecar_preferences` and `link_openrouter` are free. The viewers sit here because claude.ai doesn't render the app of a tool labelled read-only.

Reconnect the connector after an update that changes tools: claude.ai keeps the tool list from when it was added. In Claude Code, allow the free tools with `permissions.allow` entries such as `mcp__openrouter-sidecar__account_status`.

Operators: see [docs/HOSTED_OPERATIONS.md](docs/HOSTED_OPERATIONS.md) for configuration, Auth0, Railway setup, deployment, key rotation and retention.

## Tools

### Account, images and migration

| Tool | What it does |
| --- | --- |
| `account_status` | Shows your sign-in, cohort and whether OpenRouter is linked. No cost. |
| `link_openrouter` | Returns a one-time browser link that links your own OpenRouter account. |
| `unlink_openrouter` | Stops the server using your linked key; chats are kept. |
| `list_image_models` | Discovers available image models and pricing. |
| `get_image_model` | Shows model parameters, providers and prices. |
| `create_image_upload` | Returns a one-time PUT URL for uploading an image (`curl -T file URL`); the response holds the asset id. |
| `generate_image` | Generates images; results are stored as assets with 24-hour download links and reported cost. |
| `edit_image` | Edits images given as asset ids (`images=[asset_id]`, optional `mask_id`); results are stored as assets. |
| `remask_image` | Re-blends a masked edit with a new mask by asset id. Free: no model call. |
| `get_image_download` | Returns fresh 24-hour download links for one of your assets. |
| `open_image_uploader` | MCP Apps clients only: opens a drop zone that uploads from your computer and adds the asset ids to the conversation. |
| `create_local_import` | Returns a one-time link and the two commands that move your local Sidecar chats and settings here (see below). |

### Advisors and settings

| Tool | What it does |
| --- | --- |
| `list_chat_models` | Discovers text advisor models supporting tools, with pricing and context limits. |
| `start_advisor_chat` | Creates a chat using an exact ID, a saved brand default, or the general default; no inference. |
| `send_advisor_message` | Starts a background advisor job and opens its result viewer; returns status only. Accepts `files`. |
| `ask_advisor` | Headless: asks an advisor and returns its answer to the caller (wrapped as untrusted output). Creates a session or continues one by `chat_id`; accepts `files`. |
| `wait_advisor_job` | Headless: keeps waiting for a long `ask_advisor` job and returns the same result shape. |
| `list_advisor_chats` | Lists your chats. |
| `get_advisor_chat` | Shows metadata and a bounded message count, without returning the transcript to the parent model. |
| `reset_advisor_sandbox` | Assigns a fresh container while preserving chat history. Only relevant to sandbox chats, which are disabled on the server. |
| `delete_advisor_chat` | Deletes the owned transcript and mapping. |
| `retry_advisor_cleanup` | Retries exact recorded skill-upload deletions without inference. |
| `get_advisor_job` | Checks an owned job's status, cost, and cleanup without reading the answer. |
| `open_advisor_result` | Reopens the viewer for an owned job without inserting its response into the parent chat. |
| `open_sidecar_settings` | Opens first-use onboarding or lets the user change defaults at any time. |
| `get_sidecar_preferences` | Reads defaults, settings ID, save revision, and walkthrough completion. |
| `set_sidecar_preferences` | Saves validated defaults atomically; preserves omitted choices and rejects stale saves. |
| `resolve_advisor_model` | Resolves Gemini, ChatGPT, Claude or a one-off exact model ID without inference. |
| `get_advisor_result` | App-only: retrieves owned partial or final answer text for the viewer. |
| `summarize_advisor_result` | App-only: starts a paid summary job, which the viewer shares in place of a long answer. |
| `get_sidecar_settings` | App-only: loads model choices, pricing, context limits and the cohort. |
| `run_sidecar_demo` | App-only: starts an explicitly selected tiny advisor, history or skill demo (the one-image demo is unavailable on the server). |
| `get_sidecar_demo` | App-only: reads an owned image demo status and preview without inference. |

The app-only operations are omitted for clients without MCP Apps capability.

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
- **Inline sessions (the only kind on the server):** files go into the prompt, and the
  advisor has no tools. Sandbox sessions, which give the advisor a shell, are refused.

The `answer` is wrapped in `<advisor_output>` with a note to treat it as data to
verify, not as instructions. `send_advisor_message` also accepts `files` and keeps
its viewer contract: the answer never reaches the parent model.

## Images

Images are asset ids, never paths:
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

If you used the earlier local Sidecar with Claude Desktop, you can bring its advisor
chats and settings to the hosted connector. Do this on the computer that ran it, before
deleting that checkout:

1. In the hosted connector, ask Claude to import your local Sidecar chats. `create_local_import` returns a one-time link, valid for 10 minutes.
2. In the old Sidecar checkout, run the two commands it gives you (use `uv run openrouter-image-mcp ...`, or `.venv/bin/python -m openrouter_image_mcp.cli ...`, if the command isn't on your PATH):

   ```bash
   openrouter-image-mcp export-local --cohort 2027 --out sidecar-export.json
   curl -T sidecar-export.json "<link>"
   ```

`export-local` reads the chats the local Sidecar saved for that cohort and the key it
signed in with (only to find those chats; the key is never exported). The export
holds the chats (titles, models, transcripts, session files) and your default models.
Link OpenRouter on the hosted connector separately. Imported chats get new chat ids and
continue where they left off. Exports from another class year are refused, and the same
export imports only once. Your hosted settings are only filled in if you haven't chosen
any yet. Delete the export file afterwards, since it contains your transcripts. Once the
import is done, remove the old Sidecar entry from Claude Desktop's configuration.

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
chats does not run inference; sending a message incurs model charges. Defaults are
stored per signed-in person and workspace.

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
not displayed. Opening, scrolling or polling does not update the parent model context.
While a reply streams, the viewer shows a live status with elapsed time.
**Share with Claude** is one click: it adds the answer to Claude's context without
posting a chat message. Answers over 3,000 characters are summarized first by the same
advisor model (paid; the status line shows progress), and shorter ones are shared whole
for free. If the summary fails, the viewer says so and offers the full answer instead.
**More → Share full answer** always shares the whole answer for free; **More** also
holds **Copy answer**. After sharing, ask Claude to check the advisor viewer for it.
**Send follow-up** messages the advisor in the same chat (paid). If the host can't
update context, sharing copies the text to the clipboard, or shows it read-only for
manual copy when the clipboard is blocked. Use `get_advisor_job` to check status, or
`open_advisor_result` to reopen a viewer.

Jobs run inside the server process and continue while the viewer is closed. A deploy
interrupts unfinished jobs: they are marked interrupted, any retained answer text is
labelled partial, and they are never replayed automatically. Advisor inference has a
30-minute total deadline. The chat lock lasts 40 minutes to cover inference and cleanup.

### Skills sent with a brief

Claude selects relevant skills and supplies packets with `name`, `content`, and
optional `source` and `version`. Supply supporting Markdown/text as additional named
packets. A path or skill name alone does not share Claude's filesystem or connectors.
Only shareable, nonconfidential instructions belong in this cohort arrangement;
private skills or private task artifacts need a different provider boundary.
Up to 20 uniquely named packets, totaling 250 KB, are sent. Complex plugins requiring
unavailable tools, executable assets, or subagents still require main-chat assistance.

## Claude Code plugin and skills

The repository is a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`).
Its `openrouter-image` plugin (`plugin/`) adds the hosted connector over HTTP
(`plugin/.mcp.json`) together with the class skills, so `/plugin install` sets up
both; run `/mcp` afterwards to sign in. `skills/openrouter-image` carries the general
workflow; `skills/architectural-render-polish` remains optional. Run
`uv run python scripts/sync_plugin_skills.py` after source skill changes to refresh the
plugin's copy.

## Development and contributing

Docs for each audience:

| Topic | File |
| --- | --- |
| Operating the hosted server (config, Auth0, Railway, deploys, key rotation, retention) | [docs/HOSTED_OPERATIONS.md](docs/HOSTED_OPERATIONS.md) |
| Synthetic local end-to-end stack with a fake identity provider and fake OpenRouter | [docs/E2E_LOCAL.md](docs/E2E_LOCAL.md) |
| Hosted design and decisions | [design](docs/superpowers/specs/2026-10-18-hosted-sidecar-design.md), [decision record](docs/superpowers/specs/2026-10-18-hosted-decision-record.md) |
| Every document, classified | [docs/index.md](docs/index.md) |
| Contributor and agent guidance | [CLAUDE.md](CLAUDE.md), plus a `CLAUDE.md` note in each source folder and path-scoped rules in `.claude/rules/` |

The project is Python 3.12+ managed with `uv`. The command line runs the hosted server
(`serve-http`), its maintenance commands (`purge`, `active-jobs`) and `export-local`.
Run the tests and lint with:

```bash
uv sync
uv run pytest -q
uv run ruff check
```

Live tests are excluded by default because they use OpenRouter and may cost money; run them with `OPENROUTER_LIVE_KEY=... uv run pytest -m live`. Postgres-only tests run with `TEST_DATABASE_URL=... uv run pytest -m pg`.

Enable the contributor harness once per clone: `git config core.hooksPath .githooks`. The pre-commit hook runs the secret scan, missing-test and file-size checks, the documentation check, the MCP App bundle check, ruff and the unit suite. Documentation is generated by `node scripts/self-documentation/generate.js` (add `--check` to validate); see [CLAUDE.md](CLAUDE.md) for the full list of gates and gotchas.

The MCP App HTML in `src/openrouter_image_mcp/ui/` is generated and committed. Node is only needed to change or rebuild it, not to run the server:

```bash
npm --prefix ui ci
npm --prefix ui run build
npm --prefix ui test
```

Browser tests use Playwright's pinned Chromium. They exercise the bundled viewers with a synthetic image and a mock MCP Apps host, without OpenRouter calls.

Original project provenance remains in the Git remote and MIT license. Keep student roster data in the roster spreadsheet.
