"""Advisor turns can opt in to web search, with X search added on Grok models."""

import json
import time

import httpx
import pytest

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_jobs import AdvisorJobs
from openrouter_image_mcp.advisor_payload import INLINE_INSTRUCTIONS, INSTRUCTIONS
from openrouter_image_mcp.advisor_search import (
    MAX_SEARCHES,
    search_instructions,
    search_tool,
    sources_note,
)
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.advisor_tools import ADVISOR_ARGUMENTS
from openrouter_image_mcp.headless import HeadlessAdvisor

WORKSPACE = "test-workspace"
KEY = "sk-or-v1-synthetic-search"
GROK, GEMINI = "x-ai/grok-4.1-fast", "google/gemini-search-test"


def cited(text="People like it.", annotations=()):
    return {"status": "completed", "usage": {"cost": 0.004}, "output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": text, "annotations": list(annotations)}]}]}


def citation(url, title=None):
    note = {"type": "url_citation", "url": url, "start_index": 0, "end_index": 4}
    return note | ({"title": title} if title is not None else {})


# --- the pieces ---

def test_search_tool_adds_x_search_on_grok_only():
    assert search_tool(GROK) == {"type": "openrouter:web_search", "parameters": {
        "engine": "auto", "max_uses": MAX_SEARCHES, "x_search": {}}}
    assert search_tool(GEMINI) == {"type": "openrouter:web_search", "parameters": {
        "engine": "auto", "max_uses": MAX_SEARCHES}}
    assert "x_search" not in search_tool("openai/x-ai-lookalike")["parameters"]


def test_inline_instructions_drop_the_no_network_claim():
    text = search_instructions(INLINE_INSTRUCTIONS, GEMINI)
    assert "network access" not in text
    assert "search the web" in text and "untrusted" in text
    assert "X" not in text.split("search the web", 1)[1].split(".", 1)[0]
    assert "web and X" in search_instructions(INLINE_INSTRUCTIONS, GROK)


def test_sandbox_instructions_keep_the_shell_offline_and_add_search():
    text = search_instructions(INSTRUCTIONS, GROK)
    assert "internet access disabled" in text
    assert "web and X" in text


def test_sources_note_lists_unique_http_citations():
    body = cited(annotations=[
        citation("https://x.com/a/status/1", "Post by @a"),
        citation("https://x.com/a/status/1", "Post by @a"),
        citation("https://example.com/b"),
        citation("javascript:alert(1)", "bad"),
    ])
    assert sources_note(body) == ("\n\nSources:\n- [Post by @a](https://x.com/a/status/1)\n"
                                  "- https://example.com/b")


def test_sources_note_caps_the_list_and_escapes_titles():
    body = cited(annotations=[citation(f"https://example.com/{n}", "a]b") for n in range(15)])
    lines = sources_note(body).strip().splitlines()
    assert len(lines) == 11
    assert lines[1] == "- [a\\]b](https://example.com/0)"


def test_sources_note_is_empty_without_citations():
    assert sources_note(cited()) == ""
    assert sources_note({"output": "nonsense"}) == ""


def test_search_is_an_advertised_turn_argument():
    for tool in ("ask_advisor", "send_advisor_message"):
        assert "search" in ADVISOR_ARGUMENTS[tool]
    assert "search" not in ADVISOR_ARGUMENTS["start_advisor_chat"]


# --- through the service and jobs ---

@pytest.fixture
async def rig(keys, db_target):
    keys.set(KEY, workspace_id=WORKSPACE)
    seen, replies = [], []

    async def handle(request):
        seen.append(request)
        if request.url.path.endswith("/models"):
            rows = [{"id": model, "created": int(time.time()) - 86400,
                     "supported_parameters": ["tools"],
                     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}
                    for model in (GROK, GEMINI)]
            return httpx.Response(200, json={"data": rows})
        if request.url.path.endswith("/responses"):
            events = [{"type": "response.completed", "response": replies.pop(0) if replies else cited()}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        pytest.fail(f"Unexpected provider operation: {request.method} {request.url.path}")

    client = AdvisorClient(WORKSPACE, 30, transport=httpx.MockTransport(handle))
    service = AdvisorService(WORKSPACE, AdvisorStore(db_target), client,
                             credentials=keys.credentials(WORKSPACE),
                             sandbox_default=False, sandbox_allowed=False)
    yield service, AdvisorJobs(service), seen, replies
    await client.aclose()


def inference(seen):
    return [json.loads(r.content) for r in seen if r.url.path.endswith("/responses")]


async def finished(jobs, job_id):
    row = await jobs.wait(job_id, 5)
    assert row["status"] == "completed", row.get("error")
    return row


async def test_grok_turn_with_search_sends_web_and_x_search(rig):
    service, jobs, seen, replies = rig
    replies.append(cited("Mixed reactions.", [citation("https://x.com/a/status/1", "@a")]))
    chat = await service.start(GROK, "Pulse")
    started = await jobs.start(chat["chat_id"], "What are people saying on X?", search=True)
    row = await finished(jobs, started["job_id"])
    payload = inference(seen)[-1]
    assert payload["tools"] == [search_tool(GROK)]
    assert "network access" not in payload["instructions"]
    assert row["answer"] == "Mixed reactions.\n\nSources:\n- [@a](https://x.com/a/status/1)"
    assert service.get(chat["chat_id"])["messages"][-1]["content"] == row["answer"]


async def test_other_models_get_web_search_without_x(rig):
    service, jobs, seen, _ = rig
    chat = await service.start(GEMINI, "News")
    await finished(jobs, (await jobs.start(chat["chat_id"], "Latest news?", search=True))["job_id"])
    assert inference(seen)[-1]["tools"] == [search_tool(GEMINI)]


async def test_turns_without_search_send_no_tools(rig):
    service, jobs, seen, _ = rig
    chat = await service.start(GROK, "Plain")
    await finished(jobs, (await jobs.start(chat["chat_id"], "Hello"))["job_id"])
    payload = inference(seen)[-1]
    assert "tools" not in payload
    assert payload["instructions"] == INLINE_INSTRUCTIONS


async def test_search_must_be_a_boolean(rig):
    service, jobs, seen, _ = rig
    chat = await service.start(GROK, "Bad")
    with pytest.raises(Exception, match="search must be true or false"):
        await jobs.start(chat["chat_id"], "Hello", search="yes")
    assert not inference(seen)


async def test_headless_ask_passes_search_through(rig):
    service, jobs, seen, _ = rig
    result = await HeadlessAdvisor(service, jobs).ask("Trending?", model=GROK, search=True, wait_s=5)
    assert result["status"] == "completed"
    assert inference(seen)[-1]["tools"] == [search_tool(GROK)]
