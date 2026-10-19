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


def test_purge_dry_run_then_apply(monkeypatch, tmp_path, capsys):
    import json
    import time

    from openrouter_image_mcp import storage
    from openrouter_image_mcp.advisor_store import AdvisorStore, Scope

    path = tmp_path / "db.sqlite3"
    store = AdvisorStore(path)
    chat = store.create(Scope("o", "w"), "m", "t")
    with storage.connect(path) as db:
        db.execute("UPDATE advisor_chats SET last_active=? WHERE id=?", (time.time() - 100 * 86400, chat.id))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(path))
    assert cli.main(["purge"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["chats"] == 1 and dry["applied"] is False and chat.id not in json.dumps(dry)
    assert cli.main(["purge", "--apply", "--days", "30"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["chats"] == 1 and applied["applied"] is True and applied["retention_days"] == 30
    assert store.list(Scope("o", "w")) == []


def test_purge_requires_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.main(["purge"]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err  # message names the variable


def test_active_jobs_command(monkeypatch, tmp_path, capsys):
    import json

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "db.sqlite3"))
    assert cli.main(["active-jobs"]) == 0
    assert json.loads(capsys.readouterr().out) == {"active_jobs": 0}
