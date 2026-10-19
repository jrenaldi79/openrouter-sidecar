"""OpenRouter HTTP client for image generation, editing and the model catalog."""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from .config import API_BASE, APP_TITLE, APP_URL
from .credentials import CredentialSource
from .errors import (
    AuthRequiredError,
    BadRequestError,
    CatalogUnavailableError,
    ForbiddenError,
    InsufficientCreditsError,
    ModerationError,
    NoImageError,
    ProviderError,
    RateLimitError,
)

_MSG_401 = (
    "Your OpenRouter sign-in is no longer valid (revoked or deleted). "
    "Call `link_openrouter` to link it again."
)
_MSG_401_ANONYMOUS = "OpenRouter rejected the request (HTTP 401)."
_MSG_402 = "The OpenRouter workspace is out of credits. Ask the org admin to top up."
_MSG_429 = "Rate limited by OpenRouter (HTTP 429). Try again shortly."

_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BACKOFF = (1.0, 2.0)
_SERVER_ERROR_DELAY = 1.0
_MAX_RETRY_AFTER = 30.0
_MAX_BODY_CHARS = 300  # raw (non-JSON) bodies quoted in error messages


def _body_text(resp: httpx.Response) -> str:
    """The response body for an error message, cut short: it may be a whole HTML page."""
    text = resp.text.strip()
    return text if len(text) <= _MAX_BODY_CHARS else text[:_MAX_BODY_CHARS] + "…"


@dataclass
class GeneratedImage:
    data: bytes
    media_type: str | None


@dataclass
class GenerationResult:
    images: list[GeneratedImage]
    cost_usd: float | None = None
    usage: dict = field(default_factory=dict)
    generation_id: str | None = None
    provider: str | None = None
    raw_text: str | None = None


def _error_info(resp: httpx.Response) -> tuple[dict, str]:
    """Return (error object, human message) from an error response."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return error, message
        return error, _body_text(resp)
    if isinstance(error, str) and error:
        return {}, error
    return {}, _body_text(resp)


def _moderation_error(error: dict, message: str) -> ModerationError | None:
    metadata = error.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    reasons = metadata.get("reasons")
    if isinstance(reasons, list) and reasons:
        return ModerationError(
            [str(r) for r in reasons],
            metadata.get("flagged_input"),
            metadata.get("provider_name"),
        )
    haystack = f"{error.get('code', '')} {message}".lower()
    if "content_policy" in haystack or "moderation" in haystack:
        return ModerationError(
            [message or "content policy"],
            metadata.get("flagged_input"),
            metadata.get("provider_name"),
        )
    return None


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("Retry-After")
    if header is not None:
        try:
            seconds = float(header)
        except ValueError:
            seconds = math.nan
        if math.isfinite(seconds):
            return min(max(0.0, seconds), _MAX_RETRY_AFTER)
    return _RATE_LIMIT_BACKOFF[attempt]


def _decode_image(payload: str) -> bytes:
    try:
        data = base64.b64decode(payload)
    except (binascii.Error, ValueError):
        raise ProviderError("OpenRouter returned image data that could not be decoded.") from None
    if not data:
        raise ProviderError("OpenRouter returned an empty image.")
    return data


def _split_data_url(url: str) -> tuple[bytes, str | None]:
    header, _, payload = url.partition(",")
    if not header.startswith("data:") or not payload:
        raise ProviderError("OpenRouter returned an image URL that is not a base64 data URL.")
    media_type = header[len("data:") :].split(";", 1)[0] or None
    return _decode_image(payload), media_type


class OpenRouterClient:
    def __init__(
        self,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        credentials: CredentialSource,
        workspace_id: str | None = None,
    ) -> None:
        self.workspace_id = workspace_id or credentials.workspace_id
        self.credentials = credentials
        self._timeout_s = timeout_s
        # Trailing slash + relative request paths keep the /api/v1 prefix.
        self._http = httpx.AsyncClient(
            base_url=API_BASE + "/",
            timeout=timeout_s,
            transport=transport,
            headers={"HTTP-Referer": APP_URL, "X-Title": APP_TITLE},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core

    async def _send(
        self, method: str, path: str, *, headers: dict, json: Any, params: Any, catalog: bool
    ) -> httpx.Response:
        try:
            return await self._http.request(method, path, headers=headers, json=json, params=params)
        except httpx.TransportError as exc:
            # Catalog check first: timeouts are TransportErrors too, and the
            # catalog's stale-cache fallback keys off CatalogUnavailableError.
            if catalog:
                raise CatalogUnavailableError(
                    f"Couldn't reach the OpenRouter catalog: {exc}"
                ) from None
            if isinstance(exc, httpx.TimeoutException):
                raise ProviderError(
                    f"OpenRouter request timed out after {self._timeout_s:g}s. Not charged."
                ) from None
            raise ProviderError(f"Couldn't reach OpenRouter: {exc}") from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json: Any = None,
        params: Any = None,
        catalog: bool = False,
    ) -> Any:
        headers: dict[str, str] = {}
        if auth:
            key = self.credentials.resolve()[0]  # the source says how to link a key
            headers["Authorization"] = f"Bearer {key}"

        rate_retries = 0
        server_retried = False
        while True:
            resp = await self._send(
                method, path, headers=headers, json=json, params=params, catalog=catalog
            )
            status = resp.status_code
            if status < 400:
                try:
                    return resp.json()
                except ValueError:
                    message = f"OpenRouter returned a non-JSON response (HTTP {status})."
                    if catalog:
                        raise CatalogUnavailableError(message) from None
                    raise ProviderError(message) from None

            if status == 429:
                if rate_retries < _RATE_LIMIT_RETRIES:
                    await asyncio.sleep(_retry_after(resp, rate_retries))
                    rate_retries += 1
                    continue
                if catalog:
                    raise CatalogUnavailableError(
                        f"Couldn't reach the OpenRouter catalog (HTTP {status})."
                    )
                raise RateLimitError(_MSG_429)

            error, message = _error_info(resp)

            if status >= 500:
                if not server_retried:
                    server_retried = True
                    await asyncio.sleep(_SERVER_ERROR_DELAY)
                    continue
                if catalog:
                    raise CatalogUnavailableError(
                        f"Couldn't reach the OpenRouter catalog (HTTP {status})."
                    )
                raise ProviderError(
                    f"OpenRouter/provider error (HTTP {status}): {message}. "
                    "The generation failed and was not charged "
                    "(image billing is all-or-nothing)."
                )

            self._raise_client_error(status, error, message, auth)

    def _raise_client_error(self, status: int, error: dict, message: str, auth: bool) -> None:
        if status == 401:
            if auth:
                self.credentials.forget()
                raise AuthRequiredError(_MSG_401)
            raise AuthRequiredError(_MSG_401_ANONYMOUS)
        if status == 402:
            raise InsufficientCreditsError(_MSG_402)
        moderation = _moderation_error(error, message)
        if moderation is not None:
            raise moderation
        if status == 403:
            raise ForbiddenError(message)
        raise BadRequestError(message)

    # ------------------------------------------------------------ generation

    @staticmethod
    def _result(
        body: dict, images: list[GeneratedImage], raw_text: str | None = None
    ) -> GenerationResult:
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        cost = usage.get("cost")
        return GenerationResult(
            images=images,
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            usage=usage,
            generation_id=body.get("id"),
            provider=body.get("provider"),
            raw_text=raw_text,
        )

    async def images(self, payload: dict) -> GenerationResult:
        body = await self._request("POST", "images", auth=True, json=payload)
        images = [
            GeneratedImage(_decode_image(item["b64_json"]), item.get("media_type"))
            for item in body.get("data") or []
            if isinstance(item, dict) and item.get("b64_json")
        ]
        if not images:
            raise NoImageError("")
        return self._result(body, images)

    async def chat_image(
        self,
        model: str,
        prompt: str,
        data_urls: list[str],
        aspect_ratio: str | None,
    ) -> GenerationResult:
        content: list[dict] = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in data_urls]
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
        }
        if aspect_ratio:
            payload["image_config"] = {"aspect_ratio": aspect_ratio}
        body = await self._request("POST", "chat/completions", auth=True, json=payload)

        choices = body.get("choices") or [{}]
        message = choices[0].get("message") or {}
        text = message.get("content")
        text = text if isinstance(text, str) else None
        images = []
        for item in message.get("images") or []:
            url = ((item or {}).get("image_url") or {}).get("url")
            if isinstance(url, str) and url:
                data, media_type = _split_data_url(url)
                images.append(GeneratedImage(data, media_type))
        if not images:
            raise NoImageError(text or "")
        return self._result(body, images, text)

    # ------------------------------------------------------------- account

    async def key_info(self) -> dict:
        body = await self._request("GET", "key", auth=True)
        return body.get("data", body)

    async def generation(self, gen_id: str) -> dict:
        body = await self._request("GET", "generation", auth=True, params={"id": gen_id})
        return body.get("data", body)

    # -------------------------------------------------------------- catalog

    @staticmethod
    def _as_list(body: Any) -> list[dict]:
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, dict):
            data = data.get("endpoints", data.get("models", []))
        return data if isinstance(data, list) else []

    async def image_models(self) -> list[dict]:
        body = await self._request("GET", "images/models", auth=False, catalog=True)
        return self._as_list(body)

    async def image_model_endpoints(self, model_id: str) -> list[dict]:
        body = await self._request(
            "GET", f"images/models/{model_id}/endpoints", auth=False, catalog=True
        )
        return self._as_list(body)

    async def image_models_meta(self) -> list[dict]:
        body = await self._request(
            "GET",
            "models",
            auth=False,
            params={"output_modalities": "image"},
            catalog=True,
        )
        return self._as_list(body)
