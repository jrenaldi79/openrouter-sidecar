"""Hosted image tool results: asset ids and signed links in text, previews alongside.

Clients without MCP Apps (Claude Code, Claude Tag) only see the text, so it carries
everything needed to continue: asset ids for the next call and download links for
the user. Structured content mirrors it for the gallery app.
"""

from __future__ import annotations

import base64

from mcp.types import CallToolResult, ImageContent, TextContent

from ..logs import redact

LINK_NOTE = ("Download links work for 24 hours (call get_image_download for fresh ones); "
             "images are kept for 30 days.")


def asset_lines(link: dict) -> list[str]:
    return [f"Asset: {link['asset_id']}  ({link['filename']})",
            f"  Download: {link['image_url']}",
            f"  Metadata: {link['metadata_url']}"]


def _data_uri(jpeg: bytes | None) -> str | None:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii") if jpeg else None


def _structured_image(saved, link: dict) -> dict:
    return {"assetId": saved.asset_id, "filename": link["filename"], "downloadUrl": link["image_url"],
            "metadataUrl": link["metadata_url"], "dataUri": _data_uri(saved.preview_jpeg)}


def _content(lines: list[str], images) -> list[TextContent | ImageContent]:
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=redact("\n".join(lines)))]
    content += [ImageContent(type="image", data=base64.b64encode(img.preview_jpeg).decode("ascii"),
                             mime_type="image/jpeg")
                for img in images if img.preview_jpeg is not None]
    return content


def image_result(result, links: dict) -> CallToolResult:
    lines = []
    for img in result.images:
        lines += asset_lines(links[img.asset_id])
        if img.note:
            lines.append(f"  ({img.note})")
    if result.notes:
        lines += ["Notes:", *(f"- {n}" for n in result.notes)]
    if result.failures:
        lines += ["Failures:", *(f"- {f}" for f in result.failures)]
    lines.append(f"Model: {result.model} · Provider: {result.provider or 'unknown'} · "
                 f"{result.elapsed_s:.0f}s")
    lines += [result.usage_line, LINK_NOTE]
    return CallToolResult(content=_content(lines, result.images), structured_content={
        "images": [_structured_image(img, links[img.asset_id]) for img in result.images],
        "model": redact(result.model),
        "provider": redact(result.provider) if result.provider else None,
        "callCostUsd": result.call_cost_usd,
        "usageLine": redact(result.usage_line),
        "notes": [redact(note) for note in result.notes],
        "failures": [redact(failure) for failure in result.failures],
    })


def remask_result(saved, links: dict) -> CallToolResult:
    usage = "Cost: $0.00 (re-blend on the server, no model call)"
    lines = [*asset_lines(links[saved.asset_id]), usage, LINK_NOTE]
    return CallToolResult(content=_content(lines, [saved]), structured_content={
        "images": [_structured_image(saved, links[saved.asset_id])], "model": "Re-blend",
        "provider": None, "callCostUsd": 0, "usageLine": usage, "notes": [], "failures": []})


def download_result(link: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text="\n".join([*asset_lines(link), LINK_NOTE]))],
                          structured_content=link)


def upload_result(ticket: dict, filename: str) -> CallToolResult:
    instructions = (
        f"Upload the file with one HTTP PUT within {ticket['expires_in'] // 60} minutes, for example:\n"
        f"  curl -T {_shell_name(filename)} \"{ticket['upload_url']}\"\n"
        "The response is JSON with the new asset_id; pass it to edit_image as an input image "
        "or mask. The link works once, for a file of at most the declared size.")
    return CallToolResult(content=[TextContent(type="text", text=instructions)],
                          structured_content=ticket | {"instructions": instructions})


def _shell_name(filename: str) -> str:
    return "'" + filename.replace("'", "'\\''") + "'"
