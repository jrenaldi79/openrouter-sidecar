"""Image uploads (single-use PUT) and signed downloads for hosted assets."""

import io
import json
import time

import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.testclient import TestClient

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.assets import AssetError, AssetStore, LocalDirBlobs
from openrouter_image_mcp.hosted.uploads import TransferTokens, transfer_routes

ORIGIN = "https://sidecar.example"
ALICE = Scope("iss|alice", "ws-1")
BOB = Scope("iss|bob", "ws-1")


def png(w=8, h=6, fmt="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def rig(db_target, tmp_path):
    assets = AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs"))
    tokens = TransferTokens(db_target, "k" * 32, ORIGIN)
    client = TestClient(Starlette(routes=transfer_routes(tokens, assets)), base_url=ORIGIN)
    return assets, tokens, client


def path_of(url):
    return url.removeprefix(ORIGIN)


def test_upload_creates_an_owned_input_asset(rig):
    assets, tokens, client = rig
    data = png()
    ticket = tokens.issue_upload(ALICE, "room.png", "image/png", len(data))
    assert ticket["upload_url"].startswith(ORIGIN + "/uploads/") and ticket["expires_in"] == 600
    response = client.put(path_of(ticket["upload_url"]), content=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["width"], body["height"], body["filename"]) == (8, 6, "room.png")
    stored, info = assets.get(ALICE, body["asset_id"])
    assert stored == data and info.kind == "input"
    with pytest.raises(AssetError):
        assets.get(BOB, body["asset_id"])


def test_upload_url_is_single_use(rig):
    _, tokens, client = rig
    data = png()
    url = path_of(tokens.issue_upload(ALICE, "a.png", "image/png", len(data))["upload_url"])
    assert client.put(url, content=data).status_code == 200
    assert client.put(url, content=data).status_code == 400


@pytest.mark.parametrize("case", ["oversize", "not_image", "wrong_mime", "tampered", "expired", "bomb"])
def test_bad_uploads_are_rejected(rig, case, monkeypatch):
    _, tokens, client = rig
    data, mime = png(), "image/png"
    if case == "not_image":
        data = b"hello, not an image"
    if case == "wrong_mime":
        mime = "image/jpeg"
    if case == "bomb":
        monkeypatch.setattr("openrouter_image_mcp.hosted.uploads.MAX_PIXELS", 40)
    declared = len(data) - 1 if case == "oversize" else len(data)
    ticket = tokens.issue_upload(ALICE, "a.png", mime, declared)
    url = path_of(ticket["upload_url"])
    if case == "tampered":
        url = url[:-3] + ("AAA" if not url.endswith("AAA") else "BBB")
    if case == "expired":
        tokens.clock = lambda: time.time() + 601
    response = client.put(url, content=data)
    assert response.status_code in (400, 413), response.text
    assert "asset_id" not in response.text


def test_issue_validates_request(rig):
    _, tokens, _ = rig
    for args in (("a.gif", "image/gif", 10), ("a.png", "image/png", 0),
                 ("a.png", "image/png", 21 * 1024 * 1024)):
        with pytest.raises(ValueError):
            tokens.issue_upload(ALICE, *args)


def test_signed_download_returns_image_and_metadata(rig):
    assets, tokens, client = rig
    asset = assets.put(ALICE, png(), kind="output", mime="image/png", filename="out.png",
                       width=8, height=6, meta={"prompt": "red"})
    links = tokens.download_links(ALICE, assets.info(ALICE, asset.id))
    image = client.get(path_of(links["image_url"]))
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert "attachment" in image.headers["content-disposition"] and image.content == png()
    meta = client.get(path_of(links["metadata_url"]))
    assert json.loads(meta.content)["prompt"] == "red"
    assert links["expires_in"] == 86400


def test_download_link_is_bound_to_owner_and_expiry(rig):
    assets, tokens, client = rig
    asset = assets.put(ALICE, png(), kind="output", mime="image/png", filename="o.png")
    url = path_of(tokens.download_links(ALICE, asset)["image_url"])
    forged = url.replace(asset.id, assets.put(BOB, png(), kind="output", mime="image/png",
                                                 filename="b.png").id)
    assert client.get(forged).status_code == 404
    tokens.clock = lambda: time.time() + 86401
    assert client.get(url).status_code == 404


def test_upload_filename_is_made_header_safe(rig):
    assets, tokens, client = rig
    data = png()
    ticket = tokens.issue_upload(ALICE, 'evil"\r\nX-Injected: 1\\..png', "image/png", len(data))
    asset_id = client.put(path_of(ticket["upload_url"]), content=data).json()["asset_id"]
    name = assets.info(ALICE, asset_id).filename
    assert not set(name) & set('"\r\n\\/') and name.endswith(".png")
    got = client.get(path_of(tokens.download_links(ALICE, assets.info(ALICE, asset_id))["image_url"]))
    assert got.status_code == 200 and "x-injected" not in got.headers
