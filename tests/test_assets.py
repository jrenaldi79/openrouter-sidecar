"""Hosted image assets: owner-only, expiring, capped, blobs separate from metadata."""

import time

import pytest

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.assets import AssetError, AssetStore, LocalDirBlobs

ALICE = Scope("iss|alice", "ws-1")
BOB = Scope("iss|bob", "ws-1")


@pytest.fixture
def assets(db_target, tmp_path):
    return AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs"), cap_bytes=1000)


def put(assets, scope=ALICE, data=b"\x89PNG-bytes", **kw):
    return assets.put(scope, data, kind=kw.pop("kind", "input"), mime="image/png",
                      filename=kw.pop("filename", "a.png"), width=2, height=3, **kw)


def test_put_then_get_round_trips_bytes_and_metadata(assets):
    asset = put(assets, meta={"prompt": "p"})
    data, info = assets.get(ALICE, asset.id)
    assert data == b"\x89PNG-bytes"
    assert (info.kind, info.mime, info.width, info.height, info.filename) == ("input", "image/png", 2, 3, "a.png")
    assert info.meta == {"prompt": "p"} and len(info.sha256) == 64
    assert asset.id.startswith("img_")
    assert info.expires_at > time.time() + 29 * 86400


def test_other_owner_cannot_read(assets):
    asset = put(assets)
    with pytest.raises(AssetError, match="unavailable"):
        assets.get(BOB, asset.id)
    with pytest.raises(AssetError):
        assets.info(Scope("iss|alice", "ws-2"), asset.id)


def test_expired_asset_is_unavailable(assets):
    asset = put(assets)
    assets.clock = lambda: time.time() + 31 * 86400
    with pytest.raises(AssetError):
        assets.get(ALICE, asset.id)


def test_parents_and_kinds(assets):
    source = put(assets)
    output = put(assets, kind="output", parents=[source.id])
    assert assets.info(ALICE, output.id).parent_ids == [source.id]
    with pytest.raises(ValueError):
        put(assets, kind="weird")
    with pytest.raises(AssetError):
        put(assets, parents=["img_not_mine"])


def test_storage_cap_per_owner(assets):
    put(assets, data=b"x" * 600)
    with pytest.raises(AssetError, match="storage"):
        put(assets, data=b"x" * 500)
    put(assets, scope=BOB, data=b"x" * 500)  # other owners have their own cap


def test_purge_expired_removes_blob_then_row(assets, tmp_path):
    asset = put(assets)
    later = time.time() + 31 * 86400
    assert assets.expired_count(now=later) == 1
    assert assets.purge_expired(now=later) == 1
    assert not any((tmp_path / "blobs").rglob(asset.id))
    assert assets.expired_count(now=later) == 0


def test_blob_paths_cannot_escape_root(tmp_path):
    blobs = LocalDirBlobs(tmp_path / "blobs")
    with pytest.raises(ValueError):
        blobs.write("../evil", b"x")
