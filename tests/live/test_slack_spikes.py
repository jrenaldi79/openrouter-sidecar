"""Opt-in live spikes for the Slack Sidecar tool loop, shell files and Fusion.

Deselected by default; run with
`OPENROUTER_LIVE_KEY=... uv run pytest -m live tests/live/test_slack_spikes.py -s`
(workspace id from `OPENROUTER_LIVE_WORKSPACE`, else the Slack workspace). Each spike prints the facts later
tasks depend on and asserts only that the requests succeeded. They spend small amounts
of real money; run each once. Change MODEL or PANEL below to pick other models.
"""

import json
import os
import secrets
from dataclasses import dataclass, field

import httpx
import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.config import API_BASE
from openrouter_image_mcp.errors import OpenRouterError

pytestmark = pytest.mark.live

# Becomes `config.SLACK_WORKSPACE` once the Slack workspace change lands on this branch.
SLACK_WORKSPACE = "8a738968-da7d-4d55-900a-16789ba50003"

MODEL = "openai/gpt-4.1-mini"
PANEL = ["openai/gpt-4.1-mini", "google/gemini-2.5-flash"]
ECHO_TOOL = {
    "type": "function", "name": "echo", "description": "Return the given text unchanged.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                   "required": ["text"]},
}


@dataclass(frozen=True)
class Live:
    # pytest prints a failing test's arguments, so the key stays out of the repr.
    key: str = field(repr=False)
    workspace: str

    def __iter__(self):
        return iter((self.key, self.workspace))


@pytest.fixture
def live():
    key = os.environ.get("OPENROUTER_LIVE_KEY")
    if not key:
        pytest.skip("set OPENROUTER_LIVE_KEY to a class workspace key to run live tests")
    # Slack team keys live in the Slack workspace; another workspace id can be given for a scratch run.
    return Live(key, os.environ.get("OPENROUTER_LIVE_WORKSPACE") or SLACK_WORKSPACE)


@pytest.fixture
async def advisor(live):
    client = AdvisorClient(live.workspace, 300)
    yield client
    await client.aclose()


def _payload(prompt: str, tools: list[dict], **extra) -> dict:
    return {"model": MODEL, "input": [{"role": "user", "content": prompt}], "tools": tools,
            "max_output_tokens": 2000, "stream": False, "store": False, **extra}


def _types(body: dict) -> list[str]:
    return [item.get("type", "?") for item in body.get("output", [])]


def _show(label: str, value, limit: int = 1500) -> None:
    print(f"[spike] {label}: {json.dumps(value, default=str)[:limit]}")


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
    # Anywhere in the body: a shell item's `files`, a message annotation, or a shape we haven't seen yet.
    return [node for node in _walk(body) if node.get("type") == "container_file_citation"]


def _where(body: dict, cite: dict) -> str:
    """Which output item holds the citation, and under which key, so the parser can be pointed at it."""
    for index, item in enumerate(body.get("output", [])):
        for parent in _walk(item):
            for key, value in parent.items():
                if value is cite or (isinstance(value, list) and any(v is cite for v in value)):
                    return f"output[{index}] type={item.get('type')} key={key}"
    return "not found"


async def _download(key: str, workspace: str, cite: dict, query: dict) -> tuple[int, int]:
    url = f"{API_BASE}/containers/{cite['container_id']}/files/{cite['file_id']}/content"
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        response = await http.get(url, params=query,
                                  headers={"Authorization": "Bearer " + key})
    return response.status_code, len(response.content)


async def _list_files(key: str, container_id: str, query: dict) -> tuple[int, object]:
    url = f"{API_BASE}/containers/{container_id}/files"
    async with httpx.AsyncClient(timeout=60) as http:
        response = await http.get(url, params={"limit": 100, **query},
                                  headers={"Authorization": "Bearer " + key})
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text[:300]


async def test_shell_files_shape(live, advisor):
    key, workspace = live
    # The bot's own shell setup: a container id we choose ("nu_" + 32 hex), the OpenRouter engine, no network.
    container_id = "nu_" + secrets.token_hex(16)
    shell = {"type": "openrouter:shell", "parameters": {"engine": "openrouter", "environment": {
        "type": "container_reference", "container_id": container_id, "file_ids": [],
        "network_policy": {"type": "disabled"}}}}
    prompt = ("Using the shell, run exactly these commands and then confirm: "
              "mkdir -p /workspace/home/outputs /workspace/project/outputs && "
              "echo 'hello home' > /workspace/home/outputs/a.txt && "
              "echo 'hello project' > /workspace/project/outputs/b.txt && "
              "ls -la /workspace/home/outputs /workspace/project/outputs && pwd && echo $HOME")
    body = await advisor.respond(key, _payload(prompt, [shell], max_tool_calls=4))
    for query in ({}, {"workspace_id": workspace}):
        status, listed = await _list_files(key, container_id, query)
        _show(f"container file list {sorted(query) or 'plain'} (status {status})", listed, 4000)
    _show("output types", _types(body))
    for item in body.get("output", []):
        _show(f"{item.get('type')} item", item, 6000)
    files = _shell_files(body)
    for cite in files:
        _show("file citation (filename form)", {k: cite.get(k) for k in
              ("type", "container_id", "file_id", "filename")})
        print(f"[spike] citation found at: {_where(body, cite)}")
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


async def test_fetch_subdomain_matching(live, advisor):
    key, _ = live
    fetch = {"type": "openrouter:web_fetch",
             "parameters": {"allowed_domains": ["example.com"], "max_uses": 2}}
    prompt = ("Use the web_fetch tool to open https://www.example.com/ and quote its heading. "
              "If the tool refuses, say exactly: REFUSED.")
    body = await advisor.respond(key, _payload(prompt, [fetch], max_tool_calls=3))
    _show("output types", _types(body))
    text = " ".join(str(node.get("text", "")) for node in _walk(body)
                    if node.get("type") == "output_text")
    fetched = any("fetch" in item.get("type", "") for item in body["output"])
    print(f"[spike] web_fetch ran for www.example.com with allowed_domains=['example.com']: {fetched}")
    print(f"[spike] subdomain matching: {'refused' if 'REFUSED' in text else 'allowed or unclear'}; "
          f"answer: {text[:200]!r}")


async def test_fusion_plugin_accepts_tools(live, advisor):
    key, _ = live
    plugin = {"id": "fusion", "analysis_models": PANEL, "model": MODEL, "max_tool_calls": 2,
              "tools": [{"type": "openrouter:web_search"}]}
    payload = _payload("Which is older, the Eiffel Tower or the Brooklyn Bridge? One sentence.", [])
    payload.pop("tools")
    payload.update(model="openrouter/fusion", plugins=[plugin])
    try:
        body = await advisor.respond(key, payload)
    except OpenRouterError as error:  # the fact we want is whether `tools` inside the plugin is accepted
        print(f"[spike] fusion plugin with tools REJECTED: {type(error).__name__}: {str(error)[:300]}")
        return
    _show("output types", _types(body))
    print("[spike] fusion plugin with tools accepted; "
          f"analysis anywhere in response: {any('analysis' in node for node in _walk(body))}")
