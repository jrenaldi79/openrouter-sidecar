"""Every tool tells hosts whether it only reads, spends money or deletes something.

Hosts such as claude.ai group a connector's tools by these MCP annotations, so a
user can allow every read-only tool at once and keep approval on paid and
destructive calls. The server can't skip approval itself; it can only label tools.
"""

import pytest
from mcp import Client
from mcp.client import advertise
from mcp.server.mcpserver import MCPServer

from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import Settings
from openrouter_image_mcp.server import build_server
from openrouter_image_mcp.tool_annotations import annotate
from tests.hosted_harness import running_server

APPS = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
READ_ONLY = {
    "account_status", "list_image_models", "get_image_model", "list_chat_models", "resolve_advisor_model",
    "list_advisor_chats", "get_advisor_chat", "get_advisor_job", "wait_advisor_job", "get_advisor_result",
    "get_sidecar_preferences", "get_sidecar_settings", "get_sidecar_demo", "get_image_download",
    "open_advisor_result", "open_sidecar_settings", "open_image_uploader",
}
PAID = {"ask_advisor", "send_advisor_message", "generate_image", "edit_image",
        "summarize_advisor_result", "run_sidecar_demo"}
DESTRUCTIVE = {"delete_advisor_chat", "unlink_openrouter", "auth_logout", "retry_advisor_cleanup"}


def _by_hint(tools):
    assert {t.name for t in tools if t.annotations is None} == set()
    read_only = {t.name for t in tools if t.annotations.read_only_hint}
    destructive = {t.name for t in tools if t.annotations.destructive_hint}
    paid = {t.name for t in tools if t.annotations.open_world_hint and not t.annotations.read_only_hint
            and t.name in PAID}
    names = {t.name for t in tools}
    return names, read_only, destructive, paid


async def test_hosted_tools_are_labelled_read_only_paid_or_destructive(db_target, tmp_path):
    async with running_server(db_target, env={"ASSET_DIR": str(tmp_path / "assets")}) as server, \
            server.client(server.token(), extensions=APPS) as client:
        tools = (await client.list_tools()).tools
    names, read_only, destructive, paid = _by_hint(tools)
    assert read_only == READ_ONLY & names
    assert destructive == DESTRUCTIVE & names
    assert paid == PAID & names
    assert {"link_openrouter", "create_image_upload", "remask_image"} <= names - read_only - destructive


async def test_local_tools_are_labelled_too(tmp_path, memory_keyring):
    settings = Settings(workspace_id="21082e84-ae02-4639-ad40-c7251b98ab10", output_dir=tmp_path / "out",
                        max_input_edge=2048, timeout_s=30, ratio_tolerance=0.03)
    async with OpenRouterClient(timeout_s=30) as http, \
            Client(build_server(settings, http), mode="legacy", extensions=APPS) as client:
        tools = (await client.list_tools()).tools
    names, read_only, destructive, paid = _by_hint(tools)
    assert read_only == READ_ONLY & names
    assert destructive == DESTRUCTIVE & names
    assert paid == PAID & names
    assert "auth_logout" in destructive


def test_an_unlabelled_tool_fails_at_startup():
    server = MCPServer("t")

    def brand_new_tool() -> str:
        return "x"

    server.add_tool(brand_new_tool)
    with pytest.raises(ValueError, match="brand_new_tool"):
        annotate(server)
