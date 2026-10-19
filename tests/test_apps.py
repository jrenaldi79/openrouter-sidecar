"""Gallery data split: model-safe structured fields, previews only in the result's _meta."""

import base64
import re
from types import SimpleNamespace

from mcp import Client
from mcp.client import advertise
from mcp.server.mcpserver import MCPServer

from openrouter_image_mcp import apps
from openrouter_image_mcp.apps import (
    PREVIEWS_META,
    UI_EXTENSION,
    UI_MIME,
    UI_URI,
    ImageApps,
    negotiate_apps,
    preview_meta,
    register_gallery,
)

PREFIX = "data:image/jpeg;base64,"


def test_no_local_path_gallery_entries_remain():
    # Hosted results carry asset ids and signed links (`hosted/image_results.py`).
    assert not hasattr(apps, "preview_image")


def test_preview_meta_is_index_aligned_with_null_for_missing_previews():
    assert PREVIEWS_META == "sidecarPreviews"
    assert preview_meta([b"jpg", None]) == {PREVIEWS_META: [PREFIX + base64.b64encode(b"jpg").decode(), None]}
    assert preview_meta(jpeg for jpeg in [b"a"]) == {PREVIEWS_META: [PREFIX + "YQ=="]}


def _ctx(apps: bool, name="generate_image"):
    extensions = {UI_EXTENSION: {"mimeTypes": [UI_MIME]}} if apps else {}
    session = SimpleNamespace(client_capabilities=SimpleNamespace(extensions=extensions))
    return SimpleNamespace(session=session, method="tools/call", params={"name": name})


def _wire(meta):
    return {"content": [{"type": "text", "text": "Saved"}], "structuredContent": {"images": []}, "_meta": meta}


async def _negotiate(apps, wire, name="generate_image"):
    async def call_next(ctx):
        return wire
    return await negotiate_apps(_ctx(apps, name), call_next)


async def test_apps_clients_keep_previews_in_meta():
    wire = _wire({PREVIEWS_META: [PREFIX + "YQ=="]})
    assert await _negotiate(True, wire) == wire


async def test_clients_without_apps_get_neither_structured_content_nor_previews():
    result = await _negotiate(False, _wire({PREVIEWS_META: [PREFIX + "YQ=="]}))
    assert result == {"content": [{"type": "text", "text": "Saved"}]}


async def test_clients_without_apps_keep_other_meta():
    result = await _negotiate(False, _wire({PREVIEWS_META: [None], "other": 1}))
    assert result["_meta"] == {"other": 1} and "structuredContent" not in result


async def test_other_tools_pass_through_for_clients_without_apps():
    wire = _wire({PREVIEWS_META: []})
    assert await _negotiate(False, wire, name="get_image_download") == wire


def _gallery_server() -> MCPServer:
    server = MCPServer("t", extensions=[ImageApps()], middleware=[negotiate_apps])
    register_gallery(server)

    def generate_image() -> str:
        return "x"

    server.add_tool(generate_image, meta={"ui": {"resourceUri": UI_URI}})
    return server


async def test_negotiation_does_not_mutate_shared_tools():
    server = _gallery_server()
    for extensions, expected in (
        (None, False),
        ([advertise(UI_EXTENSION, {"mimeTypes": [UI_MIME]})], True),
        ([advertise(UI_EXTENSION, {"mimeTypes": ["text/plain"]})], False),
        ([advertise(UI_EXTENSION, {"mimeTypes": [UI_MIME]})], True),
    ):
        async with Client(server, mode="legacy", extensions=extensions) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "generate_image")
            assert bool(tool.meta and "ui" in tool.meta) is expected


async def test_gallery_resource_is_self_contained_html():
    async with Client(_gallery_server(), mode="legacy",
                      extensions=[advertise(UI_EXTENSION, {"mimeTypes": [UI_MIME]})]) as client:
        page = (await client.read_resource(UI_URI)).contents[0]
    assert page.mime_type == UI_MIME
    assert page.text.lower().startswith("<!doctype html>")
    assert "<script" in page.text
    assert not re.search(r"<script[^>]+src=", page.text)
    assert page.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
