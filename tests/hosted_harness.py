"""Run the hosted server for real (uvicorn on a free port) with a fake OpenRouter."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.hosted.app import build_hosted_server
from openrouter_image_mcp.hosted.openrouter_keys import BUDGET_GUARDRAIL
from openrouter_image_mcp.hosted.settings import load_hosted_settings
from tests import jwt_helpers as h

WS_2027, WS_2028 = COHORT_WORKSPACES["2027"], COHORT_WORKSPACES["2028"]
MODEL = "openai/test-advisor"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sse(kind, **fields):
    return ("data: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


class FakeOpenRouter:
    """Just enough of OpenRouter for advisor chats and student keys; records every request."""

    def __init__(self, answer="Final advice"):
        self.answer, self.requests, self.delay = answer, [], 0.0
        self.keys: dict[str, dict] = {}  # hash -> key data, as the management API reports it
        self.guardrails = [{"id": "g-budget", "name": BUDGET_GUARDRAIL}]  # tests append e.g. the team one
        self.assignments: list[tuple[str, list[str]]] = []  # (guardrail id, key hashes)

    def final(self):
        return {"status": "completed", "usage": {"cost": 0.002}, "output": [{
            "type": "message", "role": "assistant", "phase": "final_answer", "status": "completed",
            "content": [{"type": "output_text", "text": self.answer}]}]}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if "/guardrails" in path:  # the class guardrails and their key assignments
            if request.method == "POST":
                hashes = json.loads(request.content)["key_hashes"]
                self.assignments.append((path.split("/")[-3], hashes))
                return httpx.Response(200, json={"assigned_count": len(hashes)})
            return httpx.Response(200, json={"data": self.guardrails, "total_count": len(self.guardrails)})
        if "/keys" in path or path.endswith("/key"):
            return self._keys(request, path)
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{
                "id": MODEL, "name": "Test advisor", "created": int(time.time()) - 86400,
                "pricing": {"prompt": "0", "completion": "0"}, "context_length": 1000,
                "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools"]}]})
        if path.endswith("/responses"):
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      stream=_Stream(self))
            return httpx.Response(200, json=self.final())
        return httpx.Response(404, json={"error": {"message": "fake: unknown path " + path}})

    def _keys(self, request: httpx.Request, path: str) -> httpx.Response:
        if path.endswith("/key"):  # the student's own key
            return httpx.Response(200, json={"data": {"usage_monthly": 1.5, "effective_budget": {
                "scope": "api_key", "limit_usd": 25, "spend_usd": 1.5,
                "remaining_usd": 23.5, "reset_interval": "monthly", "resets_at": "2026-11-01T00:00:00Z"}}})
        if request.method == "POST":
            body = json.loads(request.content)
            n = len(self.created_bodies())  # this request is already recorded
            self.keys[f"hash{n}"] = {"hash": f"hash{n}", "disabled": False, **body}
            return httpx.Response(201, json={"key": f"sk-or-v1-fake{n}", "data": self.keys[f"hash{n}"]})
        found = self.keys.get(path.rsplit("/", 1)[-1])
        if found is None:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if request.method == "DELETE":
            self.keys.pop(found["hash"])
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(200, json={"data": found})

    def created_bodies(self):
        return [json.loads(r.content) for r in self.requests
                if r.method == "POST" and r.url.path.endswith("/api/v1/keys")]

    def bodies(self, suffix="/responses"):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix)]


class _Stream(httpx.AsyncByteStream):
    def __init__(self, fake):
        self.fake = fake

    async def __aiter__(self):
        import asyncio

        yield sse("response.output_item.added", output_index=0, item={
            "type": "message", "role": "assistant", "phase": "final_answer"})
        yield sse("response.output_text.delta", output_index=0, delta=self.fake.answer[:5])
        await asyncio.sleep(self.fake.delay)
        yield sse("response.completed", response=self.fake.final())


def hosted_env(port: int, database_url: str) -> dict:
    return {
        "AUTH0_ISSUER": h.ISSUER, "MCP_RESOURCE_URI": f"http://127.0.0.1:{port}/mcp",
        "DATABASE_URL": database_url,
        "VAULT_KEYS": "v1:" + base64.b64encode(os.urandom(32)).decode(),
        "LINK_SIGNING_KEY": "k" * 32,
        "OPENROUTER_MANAGEMENT_KEY": "sk-or-v1-management",
    }


class Running:
    def __init__(self, server, port, fake, settings):
        self.server, self.port, self.fake, self.settings = server, port, fake, settings
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.keys = server.hosted["keys"]

    def token(self, sub="email|alice", workspace=WS_2027, **overrides):
        return h.token(sub=sub, workspace=workspace, aud=[self.settings.resource], **overrides)

    @asynccontextmanager
    async def client(self, token, extensions=None):
        headers = {"Authorization": f"Bearer {token}"}
        async with (httpx2.AsyncClient(headers=headers, timeout=30) as http,
                    Client(streamable_http_client(self.url, http_client=http), mode="legacy",
                           extensions=extensions) as c):
            yield c


@asynccontextmanager
async def running_server(database_url_or_path, *, fake=None, limits=None, env=None):
    port = free_port()
    target = str(database_url_or_path)
    settings = load_hosted_settings(hosted_env(port, target) | (env or {}))
    fake = fake or FakeOpenRouter()
    server = build_hosted_server(settings, key_resolver=h.resolver,
                                 transport=httpx.MockTransport(fake.handle), limits=limits)
    from openrouter_image_mcp.hosted.app import transport_security

    app = server.streamable_http_app(transport_security=transport_security(settings),
                                     host="127.0.0.1")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(200):
        if instance.started:
            break
        await asyncio.sleep(0.02)
    try:
        yield Running(server, port, fake, settings)
    finally:
        instance.should_exit = True
        thread.join(timeout=10)
