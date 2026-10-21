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
from .errors import ProviderError

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
FINAL_NOTE = "You have used every tool round. Answer now with what you have."

FunctionRunner = Callable[[str, str], Awaitable[str]]
# Called with each response's output items as they arrive, before any function runs.
ItemsCallback = Callable[[list[dict]], Awaitable[None]]


class LoopStopped(Exception):
    """`should_stop()` was set before a request."""


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


def _callable_items(output: list[dict]) -> list[dict]:
    return [item for item in output if item.get("type") == "function_call"
            and isinstance(item.get("name"), str) and isinstance(item.get("call_id"), str)]


async def _run_one(functions: Mapping[str, FunctionRunner], item: dict) -> dict:
    name, runner = item["name"], functions.get(item["name"])
    if runner is None:
        output = "Error: unknown tool"
    else:
        arguments = item.get("arguments")
        try:
            output = await runner(name, arguments if isinstance(arguments, str) else "{}")
        except Exception as exc:  # a tool failure is an answer, not a crash
            # Type name only: exception text may carry secrets or student content.
            log.warning("Advisor tool %s failed (%s)", name, type(exc).__name__, exc_info=True)
            output = f"Error: {type(exc).__name__}"
    return {"type": "function_call_output", "call_id": item["call_id"], "output": output}


async def run_tool_loop(
    client: AdvisorClient, key: str, payload: dict, functions: Mapping[str, FunctionRunner],
    *, on_items: ItemsCallback | None = None, should_stop: Callable[[], bool] = lambda: False,
    max_rounds: int = MAX_ROUNDS,
) -> LoopResult:
    """Run `payload` until the model answers without calling one of `functions`.

    The caller's payload is not modified. After `max_rounds` rounds that still called
    functions, one more request runs with `tool_choice: "none"` and `FINAL_NOTE`.
    """
    history = _starting_input(payload.get("input", []))
    items: list[dict] = []
    costs: list[float | None] = []

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

    def result(body: dict) -> LoopResult:
        total = None if None in costs else sum(costs)
        return LoopResult(body, items, len(costs), total)

    for _ in range(max_rounds):
        body, output = await request()
        calls = _callable_items(output)
        if not calls:
            return result(body)
        history.extend(output)
        history.extend([await _run_one(functions, call) for call in calls])
    note = {"type": "message", "role": "user", "content": FINAL_NOTE}
    body, _ = await request({"tool_choice": "none", "input": [note]})
    return result(body)
