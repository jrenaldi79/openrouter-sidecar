"""Packaged MCP Apps gallery and per-client progressive enhancement."""

import base64
from importlib.resources import files
from pathlib import Path
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension
from mcp.server.mcpserver import MCPServer
from mcp.types import Icon

from .logs import redact

UI_URI = "ui://openrouter-sidecar/preview.html"
ADVISOR_UI_URI = "ui://openrouter-sidecar/advisor.html"
SETTINGS_UI_URI = "ui://openrouter-sidecar/settings.html"
UPLOADER_UI_URI = "ui://openrouter-sidecar/uploader.html"
UI_MIME = "text/html;profile=mcp-app"
UI_EXTENSION = "io.modelcontextprotocol/ui"
IMAGE_TOOLS = frozenset({"generate_image", "edit_image", "remask_image"})
ADVISOR_UI_TOOLS = frozenset({"send_advisor_message", "open_advisor_result"})
SETTINGS_UI_TOOLS = frozenset({"open_sidecar_settings"})
APP_ONLY_TOOLS = frozenset({"get_advisor_result", "summarize_advisor_result", "export_advisor_result",
                            "get_sidecar_settings", "run_sidecar_demo", "get_sidecar_demo",
                            "open_image_uploader"})


def app_icons() -> list[Icon]:
    """The supplied Northwestern mark, embedded for offline host display."""
    png = files("openrouter_image_mcp").joinpath("ui", "northwestern-logo.png").read_bytes()
    return [Icon(
        src="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        mime_type="image/png", sizes=["256x256"],
    )]


class ImageApps(Extension):
    """Advertise the standard Apps extension, without adding tools."""

    identifier = UI_EXTENSION

    def settings(self) -> dict[str, Any]:
        return {"mimeTypes": [UI_MIME]}


def register_gallery(server: MCPServer) -> None:
    @server.resource(
        UI_URI, name="OpenRouter Sidecar image preview", mime_type=UI_MIME, icons=app_icons(),
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}, "prefersBorder": True}},
    )
    def gallery() -> str:
        return files("openrouter_image_mcp").joinpath("ui", "preview.html").read_text(encoding="utf-8")


def register_advisor_viewer(server: MCPServer) -> None:
    @server.resource(
        ADVISOR_UI_URI, name="OpenRouter Sidecar advisor result", mime_type=UI_MIME, icons=app_icons(),
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}, "prefersBorder": True}},
    )
    def viewer() -> str:
        return files("openrouter_image_mcp").joinpath("ui", "advisor.html").read_text(encoding="utf-8")


def register_settings(server: MCPServer) -> None:
    @server.resource(
        SETTINGS_UI_URI, name="OpenRouter Sidecar setup and defaults", mime_type=UI_MIME, icons=app_icons(),
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}, "prefersBorder": True}},
    )
    def settings() -> str:
        return files("openrouter_image_mcp").joinpath("ui", "settings.html").read_text(encoding="utf-8")


def register_uploader(server: MCPServer, origin: str) -> None:
    """Hosted only: the drop-zone app may connect to our own origin and nowhere else."""
    @server.resource(
        UPLOADER_UI_URI, name="OpenRouter Sidecar image uploader", mime_type=UI_MIME, icons=app_icons(),
        meta={"ui": {"csp": {"connectDomains": [origin], "resourceDomains": []}, "prefersBorder": True}},
    )
    def uploader() -> str:
        html = files("openrouter_image_mcp").joinpath("ui", "uploader.html").read_text(encoding="utf-8")
        return html.replace("__SIDECAR_ORIGIN__", origin)


async def negotiate_apps(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    """Remove Apps-only metadata/data for clients without the HTML capability.

    Work on response copies, so another client using the same server can still
    receive the gallery. Ordinary text and JPEG ImageContent always remain.
    """
    capabilities = ctx.session.client_capabilities
    settings = (capabilities.extensions or {}).get(UI_EXTENSION, {}) if capabilities else {}
    supported = UI_MIME in settings.get("mimeTypes", [])
    if not supported and ctx.method == "tools/call" and (ctx.params or {}).get("name") in APP_ONLY_TOOLS:
        return {"isError": True, "content": [{"type": "text", "text": "This operation requires the Sidecar MCP App."}]}
    result = await call_next(ctx)
    if supported:
        return result
    # MCP 2.x middleware sees serialized wire dictionaries after call_next.
    if isinstance(result, dict) and ctx.method == "tools/list":
        tools = []
        for tool in result.get("tools", []):
            if tool.get("name") in APP_ONLY_TOOLS:
                continue
            copied = dict(tool)
            if tool.get("name") in IMAGE_TOOLS | ADVISOR_UI_TOOLS | SETTINGS_UI_TOOLS and "_meta" in tool:
                meta = {k: v for k, v in tool["_meta"].items() if k != "ui"}
                if meta:
                    copied["_meta"] = meta
                else:
                    copied.pop("_meta", None)
            tools.append(copied)
        return {**result, "tools": tools}
    if isinstance(result, dict) and ctx.method == "tools/call" and (ctx.params or {}).get("name") in IMAGE_TOOLS:
        return {k: v for k, v in result.items() if k != "structuredContent"}
    return result


def preview_image(path: Path, jpeg: bytes | None) -> dict[str, Any]:
    """Use an existing bounded JPEG preview; never embed SVG or full originals."""
    return {
        "filename": redact(path.name),
        "path": redact(str(path)),
        "dataUri": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii") if jpeg else None,
    }
