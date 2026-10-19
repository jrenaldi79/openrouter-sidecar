"""MCP Apps over the hosted server: gallery binding, the uploader app and its CSP."""

import httpx
import pytest

from openrouter_image_mcp.apps import UI_URI, UPLOADER_UI_URI
from openrouter_image_mcp.hosted.principal import Principal
from tests import jwt_helpers as h
from tests.hosted_harness import WS_2027, running_server
from tests.test_hosted_image_tools import APPS, FakeImages


@pytest.fixture
async def running(db_target, tmp_path):
    async with running_server(db_target, fake=FakeImages(),
                              env={"ASSET_DIR": str(tmp_path / "assets")}) as server:
        server.keys.insert(Principal(h.ISSUER, "email|alice", WS_2027, "2027"), "sk-or-v1-alice", "hash-alice", "Alice")
        yield server


def _meta(tool):
    return (tool.meta or {}).get("ui", {})


async def test_app_clients_get_gallery_and_uploader_bindings(running):
    async with running.client(running.token(), extensions=APPS) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
    for name in ("generate_image", "edit_image", "remask_image"):
        assert _meta(tools[name]).get("resourceUri") == UI_URI
    assert _meta(tools["open_image_uploader"]).get("resourceUri") == UPLOADER_UI_URI
    assert {UI_URI, UPLOADER_UI_URI} <= resources


async def test_clients_without_apps_see_no_uploader(running):
    async with running.client(running.token()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        refused = await client.call_tool("open_image_uploader", {})
    assert "open_image_uploader" not in tools and "create_image_upload" in tools
    assert refused.is_error


async def test_uploader_resource_may_only_connect_to_our_origin(running):
    origin = f"http://127.0.0.1:{running.port}"
    async with running.client(running.token(), extensions=APPS) as client:
        read = await client.read_resource(UPLOADER_UI_URI)
        opened = await client.call_tool("open_image_uploader", {})
    content = read.contents[0]
    assert content.meta["ui"]["csp"]["connectDomains"] == [origin]
    assert content.meta["ui"]["csp"]["resourceDomains"] == []
    assert f"connect-src {origin};" in content.text and "__SIDECAR_ORIGIN__" not in content.text
    assert not opened.is_error and "edit_image" in opened.content[0].text


async def test_upload_preflight_is_served_over_http(running):
    async with running.client(running.token(), extensions=APPS) as client:
        ticket = (await client.call_tool("create_image_upload", {
            "filename": "a.png", "mime": "image/png", "size_bytes": 10})).structured_content
    async with httpx.AsyncClient() as http:
        response = await http.options(ticket["upload_url"], headers={
            "Origin": "null", "Access-Control-Request-Method": "PUT"})
    assert response.status_code == 204 and response.headers["access-control-allow-origin"] == "*"


async def test_image_results_carry_no_bearer_material(running):
    async with running.client(running.token(), extensions=APPS) as client:
        result = await client.call_tool("generate_image", {"prompt": "a red barn",
                                                           "model": "openai/gpt-image-2.5-sunburst"})
    text = str(result.structured_content)
    assert result.structured_content["images"][0]["assetId"].startswith("img_")
    assert "eyJ" not in text and "sk-or" not in text and "Bearer" not in text
