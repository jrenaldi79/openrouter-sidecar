"""Database work while an advisor answer streams: throttled, never losing the last partial."""

import asyncio
import time

import httpx
import pytest

from openrouter_image_mcp import keystore, storage
from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_jobs import AdvisorJobs
from openrouter_image_mcp.advisor_store import AdvisorStore
from tests.advisor_streams import event

WORKSPACE = "stream-workspace"
KEY = "sk-or-v1-stream-account"
MODEL = "openai/test-advisor"
FAIL = object()


class QueuedStream(httpx.AsyncByteStream):
    """Yields one delta per queued item; `idle` is set once the job has handled the last one."""

    def __init__(self, state):
        self.state = state

    async def __aiter__(self):
        yield event("response.output_item.added", output_index=0, item={
            "type": "message", "role": "assistant", "phase": "final_answer"})
        text = ""
        while True:
            self.state["idle"].set()
            item = await self.state["queue"].get()
            self.state["idle"].clear()
            if item is FAIL:
                return
            if item is None:
                yield event("response.completed", response={"status": "completed", "output": [
                    {"type": "message", "role": "assistant", "phase": "final_answer",
                     "content": [{"type": "output_text", "text": text}]}]})
                return
            text += item
            yield event("response.output_text.delta", output_index=0, delta=item)


@pytest.fixture
async def rig(memory_keyring, db_target, monkeypatch):
    keystore.set_key(KEY, workspace_id=WORKSPACE)
    state = {"queue": asyncio.Queue(), "idle": asyncio.Event(), "connections": 0, "now": 1000.0}

    async def provider(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL, "created": int(time.time()) - 86400,
                "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools"]}]})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, stream=QueuedStream(state),
                              headers={"content-type": "text/event-stream"})

    real_connect = storage.connect

    def counting_connect(target):
        state["connections"] += 1
        return real_connect(target)

    monkeypatch.setattr(storage, "connect", counting_connect)
    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(provider))
    service = AdvisorService(WORKSPACE, AdvisorStore(db_target), client)
    chat = await service.start(MODEL, "Critic")
    jobs = AdvisorJobs(service, clock=lambda: state["now"])
    try:
        yield jobs, chat["chat_id"], state
    finally:
        await jobs.close()
        await client.aclose()


async def push(state, item):
    state["idle"].clear()
    await state["queue"].put(item)
    if item is not None and item is not FAIL:
        await state["idle"].wait()


async def settled(jobs, job_id):
    for _ in range(200):
        status = jobs.status(job_id)
        if status["status"] not in ("queued", "running"):
            return status
        await asyncio.sleep(0.01)
    pytest.fail("Job did not settle")


async def streaming(jobs, chat_id, state):
    job = await jobs.start(chat_id, "Prompt")
    await state["idle"].wait()  # the request is open and the job is waiting for deltas
    return job["job_id"]


async def test_many_chunks_cost_a_bounded_number_of_connections(rig):
    jobs, chat_id, state = rig
    job_id = await streaming(jobs, chat_id, state)
    await push(state, "first ")
    before = state["connections"]
    for index in range(200):
        await push(state, f"chunk{index} ")
    # Previously ~4 fresh connections per chunk (800 here); now none between saves.
    assert state["connections"] - before <= 2
    await push(state, None)
    assert (await settled(jobs, job_id))["status"] == "completed"


async def test_running_job_shows_growing_partial_text_at_the_save_cadence(rig):
    jobs, chat_id, state = rig
    job_id = await streaming(jobs, chat_id, state)
    await push(state, "One ")
    assert jobs.view(job_id)["answer"] == "One "  # the first chunk is saved at once
    await push(state, "two ")
    assert jobs.view(job_id)["answer"] == "One "  # within the window: held in memory
    state["now"] += 1.0
    await push(state, "three")
    view = jobs.view(job_id)
    assert view["answer"] == "One two three" and view["partial"] is True
    await push(state, None)
    assert (await settled(jobs, job_id))["status"] == "completed"
    assert jobs.view(job_id)["answer"] == "One two three"


async def test_failure_saves_the_unsaved_tail(rig):
    jobs, chat_id, state = rig
    job_id = await streaming(jobs, chat_id, state)
    for chunk in ("Alpha ", "beta ", "gamma"):
        await push(state, chunk)
    await push(state, FAIL)
    status = await settled(jobs, job_id)
    assert status["status"] == "failed"
    assert jobs.view(job_id)["answer"] == "Alpha beta gamma"


async def test_interruption_saves_the_unsaved_tail(rig):
    jobs, chat_id, state = rig
    job_id = await streaming(jobs, chat_id, state)
    for chunk in ("Alpha ", "beta ", "gamma"):
        await push(state, chunk)
    await jobs.close()
    assert jobs.status(job_id)["status"] == "interrupted"
    assert jobs.view(job_id)["answer"] == "Alpha beta gamma"


async def test_credential_switch_still_withholds_the_unsaved_tail(rig):
    jobs, chat_id, state = rig
    job_id = await streaming(jobs, chat_id, state)
    for chunk in ("Alpha ", "beta "):
        await push(state, chunk)
    keystore.set_key("sk-or-v1-other-account", workspace_id=WORKSPACE)
    await push(state, None)
    await asyncio.sleep(0.05)
    keystore.set_key(KEY, workspace_id=WORKSPACE)
    assert (await settled(jobs, job_id))["status"] == "interrupted"
    assert jobs.view(job_id)["answer"] == ""


async def test_heartbeat_saves_text_held_back_by_a_stalled_stream(rig, monkeypatch):
    jobs, chat_id, state = rig
    monkeypatch.setattr("openrouter_image_mcp.advisor_jobs.HEARTBEAT_S", 0.6)  # beat every 0.1 s
    job_id = await streaming(jobs, chat_id, state)
    await push(state, "One ")
    await push(state, "two")  # held: the save window has not passed and no chunk follows
    for _ in range(100):
        if jobs.view(job_id)["answer"] == "One two":
            break
        await asyncio.sleep(0.02)
    assert jobs.view(job_id)["answer"] == "One two"


async def test_a_save_cancelled_mid_write_leaves_its_text_for_the_failure_update(rig, monkeypatch):
    jobs, chat_id, state = rig
    jobs.service.client._timeout_s = 1.0  # the stream's total budget, fixed when it opens
    job_id = await streaming(jobs, chat_id, state)
    await push(state, "Saved ")
    state["now"] += 1.0

    async def stalled_to_thread(function, *args):
        await asyncio.Event().wait()  # the stream timeout cancels the job during this save

    monkeypatch.setattr("openrouter_image_mcp.advisor_jobs.asyncio.to_thread", stalled_to_thread)
    await state["queue"].put("tail")
    status = await settled(jobs, job_id)
    assert status["status"] == "failed"
    assert jobs.view(job_id)["answer"] == "Saved tail"
