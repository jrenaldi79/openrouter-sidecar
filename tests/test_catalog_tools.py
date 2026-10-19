"""The free model-discovery tools (`list_image_models`, `get_image_model`)."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from openrouter_image_mcp.catalog import Catalog, price_key
from openrouter_image_mcp.catalog_tools import catalog_tools
from openrouter_image_mcp.client import OpenRouterClient

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).parent / "fixtures"
SUNBURST = "openai/gpt-image-2.5-sunburst"
TABLE_HEADER = (
    "| id | name | inputs | aspect ratios | resolutions | quality | max n | seed | moderated | price |"
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def table_rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.startswith("| ")]
    assert lines[0] == TABLE_HEADER
    return [[cell.strip() for cell in ln.strip("|").split(" | ")] for ln in lines[1:]]


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").respond(200, json=fixture("images_models.json"))
        m.get(f"{BASE}/models", params={"output_modalities": "image"}).respond(
            200, json=fixture("models_image.json"))
        m.get(f"{BASE}/images/models/{SUNBURST}/endpoints").respond(
            200, json=fixture("endpoints_gpt-image-2.5-sunburst.json"))
        yield m


@pytest.fixture
async def http(keys):
    async with OpenRouterClient(timeout_s=30, credentials=keys.credentials()) as c:
        yield c


@pytest.fixture
def tools(http, mock):
    list_image_models, get_image_model = catalog_tools(Catalog(http))
    return {"list": list_image_models, "get": get_image_model}


async def test_list_image_models_table(tools, http):
    text = await tools["list"](accepts_images=True, sort="price")

    rows = table_rows(text)
    expected = sorted((m for m in await Catalog(http).all() if m.accepts_images), key=price_key)
    assert [r[0] for r in rows] == [m.id for m in expected]
    assert all(len(r) == 10 for r in rows)
    row = next(r for r in rows if r[0] == SUNBURST)
    assert row[2] == "text+image (max 16)"
    assert row[3].startswith("1:1, ")
    assert row[4] == "—"
    assert row[6] == "10"
    assert row[7] == "no"
    assert row[8] == "yes"
    assert row[9] == "$0.00003/img-unit"
    assert f"{len(rows)} models" in text
    assert "get_image_model" in text.splitlines()[-1]


async def test_list_image_models_filters_and_price_fallbacks(tools):
    rows = table_rows(await tools["list"](author="openrouter"))
    assert {r[0] for r in rows} == {"openrouter/auto", "openrouter/auto-beta"}
    assert all(r[9] == "—" for r in rows)  # "-1" is variable pricing, not a price

    rows = table_rows(await tools["list"](query="QWEN-IMAGE-3"))
    assert [r[0] for r in rows] == ["qwen/qwen-image-3-pro", "qwen/qwen-image-3"]
    assert rows[0][7] == "yes"

    rows = table_rows(await tools["list"](accepts_images=False))
    assert rows and all(r[2] == "text" for r in rows)

    rows = table_rows(await tools["list"](query="Recraft V4.1 Flash"))
    assert rows[0][0] == "recraft/recraft-v4.1-flash"  # matches the name too


async def test_list_image_models_newest_is_catalog_order(tools, http):
    rows = table_rows(await tools["list"]())
    assert [r[0] for r in rows] == [m.id for m in await Catalog(http).all()]


async def test_list_image_models_no_match(tools):
    assert "No image models match" in await tools["list"](query="zzz-nothing")


async def test_get_image_model(tools):
    text = await tools["get"](SUNBURST)

    assert SUNBURST in text
    assert "OpenAI" in text
    assert "quality: auto, low, medium, high, xhigh, max" in text
    assert "n: 1–10" in text
    assert "moderation" in text  # allowed passthrough parameter
    assert "output_image" in text and "$0.00003/token" in text


async def test_get_image_model_chat_route_skips_endpoints(tools, mock):
    assert "chat completions" in await tools["get"]("openrouter/auto")
    assert not [c for c in mock.calls if "/endpoints" in str(c.request.url)]


async def test_unknown_model_is_tool_error(tools):
    with pytest.raises(ToolError) as caught:
        await tools["get"]("openai/gpt-image-2.5-sunbrst")
    assert "Did you mean" in str(caught.value) and SUNBURST in str(caught.value)


async def test_catalog_unavailable_is_clean_error(http):
    list_image_models, _ = catalog_tools(Catalog(http))
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").mock(side_effect=httpx.ConnectError("network down"))
        m.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("network down"))
        with pytest.raises(ToolError) as caught:
            await list_image_models()
    assert str(caught.value).lower().startswith("couldn't reach the openrouter catalog")


async def test_catalog_error_message_always_names_the_catalog(http):
    list_image_models, _ = catalog_tools(Catalog(http))
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").respond(200, text="<html>not json</html>")
        m.get(f"{BASE}/models").respond(200, json=fixture("models_image.json"))
        with pytest.raises(ToolError) as caught:
            await list_image_models()
    assert str(caught.value).startswith("Couldn't reach the OpenRouter catalog")
