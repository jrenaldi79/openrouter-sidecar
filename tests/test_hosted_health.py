"""Readiness covers the asset volume: a root-owned mount must fail /ready, not uploads."""

import os
import sys

import pytest

from openrouter_image_mcp.hosted.health import Health
from openrouter_image_mcp.hosted.workspaces import Gate


async def _started(tmp_path, **kwargs) -> Health:
    health = Health(tmp_path / "db.sqlite3", Gate(), **kwargs)
    await health.start()
    return health


async def test_ready_creates_a_missing_asset_dir(tmp_path):
    assets = tmp_path / "volume" / "assets"
    health = await _started(tmp_path, asset_dir=str(assets))
    assert await health.ready() == (True, "ready")
    assert assets.is_dir()


async def test_ready_without_assets_ignores_the_volume(tmp_path):
    assert await (await _started(tmp_path)).ready() == (True, "ready")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX permissions; root can write anywhere")
async def test_unwritable_asset_dir_is_not_ready(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    volume.chmod(0o555)  # like a root-owned mount seen by uid 10001
    try:
        health = await _started(tmp_path, asset_dir=str(volume / "assets"))
        assert await health.ready() == (False, "asset storage is not writable")
    finally:
        volume.chmod(0o755)


async def test_health_gate_follows_the_lock_hooks(tmp_path):
    from openrouter_image_mcp.hosted.health import MAINTENANCE

    health = Health(tmp_path / "db.sqlite3", Gate(), maintenance=True)
    await health.start()
    assert health.gate.reason == MAINTENANCE and health.lock.held
    await health.lock.on_lose[0]()
    assert health.gate.reason and "Another Sidecar instance" in health.gate.reason
    await health.lock.on_gain[0]()
    assert health.gate.reason == MAINTENANCE
    await health.stop()


@pytest.mark.pg
async def test_second_instance_is_unready_until_the_owner_stops(pg_target):
    import asyncio

    first, second = Health(pg_target, Gate()), Health(pg_target, Gate())
    second.lock.every_s = 0.05
    await first.start()
    await second.start()
    try:
        assert await first.ready() == (True, "ready")
        ok, detail = await second.ready()
        assert not ok and "Another Sidecar instance" in detail
        await first.stop()
        for _ in range(100):
            if second.lock.held:
                break
            await asyncio.sleep(0.02)
        assert await second.ready() == (True, "ready")
    finally:
        await first.stop()
        await second.stop()
