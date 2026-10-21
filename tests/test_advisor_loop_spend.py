"""The tool loop's ways out carry the spend so far: a provider failure and a cancellation."""

import asyncio
import json

import httpx
import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_loop import run_tool_loop
from openrouter_image_mcp.errors import ProviderError

KEY = "sk-or-v1-alice"
CALL = {"type": "function_call", "name": "lookup", "call_id": "call_1", "arguments": "{}"}
FIRST = {"status": "completed", "output": [CALL], "usage": {"cost": 0.25}}
PAYLOAD = {"model": "m", "input": "hi", "tools": [{"type": "function", "name": "lookup"}]}


async def echo(name, arguments):
    return "ok"


def client(second):
    seen = []

    async def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=FIRST) if len(seen) == 1 else await second()

    return AdvisorClient("ws", 30, transport=httpx.MockTransport(handle)), seen


async def test_provider_error_carries_items_and_cost():
    async def fail():
        return httpx.Response(500)

    advisor, _ = client(fail)
    with pytest.raises(ProviderError) as caught:
        await run_tool_loop(advisor, KEY, PAYLOAD, {"lookup": echo})
    assert caught.value.items == [CALL] and caught.value.cost_usd == pytest.approx(0.25)


async def test_cancellation_carries_items_and_cost():
    never = asyncio.Event()

    async def hang():
        await never.wait()

    advisor, seen = client(hang)
    task = asyncio.create_task(run_tool_loop(advisor, KEY, PAYLOAD, {"lookup": echo}))
    while len(seen) < 2:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.items == [CALL] and caught.value.cost_usd == pytest.approx(0.25)


async def test_any_unexpected_error_carries_items_and_cost():
    async def answer():
        return httpx.Response(200, json={"status": "completed", "output": [], "usage": {"cost": 0.5}})

    async def broken(output):
        if not output:
            raise RuntimeError("callback failed")

    advisor, _ = client(answer)
    with pytest.raises(RuntimeError) as caught:
        await run_tool_loop(advisor, KEY, PAYLOAD, {"lookup": echo}, on_items=broken)
    assert caught.value.items == [CALL] and caught.value.cost_usd == pytest.approx(0.75)
