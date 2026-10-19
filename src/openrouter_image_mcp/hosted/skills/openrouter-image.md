---
name: openrouter-image
description: Use when the user wants to make, generate, edit, restyle, inpaint, extend (outpaint) or vary an image with the hosted Sidecar connector's `generate_image` or `edit_image` tools, when choosing an OpenRouter image model, or when an image call fails with a link, credits, size or moderation error.
---

# Northwestern AI image generation and editing (hosted Sidecar)

## Overview

The Sidecar connector sends prompts and images to any OpenRouter image model and stores the results as private image assets on the server. It has no presets: picking the model, wording the prompt and keeping spend sensible is your job. Every call costs real money, billed to the OpenRouter account the student linked.

The server can't see files on the user's computer or images pasted into the chat. Images reach it only as asset ids: from an upload, or from an earlier result.

## Tools

| Tool | Use it for |
|---|---|
| `account_status()` | Signed-in Northwestern account, class cohort, and whether OpenRouter is linked. Free. |
| `link_openrouter()` | Returns a one-time browser link that connects the student's own OpenRouter account. Needed once before any paid call. |
| `list_image_models(query?, accepts_images?, author?, sort="newest"\|"price")` | Find candidate models. `accepts_images=true` for edits. |
| `get_image_model(model_id)` | Exact aspect ratios, resolutions, quality values, max `n`, max input images, passthrough options, pricing. |
| `create_image_upload(filename, mime, size_bytes)` | A one-time PUT link for one image file (`curl -T file "<url>"`); the response holds its asset id. |
| `open_image_uploader()` | In clients with MCP Apps (claude.ai, Claude Desktop): a drop zone that uploads images and adds their asset ids to the conversation. |
| `generate_image(prompt, model, ...)` | Text to image. Results are image assets with download links. |
| `edit_image(prompt, model, images, ...)` | Image asset(s) plus prompt to image. The first id is the image being edited; the rest are references. |
| `remask_image(image_id, mask_id, ...)` | Redo a masked edit's blend with a new mask, on the server and free. |
| `get_image_download(asset_id)` | Fresh 24-hour download links for an image and its metadata. |

## Getting images in

- In claude.ai or Claude Desktop, offer `open_image_uploader()`: the user drops the files and the asset ids come back into the chat.
- Elsewhere (Claude Code, or a client without MCP Apps), call `create_image_upload` with the file's name, type and exact size, then have the user (or your shell) run the `curl -T` command it returns within 10 minutes.
- An image the user pasted into the chat is visible to you, not to the server. Describe and critique it with your own vision; upload it only when it must be edited.

## Before uploading client or project imagery

Ask the user for an explicit OK before the first `edit_image` call that sends a client or project image (render, photo, drawing, site image) and before any reference image from a new project, or images belonging to classmates, research participants or other people. Say plainly: the image goes through OpenRouter to the model's provider (for example OpenAI or Google), and it is subject to the organization's account policies. An OK for one project doesn't cover another. If the user says no, stop; don't work around it with a description of the image.

## Workflow

1. **Check the account.** `account_status()`; if OpenRouter isn't linked, give the user the `link_openrouter()` link and wait for them to finish.
2. **Discover first.** Don't pick a model from memory. Call `list_image_models` (with `accepts_images=true` for edits), then `get_image_model` on the one or two candidates to read their real parameters.
3. **Iterate cheaply.** Use a speed-tier model, or `quality="low"` where the model has `quality`, and start with `n=1` while the prompt is still changing. Increase the batch only when the student requests it and understands the cost.
4. **State the cost before spending.** Before a large `n` or a high/max-quality final, tell the user the likely cost: the cost of a comparable earlier result times `n`, or, if the cost is unknown, say so and run `n=1` first. `n` can be 1 to 10; above the model's maximum the server splits it into several calls, each billed.
5. **Final at full quality**, `n=1` unless the user wants options.

## Edits: size and masks

- **`fit="preserve"` (default) returns exactly the first input's pixel size.** The server picks the model's nearest aspect ratio and crops back, or pads with blurred mirrored edges and crops. Don't pass `aspect_ratio` or `size` with `fit="preserve"`; they are rejected. A model that offers a ratio close to the input's (21:9 for a 2.32:1 render) avoids padding, which can invent content at the edges.
- **`fit="model"`** returns whatever the model made, untouched. Use it only when the user doesn't need the original size.
- **Extending or reframing** (outpainting, a new aspect ratio) needs `fit="model"` with an `aspect_ratio`: the output is no longer the input's size, and masks can't be used.
- **Masks** (`mask_id`, an uploaded greyscale image the same size as the first input): white = may change, black = keep pixel-identical. The edge is feathered (`mask_feather_px`, chosen from the image size by default). A lighting seam can show at the mask edge; widen the feather or the white area if it does. Masks can't be combined with `fit="model"`. Black areas stay pixel-identical only with PNG output (the default); a JPEG or WebP `output_format` re-compresses them.
- **Making a mask:** ask the user to paint one at the image's exact size (white where change is allowed) and upload it. In Claude Code you can draw it with Pillow from rectangles around the protected areas and upload it with `create_image_upload`.

**Fix a seam with `remask_image`, not a new edit.** A masked edit keeps the model's unblended image on the server. If the new content is good but blends badly (a ghosted edge where the model drew past the mask, or the mask was too small or too large), upload a corrected mask and call `remask_image(image_id=<result asset id>, mask_id=<new mask id>)`. It re-blends on the server: free, nothing sent to a provider, no new consent needed.

## Results

Each result lists its asset id, a 24-hour download link for the image and one for its metadata (prompt, model, parameters, cost and fit details), and inline previews where the client shows them. Pass result ids straight back into `edit_image` or `remask_image`; call `get_image_download` for fresh links later. Assets are private to the signed-in student and kept for 30 days (2 GB per student). The result ends with the call's cost; relay it to the user.

## Errors

| Message | Do this |
|---|---|
| `openrouter_link_required` | Call `link_openrouter()`, give the user the link, and retry after they confirm. Never retry in a loop. |
| Out of credits (402) | Tell the user to add credits or raise the key's limit on OpenRouter. |
| "Blocked by … moderation … Not charged" | Nothing was billed. Reword neutrally (drop violent, body or brand wording), or check `get_image_model` for a `moderation` passthrough and offer it as a flat `provider_options={"moderation": "low"}` (the server routes it to each provider that allows it; other keys are rejected before spend), or switch to a model that isn't moderated. Tell the user which you chose. |
| Invalid parameter (lists allowed values) | Pick from the list; nothing was billed. |
| "unavailable" for an asset id | The id belongs to another sign-in or has expired; upload the image again. |
| Model replied with text, no image | Treat as a refusal; rephrase or change model. |

## Model rules of thumb (As of 2026-10)

These go stale fast. `list_image_models` and `get_image_model` are the source of truth; check them before relying on anything below.

- **OpenAI GPT Image 2.5 Sunburst** (`openai/gpt-image-2.5-sunburst`, precision tier) and **Flare** (`openai/gpt-image-2.5-flare`, speed tier): Sunburst for detailed work, Flare for high-volume everyday work. Up to 16 input images, ratios up to 21:9, `quality` from low to max, `n` up to 10, transparent `background`. Both are moderated. No `seed`. The catalog listed the same token prices for both, so Flare's advantage is speed; use `quality="low"` for cheap drafts.
- **Google Nano Banana 2** (`google/gemini-3.1-flash-image`) and **Nano Banana Pro** (`google/gemini-3-pro-image`): up to 14 input images, ratios including 21:9, resolution tiers up to 4K, `n=1`. Pro is Google's most advanced for reasoning and real-world grounding; Nano Banana 2 is the faster one, and also offers extreme ratios (1:8 to 8:1). Nano Banana 2 Lite (`google/gemini-3.1-flash-lite-image`) is the fastest, cheapest Gemini option at 1K.
- **ByteDance Seedream 5.0 Pro** (`bytedance-seed/seedream-5-0-pro`, precise editing control, lifelike scenes) and **Flash** (`bytedance-seed/seedream-5-0-flash`, fast and cost-efficient): up to 14 input images, the widest ratio list (including 20:9 and 21:9), 1K/2K, `seed` support, `n=1`.
- **Black Forest Labs FLUX.3** (`black-forest-labs/flux-3-image`): flagship generation and multi-reference editing with up to 10 input images, resolution tiers 768 to 4K, ratios including 21:9, `n=1`.
- **Microsoft MAI-Image-2.6** (`microsoft/mai-image-2.6`, precision) and **MAI-Image-2.6 Flash** (lower latency and cost): design-ready visuals, up to 5 input images, ratios only up to 16:9, so wide renders get padded under `fit="preserve"`.
- **Recraft vector models** (names ending `-vector`, such as `recraft/recraft-v4.1-vector`): output SVG, with no fitting and no preview. Recraft "styles" models require at least one style reference image.
- **Legible text in the image:** Qwen Image 3 / 3 Pro (`qwen/qwen-image-3`, `qwen/qwen-image-3-pro`) advertise text and detail down to 10px.
