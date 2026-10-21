"""Function-call tool loop over the OpenRouter Responses API.

OpenRouter rejects `previous_response_id` and `store: true`, so every round replays the
response's output items (server tool items, reasoning and messages included, verbatim) in
`input`, followed by one `function_call_output` per call, and sends the request again.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .advisor_client import AdvisorClient
from .errors import OpenRouterError, ProviderError

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
FINAL_NOTE = "You have used every tool round. Answer now with what you have."

FunctionRunner = Callable[[str, str], Awaitable[str]]
# Called with each response's output items as they arrive, before any function runs.
ItemsCallback = Callable[[list[dict]], Awaitable[None]]


class LoopStopped(Exception):
    """`should_stop()` was set before a request, or a function raised it.

    `items` and `cost_usd` cover the requests made before the stop (cost is None when
    any of them lacked a numeric cost).
    """

    def __init__(self, items: list[dict] | None = None, cost_usd: float | None = None):
        super().__init__("Tool loop stopped.")
        self.items = items if items is not None else []
        self.cost_usd = cost_usd


@dataclass
class LoopResult:
    body: dict  # the last response
    items: list[dict]  # every output item from every response, in order
    rounds: int  # responses requested, the forced final one included
    cost_usd: float | None  # None when any response did not report a numeric cost


def _output_items(body: dict) -> list[dict]:
    output = body.get("output")
    if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
        raise ProviderError("Advisor returned an invalid response.")
    return output


def _cost(body: dict) -> float | None:
    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    return cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


def _starting_input(value) -> list[dict]:
    if isinstance(value, str):
        return [{"type": "message", "role": "user", "content": value}]
    return copy.deepcopy(value)


def _plan_round(output: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a response into (items to replay, calls to answer).

    A function_call is replayed only when the API can match it: it needs a string
    `call_id`, and a repeated `call_id` in one response is dropped after the first.
    """
    replay, calls, seen = [], [], set()
    for item in output:
        if item.get("type") == "function_call":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or call_id in seen:
                continue
            seen.add(call_id)
            calls.append(item)
        replay.append(item)
    return replay, calls


async def _run_one(functions: Mapping[str, FunctionRunner], item: dict) -> dict:
    name = item.get("name")
    runner = functions.get(name) if isinstance(name, str) else None
    if not isinstance(name, str):
        output = "Error: malformed call"
    elif runner is None:
        output = "Error: unknown tool"
    else:
        arguments = item.get("arguments")
        try:
            output = await runner(name, arguments if isinstance(arguments, str) else "{}")
        except LoopStopped:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool failure is an answer, not a crash
            # Name and type only: exception text may carry student content or secrets.
            log.warning("Advisor tool %s failed (%s)", name, type(exc).__name__)
            output = f"Error: {type(exc).__name__}"
    return {"type": "function_call_output", "call_id": item["call_id"], "output": output}


async def run_tool_loop(
    client: AdvisorClient, key: str, payload: dict, functions: Mapping[str, FunctionRunner],
    *, on_items: ItemsCallback | None = None, should_stop: Callable[[], bool] = lambda: False,
    max_rounds: int = MAX_ROUNDS,
) -> LoopResult:
    """Run `payload` until the model answers without calling a function.

    The caller's payload is not modified. After `max_rounds` rounds that still called
    functions, one more request runs with `tool_choice: "none"` and `FINAL_NOTE`.
    `LoopStopped` carries the items and cost of the requests made so far. An
    `OpenRouterError` mid-loop (a failed or invalid response) gets the same `items` and
    `cost_usd` attributes before it propagates, so a caller can bill spend already made.
    """
    history = _starting_input(payload.get("input", []))
    items: list[dict] = []
    costs: list[float | None] = []

    def total() -> float | None:
        return None if None in costs else sum(costs)

    async def request(extra: dict | None = None) -> tuple[dict, list[dict]]:
        if should_stop():
            raise LoopStopped
        sent = payload | (extra or {}) | {"input": list(history) + (extra or {}).get("input", [])}
        body = await client.respond(key, sent)
        costs.append(_cost(body))
        output = _output_items(body)
        items.extend(output)
        if on_items is not None:
            await on_items(output)
        return body, output

    async def drive() -> LoopResult:
        for _ in range(max_rounds):
            body, output = await request()
            replay, calls = _plan_round(output)
            if not calls:
                return LoopResult(body, items, len(costs), total())
            history.extend(replay)
            history.extend([await _run_one(functions, call) for call in calls])
        note = {"type": "message", "role": "user", "content": FINAL_NOTE}
        body, _ = await request({"tool_choice": "none", "input": [note]})
        return LoopResult(body, items, len(costs), total())

    try:
        return await drive()
    except LoopStopped as stop:
        stop.items, stop.cost_usd = list(items), total()
        raise
    except OpenRouterError as error:
        error.items, error.cost_usd = list(items), total()
        raise
