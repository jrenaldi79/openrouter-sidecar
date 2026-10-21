"""Function-call tool loop over the Responses API, replaying output items."""

import asyncio
import copy
import json

import httpx
import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_loop import (
    FINAL_NOTE,
    MAX_ROUNDS,
    LoopStopped,
    run_tool_loop,
)

KEY = "sk-or-v1-alice"
SEARCH = {"type": "openrouter:web_search", "id": "ws_1", "status": "completed"}


def call(name="lookup", call_id="call_1", args="{}"):
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": args}


def body(*items, cost=0.01):
    out = {"status": "completed", "output": list(items)}
    if cost is not None:
        out["usage"] = {"cost": cost}
    return out


def answer(text="done"):
    return {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


class Script:
    def __init__(self, *bodies):
        self.bodies, self.requests = list(bodies), []
        self.client = AdvisorClient("ws", 30, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self.bodies.pop(0))

    def inputs(self):
        return [r["input"] for r in self.requests]


def payload():
    return {"model": "m", "tools": [{"type": "function", "name": "lookup"}],
            "input": [{"type": "message", "role": "user", "content": "hi"}]}


async def echo(name, arguments):
    return f"{name}:{arguments}"


async def test_no_calls_single_round():
    script = Script(body(answer()))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    assert result.rounds == 1 and len(script.requests) == 1
    assert result.body["output"] == [answer()]
    assert result.items == [answer()]


async def test_function_call_then_answer():
    script = Script(body(call(args='{"q":1}')), body(answer()))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    second = script.inputs()[1]
    assert second[-2] == call(args='{"q":1}')
    assert second[-1] == {"type": "function_call_output", "call_id": "call_1",
                          "output": 'lookup:{"q":1}'}
    assert result.rounds == 2
    assert result.items == [call(args='{"q":1}'), answer()]


async def test_server_items_replayed_verbatim_and_not_run():
    script = Script(body(SEARCH, call()), body(answer()))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    second = script.inputs()[1]
    assert second[1] == SEARCH and second[2] == call()
    outputs = [i for i in second if i["type"] == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["call_1"]
    assert SEARCH in result.items


async def test_round_cap_forces_answer():
    script = Script(*[body(call(call_id=f"c{i}")) for i in range(MAX_ROUNDS)], body(answer()))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    assert len(script.requests) == MAX_ROUNDS + 1
    assert all("tool_choice" not in r for r in script.requests[:MAX_ROUNDS])
    final = script.requests[-1]
    assert final["tool_choice"] == "none" and final["tools"] == payload()["tools"]
    assert final["input"][-1] == {"type": "message", "role": "user", "content": FINAL_NOTE}
    assert final["input"][-2]["type"] == "function_call_output"
    assert result.body["output"] == [answer()]


async def test_tool_error_output_is_type_name_only():
    async def boom(name, arguments):
        raise ValueError("secret detail sk-or-v1-leak")

    script = Script(body(call()), body(answer()))
    await run_tool_loop(script.client, KEY, payload(), {"lookup": boom})
    assert script.inputs()[1][-1]["output"] == "Error: ValueError"


async def test_unknown_tool_gets_error_output_and_is_not_run():
    ran = []

    async def lookup(name, arguments):
        ran.append(name)
        return "ok"

    script = Script(body(call("lookup", "a"), call("evil", "b")), body(answer()))
    await run_tool_loop(script.client, KEY, payload(), {"lookup": lookup})
    outputs = {i["call_id"]: i["output"] for i in script.inputs()[1]
               if i["type"] == "function_call_output"}
    assert outputs == {"a": "ok", "b": "Error: unknown tool"} and ran == ["lookup"]


async def test_only_unknown_calls_still_continue():
    script = Script(body(call("evil")), body(answer()))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    assert result.rounds == 2
    assert script.inputs()[1][-1]["output"] == "Error: unknown tool"


async def test_stop_at_round_boundary():
    script = Script(body(call()), body(call()), body(answer()))
    stops = iter([False, True])
    with pytest.raises(LoopStopped):
        await run_tool_loop(script.client, KEY, payload(), {"lookup": echo},
                            should_stop=lambda: next(stops))
    assert len(script.requests) == 1


async def test_stop_checked_before_first_and_forced_final_call():
    script = Script(body(answer()))
    with pytest.raises(LoopStopped):
        await run_tool_loop(script.client, KEY, payload(), {}, should_stop=lambda: True)
    assert script.requests == []

    script = Script(body(call()))
    flags = iter([False, True])
    with pytest.raises(LoopStopped):
        await run_tool_loop(script.client, KEY, payload(), {"lookup": echo}, max_rounds=1,
                            should_stop=lambda: next(flags))
    assert len(script.requests) == 1


async def test_cancellation_propagates():
    async def cancel(name, arguments):
        raise asyncio.CancelledError

    script = Script(body(call()), body(answer()))
    with pytest.raises(asyncio.CancelledError):
        await run_tool_loop(script.client, KEY, payload(), {"lookup": cancel})


async def test_cost_summed_including_forced_final():
    script = Script(body(call(), cost=0.25), body(call(), cost=1), body(answer(), cost=0.5))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo}, max_rounds=2)
    assert result.cost_usd == pytest.approx(1.75) and len(script.requests) == 3


@pytest.mark.parametrize("bad", [None, True, "0.1"])
async def test_cost_none_when_any_round_lacks_numeric_cost(bad):
    first = body(call())
    if bad is None:
        del first["usage"]
    else:
        first["usage"]["cost"] = bad
    script = Script(first, body(answer(), cost=0.5))
    result = await run_tool_loop(script.client, KEY, payload(), {"lookup": echo})
    assert result.cost_usd is None


async def test_on_items_called_per_round_before_functions_run():
    order = []

    async def on_items(items):
        order.append(("items", [i["type"] for i in items]))

    async def lookup(name, arguments):
        order.append(("run", name))
        return "ok"

    script = Script(body(SEARCH, call()), body(answer()))
    await run_tool_loop(script.client, KEY, payload(), {"lookup": lookup}, on_items=on_items)
    assert order == [("items", ["openrouter:web_search", "function_call"]),
                     ("run", "lookup"), ("items", ["message"])]


async def test_caller_payload_not_mutated_and_string_input_converted():
    original = payload()
    snapshot = copy.deepcopy(original)
    script = Script(body(call()), body(answer()))
    await run_tool_loop(script.client, KEY, original, {"lookup": echo})
    assert original == snapshot

    as_string = {"model": "m", "input": "hello"}
    script = Script(body(call()), body(answer()))
    await run_tool_loop(script.client, KEY, as_string, {"lookup": echo})
    assert as_string == {"model": "m", "input": "hello"}
    user = {"type": "message", "role": "user", "content": "hello"}
    assert script.inputs()[0] == [user]
    assert script.inputs()[1][0] == user
