"""M5 migration leg of the synthetic e2e: local export -> hosted import.

Builds a local advisor database (as the retired local server left it) in a temp folder,
exports it with the same code `openrouter-image-mcp export-local` runs, uploads it as
a fresh 2027 student and continues the imported chat on the hosted server.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import httpx

from openrouter_image_mcp import local_bundle
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.config import COHORT_WORKSPACES
from openrouter_image_mcp.session_files import FilePacket, SessionFiles


def _local_export(folder: Path) -> bytes:
    scope = Scope(hashlib.sha256(b"synthetic-local-key").hexdigest(), COHORT_WORKSPACES["2027"])
    store = AdvisorStore(folder / "advisor.sqlite3")
    chat = store.create(scope, "openai/fake-advisor", "Local critique")
    with store.lease(scope, chat.id) as token:
        store.append_turn(scope, chat.id, "Review my plan", "Looks solid.", token)
    SessionFiles(store).upsert(scope, chat.id, [FilePacket(path="notes.md", content="# Plan\n")])
    bundle = local_bundle.export_bundle(store.target, scope, cohort="2027")
    local_bundle.write_bundle(bundle, folder / "export.json")
    return (folder / "export.json").read_bytes()


async def _put(c, data: bytes):
    ticket = (await c.call_tool("create_local_import", {})).structured_content
    async with httpx.AsyncClient() as http:
        return await http.put(ticket["upload_url"], content=data)


async def migration_checks(stack, check, provisioned, text) -> None:
    with tempfile.TemporaryDirectory() as folder:
        data = _local_export(Path(folder))
    check("export holds no local chat or container ids", b"chat_" not in data and b"nu_" not in data)
    frank = stack.token("email|frank", "2027")
    await provisioned(stack, frank, "frank")
    async with stack.client(frank) as c:
        first = await _put(c, data)
        again = await _put(c, data)
        chats = json.loads(text(await c.call_tool("list_advisor_chats", {})))
        chat_id = next((row.get("chat_id") or row.get("id") for row in chats
                        if row.get("title") == "Local critique"), None)
        follow = (await c.call_tool("ask_advisor", {"prompt": "And now?", "chat_id": chat_id or ""})
                  ).structured_content or {}
    check("import creates the chat", first.status_code == 200 and first.json().get("chats") == 1,
          first.text[:200])
    check("the same export imports once", again.json().get("status") == "already_imported")
    check("imported chat continues with its history and files",
          "turn 2" in follow.get("answer", "") and "notes.md v1" in follow.get("answer", ""),
          str(follow)[:200])
    async with stack.client(stack.token("email|gina", "2028")) as c:
        other = await _put(c, data)
    check("a 2028 student can't import a 2027 export", other.status_code == 403)
