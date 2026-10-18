import httpx
import pytest

from openrouter_image_mcp import cli
from openrouter_image_mcp.hosted.devproxy import RedirectTransport


def test_serve_http_builds_app_and_runs_uvicorn(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("AUTH0_ISSUER", "https://tenant.us.auth0.com/")
    monkeypatch.setenv("MCP_RESOURCE_URI", "http://localhost:8123/mcp")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("VAULT_KEYS", "v1:" + "A" * 43 + "=")
    monkeypatch.setenv("LINK_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    assert cli.main(["serve-http"]) == 0
    assert seen["port"] == 8123 and seen["host"] == "0.0.0.0" and callable(seen["app"])


def test_serve_http_reports_bad_config(monkeypatch, capsys):
    for name in ("AUTH0_ISSUER", "MCP_RESOURCE_URI", "DATABASE_URL", "VAULT_KEYS", "LINK_SIGNING_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["serve-http"]) == 1
    assert "Missing hosted settings" in capsys.readouterr().err


async def test_redirect_transport_rewrites_openrouter_only():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    transport = RedirectTransport("http://127.0.0.1:9500", inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://openrouter.ai/api/v1/models?x=1")
        with pytest.raises(httpx.ConnectError):
            await client.get("https://elsewhere.example/")
    assert seen == ["http://127.0.0.1:9500/api/v1/models?x=1"]
