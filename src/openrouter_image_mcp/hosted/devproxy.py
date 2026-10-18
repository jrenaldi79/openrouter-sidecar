"""Local development only: send OpenRouter traffic to a fake server on localhost."""

from __future__ import annotations

import httpx

OPENROUTER_HOST = "openrouter.ai"


class RedirectTransport(httpx.AsyncBaseTransport):
    """Rewrites openrouter.ai requests to `base_url`; refuses every other host."""

    def __init__(self, base_url: str, *, inner: httpx.AsyncBaseTransport | None = None):
        self.base = httpx.URL(base_url)
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != OPENROUTER_HOST:
            raise httpx.ConnectError(f"dev proxy refuses {request.url.host}", request=request)
        request.url = request.url.copy_with(scheme=self.base.scheme, host=self.base.host,
                                            port=self.base.port)
        request.headers["host"] = self.base.netloc.decode()
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()
