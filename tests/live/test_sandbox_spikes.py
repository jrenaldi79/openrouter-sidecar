"""Opt-in live spike: which sandbox folders survive the container sleeping.

Deselected by default; run with
`OPENROUTER_LIVE_KEY=... uv run pytest -m live tests/live/test_sandbox_spikes.py -s`
(workspace id from `OPENROUTER_LIVE_WORKSPACE`, else the Slack workspace). It writes marker files under
`/workspace/home`, `/workspace/project` and `/tmp`, waits `SPIKE_SLEEP_S` seconds (default 480, past the
5 idle minutes after which OpenRouter sleeps a container), then checks what is still there, and
finally checks that a fresh container sees none of it. It prints the facts and asserts only that the
requests succeeded. It spends a few cents of real money.
"""

import asyncio
import os
import secrets
from dataclasses import dataclass, field

import pytest

from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.errors import OpenRouterError
from tests.live.test_slack_spikes import _list_files, _show, _walk

pytestmark = pytest.mark.live

MODEL = "openai/gpt-4.1-mini"
PATHS = {"keep": "/workspace/home/work/keep.txt", "out": "/workspace/home/outputs/out.txt",
         "gone": "/workspace/home/gone.txt", "project": "/workspace/project/p.txt",
         "scratch": "/tmp/sidecar/scratch/t.txt"}
# What each path should look like after the sleep, from OpenRouter's containers guide.
EXPECTED = {"keep": "survived", "out": "survived", "gone": "deleted", "project": "missing",
            "scratch": "missing"}


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
    return Live(key, os.environ.get("OPENROUTER_LIVE_WORKSPACE") or SLACK_WORKSPACE)


@pytest.fixture
async def advisor(live):
    client = AdvisorClient(live.workspace, 300)
    yield client
    await client.aclose()


def _shell(container_id: str) -> dict:
    # The shell setup the server sends: our own container id, the OpenRouter engine, no network.
    return {"type": "openrouter:shell", "parameters": {"engine": "openrouter", "environment": {
        "type": "container_reference", "container_id": container_id, "file_ids": [],
        "network_policy": {"type": "disabled"}}}}


def _payload(prompt: str, container_id: str) -> dict:
    return {"model": MODEL, "input": [{"role": "user", "content": prompt}], "tools": [_shell(container_id)],
            "max_tool_calls": 4, "max_output_tokens": 2000, "stream": False, "store": False}


def _shell_items(body: dict) -> list[dict]:
    return [item for item in body.get("output", []) if "shell" in item.get("type", "")]


def _write_command(markers: dict[str, str]) -> str:
    writes = " && ".join(f"printf '%s' {markers[name]} > {path}" for name, path in PATHS.items())
    dirs = "mkdir -p /workspace/home/work /workspace/home/outputs /workspace/project /tmp/sidecar/scratch"
    return f"{dirs} && {writes} && rm /workspace/home/gone.txt; ls -la {' '.join(PATHS.values())} 2>&1"


def _read_command() -> str:
    cat = "; ".join(f'echo "== {path}"; cat {path} 2>/dev/null || echo MISSING' for path in PATHS.values())
    return cat + "; ls -la /workspace/home /workspace/home/work /tmp/sidecar 2>&1"


def _run_exactly(command: str) -> str:
    return ("Using the shell tool, run exactly this one command and nothing else, then reply 'done': "
            + command)


async def _turn(advisor, key: str, container_id: str, label: str, command: str) -> dict:
    body = await advisor.respond(key, _payload(_run_exactly(command), container_id))
    for item in _shell_items(body):
        _show(f"{label} shell item ({item.get('type')})", item, 6000)
    return body


async def _show_files(label: str, key: str, container_id: str) -> object:
    status, listed = await _list_files(key, container_id, {})
    _show(f"{label} container file list (status {status})", listed, 4000)
    return listed


def _file_id(listed: object, suffix: str) -> str | None:
    for node in _walk(listed):
        if any(isinstance(v, str) and v.endswith(suffix) for v in node.values()):
            found = node.get("id") or node.get("file_id")
            if isinstance(found, str):
                return found
    return None


async def _check_download(advisor, key: str, container_id: str, listed: object, marker: str) -> None:
    file_id = _file_id(listed, "keep.txt")
    if file_id is None:
        print("[spike] work/keep.txt is not in the container file list after the sleep")
        return
    try:
        data = await advisor.container_file(key, container_id, file_id, max_bytes=1000)
    except OpenRouterError as error:
        print(f"[spike] downloading work/keep.txt failed: {type(error).__name__}")
        return
    print(f"[spike] downloaded work/keep.txt: {len(data)} bytes; equals its marker: {data == marker.encode()}")


def _verdict(name: str, found: bool) -> str:
    if name == "gone":
        return "deleted as expected" if not found else "STILL THERE (unexpected)"
    return "survived" if found else "missing"


def _summarize(markers: dict[str, str], after_sleep: dict, fresh: dict) -> None:
    seen = str(_shell_items(after_sleep))
    for name, path in PATHS.items():
        verdict = _verdict(name, markers[name] in seen)
        print(f"[spike] {path}: {verdict} (expected {EXPECTED[name]}; "
              f"{'as expected' if verdict.split()[0] == EXPECTED[name] else 'DIFFERENT'})")
    in_fresh = markers["keep"] in str(_shell_items(fresh))
    print(f"[spike] fresh container sees work/keep.txt: {in_fresh} (expected False)")


async def test_home_survives_a_sleep(live, advisor):
    key, _ = live
    markers = {name: secrets.token_hex(8) for name in PATHS}
    container_id = "nu_" + secrets.token_hex(16)
    first = await _turn(advisor, key, container_id, "turn 1", _write_command(markers))
    await _show_files("turn 1", key, container_id)
    sleep_s = int(os.environ.get("SPIKE_SLEEP_S", "480"))
    print(f"[spike] waiting {sleep_s} seconds so the container sleeps")
    await asyncio.sleep(sleep_s)
    second = await _turn(advisor, key, container_id, "turn 2", _read_command())
    listed = await _show_files("turn 2", key, container_id)
    await _check_download(advisor, key, container_id, listed, markers["keep"])
    fresh_id = "nu_" + secrets.token_hex(16)
    fresh = await _turn(advisor, key, fresh_id, "turn 3 (fresh container)",
                        "cat /workspace/home/work/keep.txt 2>&1")
    _summarize(markers, second, fresh)
    assert all(body.get("status", "completed") != "failed" for body in (first, second, fresh))
