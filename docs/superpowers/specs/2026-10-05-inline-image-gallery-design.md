# Inline image gallery (approved October 5, 2026)

Add an MCP Apps viewer to the existing Northwestern server. There remains one
`northwestern-images` Claude Desktop entry, with eight tools and cohort-scoped
OpenRouter sign-in. No remote deployment or paid generation during development.

`generate_image`, `edit_image`, and `remask_image` share
`ui://northwestern-images/preview.html`. Serve bundled, self-contained HTML as
`text/html;profile=mcp-app`; use the official MCP Apps SDK, with no CDN or network
access. Keep ordinary text and ImageContent results for hosts without Apps support.

Image tools return CallToolResult with structuredContent:

```json
{
  "images": [{"dataUri": "data:image/jpeg;base64,...", "path": "absolute path", "filename": "image.png"}],
  "model": "model id or Local re-blend",
  "provider": null,
  "callCostUsd": null,
  "usageLine": "existing usage/cost text",
  "notes": [],
  "failures": []
}
```

`dataUri` is null when no JPEG preview is available (SVG or preview limit).

> Changed October 19, 2026: some hosts (Claude Code) show `structuredContent` to the
> model, so the previews moved out of it. `images[]` no longer has `dataUri`; the
> result's `_meta["sidecarPreviews"]` holds the data URIs (or null) in the same order.
Display saved filename and path even without a preview. Display the call total
once, with null meaning unavailable and zero meaning free; do not invent per-image
prices. Remask has callCostUsd=0 and model=Local re-blend. No prompt or credentials
are sent to the UI. Redact all strings consistently with existing text output.

The viewer handles waiting, successful and partial results, and errors. Use safe
DOM construction and accept only JPEG base64 data URIs. Render notes and failures
as text. Follow host light/dark theme and use SDK automatic size reporting.
Do not emit the ui:// URI in result text.

Verify protocol discovery/resource delivery, all three tools, compatibility with
ordinary MCP clients, and the iframe handshake/rendering with a mocked host and
existing synthetic JPEG. Claude Desktop acceptance remains a separate host check.

## Approved logo

Use the supplied `northwestern-edu-logo.png` as the server, image-tool and resource
icon. Keep the original PNG in packaged assets, embed it as a data URI in metadata
and the gallery's 24-pixel heading mark, and keep the gallery fully offline.
