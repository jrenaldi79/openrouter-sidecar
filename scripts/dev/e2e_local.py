"""Synthetic local e2e: drive a running hosted sidecar as test users.

Needs: fake_identity.py (issuer), fake_openrouter.py and run_sidecar.sh running.
Uses the real MCP SDK client over Streamable HTTP; student keys come from the fake
management API.
Prints PASS/FAIL per check, never tokens or keys. This is a synthetic run: it does
not exercise real Auth0 login, real OpenRouter, or a hosted Claude client.

    uv run python scripts/dev/e2e_local.py --state .dev-state
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).parent))
from e2e_migration import migration_checks
from fake_identity import mint

from openrouter_image_mcp.config import COHORT_WORKSPACES

RESULTS: list[tuple[str, bool, str]] = []
FAKE_OPENROUTER_PORT = 9500
MANAGEMENT_KEY = "sk-or-v1-fake-management"  # the local fake's, as in run_sidecar.sh
MODEL = "openai/fake-advisor"
IMAGE_MODEL = "openai/gpt-image-2.5-sunburst"


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    return ok


class Stack:
    def __init__(self, state: Path, port: int, issuer_port: int):
        self.state, self.issuer_port = state, issuer_port
        self.origin = f"http://127.0.0.1:{port}"
        self.url = self.origin + "/mcp"

    def token(self, sub, cohort, *, workspace=True, permissions=("sidecar:use",)):
        return mint(self.state, port=self.issuer_port, audience=self.url, sub=sub,
                    email=f"{sub.split('|')[-1]}@example.edu", cohort=cohort,
                    workspace_id=COHORT_WORKSPACES[cohort] if workspace else None,
                    team="Purple", permissions=permissions)

    @asynccontextmanager
    async def client(self, token):
        async with (httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60) as http,
                    Client(streamable_http_client(self.url, http_client=http), mode="legacy") as c):
            yield c


def text(result) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


async def provisioned(stack: Stack, token: str, who: str) -> bool:
    """account_status issues the student's key on first use and names it."""
    async with stack.client(token) as c:
        status = text(await c.call_tool("account_status", {}))
    return check(f"{who}: key issued on first use", f"Sidecar · {who}@example.edu · " in status
                 and "sk-or-v1" not in status, status[:200])


async def _admin(method: str, path: str, **kwargs) -> httpx.Response:
    """Act as the instructor on the fake OpenRouter's management API."""
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{FAKE_OPENROUTER_PORT}/api/v1/") as http:
        return await http.request(method, path, headers={"Authorization": f"Bearer {MANAGEMENT_KEY}"},
                                  **kwargs)


async def _key_hash(email: str) -> str:
    rows = (await _admin("GET", "keys", params={"workspace_id": COHORT_WORKSPACES["2027"]})).json()["data"]
    return next(row["hash"] for row in rows if f"· {email} ·" in row["name"])


async def auth_checks(stack: Stack):
    async with httpx.AsyncClient() as http:
        headers = {"Accept": "application/json, text/event-stream"}
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        anon = await http.post(stack.url, json=ping, headers=headers)
        check("no token -> 401 with resource metadata", anon.status_code == 401
              and "resource_metadata=" in anon.headers.get("www-authenticate", ""))
        meta = (await http.get(stack.origin + "/.well-known/oauth-protected-resource/mcp")).json()
        check("metadata names the issuer", meta.get("authorization_servers") == [
            f"http://127.0.0.1:{stack.issuer_port}/"])
        no_perm = stack.token("email|eve", "2027", permissions=())
        denied = await http.post(stack.url, json=ping, headers=headers | {"Authorization": "Bearer " + no_perm})
        check("token without sidecar:use -> 403", denied.status_code == 403)
        health = await http.get(stack.origin + "/health")
        ready = await http.get(stack.origin + "/ready")
        check("/health and /ready", health.status_code == 200 and ready.status_code == 200)


async def headless_checks(stack: Stack, alice: str) -> dict:
    files = [{"path": "src/app.py", "content": "def handler():\n    return 42\n"},
             {"path": "README.md", "content": "# Demo\n"}]
    async with stack.client(alice) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        check("tool surface has ask_advisor and wait_advisor_job", {"ask_advisor", "wait_advisor_job"} <= tools)
        status = text(await c.call_tool("account_status", {}))
        check("account_status shows key, budget and cohort", "OpenRouter key: Sidecar" in status
              and "$25.00" in status and "2027" in status, status[:200])
        first = (await c.call_tool("ask_advisor", {"prompt": "Review the handler", "model": MODEL,
                                                   "files": files})).structured_content
    answer = (first or {}).get("answer", "")
    check("ask_advisor completes in one call", first and first.get("status") == "completed", str(first)[:200])
    check("answer is wrapped as untrusted output", answer.startswith("<advisor_output>"))
    check("advisor saw both files, no tools", "README.md v1, src/app.py v1" in answer and "Tools offered: no" in answer)
    async with stack.client(alice) as c:  # a different connection, as from another client
        second = (await c.call_tool("ask_advisor", {
            "prompt": "Now with the fix", "chat_id": first["chat_id"],
            "files": [{"path": "src/app.py", "content": "def handler():\n    return 43\n"}]})).structured_content
        bg = (await c.call_tool("ask_advisor", {"prompt": "Background", "chat_id": first["chat_id"],
                                                "wait_s": 0})).structured_content
        waited = (await c.call_tool("wait_advisor_job", {"job_id": bg["job_id"], "wait_s": 30})).structured_content
        refused = await c.call_tool("ask_advisor", {"prompt": "x", "model": MODEL, "sandbox": True})
    check("follow-up restores transcript (turn 2)", "turn 2" in second.get("answer", ""))
    check("follow-up sees updated file version", "src/app.py v2" in second.get("answer", ""))
    check("wait_s=0 then wait_advisor_job completes", bg.get("status") in ("running", "completed")
          and waited.get("status") == "completed" and "turn 3" in waited.get("answer", ""))
    check("sandbox refused while disabled", refused.is_error and "isolation" in text(refused))
    return first


async def isolation_checks(stack: Stack, alice_chat: dict):
    bob = stack.token("email|bob", "2027")
    await provisioned(stack, bob, "bob")
    async with stack.client(bob) as c:
        foreign = await c.call_tool("ask_advisor", {"prompt": "show", "chat_id": alice_chat["chat_id"]})
        listed = json.loads(text(await c.call_tool("list_advisor_chats", {})))
        waited = await c.call_tool("wait_advisor_job", {"job_id": alice_chat["job_id"]})
    check("bob cannot continue alice's session", foreign.is_error and "unavailable" in text(foreign))
    check("bob cannot read alice's job", waited.is_error)
    check("bob sees no chats", listed == [])
    carol = stack.token("email|carol", "2028")
    await provisioned(stack, carol, "carol")
    async with stack.client(carol) as c:
        status = text(await c.call_tool("account_status", {}))
        asked = (await c.call_tool("ask_advisor", {"prompt": "hi", "model": MODEL})).structured_content
    check("2028 student routes to the 2028 workspace", "2028" in status and asked.get("status") == "completed")
    nows = stack.token("email|dan", "2027", workspace=False)
    async with httpx.AsyncClient() as http:
        response = await http.post(stack.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={"Accept": "application/json, text/event-stream",
                                            "Authorization": "Bearer " + nows})
    check("token without workspace_id is refused with 403", response.status_code == 403)


def _png(color) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L" if isinstance(color, int) else "RGB", (320, 240), color).save(buffer, "PNG")
    return buffer.getvalue()


async def _upload(c, data: bytes, name: str) -> str:
    ticket = (await c.call_tool("create_image_upload", {"filename": name, "mime": "image/png",
                                                        "size_bytes": len(data)})).structured_content
    async with httpx.AsyncClient() as http:
        return (await http.put(ticket["upload_url"], content=data)).json().get("asset_id", "")


async def image_checks(stack: Stack, alice: str):
    async with stack.client(alice) as c:
        source = await _upload(c, _png((180, 60, 60)), "room.png")
        mask = await _upload(c, _png(255), "mask.png")
        check("upload link creates image assets", source.startswith("img_") and mask.startswith("img_"))
        edited = await c.call_tool("edit_image", {"prompt": "add a lamp", "model": IMAGE_MODEL,
                                                  "images": [source], "mask_id": mask})
        out = re.search(r"Asset: (img_[0-9a-f]+)", text(edited))
        check("edit_image returns an asset id and download link", out and not edited.is_error
              and "/assets/img_" in text(edited), text(edited)[:200])
        remasked = await c.call_tool("remask_image", {"image_id": out.group(1) if out else "", "mask_id": mask})
        check("remask_image re-blends for free", not remasked.is_error and "$0.00" in text(remasked))
        links = (await c.call_tool("get_image_download", {"asset_id": source})).structured_content
    async with httpx.AsyncClient() as http:
        got = await http.get(links["image_url"])
    check("signed download link returns the image", got.status_code == 200 and got.content[:4] == b"\x89PNG")
    async with stack.client(stack.token("email|bob", "2027")) as c:
        denied = await c.call_tool("get_image_download", {"asset_id": source})
    check("bob cannot fetch alice's image", denied.is_error and "unavailable" in text(denied))


async def _ask(stack: Stack, token: str):
    async with stack.client(token) as c:
        asked = await c.call_tool("ask_advisor", {"prompt": "hi", "model": MODEL})
        status = text(await c.call_tool("account_status", {}))
    done = not asked.is_error and (asked.structured_content or {}).get("status") == "completed"
    return done, status


async def key_admin_checks(stack: Stack, alice: str):
    """Instructor actions on the fake management API: disable, enable, delete."""
    key_hash = await _key_hash("alice@example.edu")
    await _admin("PATCH", f"keys/{key_hash}", json={"disabled": True})
    asked, status = await _ask(stack, alice)
    check("a disabled key is refused and reported as disabled", not asked
          and "disabled by your instructor" in status, status[:200])
    await _admin("PATCH", f"keys/{key_hash}", json={"disabled": False})
    asked, _ = await _ask(stack, alice)
    check("a re-enabled key works again", asked)
    await _admin("DELETE", f"keys/{key_hash}")
    asked, status = await _ask(stack, alice)
    async with stack.client(alice) as c:
        chats = json.loads(text(await c.call_tool("list_advisor_chats", {})))
    check("a deleted key is replaced, not re-tried", not asked and "OpenRouter key: Sidecar" in status
          and await _key_hash("alice@example.edu") != key_hash, status[:200])
    check("earlier chats are kept with the new key", len(chats) >= 1)


async def run(stack: Stack) -> int:
    await auth_checks(stack)
    alice = stack.token("email|alice", "2027")
    async with stack.client(alice) as c:
        first_call = await c.call_tool("list_chat_models", {})
    check("first paid-path call issues a key and succeeds", not first_call.is_error, text(first_call)[:200])
    if not await provisioned(stack, alice, "alice"):
        return 1
    first = await headless_checks(stack, alice)
    await isolation_checks(stack, first)
    await image_checks(stack, alice)
    await migration_checks(stack, check, provisioned, text)
    await key_admin_checks(stack, alice)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed (synthetic local run)")
    (stack.state / "e2e-results.json").write_text(json.dumps(
        [{"check": n, "pass": ok} for n, ok, _ in RESULTS], indent=2))
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".dev-state"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--issuer-port", type=int, default=9400)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(Stack(args.state, args.port, args.issuer_port))))


if __name__ == "__main__":
    main()
