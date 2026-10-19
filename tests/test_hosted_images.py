"""Hosted image pipeline: asset ids in, assets out, remask from stored layers."""

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from helpers import make_grid
from PIL import Image

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.assets import AssetError, AssetStore, LocalDirBlobs
from openrouter_image_mcp.catalog import Catalog
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import COHORT_WORKSPACES, Settings
from openrouter_image_mcp.hosted.images import HostedImages
from openrouter_image_mcp.hosted.uploads import TransferTokens
from openrouter_image_mcp.service import ImageService

FIXTURES = Path(__file__).parent / "fixtures"
SUNBURST = "openai/gpt-image-2.5-sunburst"
WS = COHORT_WORKSPACES["2027"]
ALICE, BOB = Scope("iss|alice", WS), Scope("iss|bob", WS)


def png(w, h, color=None):
    image = make_grid(w, h) if color is None else Image.new("L" if isinstance(color, int) else "RGB", (w, h), color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
async def rig(tmp_path, db_target, keys):
    keys.set("sk-or-v1-synthetic", workspace_id=WS)
    sent = []

    def handle(request):
        path = request.url.path
        if path.endswith("/images/models"):
            return httpx.Response(200, json=json.loads((FIXTURES / "images_models.json").read_text()))
        if path.endswith("/models"):
            return httpx.Response(200, json=json.loads((FIXTURES / "models_image.json").read_text()))
        if path.endswith("/images"):
            sent.append(json.loads(request.content))
            data = base64.b64encode(png(2016, 864)).decode()
            return httpx.Response(200, json={"id": "gen-1", "provider": "openai", "usage": {"cost": 0.2},
                                             "data": [{"b64_json": data, "media_type": "image/png"}]})
        if path.endswith("/key"):
            return httpx.Response(200, json={"data": {"usage": 1.0}})
        return httpx.Response(404, json={"error": {"message": "unexpected " + path}})

    client = OpenRouterClient(30, transport=httpx.MockTransport(handle), credentials=keys.credentials(WS))
    settings = Settings(WS, 2048, 30, 0.03)
    service = ImageService(client, Catalog(client), settings)
    assets = AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs"))
    tokens = TransferTokens(db_target, "k" * 32, "https://sidecar.example")
    scope = {"current": ALICE}
    images = HostedImages(assets, tokens, service, lambda: scope["current"], max_input_edge=2048)
    yield images, assets, sent, scope
    await client.aclose()


async def test_generate_stores_outputs_as_assets_with_download_links(rig):
    images, assets, _, _ = rig
    result, links = await images.generate("a red barn", SUNBURST, aspect_ratio="21:9")
    saved = result.images[0]
    assert saved.asset_id.startswith("img_") and saved.preview_jpeg
    _, info = assets.get(ALICE, saved.asset_id)
    assert info.kind == "output" and info.mime == "image/png" and (info.width, info.height) == (2016, 864)
    assert info.meta["prompt"] == "a red barn" and info.meta["schema_version"] == 1
    assert info.filename.startswith("a-red-barn__")
    assert links[saved.asset_id]["image_url"].startswith("https://sidecar.example/assets/")


async def test_edit_reads_owned_input_assets_and_records_them(rig):
    images, assets, sent, _ = rig
    source = assets.put(ALICE, png(1200, 900), kind="input", mime="image/png", filename="room.png",
                        width=1200, height=900)
    result, _ = await images.edit("make it dusk", SUNBURST, [source.id])
    _, info = assets.get(ALICE, result.images[0].asset_id)
    assert (info.width, info.height) == (1200, 900)  # fit="preserve"
    assert info.parent_ids == [source.id]
    assert info.meta["inputs"][0]["asset_id"] == source.id and "path" not in info.meta["inputs"][0]
    assert sent[-1]["input_references"]


async def test_foreign_input_is_refused_before_any_call(rig):
    images, assets, sent, scope = rig
    source = assets.put(ALICE, png(64, 64), kind="input", mime="image/png", filename="a.png")
    scope["current"] = BOB
    with pytest.raises(AssetError):
        await images.edit("x", SUNBURST, [source.id])
    assert sent == []


async def test_masked_edit_then_free_remask_from_stored_layer(rig):
    images, assets, sent, _ = rig
    source = assets.put(ALICE, png(1200, 900), kind="input", mime="image/png", filename="room.png")
    mask = assets.put(ALICE, png(1200, 900, 255), kind="input", mime="image/png", filename="mask.png")
    edited, _ = await images.edit("add a lamp", SUNBURST, [source.id], mask_id=mask.id)
    _, info = assets.get(ALICE, edited.images[0].asset_id)
    assert info.meta["mask"]["asset_id"] == mask.id and info.meta["unmasked_asset_id"].startswith("img_")
    calls = len(sent)
    smaller = assets.put(ALICE, png(600, 450, 0), kind="input", mime="image/png", filename="m2.png")
    done, _ = images.remask(edited.images[0].asset_id, smaller.id)
    assert len(sent) == calls  # no provider call: free
    data, out = assets.get(ALICE, done.asset_id)
    assert out.meta["tool"] == "remask_image" and out.meta["cost_usd"] == 0
    assert Image.open(io.BytesIO(data)).size == (1200, 900)
    again, _ = images.remask(done.asset_id, mask.id)  # a remask can be remasked
    assert again.asset_id


async def test_remask_needs_a_masked_result(rig):
    images, assets, _, _ = rig
    result, _ = await images.generate("plain", SUNBURST)
    mask = assets.put(ALICE, png(10, 10, 255), kind="input", mime="image/png", filename="m.png")
    with pytest.raises(Exception, match="mask"):
        images.remask(result.images[0].asset_id, mask.id)


def test_upload_ticket_and_download_links_use_caller_scope(rig):
    images, assets, _, scope = rig
    ticket = images.upload_ticket("a.png", "image/png", 1000)
    assert ticket["upload_url"].startswith("https://sidecar.example/uploads/")
    asset = assets.put(ALICE, png(4, 4), kind="input", mime="image/png", filename="a.png")
    assert images.download(asset.id)["image_url"]
    scope["current"] = BOB
    with pytest.raises(AssetError):
        images.download(asset.id)


async def test_admission_runs_before_any_paid_call(rig):
    from contextlib import contextmanager

    images, assets, sent, _ = rig

    @contextmanager
    def refuse(scope):
        assert scope == ALICE
        raise AssetError("rate_limited: slow down")
        yield

    images.admit = refuse
    with pytest.raises(AssetError, match="rate_limited"):
        await images.generate("x", SUNBURST)
    source = assets.put(ALICE, png(64, 64), kind="input", mime="image/png", filename="a.png")
    with pytest.raises(AssetError, match="rate_limited"):
        await images.edit("x", SUNBURST, [source.id])
    assert sent == []
