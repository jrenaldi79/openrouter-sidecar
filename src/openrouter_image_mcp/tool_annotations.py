"""MCP tool annotations for every hosted Sidecar tool.

Hosts (claude.ai, Claude Desktop) group a connector's tools by these hints, so a
user can allow the free read-only tools at once and keep approval on calls that
spend money or delete something. Tools that open an MCP App for the user are not
labelled read-only: claude.ai doesn't render a read-only tool's app. They are hints
only: the server can't skip a host's approval prompt. All four hints are set
explicitly, because the MCP defaults (destructive, open world) would mislabel most tools.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations


def _hints(*, read_only=False, destructive=False, idempotent=False, open_world=False) -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=idempotent or read_only, openWorldHint=open_world)


# Free and read-only; open_world where the live OpenRouter catalog or key is read.
# account_status may issue the caller's class key on first use: free and idempotent.
_READ = _hints(read_only=True)
_READ_OPENROUTER = _hints(read_only=True, open_world=True)
# Paid inference through the user's OpenRouter key.
_PAID = _hints(open_world=True)
# Free, and creates or changes something without destroying anything.
_WRITE = _hints()

ANNOTATIONS: dict[str, ToolAnnotations] = {
    **dict.fromkeys(("account_status", "list_image_models", "get_image_model", "list_chat_models",
                     "resolve_advisor_model"), _READ_OPENROUTER),
    **dict.fromkeys(("list_advisor_chats", "get_advisor_chat", "get_advisor_job", "wait_advisor_job",
                     "get_advisor_result", "get_sidecar_preferences", "get_sidecar_settings",
                     "get_sidecar_demo", "get_image_download"), _READ),
    # Free, but never read-only: claude.ai shows a read-only tool's result as a plain
    # call and doesn't render its MCP App.
    **dict.fromkeys(("open_advisor_result", "open_sidecar_settings", "open_image_uploader"),
                    _hints(idempotent=True)),
    **dict.fromkeys(("ask_advisor", "send_advisor_message", "generate_image", "edit_image",
                     "summarize_advisor_result", "run_sidecar_demo"), _PAID),
    **dict.fromkeys(("create_image_upload", "remask_image", "create_local_import",
                     "reset_advisor_sandbox"), _WRITE),
    "set_sidecar_preferences": _hints(idempotent=True),
    "start_advisor_chat": _hints(open_world=True),  # checks the live catalog; no inference
    "delete_advisor_chat": _hints(destructive=True, idempotent=True),
    "retry_advisor_cleanup": _hints(destructive=True, idempotent=True, open_world=True),
}


def annotate(server: MCPServer) -> MCPServer:
    """Label every registered tool; an unlabelled tool stops the server from starting."""
    # The SDK has no public setter after registration; Tool.annotations is what tools/list sends.
    tools = server._tool_manager.list_tools()
    missing = sorted(tool.name for tool in tools if tool.name not in ANNOTATIONS)
    if missing:
        raise ValueError(f"Tools without annotations in tool_annotations.py: {', '.join(missing)}")
    for tool in tools:
        tool.annotations = ANNOTATIONS[tool.name]
    return server
