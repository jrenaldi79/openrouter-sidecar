"""A hand-built Slack runner rig: FakeSlack, real stores on `db_target`, a scripted OpenRouter.

`wiring.slack_services` builds the real `SlackServices` for the Slack workspace; this rig builds one by hand
around a KeyBox key.
"""

import asyncio
import inspect
import itertools
import json
import secrets
from dataclasses import replace

import httpx
from slack_fakes import FakeSlack

from openrouter_image_mcp.advisor import AdvisorService
from openrouter_image_mcp.advisor_client import AdvisorClient
from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.hosted.limits import Limits
from openrouter_image_mcp.hosted.slack.context import Context
from openrouter_image_mcp.hosted.slack.jobs import SlackJobs
from openrouter_image_mcp.hosted.slack.memory import ChannelMemory
from openrouter_image_mcp.hosted.slack.principal import team_principal
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row
from openrouter_image_mcp.hosted.slack.runner import (
    SlackJob,
    SlackRunner,
    SlackServices,
)
from openrouter_image_mcp.hosted.slack.threads import ThreadMap
from openrouter_image_mcp.preferences import PreferenceService

KEY = "sk-or-v1-team"
CHANNEL, THREAD = "C1", "1700000000.000001"
NEWEST = "1700000000.000005"


def answer_item(text="The answer."):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def body(*items, cost=0.01):
    return {"status": "completed", "output": list(items) or [answer_item()], "usage": {"cost": cost}}


def call(name="read_thread", call_id="call_1", args="{}"):
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": args}


class Model:
    """Scripted OpenRouter. `respond(body)` returns a body dict or an httpx.Response, sync or async."""

    def __init__(self):
        self.requests: list[dict] = []
        self.uploads: list[str] = []
        self.deletes: list[str] = []
        self.container_files: dict[str, bytes] = {}  # file id -> content served from the sandbox
        self.catalog: list[dict] = []  # GET /models rows (AdvisorService.start checks the model is listed)
        self.respond = lambda request: body()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            file_id = "or_file_" + secrets.token_hex(4)
            self.uploads.append(file_id)
            return httpx.Response(200, json={"id": file_id})
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": self.catalog})
        if request.method == "GET" and "/containers/" in request.url.path:
            content = self.container_files.get(request.url.path.split("/files/")[1].split("/")[0])
            return httpx.Response(404) if content is None else httpx.Response(200, content=content)
        if request.method == "DELETE":
            self.deletes.append(request.url.path)
            return httpx.Response(204)
        sent = json.loads(request.content)
        self.requests.append(sent)
        reply = self.respond(sent)
        if inspect.isawaitable(reply):
            reply = await reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)


class Rig:
    def __init__(self, target, keys):
        keys.set(KEY, workspace_id=SLACK_WORKSPACE)
        self.slack, self.model = FakeSlack(), Model()
        self.store = AdvisorStore(target)
        self.client = AdvisorClient(SLACK_WORKSPACE, 30, transport=httpx.MockTransport(self.model.handle))
        self.advisors = AdvisorService(SLACK_WORKSPACE, self.store, self.client, credentials=keys.credentials(SLACK_WORKSPACE))
        self.results = AdvisorResults(self.store)
        self.services = SlackServices(self.advisors, PreferenceService(self.advisors), self.store,
                                      self.results, self.advisors.files, self.client)
        self.row = new_row("T1", CHANNEL, "team-a", "2027", fusion_panel=("a/one", "b/two"),
                           fusion_analyst="d/analyst", subagent_model="e/sub")
        self.principal = team_principal("T1", CHANNEL, self.row)
        _, self.scope = keys.credentials(SLACK_WORKSPACE).resolve()
        self.threads, self.memory, self.jobs = ThreadMap(target), ChannelMemory(target), SlackJobs(target)
        self.limits = Limits(self.store)
        self.registry = ChannelRegistry(target)  # a test registers the row itself to get the budget warning
        self.key_info = {}  # what the stubbed GET /key returns (an Exception is raised, an async function is awaited)
        self.key_info_keys: list[str] = []
        ticks = itertools.count()
        # Every clock read is 11 seconds later, so each progress step may edit.
        self.runner = SlackRunner(self.slack, self.limits, self.threads, self.memory, self.jobs,
                                  process_id="proc-test", clock=lambda: next(ticks) * 11.0,
                                  registry=self.registry, key_info=self._key_info)

    async def _key_info(self, key):
        self.key_info_keys.append(key)
        if isinstance(self.key_info, Exception):
            raise self.key_info
        if callable(self.key_info):  # an async function, to gate or delay the fetch
            return await self.key_info(key)
        return self.key_info

    def set_row(self, **changes):
        self.row = replace(self.row, **changes)

    def chat(self, *, sandbox=False, model="g/model", alias="gemini", mode="inline"):
        chat = self.store.create(self.scope, model, "Slack", sandbox=sandbox)
        self.threads.put(self.scope, CHANNEL, THREAD, alias, mode, chat.id)
        return chat

    def job(self, chat, brief="What should we do?", **fields) -> SlackJob:
        values = {"principal": self.principal, "row": self.row, "channel_id": CHANNEL, "thread_ts": THREAD,
                  "user_id": "U1", "alias": "gemini", "label": "Gemini", "mode": "inline", "chat_id": chat.id,
                  "brief": brief, "context": Context("[Oct 20 10:14] Ana: hello", NEWEST), "domains": [],
                  "compare": False}
        return SlackJob(**(values | fields))

    async def run(self, job: SlackJob) -> None:
        await self.runner.run(self.services, job)

    def job_rows(self) -> list[dict]:
        with self.results.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM advisor_jobs ORDER BY created_at, rowid")]

    def texts(self) -> list[str]:
        return [post["text"] for post in self.slack.posts]

    def history(self, chat) -> list[dict]:
        return self.store.get(self.scope, chat.id).history


async def wait_until(predicate, *, tries=2000):
    """Yield to the loop until `predicate()` holds; no fixed sleeps."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition never held")
