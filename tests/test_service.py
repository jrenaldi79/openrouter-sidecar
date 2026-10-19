import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
import respx
from helpers import make_grid, save, unc_paths
from PIL import Image

from openrouter_image_mcp import outputs, service
from openrouter_image_mcp.catalog import Catalog
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import Settings
from openrouter_image_mcp.errors import (
    BadRequestError,
    InsufficientCreditsError,
    ProviderError,
)
from openrouter_image_mcp.imaging import InputError
from openrouter_image_mcp.service import ImageService

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 3, 22, 15, 0, tzinfo=UTC)
SUNBURST = "openai/gpt-image-2.5-sunburst"  # 21:9, quality enum, no resolutions, max_n 10
FLUX_PRO = "black-forest-labs/flux.2-pro"  # max_n 1
FLUX_3 = "black-forest-labs/flux-3-image"  # resolutions 768..4K, 21:9
VECTOR = "recraft/recraft-v4-vector"  # output_format svg
CHAT = "openrouter/auto"  # chat route

META_KEYS = {
    "tool", "created_at", "model", "provider", "prompt", "params", "seed", "generation_id",
    "elapsed_s", "usage", "cost_usd", "call_cost_usd", "inputs", "mask", "fit",
}


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def png_bytes(w, h, color=None):
    img = make_grid(w, h) if color is None else Image.new("RGB", (w, h), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def images_body(datas, cost=0.21, gen_id="gen-1", provider="openai", media_type="image/png"):
    body = {
        "id": gen_id,
        "provider": provider,
        "data": [
            {"b64_json": base64.b64encode(d).decode(), "media_type": media_type} for d in datas
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    if cost is not None:
        body["usage"]["cost"] = cost
    return body


def ok(w=2016, h=864, **kw):
    return httpx.Response(200, json=images_body([png_bytes(w, h)], **kw))


def body_of(call):
    return json.loads(call.request.content)


def decode_data_url(url):
    header, _, payload = url.partition(",")
    return header, Image.open(BytesIO(base64.b64decode(payload)))


class MemorySink:
    """Keeps what the service hands its output sink (hosted: `AssetSink`, stored as assets)."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.fail_on: set[int] = set()  # 1-based save attempts that fail
        self.attempts = 0

    def save(self, job, *, data, ext, unmasked, meta, index):
        self.attempts += 1
        if self.attempts in self.fail_on:
            raise OSError("disk full")
        asset_id = f"img_{len(self.records) + 1}"
        self.records[asset_id] = {"data": data, "ext": ext, "unmasked": unmasked, "meta": meta,
                                  "name": f"{job.stem}__{outputs.model_short(job.model.id)}_{index}"}
        return asset_id


class Rig:
    """The service with a memory sink, as `HostedImages` drives it."""

    def __init__(self, svc: ImageService):
        self.svc, self.sink = svc, MemorySink()

    async def generate(self, *args, **kwargs):
        return await self.svc.generate(*args, sink=self.sink, **kwargs)

    async def edit(self, *args, **kwargs):
        return await self.svc.edit(*args, sink=self.sink, **kwargs)

    def record(self, saved) -> dict:
        return self.sink.records[saved.asset_id]

    def meta(self, saved) -> dict:
        return self.record(saved)["meta"]

    def data(self, saved) -> bytes:
        return self.record(saved)["data"]

    def image(self, saved) -> Image.Image:
        return Image.open(BytesIO(self.data(saved)))


class Mono:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def keyed(keys):
    keys.set("sk-or-test-abc123")
    return keys


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").respond(200, json=fixture("images_models.json"))
        m.get(f"{BASE}/models", params={"output_modalities": "image"}).respond(
            200, json=fixture("models_image.json")
        )
        m.get(f"{BASE}/key").respond(
            200, json={"data": {"usage_daily": 1.84, "usage_monthly": 12.4}}
        )
        m.get(f"{BASE}/images/models/{SUNBURST}/endpoints").respond(
            200, json=fixture("endpoints_gpt-image-2.5-sunburst.json")
        )
        yield m


@pytest.fixture
def images_route(mock):
    return mock.post(f"{BASE}/images")


@pytest.fixture
async def client(keyed):
    async with OpenRouterClient(timeout_s=30, credentials=keyed.credentials()) as c:
        yield c


@pytest.fixture
def settings():
    return Settings(
        workspace_id="21082e84-ae02-4639-ad40-c7251b98ab10",
        max_input_edge=2048,
        timeout_s=30,
        ratio_tolerance=0.03,
    )


@pytest.fixture
def mono():
    return Mono()


@pytest.fixture
def svc(client, mock, settings, mono):
    return Rig(ImageService(client, Catalog(client), settings, clock=lambda: NOW, monotonic=mono))


@pytest.fixture
def sample_render(tmp_path):
    return save(make_grid(1920, 828), tmp_path / "proj" / "Sample_Render_v5.jpg", "JPEG")


# ------------------------------------------------------------------- edit


async def test_edit_sample_render_shape(svc, images_route, sample_render):
    images_route.mock(return_value=ok(2016, 864))

    result = await svc.edit("make it dusk", SUNBURST, [str(sample_render)])

    assert images_route.call_count == 1
    body = body_of(images_route.calls[0])
    assert body["model"] == SUNBURST
    assert body["prompt"] == "make it dusk"
    assert body["aspect_ratio"] == "21:9"
    assert "n" not in body and "resolution" not in body and "provider" not in body
    refs = body["input_references"]
    assert len(refs) == 1
    assert refs[0]["type"] == "image_url"
    assert refs[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    assert len(result.images) == 1 and result.failures == []
    saved = result.images[0]
    assert saved.asset_id == "img_1"
    assert svc.record(saved)["name"] == "Sample_Render_v5__gpt-image-2.5-sunburst_1"
    assert svc.record(saved)["ext"] == ".png" and svc.record(saved)["unmasked"] is None
    assert svc.image(saved).size == (1920, 828)
    assert saved.cost_usd == pytest.approx(0.21)
    assert saved.preview_jpeg is not None
    assert Image.open(BytesIO(saved.preview_jpeg)).format == "JPEG"

    meta = svc.meta(saved)
    assert set(meta) == META_KEYS
    assert meta["tool"] == "edit_image"
    assert meta["created_at"] == NOW.isoformat()
    assert meta["model"] == SUNBURST and meta["provider"] == "openai"
    assert meta["params"] == {
        "aspect_ratio": "21:9",
        "resolution": None,
        "quality": None,
        "n": 1,
        "seed": None,
        "provider_options": {},
    }
    assert meta["generation_id"] == "gen-1"
    assert meta["cost_usd"] == pytest.approx(0.21)
    assert meta["call_cost_usd"] == pytest.approx(0.21)
    assert meta["usage"]["cost"] == pytest.approx(0.21)
    assert meta["mask"] is None
    assert meta["inputs"] == [
        {
            "path": sample_render.as_posix(),
            "sha256": hashlib.sha256(sample_render.read_bytes()).hexdigest(),
            "size": [1920, 828],
        }
    ]
    assert meta["fit"] == {
        "strategy": "crop_back",
        "requested_ratio": "21:9",
        "raw_size": [2016, 864],
        "final_size": [1920, 828],
        "crop_box": [6, 0, 1926, 828],
        "padded": False,
        "upscaled": False,
    }
    assert result.model == SUNBURST and result.provider == "openai"
    assert result.call_cost_usd == pytest.approx(0.21)
    assert result.elapsed_s >= 0


async def test_large_input_preserve_returns_original_size(svc, images_route, tmp_path):
    big = save(make_grid(7680, 3300), tmp_path / "big.jpg", "JPEG")
    images_route.mock(return_value=ok(2016, 864))

    result = await svc.edit("x", SUNBURST, [str(big)])

    url = body_of(images_route.calls[0])["input_references"][0]["image_url"]["url"]
    _, sent = decode_data_url(url)
    assert max(sent.size) == 2048
    saved = result.images[0]
    assert svc.image(saved).size == (7680, 3300)
    fit = svc.meta(saved)["fit"]
    assert fit["upscaled"] is True
    assert fit["final_size"] == [7680, 3300]
    assert svc.meta(saved)["inputs"][0]["size"] == [7680, 3300]
    assert saved.note and "upscaled from 2016×864" in saved.note


async def test_pad_strategy_uses_plan_content_box(svc, images_route, tmp_path):
    wide = save(make_grid(3000, 1000), tmp_path / "wide.png", "PNG")  # 3:1, nothing near
    images_route.mock(return_value=ok(2016, 864))

    result = await svc.edit("x", SUNBURST, [str(wide)])

    body = body_of(images_route.calls[0])
    assert body["aspect_ratio"] == "21:9"
    header, sent = decode_data_url(body["input_references"][0]["image_url"]["url"])
    assert header == "data:image/png;base64"
    assert sent.format == "PNG"
    assert sent.size == (2048, 878)  # sent copy (2048x683) padded out to 21:9
    saved = result.images[0]
    assert svc.image(saved).size == (3000, 1000)
    fit = svc.meta(saved)["fit"]
    assert fit["strategy"] == "pad" and fit["padded"] is True
    assert fit["crop_box"] == [0, 96, 2016, 768]
    assert any("padded" in n.lower() for n in result.notes)


async def test_resolution_from_plan_unless_given(svc, images_route, sample_render):
    images_route.mock(side_effect=[ok(2016, 864), ok(2016, 864)])

    await svc.edit("x", FLUX_3, [str(sample_render)])
    await svc.edit("x", FLUX_3, [str(sample_render)], resolution="4K")

    assert body_of(images_route.calls[0])["resolution"] == "2K"
    assert body_of(images_route.calls[1])["resolution"] == "4K"


async def test_mask_applied_after_fit(svc, images_route, tmp_path):
    src = save(make_grid(1920, 828), tmp_path / "in.png", "PNG")
    mask = Image.new("L", (1920, 828), 0)
    mask.paste(255, (960, 0, 1920, 828))  # right half: take the model's pixels
    mask_path = save(mask, tmp_path / "mask.png", "PNG")
    images_route.mock(return_value=httpx.Response(
        200, json=images_body([png_bytes(2016, 864, color=(0, 0, 255))])
    ))

    result = await svc.edit("x", SUNBURST, [str(src)], mask_path=str(mask_path), mask_feather_px=0)

    out = svc.image(result.images[0]).convert("RGB")
    original = Image.open(src).convert("RGB")
    assert out.size == (1920, 828)
    for x, y in [(0, 0), (100, 400), (959, 827)]:
        assert out.getpixel((x, y)) == original.getpixel((x, y))
    for x, y in [(960, 0), (1500, 400), (1919, 827)]:
        assert out.getpixel((x, y)) == (0, 0, 255)
    meta = svc.meta(result.images[0])
    assert meta["mask"] == {"path": mask_path.as_posix(), "feather_px": 0}


async def test_masked_edit_keeps_unmasked_layer(svc, images_route, tmp_path):
    src = save(make_grid(1920, 828), tmp_path / "in.png", "PNG")
    mask = Image.new("L", (1920, 828), 0)
    mask.paste(255, (960, 0, 1920, 828))  # right half: take the model's pixels
    mask_path = save(mask, tmp_path / "mask.png", "PNG")
    model_out = Image.new("RGB", (2016, 864), (0, 0, 255))
    model_out.paste((255, 0, 0), (0, 0, 1008, 864))  # left half red, never shown in the result
    buf = BytesIO()
    model_out.save(buf, format="PNG")
    images_route.mock(return_value=httpx.Response(200, json=images_body([buf.getvalue()])))

    result = await svc.edit("x", SUNBURST, [str(src)], mask_path=str(mask_path), mask_feather_px=0)

    saved = result.images[0]
    with Image.open(BytesIO(svc.record(saved)["unmasked"])) as opened:
        assert opened.format == "PNG"
        layer = opened.convert("RGB")
    assert layer.size == (1920, 828)  # the input's size, not the model's
    out = svc.image(saved).convert("RGB")
    # Pixel-aligned with the result: inside the mask the two are identical...
    # (sample points stay clear of the resampled red/blue boundary near x=960)
    for x, y in [(1100, 0), (1500, 400), (1919, 827)]:
        assert layer.getpixel((x, y)) == out.getpixel((x, y)) == (0, 0, 255)
    right = (960, 0, 1920, 828)
    assert layer.crop(right).tobytes() == out.crop(right).tobytes()
    # ...and outside it the layer still has the model's pixels the blend discarded.
    for x, y in [(0, 0), (100, 400), (800, 827)]:
        assert layer.getpixel((x, y)) == (255, 0, 0)
    assert set(svc.meta(saved)) == META_KEYS


async def test_unmasked_layer_is_png_whatever_the_output_format(svc, images_route, tmp_path):
    src = save(make_grid(1920, 828), tmp_path / "in.png", "PNG")
    mask_path = save(Image.new("L", (1920, 828), 255), tmp_path / "mask.png", "PNG")
    images_route.mock(return_value=ok(2016, 864))

    result = await svc.edit(
        "x", FLUX_PRO, [str(src)], mask_path=str(mask_path), output_format="jpeg"
    )

    saved = result.images[0]
    assert svc.record(saved)["ext"] == ".jpg" and svc.image(saved).format == "JPEG"
    assert Image.open(BytesIO(svc.record(saved)["unmasked"])).format == "PNG"


async def test_mask_with_fit_model_rejected_before_network(svc, mock, images_route, sample_render, tmp_path):
    mask_path = save(Image.new("L", (8, 8), 255), tmp_path / "m.png", "PNG")
    with pytest.raises(InputError, match="mask_path requires fit='preserve'"):
        await svc.edit("x", SUNBURST, [str(sample_render)], mask_path=str(mask_path), fit="model")
    assert mock.calls.call_count == 0


async def test_preserve_with_aspect_ratio_rejected(svc, images_route, sample_render):
    with pytest.raises(BadRequestError, match="can't be combined with fit='preserve'"):
        await svc.edit("x", SUNBURST, [str(sample_render)], aspect_ratio="16:9")
    assert images_route.call_count == 0


async def test_fit_model_untouched(svc, images_route, sample_render):
    provider_bytes = png_bytes(1344, 768)
    images_route.mock(return_value=httpx.Response(200, json=images_body([provider_bytes])))

    result = await svc.edit("x", SUNBURST, [str(sample_render)], fit="model", aspect_ratio="16:9")

    assert body_of(images_route.calls[0])["aspect_ratio"] == "16:9"
    saved = result.images[0]
    assert hashlib.sha256(svc.data(saved)).digest() == hashlib.sha256(provider_bytes).digest()
    assert svc.meta(saved)["fit"] == {
        "strategy": "model",
        "requested_ratio": "16:9",
        "raw_size": [1344, 768],
        "final_size": [1344, 768],
        "crop_box": None,
        "padded": False,
        "upscaled": False,
    }


async def test_chat_route_used_for_chat_models(svc, mock, images_route, tmp_path):
    src = save(make_grid(1000, 500), tmp_path / "chat.png", "PNG")
    url = "data:image/png;base64," + base64.b64encode(png_bytes(1024, 1024)).decode()
    chat = mock.post(f"{BASE}/chat/completions").respond(200, json={
        "id": "gen-chat",
        "provider": "google",
        "choices": [{"message": {"content": "here", "images": [{"image_url": {"url": url}}]}}],
        "usage": {"cost": 0.04},
    })

    result = await svc.edit("x", CHAT, [str(src)])

    assert images_route.call_count == 0
    assert chat.call_count == 1
    body = json.loads(chat.calls[0].request.content)
    assert body["model"] == CHAT
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "x"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "image_config" not in body
    saved = result.images[0]
    assert svc.image(saved).size == (1000, 500)
    assert svc.meta(saved)["fit"]["strategy"] == "none_supported"
    assert result.provider == "google"
    assert result.call_cost_usd == pytest.approx(0.04)


# --------------------------------------------------------------- generate


async def test_generate_names_results_with_a_prompt_slug(svc, images_route):
    provider_bytes = png_bytes(1024, 1024)
    images_route.mock(return_value=httpx.Response(200, json=images_body([provider_bytes])))

    result = await svc.generate("A red panda astronaut floating in deep space", SUNBURST, quality="high")

    body = body_of(images_route.calls[0])
    assert body == {
        "model": SUNBURST,
        "prompt": "A red panda astronaut floating in deep space",
        "quality": "high",
    }
    saved = result.images[0]
    assert svc.record(saved)["name"] == "a-red-panda-astronaut-floating-in__gpt-image-2.5-sunburst_1"
    assert svc.data(saved) == provider_bytes and svc.record(saved)["unmasked"] is None
    meta = svc.meta(saved)
    assert set(meta) == META_KEYS
    assert meta["tool"] == "generate_image"
    assert meta["inputs"] == [] and meta["fit"] is None and meta["mask"] is None
    assert meta["params"]["quality"] == "high"


async def test_generate_prefix_and_n_in_one_call(svc, images_route):
    images_route.mock(return_value=httpx.Response(
        200, json=images_body([png_bytes(64, 64), png_bytes(64, 64)], cost=0.3)
    ))

    result = await svc.generate(
        "x", SUNBURST, n=2, filename_prefix="hero",
        provider_options={"moderation": "low"},
    )

    assert images_route.call_count == 1
    body = body_of(images_route.calls[0])
    assert body["n"] == 2
    assert body["provider"] == {"options": {"openai": {"moderation": "low"}}}
    names = [svc.record(s)["name"] for s in result.images]
    assert names == ["hero__gpt-image-2.5-sunburst_1", "hero__gpt-image-2.5-sunburst_2"]
    assert [s.cost_usd for s in result.images] == [pytest.approx(0.15), pytest.approx(0.15)]
    assert result.call_cost_usd == pytest.approx(0.3)
    meta = svc.meta(result.images[0])
    assert meta["params"]["n"] == 2
    assert meta["params"]["provider_options"] == {"moderation": "low"}


async def test_validation_before_spend(svc, images_route):
    with pytest.raises(BadRequestError, match="quality 'ultra'"):
        await svc.generate("x", SUNBURST, quality="ultra")
    assert images_route.call_count == 0


async def test_variations_loop(svc, images_route):
    images_route.mock(side_effect=[ok(64, 64, gen_id=f"g{i}") for i in range(3)])

    result = await svc.generate("x", FLUX_PRO, n=3)

    assert images_route.call_count == 3
    assert all("n" not in body_of(c) for c in images_route.calls)
    assert "model max n is 1; will make 3 calls" in result.notes
    assert len(result.images) == 3
    assert [svc.record(s)["name"].rsplit("_", 1)[1] for s in result.images] == ["1", "2", "3"]
    assert result.call_cost_usd == pytest.approx(0.63)


async def test_variations_loop_partial_failure(svc, images_route):
    images_route.mock(side_effect=[ok(64, 64), httpx.ConnectError("boom"), ok(64, 64)])

    result = await svc.generate("x", FLUX_PRO, n=3)

    assert images_route.call_count == 3
    assert len(result.images) == 2
    assert len(result.failures) == 1
    assert "Couldn't reach OpenRouter" in result.failures[0]
    assert result.call_cost_usd == pytest.approx(0.42)


async def test_variations_stop_on_credits(svc, images_route):
    images_route.mock(side_effect=[ok(64, 64), httpx.Response(402, json={"error": {"message": "no"}})])

    result = await svc.generate("x", FLUX_PRO, n=3)

    assert images_route.call_count == 2
    assert len(result.images) == 1
    assert len(result.failures) == 1 and "out of credits" in result.failures[0]


async def test_stop_error_with_nothing_saved_reraises(svc, images_route):
    images_route.mock(return_value=httpx.Response(402, json={"error": {"message": "no"}}))
    with pytest.raises(InsufficientCreditsError):
        await svc.generate("x", FLUX_PRO, n=3)
    assert images_route.call_count == 1


@pytest.mark.parametrize("gen_body", [{"data": {"total_cost": 0.05}}, {"total_cost": 0.05}])
async def test_cost_fallback_via_generation(svc, mock, images_route, gen_body):
    images_route.mock(return_value=ok(64, 64, cost=None, gen_id="gen-xyz"))
    gen = mock.get(f"{BASE}/generation", params={"id": "gen-xyz"}).respond(200, json=gen_body)

    result = await svc.generate("x", SUNBURST)

    assert gen.call_count == 1
    assert result.call_cost_usd == pytest.approx(0.05)
    assert svc.meta(result.images[0])["cost_usd"] == pytest.approx(0.05)
    assert result.usage_line.startswith("This call: $0.0500")


async def test_usage_line(svc, mock, images_route, mono):
    key = mock.get(f"{BASE}/key").respond(
        200, json={"data": {"usage_daily": 1.84, "usage_monthly": 12.4}}
    )
    images_route.mock(return_value=ok(64, 64))

    result = await svc.generate("x", SUNBURST)
    assert result.usage_line == "This call: $0.2100 · Key usage today: $1.84 / month: $12.40"
    assert key.call_count == 1

    mono.now += 30
    await svc.generate("x", SUNBURST)
    assert key.call_count == 1  # cached

    mono.now += 31
    await svc.generate("x", SUNBURST)
    assert key.call_count == 2


async def test_usage_line_unknown_cost_and_key_failure(svc, mock, images_route):
    mock.get(f"{BASE}/key").mock(side_effect=httpx.ConnectError("down"))
    images_route.mock(return_value=ok(64, 64, cost=None, gen_id=None))

    result = await svc.generate("x", SUNBURST)

    assert result.usage_line == "This call: cost unavailable"
    assert result.call_cost_usd is None
    assert result.images[0].cost_usd is None


async def test_progress_heartbeat(svc, images_route, monkeypatch):
    monkeypatch.setattr(service, "HEARTBEAT_S", 0.1)

    async def slow(request):
        await asyncio.sleep(0.35)
        return ok(64, 64)

    images_route.mock(side_effect=slow)
    events = []

    async def progress(value, total, message):
        events.append((value, total, message))

    await svc.generate("x", SUNBURST, progress=progress)

    waiting = [e for e in events if e[2].startswith(f"Waiting for {SUNBURST}…")]
    assert len(waiting) >= 3
    assert all(0 < v < 1 and t == 1 for v, t, _ in waiting)
    values = [v for v, _, _ in events]
    assert all(b > a for a, b in pairwise(values)), values
    assert events[-1] == (1, 1, "Finished call 1 of 1")


async def test_progress_strictly_increases_across_calls(svc, images_route, monkeypatch):
    monkeypatch.setattr(service, "HEARTBEAT_S", 0.05)

    async def slow(request):
        await asyncio.sleep(0.18)
        return ok(64, 64)

    images_route.mock(side_effect=slow)
    events = []

    async def progress(value, total, message):
        events.append((value, total, message))

    await svc.generate("x", FLUX_PRO, n=2, progress=progress)  # max_n 1: two calls

    values = [v for v, _, _ in events]
    assert all(b > a for a, b in pairwise(values)), values
    assert all(t == 2 for _, t, _ in events)
    assert (1, 2, "Finished call 1 of 2") in events
    assert events[-1] == (2, 2, "Finished call 2 of 2")
    assert any(1 < v < 2 for v, _, m in events if m.startswith("Waiting"))


async def test_svg_output_saved_raw_no_preview(svc, images_route):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    images_route.mock(return_value=httpx.Response(
        200, json=images_body([svg], media_type="image/svg+xml")
    ))

    result = await svc.generate("a logo", VECTOR, output_format="svg")

    assert body_of(images_route.calls[0])["output_format"] == "svg"
    saved = result.images[0]
    assert svc.record(saved)["ext"] == ".svg"
    assert svc.data(saved) == svg
    assert saved.preview_jpeg is None
    assert "SVG output saved without preview or fitting" in result.notes
    meta = svc.meta(saved)
    assert meta["fit"] is None
    assert meta["params"]["output_format"] == "svg"


async def test_single_call_failure_raises(svc, images_route):
    images_route.mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ProviderError, match="Couldn't reach OpenRouter"):
        await svc.generate("x", SUNBURST)


# ------------------------------------------------------------ fix round 1


async def test_garbage_mask_rejected_before_network(svc, mock, sample_render, tmp_path):
    bad = tmp_path / "mask.png"
    bad.write_bytes(b"this is not an image")
    with pytest.raises(InputError, match="mask"):
        await svc.edit("x", SUNBURST, [str(sample_render)], mask_path=str(bad))
    assert mock.calls.call_count == 0


@pytest.mark.parametrize("unc", unc_paths("pic.png"))
async def test_edit_rejects_network_input_before_any_call(svc, mock, sample_render, no_network_probe, unc):
    with pytest.raises(InputError, match="Network .*paths aren't supported"):
        await svc.edit("x", SUNBURST, [str(sample_render), unc])
    assert mock.calls.call_count == 0  # no catalog lookup, no generation
    assert no_network_probe == []


@pytest.mark.parametrize("unc", [r"\/evil-host/share/m.png", r"/\evil-host\share\m.png"])
async def test_edit_rejects_network_mask_before_any_call(
    svc, mock, sample_render, no_network_probe, unc
):
    with pytest.raises(InputError, match="Network .*paths aren't supported"):
        await svc.edit("x", SUNBURST, [str(sample_render)], mask_path=unc)
    assert mock.calls.call_count == 0
    assert no_network_probe == []


async def test_save_failure_mid_loop_keeps_other_images(svc, images_route):
    images_route.mock(side_effect=[ok(64, 64, gen_id=f"g{i}") for i in range(3)])
    svc.sink.fail_on = {2}  # the image from call 2

    result = await svc.generate("x", FLUX_PRO, n=3)

    assert images_route.call_count == 3
    assert len(result.images) == 2
    assert all(s.asset_id in svc.sink.records for s in result.images)
    assert len(result.failures) == 1 and "disk full" in result.failures[0]


async def test_save_failure_with_nothing_saved_raises(svc, images_route):
    images_route.mock(return_value=ok(64, 64))
    svc.sink.fail_on = {1}
    with pytest.raises(OSError, match="disk full"):
        await svc.generate("x", SUNBURST)


async def test_preserve_with_size_rejected(svc, mock, sample_render):
    with pytest.raises(BadRequestError, match="size can't be combined with fit='preserve'"):
        await svc.edit("x", SUNBURST, [str(sample_render)], size="1024x1024")
    assert mock.calls.call_count == 0


async def test_size_recorded_in_sidecar_params(svc, images_route):
    images_route.mock(return_value=ok(64, 64))
    result = await svc.generate("x", "inclusionai/ming-image-0.1-design", size="1024x1024")
    assert body_of(images_route.calls[0])["size"] == "1024x1024"
    assert svc.meta(result.images[0])["params"]["size"] == "1024x1024"


async def test_usage_line_survives_unexpected_key_info_error(svc, client, images_route, monkeypatch):
    async def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(client, "key_info", boom)
    images_route.mock(return_value=ok(64, 64))
    result = await svc.generate("x", SUNBURST)
    assert result.usage_line == "This call: $0.2100"


async def test_cancellation_propagates_and_cancels_request(svc, images_route, monkeypatch):
    monkeypatch.setattr(service, "HEARTBEAT_S", 0.01)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hang(request):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ok(64, 64)

    images_route.mock(side_effect=hang)

    async def progress(value, total, message):
        pass

    task = asyncio.create_task(svc.generate("x", SUNBURST, progress=progress))
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.sleep(0.05)  # let a few heartbeats run
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


# ------------------------------------------------------- final fix wave


def endpoint(slug, allowed):
    return {"provider_slug": slug, "allowed_passthrough_parameters": allowed}


async def test_provider_options_unknown_key_rejected_before_spend(svc, images_route):
    with pytest.raises(BadRequestError) as exc:
        await svc.generate("x", SUNBURST, provider_options={"style": "vivid"})
    assert "style" in str(exc.value) and "moderation" in str(exc.value)
    assert images_route.call_count == 0


async def test_provider_options_none_allowed_rejected(svc, mock, images_route):
    mock.get(f"{BASE}/images/models/{FLUX_PRO}/endpoints").respond(
        200, json={"endpoints": [endpoint("black-forest-labs", [])]}
    )
    with pytest.raises(BadRequestError, match="doesn't accept any provider_options"):
        await svc.generate("x", FLUX_PRO, provider_options={"moderation": "low"})
    assert images_route.call_count == 0


async def test_provider_options_keyed_by_each_allowing_provider(svc, mock, images_route):
    mock.get(f"{BASE}/images/models/{FLUX_PRO}/endpoints").respond(200, json={"endpoints": [
        endpoint("alpha", ["moderation"]),
        endpoint("beta", ["moderation", "safety_tolerance"]),
        endpoint("gamma", []),
    ]})
    images_route.mock(return_value=ok(64, 64))
    options = {"moderation": "low", "safety_tolerance": 5}

    result = await svc.generate("x", FLUX_PRO, provider_options=options)

    assert body_of(images_route.calls[0])["provider"] == {"options": {
        "alpha": {"moderation": "low"},
        "beta": {"moderation": "low", "safety_tolerance": 5},
    }}
    assert svc.meta(result.images[0])["params"]["provider_options"] == options


@pytest.mark.parametrize("n", [0, 11])
async def test_n_out_of_range_rejected_before_spend(svc, images_route, n):
    with pytest.raises(BadRequestError, match="n must be between 1 and 10"):
        await svc.generate("x", SUNBURST, n=n)
    assert images_route.call_count == 0


async def test_n_above_max_n_is_batched(svc, mock, images_route):
    lite = "bytedance-seed/seedream-5-0-lite"  # max_n 4
    images_route.mock(side_effect=[
        httpx.Response(200, json=images_body([png_bytes(64, 64)] * k, gen_id=f"g{k}"))
        for k in (4, 4, 2)
    ])

    result = await svc.generate("x", lite, n=10)

    assert images_route.call_count == 3
    assert [body_of(c).get("n") for c in images_route.calls] == [4, 4, 2]
    assert "model max n is 4; will make 3 calls" in result.notes
    assert len(result.images) == 10
    assert [svc.meta(s)["params"]["n"] for s in result.images] == [4] * 8 + [2] * 2


async def test_n_batched_last_call_of_one_omits_n(svc, images_route):
    lite = "bytedance-seed/seedream-5-0-lite"  # max_n 4
    images_route.mock(side_effect=[
        httpx.Response(200, json=images_body([png_bytes(64, 64)] * k)) for k in (4, 1)
    ])

    await svc.generate("x", lite, n=5)

    assert body_of(images_route.calls[0])["n"] == 4
    assert "n" not in body_of(images_route.calls[1])
