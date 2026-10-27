"""Opt-in live smoke: a vision advisor reads an uploaded image.

Deselected by default; run with
`OPENROUTER_LIVE_KEY=... OPENROUTER_LIVE_WORKSPACE=... uv run pytest -m live tests/live/test_advisor_images_live.py -s`
It makes one paid inference (a few cents). The image is a 64x64 red square on white,
stored as an input asset the way the uploader stores one, then passed by id.
"""

import io
import os

import pytest
from PIL import Image

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_jobs import AdvisorJobs
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.assets import AssetStore, LocalDirBlobs
from tests.helpers import KeyBox

pytestmark = pytest.mark.live

MODEL = "google/gemini-3.8-flash"


def red_square() -> bytes:
    image = Image.new("RGB", (64, 64), "white")
    image.paste((255, 0, 0), (16, 16, 48, 48))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_vision_advisor_names_the_red_square(tmp_path):
    key = os.environ.get("OPENROUTER_LIVE_KEY")
    workspace = os.environ.get("OPENROUTER_LIVE_WORKSPACE", "live-smoke")
    if not key:
        pytest.skip("Set OPENROUTER_LIVE_KEY to run the live advisor image smoke test.")
    keys = KeyBox()
    keys.set(key, workspace_id=workspace)
    credentials = keys.credentials(workspace)
    scope = credentials.resolve()[1]
    client = AdvisorClient(workspace, 120)
    try:
        assets = AssetStore(tmp_path / "assets.sqlite3", LocalDirBlobs(tmp_path / "blobs"))
        image = assets.put(scope, red_square(), kind="input", mime="image/png", filename="square.png",
                           width=64, height=64)
        service = AdvisorService(workspace, AdvisorStore(tmp_path / "advisor.sqlite3"), client,
                                 credentials=credentials, sandbox_default=False,
                                 sandbox_allowed=False, assets=assets)
        jobs = AdvisorJobs(service)
        chat = await service.start(MODEL, "Live image smoke")
        started = await jobs.start(chat["chat_id"], "Describe this image in one sentence.",
                                   images=[image.id])
        row = await jobs.wait(started["job_id"], 120)
        assert row["status"] == "completed", row.get("error")
        answer = row["answer"].lower()
        assert "red" in answer and "square" in answer, answer
        assert row["cost_usd"] and row["cost_usd"] > 0
    finally:
        await client.aclose()
