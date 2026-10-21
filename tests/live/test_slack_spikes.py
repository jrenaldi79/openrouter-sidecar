"""Opt-in live spikes for the Slack Sidecar tool loop, shell files and Fusion.

Deselected by default; run with
`OPENROUTER_LIVE_KEY=... uv run pytest -m live tests/live/test_slack_spikes.py -s`
(cohort from `OPENROUTER_LIVE_COHORT`, default 2027). Each spike prints the facts later
tasks depend on and asserts only that the requests succeeded. They spend small amounts
of real money; run each once. Change MODEL or PANEL below to pick other models.
"""

import json
import os

import httpx
import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.config import API_BASE, COHORT_WORKSPACES

pytestmark = pytest.mark.live

MODEL = "openai/gpt-4.1-mini"
PANEL = ["openai/gpt-4.1-mini", "google/gemini-2.5-flash"]
SHELL_TOOL = {"type": "openrouter:shell", "parameters": {
    "engine": "openrouter", "environment": {"type": "container_auto"}}}
ECHO_TOOL = {
    "type": "function", "name": "echo", "description": "Return the given text unchanged.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                   "required": ["text"]},
}


@pytest.fixture
def live():
    key = os.environ.get("OPENROUTER_LIVE_KEY")
    if not key:
        pytest.skip("set OPENROUTER_LIVE_KEY to a class workspace key to run live tests")
    return key, COHORT_WORKSPACES[os.environ.get("OPENROUTER_LIVE_COHORT", "2027")]


@pytest.fixture
async def advisor(live):
    client = AdvisorClient(live[1], 300)
    yield client
    await client.aclose()


def _payload(prompt: str, tools: list[dict], **extra) -> dict:
    return {"model": MODEL, "input": [{"role": "user", "content": prompt}], "tools": tools,
            "max_output_tokens": 2000, "stream": False, "store": False, **extra}


def _types(body: dict) -> list[str]:
    return [item.get("type", "?") for item in body.get("output", [])]


def _show(label: str, value) -> None:
    print(f"[spike] {label}: {json.dumps(value, default=str)[:1500]}")


def _walk(value):
    """Yield every dict nested anywhere in a response body."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


async def test_function_call_replay(live, advisor):
    key, _ = live
    prompt = ("Call the echo tool with text 'ping' and also tell me the current time in "
              "UTC using the datetime tool. Then answer in one sentence.")
    tools = [ECHO_TOOL, {"type": "openrouter:datetime", "parameters": {"timezone": "UTC"}}]
    first = await advisor.respond(key, _payload(prompt, tools))
    _show("first output types", _types(first))
    calls = [item for item in first["output"] if item.get("type") == "function_call"]
    _show("function_call items", calls)
    results = [{"type": "function_call_output", "call_id": call["call_id"], "output": "ping"}
               for call in calls]
    replay = [{"role": "user", "content": prompt}, *first["output"], *results]
    second = await advisor.respond(key, _payload(prompt, tools, input=replay))
    _show("second output types", _types(second))
    assert any(item.get("type") == "message" for item in second["output"])


def _shell_files(body: dict) -> list[dict]:
    return [f for item in _walk(body) for f in item.get("files", []) or []
            if isinstance(f, dict) and f.get("type") == "container_file_citation"]


async def _download(key: str, workspace: str, cite: dict, query: dict) -> tuple[int, int]:
    url = f"{API_BASE}/containers/{cite['container_id']}/files/{cite['file_id']}/content"
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        response = await http.get(url, params=query,
                                  headers={"Authorization": "Bearer " + key})
    return response.status_code, len(response.content)


async def test_shell_files_shape(live, advisor):
    key, workspace = live
    prompt = ("Using the shell, create the directory /workspace/project/outputs and write the "
              "text 'hello spike' to /workspace/project/outputs/a.txt, then confirm it.")
    body = await advisor.respond(key, _payload(prompt, [SHELL_TOOL], max_tool_calls=4))
    _show("output types", _types(body))
    files = _shell_files(body)
    for cite in files:
        _show("file citation (filename form)", {k: cite.get(k) for k in
              ("type", "container_id", "file_id", "filename")})
    assert files, "no container_file_citation found in the shell items; see output types"
    cite = files[0]
    plain = await _download(key, workspace, cite, {})
    scoped = await _download(key, workspace, cite, {"workspace_id": workspace})
    print(f"[spike] download status/bytes without workspace_id: {plain}")
    print(f"[spike] download status/bytes with workspace_id: {scoped}")
    assert 200 in (plain[0], scoped[0])


async def test_fusion_forced(live, advisor):
    key, _ = live
    fusion = {"type": "openrouter:fusion", "parameters": {
        "analysis_models": PANEL, "model": MODEL, "max_tool_calls": 2,
        "tools": [{"type": "openrouter:web_search"}]}}
    choice = {"type": "allowed_tools", "mode": "required",
              "tools": [{"type": "openrouter:fusion"}]}
    prompt = "Which is older, the Eiffel Tower or the Brooklyn Bridge? Answer in one sentence."
    body = await advisor.respond(key, _payload(prompt, [fusion], tool_choice=choice))
    _show("output types", _types(body))
    fusion_items = [item for item in body["output"] if "fusion" in item.get("type", "")]
    print(f"[spike] fusion item ran: {bool(fusion_items)}")
    for item in fusion_items:
        print(f"[spike] fusion item keys: {sorted(item)}; analysis present: {'analysis' in item}")
    print(f"[spike] analysis anywhere in response: "
          f"{any('analysis' in node for node in _walk(body))}")
    assert body.get("status", "completed") != "failed"
