"""Entry point for `openrouter-image-mcp serve-http` (Railway, or local development)."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import uvicorn

from ..auth import AUTH_URL
from ..logs import configure_logging
from .app import build_hosted_server, transport_security
from .devproxy import RedirectTransport
from .openrouter_keys import exchange_code, verify_key
from .settings import load_hosted_settings

log = logging.getLogger(__name__)


def build_app(env: Mapping[str, str]):
    settings = load_hosted_settings(env)
    options = {}
    if settings.dev_openrouter_url:
        log.warning("DEVELOPMENT MODE: OpenRouter traffic goes to %s", settings.dev_openrouter_url)
        transport = RedirectTransport(settings.dev_openrouter_url)

        async def exchange(code, verifier):
            return await exchange_code(code, verifier, transport=transport)

        async def verify(key, workspace_id):
            return await verify_key(key, workspace_id, transport=transport)

        options = {"transport": transport, "exchange": exchange, "verify": verify,
                   "auth_url": settings.dev_openrouter_url + AUTH_URL.removeprefix("https://openrouter.ai")}
    if settings.issuer.startswith("http://"):
        log.warning("DEVELOPMENT MODE: accepting tokens from local issuer %s", settings.issuer)
    server = build_hosted_server(settings, **options)
    return server.streamable_http_app(transport_security=transport_security(settings)), settings


def serve(env: Mapping[str, str]) -> None:
    configure_logging()
    app, _settings = build_app(env)
    uvicorn.run(app, host=env.get("HOST", "0.0.0.0"), port=int(env.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips=env.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                timeout_graceful_shutdown=30, log_config=None)
