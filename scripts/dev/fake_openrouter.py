"""Local stand-in for OpenRouter (development only). No real keys, no spend.

Implements just what the hosted sidecar calls: the management API for student keys
(create, get, list, update, delete), key info, the model catalogs, Responses (streaming and not) and image
generation. Replies describe what the advisor received (files, prior turns) so an
e2e run can check that session state is restored. Images are flat test pictures.

    python scripts/dev/fake_openrouter.py --port 9500
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import secrets
import time
from pathlib import Path

import uvicorn
from PIL import Image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

MODEL = "openai/fake-advisor"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"  # image model catalog
MANAGEMENT_KEY = "sk-or-v1-fake-management"  # run_sidecar.sh passes the same value
_keys: dict[str, dict] = {}    # key -> {hash, name, workspace_id, disabled, created_at}
calls: list[dict] = []          # what each /responses call carried (for inspection)


def _managed(request: Request) -> bool:
    return request.headers.get("authorization", "") == f"Bearer {MANAGEMENT_KEY}"


def _denied():
    return JSONResponse({"error": {"message": "Only management keys can perform this operation."}}, 403)


def _by_hash(key_hash: str):
    return next(((key, data) for key, data in _keys.items() if data["hash"] == key_hash), (None, None))


async def create_key(request: Request):
    if not _managed(request):
        return _denied()
    body = await request.json()
    key = "sk-or-v1-fake-" + secrets.token_hex(16)
    _keys[key] = {"hash": hashlib.sha256(key.encode()).hexdigest(), "name": body.get("name", ""),
                  "workspace_id": body.get("workspace_id"), "disabled": False, "usage_monthly": 0.0,
                  "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return JSONResponse({"key": key, "data": _keys[key]}, 201)


async def list_keys(request: Request):
    if not _managed(request):
        return _denied()
    workspace = request.query_params.get("workspace_id")
    return JSONResponse({"data": [d for d in _keys.values() if d["workspace_id"] == workspace]})


async def one_key(request: Request):
    if not _managed(request):
        return _denied()
    key, data = _by_hash(request.path_params["hash"])
    if key is None:
        return JSONResponse({"error": {"message": "Resource not found"}}, 404)
    if request.method == "DELETE":
        del _keys[key]
        return JSONResponse({"deleted": True})
    if request.method == "PATCH":
        data["disabled"] = bool((await request.json()).get("disabled"))
    return JSONResponse({"data": data})


def _owner(request: Request):
    """The calling student key's data, if it exists and is enabled (else OpenRouter's 401)."""
    data = _keys.get(request.headers.get("authorization", "").removeprefix("Bearer "))
    return data if data and not data["disabled"] else None


async def key_info(request: Request):
    owner = _owner(request)
    if not owner:
        return JSONResponse({"error": {"message": "invalid key"}}, 401)
    return JSONResponse({"data": {"label": owner["name"], "workspace_id": owner["workspace_id"],
                                  "usage_daily": 0.0, "usage_monthly": 0.0, "limit": None,
                                  "effective_budget": {"scope": "workspace_default_key", "limit_usd": 25,
                                                       "spend_usd": 0.0, "remaining_usd": 25,
                                                       "reset_interval": "monthly", "resets_at": None}}})


async def models(request: Request):
    if request.query_params.get("output_modalities") == "image":
        return JSONResponse(json.loads((FIXTURES / "models_image.json").read_text()))
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


async def image_models(_request: Request):
    return JSONResponse(json.loads((FIXTURES / "images_models.json").read_text()))


async def images(request: Request):
    if not _owner(request):
        return JSONResponse({"error": {"message": "invalid key"}}, 401)
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (40, 160, 90)).save(buffer, "PNG")
    return JSONResponse({"id": "gen-fake", "provider": "fake", "usage": {"cost": 0.0},
                         "data": [{"b64_json": base64.b64encode(buffer.getvalue()).decode(),
                                   "media_type": "image/png"}]})


def app() -> Starlette:
    return Starlette(routes=[
        Route("/api/v1/keys", create_key, methods=["POST"]), Route("/api/v1/keys", list_keys),
        Route("/api/v1/keys/{hash}", one_key, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/v1/key", key_info), Route("/api/v1/models", models),
        Route("/api/v1/images/models", image_models), Route("/api/v1/images", images, methods=["POST"]),
        Route("/api/v1/responses", responses, methods=["POST"])])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9500)
    args = parser.parse_args()
    uvicorn.run(app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
