"""Opt-in live tests against the real OpenRouter API.

Deselected by default (`addopts = "-m 'not live'"`); run with `pytest -m live` and an
OpenRouter key for a class workspace in `OPENROUTER_LIVE_KEY` (cohort from
`OPENROUTER_LIVE_COHORT`, default 2027). They spend real (small) amounts of money,
use synthetic images only, and keep results in memory.
"""

import hashlib
import os
from io import BytesIO

import pytest
from helpers import make_grid, save
from PIL import Image

from openrouter_image_mcp.advisor_store import Scope
from openrouter_image_mcp.catalog import Catalog, ModelCapabilities, price_key
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import COHORT_WORKSPACES, Settings
from openrouter_image_mcp.service import ImageService

pytestmark = pytest.mark.live


class EnvCredentials:
    """The live key from the environment; never stored or printed."""

    def __init__(self, key: str, workspace_id: str):
        self._key, self.workspace_id = key, workspace_id

    def resolve(self):
        return self._key, Scope(hashlib.sha256(self._key.encode()).hexdigest(), self.workspace_id)

    def check(self, scope):
        pass

    def forget(self):
        pass


class MemorySink:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def save(self, job, *, data, ext, unmasked, meta, index):
        asset_id = f"live_{len(self.data) + 1}"
        self.data[asset_id] = data
        return asset_id


@pytest.fixture
def settings():
    key = os.environ.get("OPENROUTER_LIVE_KEY")
    if not key:
        pytest.skip("set OPENROUTER_LIVE_KEY to a class workspace key to run live tests")
    workspace = COHORT_WORKSPACES[os.environ.get("OPENROUTER_LIVE_COHORT", "2027")]
    return Settings(workspace_id=workspace, max_input_edge=2048, timeout_s=600, ratio_tolerance=0.03), key


@pytest.fixture
async def client(settings):
    values, key = settings
    async with OpenRouterClient(timeout_s=values.timeout_s,
                                credentials=EnvCredentials(key, values.workspace_id)) as c:
        yield c


@pytest.fixture
def catalog(client):
    return Catalog(client)


@pytest.fixture
def svc(client, catalog, settings):
    return ImageService(client, catalog, settings[0])


def _cheapest(models: list[ModelCapabilities], *, need_edit: bool = False) -> ModelCapabilities:
    """Cheapest priced images-route model, preferring ones that support quality="low"."""
    usable = [
        m
        for m in models
        if m.route != "chat"
        and price_key(m) != float("inf")
        and (not need_edit or (m.accepts_images and m.aspect_ratios is not None))
    ]
    if not usable:
        pytest.skip("no suitable priced image model in the live catalog")
    low = [m for m in usable if "low" in (m.qualities or [])]
    return min(low or usable, key=price_key)


def _quality(m: ModelCapabilities) -> str | None:
    return "low" if "low" in (m.qualities or []) else None


async def test_live_catalog(catalog):
    models = await catalog.all()
    assert any(m.route == "images" and m.accepts_images for m in models)


async def test_live_account(client):
    info = await client.key_info()
    assert "usage_daily" in info


async def test_live_generate_cheapest(svc, catalog):
    model = _cheapest(await catalog.all())
    sink = MemorySink()
    result = await svc.generate(
        "a small red cube on a white table", model.id, n=1, quality=_quality(model), sink=sink
    )
    assert len(result.images) == 1
    assert sink.data[result.images[0].asset_id]
    assert result.images[0].cost_usd is not None


async def test_live_edit_synthetic_wide(svc, catalog, tmp_path):
    model = _cheapest(await catalog.all(), need_edit=True)
    source = save(make_grid(1920, 828), tmp_path / "wide.jpg", "JPEG")
    sink = MemorySink()
    result = await svc.edit(
        "make the top-left cell slightly brighter",
        model.id,
        [str(source)],
        n=1,
        quality=_quality(model),
        sink=sink,
    )
    assert len(result.images) == 1
    with Image.open(BytesIO(sink.data[result.images[0].asset_id])) as out:
        assert out.size == (1920, 828)
