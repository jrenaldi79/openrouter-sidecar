"""Local stand-in for OpenRouter (development only). No real keys, no spend.

Implements just what the hosted sidecar calls: the PKCE sign-in page and code
exchange, key info, the model catalog, and Responses (streaming and not). Replies
describe what the advisor received (files, prior turns) so an e2e run can check
that session state is restored.

    python scripts/dev/fake_openrouter.py --port 9500
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from starlette.routing import Route

MODEL = "openai/fake-advisor"
_codes: dict[str, dict] = {}   # code -> {challenge, workspace, label}
_keys: dict[str, dict] = {}    # key -> {workspace, label}
calls: list[dict] = []          # what each /responses call carried (for inspection)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


async def auth_page(request: Request):
    q = request.query_params
    callback = q.get("callback_url", "")
    if urlsplit(callback).hostname not in {"127.0.0.1", "localhost"}:
        return HTMLResponse("fake OpenRouter only redirects to localhost", 400)
    code = "code_" + secrets.token_hex(12)
    _codes[code] = {"challenge": q.get("code_challenge"), "workspace": q.get("required_workspace_id"),
                    "label": q.get("key_label") or "Fake key"}
    target = callback + ("&" if "?" in callback else "?") + urlencode({"code": code, "state": q.get("state", "")})
    return HTMLResponse(
        "<!doctype html><title>Fake OpenRouter</title><h1>Fake OpenRouter (local)</h1>"
        f"<p>Workspace: {html.escape(str(q.get('required_workspace_id')))}</p>"
        f"<p><a id=authorize href='{html.escape(target)}'>Authorize</a></p>")


async def exchange(request: Request):
    body = await request.json()
    grant = _codes.pop(body.get("code", ""), None)
    if not grant or _challenge(body.get("code_verifier", "")) != grant["challenge"]:
        return JSONResponse({"error": {"message": "invalid code"}}, 400)
    key = "sk-or-v1-fake-" + secrets.token_hex(16)
    _keys[key] = {"workspace": grant["workspace"], "label": grant["label"]}
    return JSONResponse({"key": key})


def _owner(request: Request):
    key = request.headers.get("authorization", "").removeprefix("Bearer ")
    return _keys.get(key)


async def key_info(request: Request):
    owner = _owner(request)
    if not owner:
        return JSONResponse({"error": {"message": "invalid key"}}, 401)
    return JSONResponse({"data": {"label": owner["label"], "workspace_id": owner["workspace"],
                                  "usage_daily": 0.0, "usage_monthly": 0.0, "limit": None}})


async def models(_request: Request):
    return JSONResponse({"data": [{
        "id": MODEL, "name": "Fake advisor", "created": int(time.time()) - 86400,
        "pricing": {"prompt": "0", "completion": "0"}, "context_length": 100000,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"]}]})


def _describe(body: dict) -> str:
    items = body.get("input", [])
    text = " ".join(str(item.get("content")) for item in items)
    files = sorted(set(re.findall(r'<file path="([^"]+)" version="(\d+)"', text)))
    prior = sum(1 for item in items if item.get("role") == "assistant")
    listed = ", ".join(f"{path} v{version}" for path, version in files) or "none"
    return (f"Fake advice (turn {prior + 1}). Files I can see: {listed}. "
            f"Tools offered: {'yes' if body.get('tools') else 'no'}.")


def _final(answer: str) -> dict:
    return {"status": "completed", "usage": {"cost": 0.0, "input_tokens": 10}, "output": [{
        "type": "message", "role": "assistant", "phase": "final_answer", "status": "completed",
        "content": [{"type": "output_text", "text": answer}]}]}


async def responses(request: Request):
    if not _owner(request):
        return JSONResponse({"error": {"message": "invalid key"}}, 401)
    body = await request.json()
    answer = _describe(body)
    calls.append({"tools": bool(body.get("tools")), "items": len(body.get("input", []))})
    if not body.get("stream"):
        return JSONResponse(_final(answer))

    async def events():
        def sse(kind, **fields):
            return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"
        yield sse("response.output_item.added", output_index=0,
                  item={"type": "message", "role": "assistant", "phase": "final_answer"})
        yield sse("response.output_text.delta", output_index=0, delta=answer)
        yield sse("response.completed", response=_final(answer))

    return StreamingResponse(events(), media_type="text/event-stream")


def app() -> Starlette:
    return Starlette(routes=[
        Route("/auth", auth_page), Route("/api/v1/auth/keys", exchange, methods=["POST"]),
        Route("/api/v1/key", key_info), Route("/api/v1/models", models),
        Route("/api/v1/responses", responses, methods=["POST"])])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9500)
    args = parser.parse_args()
    uvicorn.run(app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
