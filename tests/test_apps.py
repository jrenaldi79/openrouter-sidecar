"""Gallery data split: model-safe structured fields, previews only in the result's _meta."""

import base64
from pathlib import Path
from types import SimpleNamespace

from openrouter_image_mcp.apps import (
    PREVIEWS_META,
    UI_EXTENSION,
    UI_MIME,
    negotiate_apps,
    preview_image,
    preview_meta,
)

PREFIX = "data:image/jpeg;base64,"


def test_preview_image_carries_no_image_bytes():
    path = Path("/tmp/out/render_1.png")
    assert preview_image(path) == {"filename": "render_1.png", "path": str(path)}


def test_preview_meta_is_index_aligned_with_null_for_missing_previews():
    assert PREVIEWS_META == "sidecarPreviews"
    assert preview_meta([b"jpg", None]) == {PREVIEWS_META: [PREFIX + base64.b64encode(b"jpg").decode(), None]}
    assert preview_meta(jpeg for jpeg in [b"a"]) == {PREVIEWS_META: [PREFIX + "YQ=="]}


def _ctx(apps: bool, name="generate_image"):
    extensions = {UI_EXTENSION: {"mimeTypes": [UI_MIME]}} if apps else {}
    session = SimpleNamespace(client_capabilities=SimpleNamespace(extensions=extensions))
    return SimpleNamespace(session=session, method="tools/call", params={"name": name})


def _wire(meta):
    return {"content": [{"type": "text", "text": "Saved"}], "structuredContent": {"images": []}, "_meta": meta}


async def _negotiate(apps, wire, name="generate_image"):
    async def call_next(ctx):
        return wire
    return await negotiate_apps(_ctx(apps, name), call_next)


async def test_apps_clients_keep_previews_in_meta():
    wire = _wire({PREVIEWS_META: [PREFIX + "YQ=="]})
    assert await _negotiate(True, wire) == wire


async def test_clients_without_apps_get_neither_structured_content_nor_previews():
    result = await _negotiate(False, _wire({PREVIEWS_META: [PREFIX + "YQ=="]}))
    assert result == {"content": [{"type": "text", "text": "Saved"}]}


async def test_clients_without_apps_keep_other_meta():
    result = await _negotiate(False, _wire({PREVIEWS_META: [None], "other": 1}))
    assert result["_meta"] == {"other": 1} and "structuredContent" not in result


async def test_other_tools_pass_through_for_clients_without_apps():
    wire = _wire({PREVIEWS_META: []})
    assert await _negotiate(False, wire, name="get_image_download") == wire
