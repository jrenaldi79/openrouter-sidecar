"""Hosted image results: previews ride in _meta for the gallery, never in structuredContent."""

import base64
import json
from types import SimpleNamespace

from mcp.types import ImageContent, TextContent

from openrouter_image_mcp.hosted.image_results import image_result, remask_result

JPEG = b"\xff\xd8\xff" + b"j" * 4000
PREFIX = "data:image/jpeg;base64,"


def _saved(n, jpeg=JPEG, note=None):
    return SimpleNamespace(asset_id="img_" + str(n) * 32, preview_jpeg=jpeg, note=note)


def _link(saved, name):
    return {"asset_id": saved.asset_id, "filename": name,
            "image_url": f"https://sidecar.example/a/{name}", "metadata_url": f"https://sidecar.example/m/{name}"}


def _service_result(images):
    return SimpleNamespace(images=images, notes=["Preview reduced."], failures=["Image 3 failed."],
                           model="openai/gpt-image", provider="openai", elapsed_s=3.2,
                           usage_line="Cost: $0.02", call_cost_usd=0.02)


def _assert_model_safe(result):
    serialized = json.dumps(result.structured_content)
    assert "dataUri" not in serialized and PREFIX not in serialized
    assert base64.b64encode(JPEG).decode()[:40] not in serialized
    assert len(serialized) < 2000


def test_image_result_moves_previews_to_meta_aligned_with_images():
    first, svg = _saved(1), _saved(2, jpeg=None, note="no preview for SVG")
    links = {first.asset_id: _link(first, "barn_1.png"), svg.asset_id: _link(svg, "barn_2.svg")}
    result = image_result(_service_result([first, svg]), links)
    _assert_model_safe(result)
    images = result.structured_content["images"]
    assert images[0] == {"assetId": first.asset_id, "filename": "barn_1.png",
                         "downloadUrl": "https://sidecar.example/a/barn_1.png",
                         "metadataUrl": "https://sidecar.example/m/barn_1.png"}
    assert [image["assetId"] for image in images] == [first.asset_id, svg.asset_id]
    assert result.structured_content["notes"] == ["Preview reduced."]
    assert result.structured_content["failures"] == ["Image 3 failed."]
    assert result.meta == {"sidecarPreviews": [PREFIX + base64.b64encode(JPEG).decode(), None]}


def test_image_result_keeps_text_lines_and_image_blocks():
    first = _saved(1)
    result = image_result(_service_result([first]), {first.asset_id: _link(first, "barn_1.png")})
    text, *blocks = result.content
    assert isinstance(text, TextContent)
    assert f"Asset: {first.asset_id}  (barn_1.png)" in text.text
    assert "Download: https://sidecar.example/a/barn_1.png" in text.text
    assert "Model: openai/gpt-image" in text.text and "Download links work for 24 hours" in text.text
    assert [type(block) for block in blocks] == [ImageContent]
    assert blocks[0].data == base64.b64encode(JPEG).decode()


def test_remask_result_moves_preview_to_meta():
    saved = _saved(3)
    result = remask_result(saved, {saved.asset_id: _link(saved, "barn_remask.png")})
    _assert_model_safe(result)
    assert result.structured_content["images"][0]["assetId"] == saved.asset_id
    assert result.structured_content["callCostUsd"] == 0
    assert result.meta == {"sidecarPreviews": [PREFIX + base64.b64encode(JPEG).decode()]}
    assert "Cost: $0.00" in result.content[0].text and isinstance(result.content[1], ImageContent)


def test_meta_serializes_as_wire_meta():
    saved = _saved(3)
    wire = remask_result(saved, {saved.asset_id: _link(saved, "x.png")}).model_dump(by_alias=True,
                                                                                   exclude_none=True)
    assert wire["_meta"]["sidecarPreviews"][0].startswith(PREFIX)
    assert "dataUri" not in json.dumps(wire["structuredContent"])
