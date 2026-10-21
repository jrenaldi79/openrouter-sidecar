"""Credential-scoped advisor service. No caller-selected containers or file APIs."""

from __future__ import annotations

import json
import secrets
from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field

from . import storage
from .advisor_client import AdvisorClient
from .advisor_payload import INSTRUCTIONS, SANDBOX_DISABLED, build_payload, files_bundle
from .advisor_store import AdvisorAccessError, AdvisorStore, Scope
from .credentials import CHANGED, CredentialSource
from .errors import BadRequestError, OpenRouterError, ProviderError
from .logs import redact
from .prompt_cache import cache_usage, configure_cache
from .session_files import FilePacket, SessionFiles, validate_files

__all__ = ["INSTRUCTIONS", "RETENTION", "AdvisorService", "FilePacket", "SkillPacket"]

RETENTION = (
    "Shareable skills only. Generated container home files may remain for 30 days "
    "after last use. Reset changes the working container; it does not erase the old "
    "provider container. Transcripts are stored locally, not as container files."
)


class SkillPacket(BaseModel):
    """Deliberately supplied shareable instructions; never local file paths."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    content: str = Field(min_length=1, max_length=100_000)
    source: str | None = Field(default=None, max_length=300)
    version: str | None = Field(default=None, max_length=100)


class AdvisorService:
    def __init__(self, workspace_id: str, store: AdvisorStore, client: AdvisorClient, *,
                 credentials: CredentialSource, sandbox_default: bool = True,
                 sandbox_allowed: bool = True):
        if workspace_id != client.workspace_id:
            raise ValueError("Advisor client workspace does not match configuration.")
        self.workspace_id, self.store, self.client = workspace_id, store, client
        self.files = SessionFiles(store)
        # The hosted server passes inline by default, with the sandbox behind a server flag.
        self.sandbox_default, self.sandbox_allowed = sandbox_default, sandbox_allowed
        self.credentials = credentials
        if self.credentials.workspace_id != workspace_id:
            raise ValueError("Advisor credentials workspace does not match configuration.")

    def _credentials(self) -> tuple[str, Scope]:
        return self.credentials.resolve()

    def _current(self, scope):
        if self.workspace_id != scope.workspace or self.client.workspace_id != scope.workspace:
            raise AdvisorAccessError(CHANGED)
        self.credentials.check(scope)

    @staticmethod
    def _summary(chat) -> dict:
        return {"chat_id": chat.id, "title": redact(chat.title), "model": chat.model}

    async def models(self) -> list[dict]:
        key, scope = self._credentials()
        rows = await self.client.models(key)
        self._current(scope)
        return [{name: row.get(name) for name in ("id", "name", "pricing", "context_length", "created")}
                for row in rows]

    async def start(self, model: str, title: str = "Advisor", *, sandbox: bool | None = None) -> dict:
        key, scope = self._credentials()
        sandbox = self.sandbox_default if sandbox is None else sandbox
        if type(sandbox) is not bool:
            raise BadRequestError("sandbox must be true or false.")
        if sandbox and not self.sandbox_allowed:
            raise BadRequestError(SANDBOX_DISABLED)
        if not isinstance(model, str) or not model or len(model) > 200:
            raise BadRequestError("Choose an exact advisor model ID from list_chat_models.")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise BadRequestError("Advisor title must contain 1 to 200 characters.")
        if key in title or key in model:
            raise BadRequestError("Advisor title or model contains the active credential.")
        if model not in {row.get("id") for row in await self.client.models(key)}:
            raise BadRequestError("Unknown or unsupported advisor model; call list_chat_models.")
        self._current(scope)
        chat = self.store.create(scope, model, redact(title), sandbox=sandbox)
        return self._summary(chat) | {"sandbox": sandbox, "storage_note": RETENTION}

    def list(self) -> list[dict]:
        _, scope = self._credentials()
        return [self._summary(chat) for chat in self.store.list(scope)]

    def get(self, chat_id: str, limit: int = 20) -> dict:
        _, scope = self._credentials()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BadRequestError("Message limit must be 1 to 100.")
        chat = self.store.get(scope, chat_id)
        return self._summary(chat) | {"messages": chat.history[-limit:],
                                      "cleanup_pending": bool(self.store.pending_uploads(scope, chat_id))}

    def reset(self, chat_id: str) -> dict:
        _, scope = self._credentials()
        return self._summary(self.store.reset(scope, chat_id)) | {"storage_note": RETENTION}

    def delete(self, chat_id: str) -> dict:
        _, scope = self._credentials()
        self.store.delete(scope, chat_id)
        return {"deleted": True, "storage_note": RETENTION}

    async def _cleanup(self, key, scope, chat_id, token) -> bool:
        ok = True
        for file_id in self.store.pending_uploads(scope, chat_id, token):
            # Recheck before each HTTP deletion, not merely after it completes.
            self.store.pending_uploads(scope, chat_id, token)
            try:
                await self.client.delete_upload(key, file_id)
            except OpenRouterError:
                ok = False  # Keep the exact owned receipt for a later explicit retry.
            else:
                self.store.forget_upload(scope, chat_id, file_id, token)
        return ok

    async def cleanup(self, chat_id: str) -> dict:
        key, scope = self._credentials()
        with self.store.lease(scope, chat_id) as token:
            complete = await self._cleanup(key, scope, chat_id, token)
        self._current(scope)
        return {"cleanup_pending": not complete}

    @staticmethod
    def _check_container(body: dict, container_id: str):
        def check(value):
            if isinstance(value, dict):
                if "container_id" in value and value["container_id"] != container_id:
                    raise ProviderError("Advisor returned an unexpected container reference.")
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(body)

    @staticmethod
    def _answer(body: dict, container_id: str) -> str:
        AdvisorService._check_container(body, container_id)
        if body.get("status") != "completed":
            raise ProviderError("Advisor did not complete; no automatic retry was made.")
        output = body.get("output")
        if not isinstance(output, list):
            raise ProviderError("Advisor returned no final answer.")
        messages = [item for item in output if isinstance(item, dict)
                    and item.get("type") == "message" and item.get("role") == "assistant"
                    and item.get("status") in (None, "completed")
                    and item.get("phase") in (None, "final_answer")]
        content = messages[-1].get("content") if messages else []
        if not isinstance(content, list):
            raise ProviderError("Advisor returned no final answer.")
        parts = [part["text"] for part in content if isinstance(part, dict)
                 and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
        result = "\n".join(parts).strip()
        if not result or len(result) > 100_000:
            raise ProviderError("Advisor returned an empty or oversized final answer.")
        return redact(result)

    def _prepare(self, chat_id, prompt, skills, key, scope, files=None):
        # Authorize before validation or any provider request, including cleanup.
        chat = self.store.get(scope, chat_id)
        if any(key in content for content in validate_files(files).values()):
            raise BadRequestError("A session file contains the active credential.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 80_000:
            raise BadRequestError("Advisor brief must contain 1 to 80000 characters.")
        skills = skills or []
        if len(skills) > 20 or len({skill.name for skill in skills}) != len(skills):
            raise BadRequestError("Supply at most 20 uniquely named shareable skills.")
        bundle = json.dumps([skill.model_dump(exclude_none=True) for skill in skills], ensure_ascii=False)
        if len(bundle.encode()) > 250_000 or key in prompt or key in bundle:
            raise BadRequestError("Skill packet is too large or contains the active credential.")
        brief = redact(prompt)
        if skills:
            brief += "\n\nShareable skill instructions (JSON):\n" + redact(bundle)
        return chat, skills, bundle, brief

    async def _upload(self, key, scope, chat_id, token, filename, data, content_type):
        file_id = await self.client.upload_skills(key, filename, data, content_type=content_type)
        try:
            self.store.record_upload(scope, chat_id, file_id, token)
        except (*storage.DB_ERRORS, OSError, AdvisorAccessError):
            # The confirmed ID belongs to this upload even if local receipt
            # persistence fails. Never enumerate the workspace to guess it.
            try:
                await self.client.delete_upload(key, file_id)
            except OpenRouterError:
                raise ProviderError(
                    "Skill upload receipt could not be saved and deletion "
                    "could not be confirmed. No inference was requested."
                ) from None
            raise ProviderError(
                "Skill upload receipt could not be saved; the confirmed "
                "upload was deleted. No inference was requested."
            ) from None
        return {"id": file_id, "filename": filename}

    async def _sandbox_uploads(self, key, scope, chat, token, skills, bundle, files, guard):
        """Sandbox chats only: skills JSON and a JSON bundle of the session files, each turn."""
        uploads = []
        stem = chat.id + "-" + secrets.token_hex(8)
        if skills:
            uploads.append(await self._upload(key, scope, chat.id, token, "nu_skills/" + stem + ".json",
                                              redact(bundle).encode(), "application/json")
                           | {"kind": "skills"})
            guard()
        if files:
            uploads.append(await self._upload(key, scope, chat.id, token, "nu_files/" + stem + ".json",
                                              files_bundle(files), "application/json")
                           | {"kind": "files"})
        return uploads

    async def _infer(self, key, chat, payload, on_text, guard):
        if on_text is None:
            return await self.client.respond(key, payload)

        def check_event(event):
            guard()
            self._check_container(event, chat.container_id)

        async def emit(text):  # check_event already guarded this event
            await on_text(redact(text).replace(key, "[credential withheld]"))

        return await self.client.respond_stream(key, payload, emit, check_event)

    def _sync_files(self, scope, chat_id, files, replace_files, brief):
        if files or replace_files:
            note = self.files.upsert(scope, chat_id, files, replace=replace_files).note()
            brief += "\n\n" + note if note else ""
        return self.files.current(scope, chat_id), brief

    async def send(self, chat_id: str, prompt: str, skills: list[SkillPacket] | None = None,
                   *, files: list[FilePacket] | None = None, replace_files: bool = False,
                   on_text=None, _scope=None, _lease_token=None, _guard=None,
                   _demo_limits=None, _before_request=None,
                   _stream_guard=None) -> dict:
        if _demo_limits not in (None, (256, 1)):
            raise ValueError('Invalid internal demo limits')
        key, scope = self._credentials()
        if _scope is not None:
            self._current(_scope)
            scope = _scope
        chat, skills, bundle, brief = self._prepare(chat_id, prompt, skills, key, scope, files)

        def guard():
            self._current(scope)
            if _guard:
                _guard()

        with (nullcontext(_lease_token) if _lease_token else self.store.lease(scope, chat_id)) as token:
            # Reload after acquiring the cross-instance mutation lease.
            chat = self.store.get(scope, chat_id)
            self.store.pending_uploads(scope, chat_id, token)
            guard()
            if sum(len(message["content"]) for message in chat.history) + len(brief) > 300_000:
                raise BadRequestError("Advisor history is too long; start a new chat with a concise handoff.")
            if not await self._cleanup(key, scope, chat_id, token):
                raise ProviderError("Skill upload cleanup failed; no new inference was requested.")
            current, brief = self._sync_files(scope, chat_id, files, replace_files, brief)
            try:
                guard()
                uploads = (await self._sandbox_uploads(key, scope, chat, token, skills, bundle,
                                                       current, guard) if chat.sandbox else [])
                guard()
                payload = build_payload(chat, brief, current, uploads, _demo_limits)
                configure_cache(payload, chat.id, allow_explicit=not uploads)
                if _before_request:
                    _before_request()
                # Jobs pass a cheaper per-event check; checkpoints keep the full guard.
                body = await self._infer(key, chat, payload, on_text, _stream_guard or guard)
                final = self._answer(body, chat.container_id).replace(key, "[credential withheld]")
                guard()
            finally:
                cleanup_ok = await self._cleanup(key, scope, chat_id, token)
            guard()
            self.store.append_turn(scope, chat_id, brief, final, token)
            usage = body.get("usage") or {}
            cost = usage.get("cost") if isinstance(usage, dict) else None
            return {"chat_id": chat_id, "model": chat.model, "answer": final,
                    "cost_usd": cost if type(cost) in (float, int) else None,
                    "cache_usage": cache_usage(usage),
                    "cleanup_pending": not cleanup_ok, "storage_note": RETENTION}
