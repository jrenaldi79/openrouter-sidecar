"""Hosted image tools over real HTTP: upload, edit, remask and download by asset id."""

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from mcp.client import advertise
from PIL import Image

from openrouter_image_mcp.hosted.principal import Principal
from tests import jwt_helpers as h
from tests.hosted_harness import WS_2027, FakeOpenRouter, running_server

FIXTURES = Path(__file__).parent / "fixtures"
SUNBURST = "openai/gpt-image-2.5-sunburst"
APPS = [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
IMAGE_TOOLS = {"create_image_upload", "generate_image", "edit_image", "remask_image",
               "get_image_download", "list_image_models", "get_image_model"}


def png(w, h, color=(200, 10, 10)):
    buffer = io.BytesIO()
    Image.new("L" if isinstance(color, int) else "RGB", (w, h), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeImages(FakeOpenRouter):
    """The advisor fake plus the image catalog and the images endpoint."""

    async def handle(self, request):
        path = request.url.path
        if path.endswith("/images/models"):
            self.requests.append(request)
            return httpx.Response(200, json=json.loads((FIXTURES / "images_models.json").read_text()))
        if path.endswith("/models") and request.url.params.get("output_modalities") == "image":
            self.requests.append(request)
            return httpx.Response(200, json=json.loads((FIXTURES / "models_image.json").read_text()))
        if path.endswith("/images"):
            self.requests.append(request)
            data = base64.b64encode(png(640, 480, (10, 200, 10))).decode()
            return httpx.Response(200, json={"id": "gen-1", "provider": "openai", "usage": {"cost": 0.2},
                                             "data": [{"b64_json": data, "media_type": "image/png"}]})
        if path.endswith("/key"):
            return httpx.Response(200, json={"data": {"usage": 1.0}})
        return await super().handle(request)


def _text(result):
    return "\n".join(getattr(item, "text", "") for item in result.content)


@pytest.fixture
async def running(db_target, tmp_path):
    async with running_server(db_target, fake=FakeImages(),
                              env={"ASSET_DIR": str(tmp_path / "assets")}) as server:
        server.keys.insert(Principal(h.ISSUER, "email|alice", WS_2027, "2027"), "sk-or-v1-alice", "hash-alice", "Alice")
        yield server


async def upload(running, client, data, name="room.png"):
    ticket = (await client.call_tool("create_image_upload", {
        "filename": name, "mime": "image/png", "size_bytes": len(data)})).structured_content
    assert "curl -T" in ticket["instructions"]
    async with httpx.AsyncClient() as http:
        response = await http.put(ticket["upload_url"], content=data)
    assert response.status_code == 200, response.text
    return response.json()["asset_id"]


async def test_image_tools_need_an_asset_dir(db_target):
    async with running_server(db_target) as plain, plain.client(plain.token()) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not names & (IMAGE_TOOLS - {"list_image_models", "get_image_model"})


async def test_upload_edit_remask_download_round_trip(running):
    async with running.client(running.token(), extensions=APPS) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert IMAGE_TOOLS <= names
        source = await upload(running, client, png(640, 480))
        mask = await upload(running, client, png(640, 480, 255), "mask.png")
        edited = await client.call_tool("edit_image", {"prompt": "add a lamp", "model": SUNBURST,
                                                       "images": [source], "mask_id": mask})
        assert not edited.is_error, _text(edited)
        image = edited.structured_content["images"][0]
        assert image["assetId"].startswith("img_") and "dataUri" not in image
        assert edited.meta["sidecarPreviews"][0].startswith("data:image/jpeg;base64,")
        assert "base64" not in json.dumps(edited.structured_content)
        assert image["assetId"] in _text(edited) and image["downloadUrl"] in _text(edited)
        calls = len(running.fake.bodies("/images"))
        remasked = await client.call_tool("remask_image", {"image_id": image["assetId"],
                                                           "mask_id": mask, "mask_feather_px": 2})
        assert not remasked.is_error, _text(remasked)
        assert len(running.fake.bodies("/images")) == calls  # free: no provider call
        again = (await client.call_tool("get_image_download",
                                        {"asset_id": image["assetId"]})).structured_content
    async with httpx.AsyncClient() as http:
        got = await http.get(again["image_url"])
        meta = (await http.get(again["metadata_url"])).json()
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).size == (640, 480)
    assert meta["inputs"][0]["asset_id"] == source and meta["mask"]["asset_id"] == mask
    assert remasked.structured_content["callCostUsd"] == 0


async def test_generate_text_carries_asset_ids_and_links_without_apps(running):
    async with running.client(running.token()) as client:  # e.g. Claude Code: no MCP Apps
        models = await client.call_tool("list_image_models", {"query": "sunburst"})
        assert SUNBURST in _text(models)
        result = await client.call_tool("generate_image", {"prompt": "a red barn", "model": SUNBURST})
    assert not result.is_error, _text(result)
    text = _text(result)
    assert "Asset: img_" in text and "a-red-barn__" in text
    assert f"http://127.0.0.1:{running.port}/assets/img_" in text
    assert result.structured_content is None
    assert "sidecarPreviews" not in (result.meta or {})  # app-only data stays with Apps clients


async def test_assets_are_private_to_their_owner(running):
    async with running.client(running.token()) as alice:
        source = await upload(running, alice, png(64, 64))
    async with running.client(running.token(sub="email|bob")) as bob:
        denied = await bob.call_tool("get_image_download", {"asset_id": source})
        edit = await bob.call_tool("edit_image", {"prompt": "x", "model": SUNBURST, "images": [source]})
    assert denied.is_error and "unavailable" in _text(denied)
    assert edit.is_error and running.fake.bodies("/images") == []


async def test_first_generate_provisions_a_key_and_charges_it(running):
    async with running.client(running.token(sub="email|carol")) as client:
        result = await client.call_tool("generate_image", {"prompt": "x", "model": SUNBURST})
    assert not result.is_error, _text(result)
    assert [body["name"] for body in running.fake.created_bodies()] == [
        "Sidecar · carol@u.northwestern.edu · 2027"]
    calls = [r for r in running.fake.requests if r.url.path.endswith("/images")]
    assert calls[-1].headers["Authorization"] == "Bearer sk-or-v1-fake1"
