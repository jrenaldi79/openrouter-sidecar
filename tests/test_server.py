"""MCP layer tests: an in-memory client session (mcp 2.x `Client`) against `build_server`."""

import asyncio
import base64
import json
import re
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import keyring
import keyring.backend
import keyring.errors
import pytest
import respx
from helpers import make_grid, save, unc_paths
from mcp import Client
from mcp.client import advertise
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from PIL import Image

from openrouter_image_mcp import keystore
from openrouter_image_mcp import server as server_mod
from openrouter_image_mcp.advisor_tools import ADVISOR_ARGUMENTS
from openrouter_image_mcp.apps import (
    ADVISOR_UI_TOOLS,
    ADVISOR_UI_URI,
    APP_ONLY_TOOLS,
    SETTINGS_UI_TOOLS,
    SETTINGS_UI_URI,
)
from openrouter_image_mcp.catalog import Catalog, price_key
from openrouter_image_mcp.client import OpenRouterClient
from openrouter_image_mcp.config import Settings
from openrouter_image_mcp.server import build_server

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).parent / "fixtures"
SUNBURST = "openai/gpt-image-2.5-sunburst"
SECRET = "sk-or-secret"
TOOLS = {
    "account_status",
    "auth_login",
    "auth_logout",
    "list_image_models",
    "get_image_model",
    "generate_image",
    "edit_image",
    "remask_image",
} | ADVISOR_ARGUMENTS.keys()
TABLE_HEADER = (
    "| id | name | inputs | aspect ratios | resolutions | quality | max n | seed | moderated | price |"
)
KEY_INFO = {
    "label": "openrouter-image-mcp (test-host)",
    "usage": 3.5,
    "usage_daily": 1.84,
    "usage_weekly": 2.1,
    "usage_monthly": 12.4,
    "limit": 50,
    "limit_remaining": 37.6,
}


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def test_server_wires_separate_advisor_timeout(tmp_path, monkeypatch):
    from openrouter_image_mcp.config import load_settings

    settings = load_settings({"OPENROUTER_IMAGE_COHORT": "2027",
                              "OPENROUTER_IMAGE_TIMEOUT_S": "12",
                              "OPENROUTER_ADVISOR_TIMEOUT_S": "1200"})
    clients = []
    real_client = server_mod.AdvisorClient

    def capture_client(workspace, timeout_s):
        client = real_client(workspace, timeout_s)
        clients.append(client)
        return client

    monkeypatch.setattr(server_mod, "AdvisorClient", capture_client)
    image_client = OpenRouterClient(timeout_s=settings.timeout_s, workspace_id=settings.workspace_id)
    try:
        build_server(settings=settings, client=image_client, advisor_store_path=tmp_path / "advisor.sqlite3")
        assert clients[0]._timeout_s == 1200
        assert image_client._timeout_s == 12
    finally:
        await image_client.aclose()
        for client in clients:
            await client.aclose()


def png_b64(w, h):
    buf = BytesIO()
    make_grid(w, h).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def images_ok(w=2016, h=864, n=1):
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "provider": "openai",
            "data": [{"b64_json": png_b64(w, h), "media_type": "image/png"} for _ in range(n)],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.21},
        },
    )


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def error_text(result) -> str:
    """The tool's own message: mcp 2.x prefixes every ToolError with `Error executing tool <name>: `."""
    assert result.is_error is True
    return re.sub(r"^Error executing tool \w+: ", "", text_of(result))


def table_rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.startswith("| ")]
    assert lines[0] == TABLE_HEADER
    return [[cell.strip() for cell in ln.strip("|").split(" | ")] for ln in lines[1:]]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        workspace_id="21082e84-ae02-4639-ad40-c7251b98ab10",
        output_dir=tmp_path / "out",
        max_input_edge=2048,
        timeout_s=30,
        ratio_tolerance=0.03,
    )


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").respond(200, json=fixture("images_models.json"))
        m.get(f"{BASE}/models", params={"output_modalities": "image"}).respond(
            200, json=fixture("models_image.json")
        )
        m.get(f"{BASE}/images/models/{SUNBURST}/endpoints").respond(
            200, json=fixture("endpoints_gpt-image-2.5-sunburst.json")
        )
        m.get(f"{BASE}/key").respond(200, json={"data": KEY_INFO})
        yield m


@pytest.fixture
def images_route(mock):
    return mock.post(f"{BASE}/images")


@pytest.fixture
async def http(memory_keyring):
    async with OpenRouterClient(timeout_s=30) as c:
        yield c


@pytest.fixture
def keyed(memory_keyring):
    keystore.set_key(SECRET)
    return memory_keyring


@pytest.fixture
def server(settings, http, mock):
    return build_server(settings, http)


@pytest.fixture
async def session(server, request):
    """A legacy-mode (JSON-RPC over memory streams) client session.

    The client's task group lives in its own task: pytest-asyncio runs fixture
    setup and teardown in different tasks, and anyio cancel scopes can't span them.
    """
    ready, done = asyncio.Event(), asyncio.Event()
    holder = {}

    async def hold():
        extensions = (
            [advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]
            if getattr(request, "param", False) else None
        )
        async with Client(server, mode="legacy", extensions=extensions) as c:
            holder["client"] = c
            ready.set()
            await done.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(ready.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        waiter.cancel()
        task.result()  # re-raise the connection failure
    yield holder["client"]
    done.set()
    await task


@pytest.fixture
def sample_render(tmp_path):
    return save(make_grid(1920, 828), tmp_path / "proj" / "Sample_Render_v5.jpg", "JPEG")


# ------------------------------------------------------------------ registration


async def test_build_server_returns_named_mcpserver(server):
    assert isinstance(server, MCPServer)
    assert server.name == "openrouter-sidecar"
    assert server.version == "0.2.0"


async def test_tool_names(session):
    tools = (await session.list_tools()).tools
    assert {t.name for t in tools} == TOOLS - APP_ONLY_TOOLS
    assert len(tools) == 24


@pytest.mark.parametrize("session", [True], indirect=True)
async def test_image_apps_discovery(session):
    uri = "ui://openrouter-sidecar/preview.html"
    tools = {t.name: t for t in (await session.list_tools()).tools}
    for name in ("generate_image", "edit_image", "remask_image"):
        assert tools[name].meta == {"ui": {"resourceUri": uri}}
    for name in TOOLS - {"generate_image", "edit_image", "remask_image"} - ADVISOR_UI_TOOLS - APP_ONLY_TOOLS - SETTINGS_UI_TOOLS:
        assert not tools[name].meta
    resources = (await session.list_resources()).resources
    assert {str(r.uri) for r in resources} == {uri, ADVISOR_UI_URI, SETTINGS_UI_URI}
    page = (await session.read_resource(uri)).contents[0]
    assert page.mime_type == "text/html;profile=mcp-app"
    assert page.text.lower().startswith("<!doctype html>")
    assert "<script" in page.text
    assert not re.search(r"<script[^>]+src=", page.text)
    assert page.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}


async def test_image_apps_fallback_client(session, keyed, images_route):
    tools = (await session.list_tools()).tools
    assert all(not t.meta or "ui" not in t.meta for t in tools)
    images_route.mock(return_value=images_ok(64, 64))
    result = await session.call_tool("generate_image", {"prompt": "test", "model": SUNBURST})
    assert result.structured_content is None
    assert any(isinstance(c, ImageContent) for c in result.content)
    assert "Saved:" in text_of(result)


@pytest.mark.parametrize("session", [True], indirect=True)
@pytest.mark.parametrize("tool", ["generate_image", "edit_image"])
async def test_image_apps_jpeg_result(session, keyed, images_route, sample_render, tool):
    images_route.mock(return_value=images_ok(64, 64))
    arguments = {"prompt": "test", "model": SUNBURST}
    if tool == "edit_image":
        arguments["images"] = [str(sample_render)]
    result = await session.call_tool(tool, arguments)
    assert result.is_error is False
    data = result.structured_content
    assert data["model"] == SUNBURST
    assert data["provider"] == "openai"
    assert data["callCostUsd"] == pytest.approx(0.21)
    assert data["usageLine"] in text_of(result)
    assert data["notes"] == data["failures"] == []
    image = data["images"][0]
    saved = Path(image["path"])
    assert saved.is_file() and image["filename"] == saved.name
    preview = next(c for c in result.content if isinstance(c, ImageContent))
    assert image["dataUri"] == "data:image/jpeg;base64," + preview.data
    assert "ui://" not in text_of(result)


async def test_image_apps_negotiation_does_not_mutate_shared_tools(server):
    for extensions, expected in (
        (None, False),
        ([advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})], True),
        ([advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/plain"]})], False),
        ([advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})], True),
    ):
        async with Client(server, mode="legacy", extensions=extensions) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "generate_image")
            assert bool(tool.meta and "ui" in tool.meta) is expected


def test_image_apps_unknown_cost_missing_preview_and_redaction(tmp_path):
    from openrouter_image_mcp.service import SavedImage, ServiceResult

    path = tmp_path / f"{SECRET}.svg"
    result = server_mod._image_result(ServiceResult(
        images=[SavedImage(path, path.with_suffix(".json"), None, None, None)],
        model=SECRET, provider=None, call_cost_usd=None, usage_line="Cost unavailable",
        elapsed_s=0, notes=[SECRET], failures=[SECRET],
    ))
    data = result.structured_content
    assert data["callCostUsd"] is None
    assert data["images"][0]["dataUri"] is None
    assert data["images"][0]["filename"].endswith(".svg")
    assert SECRET not in json.dumps(data)
    assert [type(c) for c in result.content] == [TextContent]


async def test_tool_parameters_match_spec(session):
    tools = {t.name: t for t in (await session.list_tools()).tools}
    props = {name: set(t.input_schema.get("properties", {})) for name, t in tools.items()}
    assert props["account_status"] == set()
    assert props["auth_login"] == {"switch_account"}
    assert props["auth_logout"] == set()
    assert props["list_image_models"] == {"query", "accepts_images", "author", "sort"}
    assert props["get_image_model"] == {"model_id"}
    shared = {
        "prompt", "model", "n", "aspect_ratio", "resolution", "size", "quality", "seed",
        "background", "output_format", "output_dir", "filename_prefix", "provider_options",
    }
    assert props["generate_image"] == shared
    assert props["edit_image"] == shared | {"images", "mask_path", "mask_feather_px", "fit"}
    assert set(tools["edit_image"].input_schema["required"]) == {"prompt", "model", "images"}
    assert set(tools["generate_image"].input_schema["required"]) == {"prompt", "model"}
    assert props["remask_image"] == {"image", "mask_path", "mask_feather_px", "output_dir"}
    assert set(tools["remask_image"].input_schema["required"]) == {"image", "mask_path"}


async def test_image_tool_docstrings_guide_the_caller(session):
    tools = {t.name: t for t in (await session.list_tools()).tools}
    for name in ("generate_image", "edit_image"):
        desc = tools[name].description
        assert "Call `list_image_models` first to choose a model" in desc
        assert "third-party" in desc and "OK" in desc
    edit = tools["edit_image"].description
    assert 'fit="preserve"' in edit
    assert "white" in edit and "black" in edit
    assert "seam" in edit
    assert "absolute" in edit
    assert "cost" in edit.lower()
    assert ".unmasked.png" in edit and "remask_image" in edit
    remask_doc = tools["remask_image"].description
    assert "seam" in remask_doc and "ghost" in remask_doc
    assert "free" in remask_doc and "offline" in remask_doc
    assert "edit_image" in remask_doc and "mask_path" in remask_doc
    assert "0.2.0" in remask_doc
    assert "absolute" in remask_doc


# ------------------------------------------------------------------ image tools


async def test_edit_image_returns_text_and_images(keyed, session, images_route, sample_render):
    images_route.mock(return_value=images_ok())

    result = await session.call_tool(
        "edit_image", {"prompt": "make it dusk", "model": SUNBURST, "images": [str(sample_render)]}
    )

    assert result.is_error is False
    texts = [c for c in result.content if isinstance(c, TextContent)]
    imgs = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(texts) == 1 and len(imgs) == 1
    assert imgs[0].mime_type == "image/jpeg"
    assert Image.open(BytesIO(base64.b64decode(imgs[0].data))).format == "JPEG"
    text = texts[0].text
    saved = next(sample_render.parent.glob("Sample_Render_v5__*_1.png"))
    assert f"Saved: {saved}  (sidecar: {saved.with_suffix('.json')})" in text
    assert "This call: $" in text
    assert f"Model: {SUNBURST} · Provider: openai · " in text


@pytest.mark.parametrize("session", [True], indirect=True)
async def test_summary_order_notes_failures(keyed, session, images_route, monkeypatch):
    monkeypatch.setattr("openrouter_image_mcp.client._SERVER_ERROR_DELAY", 0)
    flux_pro = "black-forest-labs/flux.2-pro"  # max_n 1 -> one call per image, with a note
    images_route.mock(
        side_effect=[
            images_ok(1024, 1024),
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(500, json={"error": {"message": "boom"}}),
        ]
    )

    result = await session.call_tool(
        "generate_image", {"prompt": "a red barn", "model": flux_pro, "n": 2}
    )

    assert result.is_error is False
    text = text_of(result)
    lines = text.splitlines()
    assert lines[0].startswith("Saved: ")
    i_notes, i_fail = lines.index("Notes:"), lines.index("Failures:")
    i_model = next(i for i, ln in enumerate(lines) if ln.startswith("Model: "))
    assert 0 < i_notes < i_fail < i_model
    assert lines[i_notes + 1] == "- model max n is 1; will make 2 calls"
    assert lines[i_fail + 1].startswith("- ") and "HTTP 500" in lines[i_fail + 1]
    assert re.fullmatch(rf"Model: {re.escape(flux_pro)} · Provider: openai · \d+s", lines[i_model])
    assert lines[i_model + 1].startswith("This call: $")
    assert len([c for c in result.content if isinstance(c, ImageContent)]) == 1
    data = result.structured_content
    assert len(data["images"]) == 1
    assert "HTTP 500" in data["failures"][0]
    assert data["notes"][0] == "model max n is 1; will make 2 calls"


@pytest.mark.parametrize("session", [True], indirect=True)
async def test_generate_without_preview_has_only_text(keyed, session, images_route):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>'
    images_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-2",
                "provider": "recraft",
                "data": [{"b64_json": base64.b64encode(svg).decode(), "media_type": "image/svg+xml"}],
                "usage": {"cost": 0.04},
            },
        )
    )

    result = await session.call_tool(
        "generate_image", {"prompt": "logo", "model": "recraft/recraft-v4-vector"}
    )

    assert result.is_error is False
    assert [type(c) for c in result.content] == [TextContent]
    assert result.structured_content["images"][0]["dataUri"] is None
    assert result.structured_content["images"][0]["filename"].endswith(".svg")


async def test_progress_is_forwarded(keyed, session, images_route, sample_render):
    images_route.mock(return_value=images_ok())
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    result = await session.call_tool(
        "edit_image",
        {"prompt": "x", "model": SUNBURST, "images": [str(sample_render)]},
        progress_callback=on_progress,
    )

    assert result.is_error is False
    assert (1.0, 1.0, "Finished call 1 of 1") in seen


async def test_progress_guard_keeps_values_strictly_increasing():
    sent = []

    class Ctx:
        async def report_progress(self, progress, total=None, message=None):
            sent.append((progress, total, message))

    report = server_mod._progress(Ctx())
    for value in (0.5, 0.5, 0.4, 1.0):
        await report(value, 2, "m")

    values = [v for v, _, _ in sent]
    assert all(b > a for a, b in pairwise(values)), values
    assert values[0] == 0.5 and values[-1] == 1.0
    assert all(t == 2 for _, t, _ in sent)


def _halves_mask(path, w=1920, h=828, left=True):
    mask = Image.new("L", (w, h), 0)
    mask.paste(255, (0, 0, w // 2, h) if left else (w // 2, 0, w, h))
    return save(mask, path, "PNG")


@pytest.mark.parametrize("session", [True], indirect=True)
async def test_remask_image_round_trip(keyed, session, mock, images_route, sample_render, tmp_path):
    images_route.mock(return_value=images_ok())
    first_mask = _halves_mask(tmp_path / "mask-right.png", left=False)
    edited = await session.call_tool(
        "edit_image",
        {"prompt": "x", "model": SUNBURST, "images": [str(sample_render)],
         "mask_path": str(first_mask)},
    )
    assert edited.is_error is False
    source = next(sample_render.parent.glob("Sample_Render_v5__*_1.png"))
    assert source.with_suffix(".unmasked.png").is_file()
    calls_before = mock.calls.call_count

    new_mask = _halves_mask(tmp_path / "mask-left.png", left=True)
    result = await session.call_tool(
        "remask_image",
        {"image": str(source), "mask_path": str(new_mask), "mask_feather_px": 0},
    )

    assert result.is_error is False
    assert mock.calls.call_count == calls_before  # nothing sent anywhere
    saved = next(sample_render.parent.glob(f"{source.stem}__remask_*_1.png"))
    text = text_of(result)
    lines = text.splitlines()
    assert lines[0] == f"Saved: {saved}  (sidecar: {saved.with_suffix('.json')})"
    assert lines[1] == "Cost: $0.00 (local re-blend, nothing uploaded)"
    imgs = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(imgs) == 1 and imgs[0].mime_type == "image/jpeg"
    assert Image.open(BytesIO(base64.b64decode(imgs[0].data))).format == "JPEG"
    data = result.structured_content
    assert data["callCostUsd"] == 0
    assert data["model"] == "Local re-blend"
    assert data["images"][0]["path"] == str(saved)
    assert data["images"][0]["dataUri"] == "data:image/jpeg;base64," + imgs[0].data
    out = Image.open(saved).convert("RGB")
    layer = Image.open(source.with_suffix(".unmasked.png")).convert("RGB")
    original = Image.open(sample_render).convert("RGB")
    left, right = (0, 0, 960, 828), (960, 0, 1920, 828)
    assert out.crop(left).tobytes() == layer.crop(left).tobytes()
    assert out.crop(right).tobytes() == original.crop(right).tobytes()


async def test_remask_image_errors_are_tool_errors(keyed, session, images_route, sample_render, tmp_path):
    images_route.mock(return_value=images_ok())
    edited = await session.call_tool(
        "edit_image", {"prompt": "x", "model": SUNBURST, "images": [str(sample_render)]}
    )
    assert edited.is_error is False
    plain = next(sample_render.parent.glob("Sample_Render_v5__*_1.png"))
    mask = _halves_mask(tmp_path / "mask.png")

    result = await session.call_tool("remask_image", {"image": str(plain), "mask_path": str(mask)})
    assert error_text(result) == (
        "This image wasn't made with a mask, so there is no unblended layer to re-mask."
    )

    result = await session.call_tool(
        "remask_image", {"image": "relative/pic.png", "mask_path": str(mask)}
    )
    assert "absolute" in error_text(result)

    for unc in unc_paths("pic.png"):
        result = await session.call_tool("remask_image", {"image": unc, "mask_path": str(mask)})
        assert "Network" in error_text(result), unc

    result = await session.call_tool(
        "remask_image", {"image": str(tmp_path / "nope.png"), "mask_path": str(mask)}
    )
    assert "not found" in error_text(result)


# ------------------------------------------------------------------ errors


async def test_edit_image_mixed_separator_unc_is_tool_error(keyed, session, mock):
    result = await session.call_tool(
        "edit_image", {"prompt": "x", "model": SUNBURST, "images": [r"\/evil-host/share/pic.png"]}
    )
    assert "Network" in error_text(result)
    assert mock.calls.call_count == 0  # rejected before the catalog or any generation


async def test_errors_are_tool_errors(memory_keyring, session, images_route, sample_render):
    # No key stored -> AuthRequiredError
    result = await session.call_tool("generate_image", {"prompt": "x", "model": SUNBURST})
    assert result.is_error is True
    assert "auth_login" in text_of(result)

    keystore.set_key(SECRET)
    images_route.mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "flagged",
                    "metadata": {"reasons": ["violence"], "provider_name": "OpenAI"},
                }
            },
        )
    )
    result = await session.call_tool("generate_image", {"prompt": "x", "model": SUNBURST})
    assert error_text(result).startswith("Blocked by OpenAI moderation")


async def test_input_error_is_tool_error(keyed, session, images_route):
    result = await session.call_tool(
        "edit_image", {"prompt": "x", "model": SUNBURST, "images": ["relative/pic.jpg"]}
    )
    assert result.is_error is True
    assert "absolute" in text_of(result)
    assert images_route.call_count == 0


async def test_service_rulings_surface_as_tool_errors(keyed, session, images_route, sample_render):
    result = await session.call_tool(
        "edit_image",
        {"prompt": "x", "model": SUNBURST, "images": [str(sample_render)], "aspect_ratio": "1:1"},
    )
    assert result.is_error is True
    assert "fit='model'" in text_of(result)
    assert images_route.call_count == 0


async def test_provider_options_slug_keyed_and_checked(keyed, session, images_route):
    images_route.mock(return_value=images_ok())
    bad = await session.call_tool(
        "generate_image",
        {"prompt": "x", "model": SUNBURST, "provider_options": {"style": "vivid"}},
    )
    assert "Allowed: moderation" in error_text(bad)
    assert images_route.call_count == 0

    good = await session.call_tool(
        "generate_image",
        {"prompt": "x", "model": SUNBURST, "provider_options": {"moderation": "low"}},
    )
    assert good.is_error is False
    sent = json.loads(images_route.calls[0].request.content)
    assert sent["provider"] == {"options": {"openai": {"moderation": "low"}}}


async def test_unknown_model_is_tool_error(session):
    result = await session.call_tool("get_image_model", {"model_id": "openai/gpt-image-2.5-sunbrst"})
    assert result.is_error is True
    assert "Did you mean" in text_of(result) and SUNBURST in text_of(result)


async def test_catalog_unavailable_is_clean_error(memory_keyring, settings, http):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").mock(side_effect=httpx.ConnectError("network down"))
        m.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("network down"))
        async with Client(build_server(settings, http), mode="legacy") as c:
            result = await c.call_tool("list_image_models", {})
    assert error_text(result).lower().startswith("couldn't reach the openrouter catalog")


async def test_catalog_error_message_always_names_the_catalog(memory_keyring, settings, http):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/images/models").respond(200, text="<html>not json</html>")
        m.get(f"{BASE}/models").respond(200, json=fixture("models_image.json"))
        async with Client(build_server(settings, http), mode="legacy") as c:
            result = await c.call_tool("list_image_models", {})
    assert error_text(result).startswith("Couldn't reach the OpenRouter catalog")


# ------------------------------------------------------------------ discovery


async def test_list_image_models_table(session, http):
    result = await session.call_tool("list_image_models", {"accepts_images": True, "sort": "price"})

    assert result.is_error is False
    text = text_of(result)
    rows = table_rows(text)
    expected = sorted(
        (m for m in await Catalog(http).all() if m.accepts_images), key=price_key
    )
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


async def test_list_image_models_filters_and_price_fallbacks(session):
    text = text_of(await session.call_tool("list_image_models", {"author": "openrouter"}))
    rows = table_rows(text)
    assert {r[0] for r in rows} == {"openrouter/auto", "openrouter/auto-beta"}
    assert all(r[9] == "—" for r in rows)  # "-1" is variable pricing, not a price

    text = text_of(await session.call_tool("list_image_models", {"query": "QWEN-IMAGE-3"}))
    rows = table_rows(text)
    assert [r[0] for r in rows] == ["qwen/qwen-image-3-pro", "qwen/qwen-image-3"]
    assert rows[0][7] == "yes"

    text = text_of(await session.call_tool("list_image_models", {"accepts_images": False}))
    rows = table_rows(text)
    assert rows and all(r[2] == "text" for r in rows)

    text = text_of(await session.call_tool("list_image_models", {"query": "Recraft V4.1 Flash"}))
    assert table_rows(text)[0][0] == "recraft/recraft-v4.1-flash"  # matches the name too


async def test_list_image_models_newest_is_catalog_order(session, http):
    rows = table_rows(text_of(await session.call_tool("list_image_models", {})))
    assert [r[0] for r in rows] == [m.id for m in await Catalog(http).all()]


async def test_list_image_models_no_match(session):
    result = await session.call_tool("list_image_models", {"query": "zzz-nothing"})
    assert result.is_error is False
    assert "No image models match" in text_of(result)


async def test_get_image_model(session):
    result = await session.call_tool("get_image_model", {"model_id": SUNBURST})

    assert result.is_error is False
    text = text_of(result)
    assert SUNBURST in text
    assert "OpenAI" in text
    assert "quality: auto, low, medium, high, xhigh, max" in text
    assert "n: 1–10" in text
    assert "moderation" in text  # allowed passthrough parameter
    assert "output_image" in text and "$0.00003/token" in text


async def test_get_image_model_chat_route_skips_endpoints(session, mock):
    result = await session.call_tool("get_image_model", {"model_id": "openrouter/auto"})

    assert result.is_error is False
    assert "chat completions" in text_of(result)
    assert not [c for c in mock.calls if "/endpoints" in str(c.request.url)]


# ------------------------------------------------------------------ account


async def test_account_status_signed_out(memory_keyring, session):
    result = await session.call_tool("account_status", {})
    assert result.is_error is False
    text = text_of(result)
    assert "Not signed in" in text
    assert "auth_login" in text


async def test_account_status_signed_in(keyed, session):
    text = text_of(await session.call_tool("account_status", {}))
    assert "openrouter-image-mcp (test-host)" in text
    assert "$1.84" in text and "$2.10" in text and "$12.40" in text and "$3.50" in text
    assert "$50.00" in text and "$37.60" in text


async def test_auth_login_already_signed_in(keyed, session):
    text = text_of(await session.call_tool("auth_login", {}))
    assert "Already signed in" in text


async def test_auth_login_pending_then_status(memory_keyring, session, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)

    text = text_of(await session.call_tool("auth_login", {"switch_account": False}))

    assert len(opened) == 1 and opened[0].startswith("https://openrouter.ai/auth?")
    assert "Finish signing in" in text and opened[0] in text
    status = text_of(await session.call_tool("account_status", {}))
    assert "Finish signing in" in status


async def test_auth_logout_message_links_dashboard(keyed, session):
    result = await session.call_tool("auth_logout", {})
    assert result.is_error is False
    assert text_of(result).endswith(
        "Signed out of OpenRouter on this computer. To revoke the key itself, "
        "delete it at https://openrouter.ai/settings/keys"
    )
    assert keystore.get_key() is None


async def test_no_key_in_any_result(keyed, session, mock, images_route, sample_render):
    # Worst case: OpenRouter echoes the key in a label and in an error message.
    mock.get(f"{BASE}/key").respond(200, json={"data": {**KEY_INFO, "label": f"key {SECRET}"}})
    images_route.mock(
        side_effect=[
            images_ok(),
            images_ok(1024, 1024),
            httpx.Response(400, json={"error": {"message": f"bad key {SECRET}"}}),
        ]
    )
    calls = [
        ("account_status", {}),
        ("auth_login", {}),
        ("list_image_models", {}),
        ("get_image_model", {"model_id": SUNBURST}),
        ("edit_image", {"prompt": "x", "model": SUNBURST, "images": [str(sample_render)]}),
        ("generate_image", {"prompt": "x", "model": SUNBURST}),
        ("generate_image", {"prompt": "x", "model": SUNBURST}),
        ("auth_logout", {}),
    ]
    outputs = []
    for name, args in calls:
        result = await session.call_tool(name, args)
        outputs.append(result.model_dump_json())
    assert all(SECRET not in out for out in outputs)
    assert "sk-or-***" in outputs[0]


# ------------------------------------------------------------------ run()


def test_run_uses_stdio_and_prints_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(server_mod, "configure_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(
        MCPServer, "run", lambda self, transport="stdio", **kw: calls.append((self.name, transport))
    )

    monkeypatch.setenv("OPENROUTER_IMAGE_COHORT", "2027")
    monkeypatch.delenv("OPENROUTER_IMAGE_WORKSPACE_ID", raising=False)
    server_mod.run()

    assert calls == ["logging", ("openrouter-sidecar", "stdio")]
    assert capsys.readouterr().out == ""


# ------------------------------------------------------- final fix wave


async def test_auth_logout_cancels_pending_sign_in(memory_keyring, session, mock, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    mock.post(f"{BASE}/auth/keys").respond(200, json={"key": SECRET})
    mock.route(host="127.0.0.1").pass_through()

    await session.call_tool("auth_login", {})
    query = {k: v[0] for k, v in parse_qs(urlparse(opened[0]).query).items()}
    await session.call_tool("auth_logout", {})

    status = text_of(await session.call_tool("account_status", {}))
    assert "Not signed in" in status and "Finish signing in" not in status
    try:  # a late browser callback must not sign in
        async with httpx.AsyncClient(timeout=5) as http:
            await http.get(query["callback_url"], params={"code": "abc", "state": query["state"]})
    except httpx.TransportError:
        pass  # listener already closed
    await asyncio.sleep(0.2)
    assert keystore.get_key() is None
    assert "Not signed in" in text_of(await session.call_tool("account_status", {}))


class BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("locked")


async def test_keyring_error_is_tool_error(memory_keyring, session):
    keyring.set_keyring(BrokenKeyring())  # memory_keyring fixture restores afterwards
    for name in ("account_status", "auth_logout"):
        message = error_text(await session.call_tool(name, {}))
        assert message.splitlines()[-1] == "Couldn't access the OS credential store: locked"


async def test_output_dir_error_is_tool_error(keyed, session, images_route, monkeypatch):
    def unwritable(directory):
        raise PermissionError(13, "Permission denied", str(directory))

    monkeypatch.setattr("openrouter_image_mcp.outputs._probe_writable", unwritable)
    result = await session.call_tool("generate_image", {"prompt": "x", "model": SUNBURST})
    message = error_text(result)
    assert "Couldn't write to the output folder" in message
    assert "OPENROUTER_IMAGE_OUTPUT_DIR" in message
    assert images_route.call_count == 0


async def test_listener_bind_error_is_tool_error(memory_keyring, session, monkeypatch):
    def no_bind(*args, **kwargs):
        raise OSError("[WinError 10013] access forbidden")

    monkeypatch.setattr("openrouter_image_mcp.auth._CallbackServer", no_bind)
    message = error_text(await session.call_tool("auth_login", {}))
    assert "Couldn't start the local sign-in listener" in message and "10013" in message


async def test_account_status_usage_unavailable(keyed, session, mock):
    mock.get(f"{BASE}/key").respond(403, json={"error": {"message": "key info disabled"}})
    result = await session.call_tool("account_status", {})
    assert result.is_error is False
    assert text_of(result).splitlines()[-1] == "Signed in to OpenRouter (couldn't fetch usage: key info disabled)"
