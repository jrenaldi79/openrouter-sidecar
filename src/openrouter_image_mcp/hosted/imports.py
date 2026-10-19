"""Authenticated import of a local export bundle (M5 migration).

The student runs `openrouter-image-mcp export-local` on their computer, asks the
connector for a one-time link (`create_local_import`) and PUTs the file to it. The
link is bound to the signed-in principal and cohort, so a bundle lands only in the
caller's own workspace. Chats get new ids and become inline (hosted) chats; the same
content imports once, keyed by a digest that ignores when it was exported.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import storage
from ..advisor_store import AdvisorStore, Scope
from ..local_bundle import BundleError, bundle_digest, parse_bundle, seed_preferences
from ..session_files import FilePacket, SessionFiles
from .uploads import _HEADERS, BUNDLE_MIME, TransferError, TransferTokens, read_limited

OTHER_COHORT = ("This export is from a different class cohort than the one you're signed in "
                "to; export with the matching --cohort.")


class LocalImports:
    def __init__(self, target):
        self.target = target
        self.store = AdvisorStore(target)
        self.files = SessionFiles(self.store)

    @contextmanager
    def _db(self):
        with storage.connect(self.target) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS local_imports (
                owner TEXT NOT NULL, workspace TEXT NOT NULL, digest TEXT NOT NULL,
                chats INTEGER NOT NULL, imported_at REAL NOT NULL,
                PRIMARY KEY (owner, workspace, digest))""")
            db.commit()
            yield db

    def _claim(self, scope: Scope, digest: str, chats: int) -> bool:
        """Record the import first, so concurrent or repeated uploads import once."""
        with self._db() as db:
            return db.execute(
                "INSERT INTO local_imports (owner,workspace,digest,chats,imported_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                (scope.owner, scope.workspace, digest, chats, time.time())).rowcount == 1

    def _release(self, scope: Scope, digest: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM local_imports WHERE owner=? AND workspace=? AND digest=?",
                       (scope.owner, scope.workspace, digest))

    def run(self, scope: Scope, bundle: dict) -> dict:
        if bundle["workspace"] != scope.workspace:
            raise TransferError(OTHER_COHORT, 403)
        digest = bundle_digest(bundle)
        if not self._claim(scope, digest, len(bundle["chats"])):
            return {"status": "already_imported", "chats": len(bundle["chats"])}
        try:
            files = sum(self._chat(scope, chat) for chat in bundle["chats"])
            prefs = bundle["preferences"]
            outcome = ("none" if prefs is None
                       else "applied" if seed_preferences(self.target, scope, prefs) else "kept")
        except Exception:
            self._release(scope, digest)  # let a retry run; chats made so far remain
            raise
        return {"status": "imported", "chats": len(bundle["chats"]), "files": files,
                "preferences": outcome}

    def _chat(self, scope: Scope, chat: dict) -> int:
        # Hosted chats are inline: the sandbox shell isn't offered to imported chats.
        created = self.store.import_chat(scope, chat["model"], chat["title"], chat["history"],
                                         sandbox=False)
        if chat["files"]:
            self.files.upsert(scope, created.id, [FilePacket(**f) for f in chat["files"]])
        return len(chat["files"])


def import_routes(tokens: TransferTokens, importer: LocalImports) -> list[Route]:
    async def receive(request: Request):
        try:
            row = tokens.claim_upload(request.path_params["token"])
            if row["mime"] != BUNDLE_MIME:
                raise TransferError("This link is not an import link.")
            bundle = parse_bundle(await read_limited(request, row["size"]))
            result = await asyncio.to_thread(importer.run, Scope(row["owner"], row["workspace"]),
                                             bundle)
        except TransferError as exc:
            return JSONResponse({"error": str(exc)}, exc.status, headers=_HEADERS)
        except BundleError as exc:
            return JSONResponse({"error": str(exc)}, 400, headers=_HEADERS)
        return JSONResponse(result, headers=_HEADERS)

    return [Route("/imports/{token}", receive, methods=["PUT"])]


def register_import_tool(server: MCPServer, tokens: TransferTokens,
                         scope_for: Callable[[], Scope], tool_errors) -> None:
    async def create_local_import() -> CallToolResult:
        """Move the user's chats and settings from the local Sidecar to this connector.

        Use when the user asks to bring over chats from the Claude Desktop (local) Sidecar.
        Returns a one-time link and two commands the user runs on their own computer:
        `openrouter-image-mcp export-local` writes a JSON export, and `curl -T` uploads it
        within 10 minutes. Imported chats get new chat ids; the same export imports once.
        Keys never move: the user links OpenRouter here separately. No cost.
        """
        with tool_errors():
            ticket = tokens.issue_import(scope_for())
            minutes = ticket["expires_in"] // 60
            instructions = (
                f"On your computer, within {minutes} minutes, run:\n"
                "  openrouter-image-mcp export-local --cohort <your class year> --out sidecar-export.json\n"
                f"  curl -T sidecar-export.json \"{ticket['upload_url']}\"\n"
                "The response lists how many chats were imported. The link works once.")
            return CallToolResult(content=[TextContent(type="text", text=instructions)],
                                  structured_content=ticket | {"instructions": instructions})

    server.add_tool(create_local_import, description=inspect.cleandoc(create_local_import.__doc__),
                    structured_output=False)
