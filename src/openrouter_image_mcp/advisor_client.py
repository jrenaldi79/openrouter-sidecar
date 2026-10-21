"""Narrow advisor HTTP operations; credentials never become model-visible data."""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from .config import ADVISOR_MAX_TIMEOUT_S, API_BASE, APP_TITLE, APP_URL
from .error_bodies import error_info, moderation_error
from .errors import (
    KEY_REJECTED,
    AuthRequiredError,
    BadRequestError,
    ContainerFileTooLargeError,
    ForbiddenError,
    InsufficientCreditsError,
    OpenRouterError,
    ProviderError,
)
from .logs import redact

FILE_ID = re.compile(r"or_file_[A-Za-z0-9]+\Z")
CONTAINER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_STREAM_FRAME = 256_000
MAX_STREAM_BYTES = 20_000_000


def _status_error(response: httpx.Response) -> OpenRouterError:
    """A failed advisor call (body already read). 402 and 403 (credits or the key's budget) are typed so
    callers can say so; a 403 that is a moderation block is a `ModerationError`, as for images."""
    status = response.status_code
    message = (f"Advisor operation failed (HTTP {status}). "
               "Inference may have been billed; no automatic retry was made.")
    if status == 402:
        return InsufficientCreditsError(message)
    if status == 403:
        return moderation_error(*error_info(response)) or ForbiddenError(message)
    return ProviderError(message)


class AdvisorClient:
    def __init__(self, workspace_id: str, timeout_s: float, *, transport=None):
        self.workspace_id = workspace_id
        self._timeout_s = min(timeout_s, ADVISOR_MAX_TIMEOUT_S)
        self._http = httpx.AsyncClient(
            base_url=API_BASE + "/", timeout=self._timeout_s, transport=transport,
            follow_redirects=False, headers={"HTTP-Referer": APP_URL, "X-Title": APP_TITLE},
        )

    async def aclose(self):
        await self._http.aclose()

    async def _request(self, method, path, key, *, json=None, files=None, missing_ok=False):
        try:
            deadline = self._timeout_s if path == "responses" else 30
            async with asyncio.timeout(deadline):
                response = await self._http.request(
                    method, path, headers={"Authorization": "Bearer " + key}, json=json,
                    files=files, params={"workspace_id": self.workspace_id},
                    timeout=deadline,
                )
        except (httpx.TransportError, TimeoutError):
            raise ProviderError(
                "Advisor request could not be confirmed. Inference may have been billed; "
                "no automatic retry was made."
            ) from None
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code == 401:
            # Leave the stored key alone: account_status rechecks it with OpenRouter.
            raise AuthRequiredError(KEY_REJECTED)
        if not 200 <= response.status_code < 300:
            raise _status_error(response)
        if response.status_code == 204 or missing_ok:
            return None
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("Advisor returned an invalid JSON response.") from None
        if not isinstance(body, dict):
            raise ProviderError("Advisor returned an invalid response.")
        return body

    async def models(self, key: str) -> list[dict]:
        body = await self._request("GET", "models", key)
        models = body.get("data")
        if not isinstance(models, list):
            raise ProviderError("Advisor model catalog is unavailable.")
        return [item for item in models if isinstance(item, dict)
                and (item.get("architecture") or {}).get("output_modalities") == ["text"]
                and "tools" in (item.get("supported_parameters") or [])]

    async def upload_skills(self, key: str, filename: str, content: bytes, *,
                            content_type: str = "application/json") -> str:
        body = await self._request("POST", "files", key,
                                   files={"file": (filename, content, content_type)})
        file_id = body.get("id")
        if not isinstance(file_id, str) or not FILE_ID.fullmatch(file_id):
            raise ProviderError("Skill upload returned an invalid file identifier.")
        return file_id

    async def delete_upload(self, key: str, file_id: str):
        if not FILE_ID.fullmatch(file_id):
            raise ProviderError("Skill cleanup rejected an invalid file identifier.")
        await self._request("DELETE", "files/" + file_id, key, missing_ok=True)

    async def respond(self, key: str, payload: dict) -> dict:
        # This adapter is internal. MCP callers can never supply this payload.
        return await self._request("POST", "responses", key, json=payload)

    async def container_file(self, key: str, container_id: str, file_id: str, *,
                             max_bytes: int) -> bytes:
        """Download a sandbox output file, refusing anything over `max_bytes`."""
        # Ids come from model output; the pattern keeps them from altering the path.
        if not (CONTAINER_ID.fullmatch(container_id) and CONTAINER_ID.fullmatch(file_id)):
            raise BadRequestError("Sandbox file reference is invalid.")
        too_big = ContainerFileTooLargeError(f"Sandbox file is larger than {max_bytes} bytes.")
        chunks, total = [], 0
        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._http.stream(
                    "GET", f"containers/{container_id}/files/{file_id}/content",
                    headers={"Authorization": "Bearer " + key},
                    params={"workspace_id": self.workspace_id}, timeout=self._timeout_s,
                ) as response:
                    if response.status_code == 401:
                        raise AuthRequiredError(KEY_REJECTED)
                    if not 200 <= response.status_code < 300:
                        raise ProviderError(redact(
                            f"Sandbox file download failed (HTTP {response.status_code})."))
                    declared = response.headers.get("content-length", "")
                    if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
                        raise too_big
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise too_big
                        chunks.append(chunk)
        except (httpx.HTTPError, TimeoutError):
            raise ProviderError(redact("Sandbox file download could not be completed.")) from None
        return b"".join(chunks)

    async def respond_stream(self, key: str, payload: dict, on_text, check_event) -> dict:
        """Bounded SSE reader. Only final assistant output text reaches the callback.

        Reasoning, shell output and commentary are ignored. The caller validates
        ownership and container references on every decoded event before use.
        """
        total = 0
        buffer = b""
        current_index = None
        text = ""
        completed = None

        async def frame(raw):
            nonlocal current_index, text, completed
            try:
                lines = raw.decode("utf-8").splitlines()
                data = "\n".join(line[5:].lstrip(" ") for line in lines if line.startswith("data:"))
                if not data or data == "[DONE]":
                    return
                body = json.loads(data)
            except (UnicodeError, ValueError):
                raise ProviderError("Advisor returned a malformed stream.") from None
            if not isinstance(body, dict) or not isinstance(body.get("type"), str):
                raise ProviderError("Advisor returned a malformed stream event.")
            event_names = [line[6:].strip() for line in lines if line.startswith("event:")]
            if event_names and (len(event_names) != 1 or event_names[0] != body["type"]):
                raise ProviderError("Advisor returned a mismatched stream event.")
            check_event(body)
            kind = body["type"]
            if completed is not None:
                raise ProviderError("Advisor returned data after its final response.")
            if kind == "response.output_item.added":
                item = body.get("item")
                index = body.get("output_index")
                if not isinstance(item, dict) or type(index) is not int or index < 0:
                    raise ProviderError("Advisor returned a malformed output item.")
                if (item.get("type") == "message" and item.get("role") == "assistant"
                        and item.get("phase") in (None, "final_answer")):
                    current_index, text = index, ""
            elif kind == "response.output_text.delta":
                delta = body.get("delta")
                index = body.get("output_index")
                if not isinstance(delta, str) or type(index) is not int or index < 0:
                    raise ProviderError("Advisor returned a malformed text delta.")
                if index == current_index:
                    text += delta
                    if len(text) > 100_000:
                        raise ProviderError("Advisor returned an oversized answer stream.")
                    await on_text(text)
            elif kind == "response.completed":
                if not isinstance(body.get("response"), dict):
                    raise ProviderError("Advisor returned a malformed final response.")
                completed = body["response"]
            elif kind in ("response.failed", "response.incomplete", "error"):
                raise ProviderError("Advisor did not complete; no automatic retry was made.")

        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._http.stream(
                    "POST", "responses", headers={"Authorization": "Bearer " + key},
                    json=payload | {"stream": True}, params={"workspace_id": self.workspace_id},
                    timeout=self._timeout_s,
                ) as response:
                    if response.status_code == 401:
                        raise AuthRequiredError(KEY_REJECTED)
                    if not 200 <= response.status_code < 300:
                        if response.status_code == 403:  # small JSON: read it for a moderation block
                            await response.aread()
                        raise _status_error(response)
                    if "text/event-stream" not in response.headers.get("content-type", "").lower():
                        raise ProviderError("Advisor returned an invalid stream content type.")
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_STREAM_BYTES:
                            raise ProviderError("Advisor returned an oversized stream.")
                        # SSE accepts CRLF and LF. Bound each frame even when a
                        # transport delivers many frames in a single chunk.
                        buffer += chunk
                        while True:
                            boundary = re.search(rb"\r?\n\r?\n", buffer)
                            if boundary is None:
                                break
                            if boundary.start() > MAX_STREAM_FRAME:
                                raise ProviderError("Advisor returned an oversized stream frame.")
                            await frame(buffer[:boundary.start()])
                            buffer = buffer[boundary.end():]
                        if len(buffer) > MAX_STREAM_FRAME:
                            raise ProviderError("Advisor returned an oversized stream frame.")
                    if buffer.strip():
                        raise ProviderError("Advisor returned a truncated stream frame.")
        except (httpx.TransportError, TimeoutError):
            raise ProviderError(
                "Advisor request could not be confirmed. Inference may have been billed; "
                "no automatic retry was made."
            ) from None
        if completed is None:
            raise ProviderError("Advisor stream ended without a completed response; no automatic retry was made.")
        return completed
