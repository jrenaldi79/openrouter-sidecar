"""Advisor turns can carry up to 8 images: asset ids or inline base64, kept in the transcript."""

import base64
import io
import json
import time

import httpx
import pytest
from PIL import Image

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_jobs import AdvisorJobs
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.assets import AssetStore, LocalDirBlobs
from openrouter_image_mcp.errors import BadRequestError

WORKSPACE = "test-workspace"
KEY = "sk-or-v1-synthetic-images"
VISION, TEXT_ONLY = "google/vision-test", "google/text-test"


def png(color="red", size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def reply(text="It is a red square."):
    return {"status": "completed", "usage": {"cost": 0.002}, "output": [{
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}]}]}


@pytest.fixture
async def rig(tmp_path, keys, db_target):
    keys.set(KEY, workspace_id=WORKSPACE)
    seen = []
    models = [
        {"id": VISION, "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": TEXT_ONLY, "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    ]

    async def handle(request):
        seen.append(request)
        if request.url.path.endswith("/models"):
            rows = [row | {"created": int(time.time()) - 86400, "supported_parameters": ["tools"]}
                    for row in models]
            return httpx.Response(200, json={"data": rows})
        if request.url.path.endswith("/responses"):
            events = [{"type": "response.completed", "response": reply()}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        pytest.fail(f"Unexpected provider operation: {request.method} {request.url.path}")

    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(handle))
    credentials = keys.credentials(WORKSPACE)
    assets = AssetStore(db_target, LocalDirBlobs(tmp_path / "blobs"))
    service = AdvisorService(WORKSPACE, AdvisorStore(db_target), client, credentials=credentials,
                             sandbox_default=False, sandbox_allowed=False, assets=assets)
    scope = credentials.resolve()[1]
    yield service, AdvisorJobs(service), assets, scope, seen
    await client.aclose()


def inference(seen):
    return [json.loads(r.content) for r in seen if r.url.path.endswith("/responses")]


def upload(assets, scope, data=None, color="red"):
    asset = assets.put(scope, data or png(color), kind="output", mime="image/png",
                       filename="square.png", width=64, height=64)
    return asset.id


async def finished(jobs, job_id):
    row = await jobs.wait(job_id, 5)
    assert row["status"] == "completed", row.get("error")
    return row


async def test_asset_ids_attach_as_image_parts_on_the_turn(rig):
    service, jobs, assets, scope, seen = rig
    image = upload(assets, scope)
    chat = await service.start(VISION, "Critique")
    started = await jobs.start(chat["chat_id"], "What is in this image?", images=[image])
    await finished(jobs, started["job_id"])
    turn = inference(seen)[-1]["input"][-1]
    assert turn["role"] == "user"
    assert turn["content"][0] == {"type": "input_text", "text": "What is in this image?"}
    part = turn["content"][1]
    assert part["type"] == "input_image"
    assert part["image_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(part["image_url"].split(",", 1)[1]) == png("red")


async def test_follow_up_refers_to_earlier_images_without_resending_ids(rig):
    service, jobs, assets, scope, seen = rig
    image = upload(assets, scope)
    chat = await service.start(VISION, "Critique")
    first = await jobs.start(chat["chat_id"], "Describe it", images=[image])
    await finished(jobs, first["job_id"])
    second = await jobs.start(chat["chat_id"], "Is the square centred?")
    assert (await finished(jobs, second["job_id"]))["status"] == "completed"
    history = inference(seen)[-1]["input"]
    earlier = history[0]["content"]
    assert earlier[1]["type"] == "input_image"
    assert history[-1]["content"] == "Is the square centred?"


async def test_unknown_or_foreign_image_id_fails_before_inference(rig):
    service, jobs, assets, _scope, seen = rig
    other = Scope("someone-else", WORKSPACE)
    foreign = upload(assets, other)
    chat = await service.start(VISION, "Critique")
    for bad in ("img_" + "0" * 32, foreign):
        with pytest.raises(BadRequestError, match=bad):
            await jobs.start(chat["chat_id"], "Look", images=[bad])
    assert inference(seen) == []


async def test_more_than_eight_images_is_refused(rig):
    service, jobs, assets, scope, seen = rig
    ids = [upload(assets, scope, color=c) for c in ("red", "blue", "green", "white", "black",
                                                    "yellow", "cyan", "magenta", "gray")]
    chat = await service.start(VISION, "Critique")
    with pytest.raises(BadRequestError, match="8"):
        await jobs.start(chat["chat_id"], "Look", images=ids)
    assert inference(seen) == []


async def test_non_vision_model_is_refused_before_inference(rig):
    service, jobs, assets, scope, seen = rig
    image = upload(assets, scope)
    chat = await service.start(TEXT_ONLY, "Plain")
    with pytest.raises(BadRequestError, match="vision"):
        await jobs.start(chat["chat_id"], "Look", images=[image])
    assert inference(seen) == []


async def test_inline_base64_image_is_stored_and_referenced(rig):
    service, jobs, assets, scope, seen = rig
    data_url = "data:image/png;base64," + base64.b64encode(png("red")).decode()
    chat = await service.start(VISION, "Critique")
    started = await jobs.start(chat["chat_id"], "What colour?", images=[data_url])
    await finished(jobs, started["job_id"])
    part = inference(seen)[-1]["input"][-1]["content"][1]
    assert part["image_url"] == data_url
    stored = service.store.get(scope, chat["chat_id"]).history  # transcript keeps the id, not bytes
    assert stored[-2]["images"][0].startswith("img_")
    assert assets.info(scope, stored[-2]["images"][0]).kind == "input"


async def test_inline_value_that_is_not_an_image_is_refused(rig):
    service, jobs, _assets, _scope, seen = rig
    chat = await service.start(VISION, "Critique")
    bogus = "data:image/png;base64," + base64.b64encode(b"not a png").decode()
    with pytest.raises(BadRequestError, match="image"):
        await jobs.start(chat["chat_id"], "Look", images=[bogus])
    assert inference(seen) == []


async def test_list_models_reports_vision_flag(rig):
    service, *_ = rig
    rows = {row["id"]: row for row in await service.models()}
    assert rows[VISION]["vision"] is True
    assert rows[TEXT_ONLY]["vision"] is False


async def test_send_without_images_is_unchanged(rig):
    service, jobs, _assets, _scope, seen = rig
    chat = await service.start(TEXT_ONLY, "Plain")
    started = await jobs.start(chat["chat_id"], "Hello")
    await finished(jobs, started["job_id"])
    assert inference(seen)[-1]["input"][-1]["content"] == "Hello"
