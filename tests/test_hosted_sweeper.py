"""In-process sweep of expired image assets (blobs live on the server's volume)."""

import asyncio
import time

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.assets import AssetStore, LocalDirBlobs
from openrouter_image_mcp.hosted.sweeper import Sweeper
from openrouter_image_mcp.hosted.uploads import TransferTokens

ALICE = Scope("iss|alice", "ws-1")


def rig(db_target, tmp_path):
    assets = AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs"))
    tokens = TransferTokens(db_target, "k" * 32, "https://sidecar.example")
    return assets, tokens


def test_sweep_removes_expired_assets_and_upload_tickets(db_target, tmp_path):
    assets, tokens = rig(db_target, tmp_path)
    old = assets.put(ALICE, b"old", kind="input", mime="image/png", filename="o.png")
    tokens.issue_upload(ALICE, "a.png", "image/png", 10)
    later = time.time() + 31 * 86400
    assets.clock = tokens.clock = lambda: later
    fresh = assets.put(ALICE, b"new", kind="input", mime="image/png", filename="n.png")
    report = Sweeper(assets, tokens).sweep()
    assert report == {"image_assets": 1, "image_uploads": 1}
    assert not (tmp_path / "blobs" / old.id[-2:] / old.id).exists()
    assert assets.get(ALICE, fresh.id)[0] == b"new"
    assert Sweeper(assets, tokens).sweep() == {"image_assets": 0, "image_uploads": 0}


def test_recent_upload_tickets_are_kept(db_target, tmp_path):
    assets, tokens = rig(db_target, tmp_path)
    tokens.issue_upload(ALICE, "a.png", "image/png", 10)
    tokens.clock = lambda: time.time() + 700  # expired, but within the grace day
    assert Sweeper(assets, tokens).sweep()["image_uploads"] == 0


async def test_loop_sweeps_until_stopped(db_target, tmp_path):
    assets, tokens = rig(db_target, tmp_path)
    sweeper = Sweeper(assets, tokens, every_s=0.01)
    calls = []
    sweeper.sweep = lambda: calls.append(1) or {}
    await sweeper.start()
    await asyncio.sleep(0.05)
    await sweeper.stop()
    count = len(calls)
    await asyncio.sleep(0.03)
    assert count >= 2 and len(calls) == count
