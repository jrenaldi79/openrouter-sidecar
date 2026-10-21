"""The /admin page served by the hosted server: off, not configured, configured, and with Slack off."""

import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from markupsafe import escape

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.slack.registry import new_row
from tests import jwt_helpers as h
from tests.hosted_harness import running_server

CLIENT_ID = "admin-client"
CONFIGURED = {"SIDECAR_ADMIN_CLIENT_ID": CLIENT_ID, "SIDECAR_ADMIN_CLIENT_SECRET": "s" * 40}


def _http(running) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{running.port}", follow_redirects=False)


async def test_disabled_is_404(db_target):
    async with running_server(db_target, env={"SIDECAR_ADMIN_ENABLED": "false"} | CONFIGURED) as running:
        assert running.server.hosted["admin"] is None
        async with _http(running) as http:
            for path in ("/admin", "/admin/login", "/admin/public/admin.css", "/admin/log"):
                assert (await http.get(path)).status_code == 404, path


async def test_not_configured_page_and_warning(db_target, caplog):
    with caplog.at_level(logging.WARNING):
        async with running_server(db_target) as running:
            async with _http(running) as http:
                for path in ("/admin", "/admin/login", "/admin/log"):
                    response = await http.get(path)
                    assert response.status_code == 503 and escape(copy.NOT_CONFIGURED) in response.text, path
                assert (await http.get("/admin/public/admin.css")).status_code == 200
            assert running.server.hosted["admin"]["login"] is None
    warnings = [r.getMessage() for r in caplog.records if "Admin sign-in is not configured" in r.getMessage()]
    assert len(warnings) == 1
    assert "SIDECAR_ADMIN_CLIENT_ID" in warnings[0] and "SIDECAR_ADMIN_CLIENT_SECRET" in warnings[0]


async def test_configured_redirects_to_auth0(db_target, caplog):
    with caplog.at_level(logging.WARNING):
        async with (running_server(db_target, env=CONFIGURED, admin={"key_resolver": h.resolver}) as running,
                    _http(running) as http):
            first = await http.get("/admin")
            assert first.status_code == 302 and first.headers["location"] == "/admin/login"
            login = await http.get("/admin/login")
    assert login.status_code == 302
    target = urlsplit(login.headers["location"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}/" == h.ISSUER
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [f"http://127.0.0.1:{running.port}/admin/callback"]
    assert "sidecar_admin_login=" in login.headers["set-cookie"]
    assert not [r for r in caplog.records if "not configured" in r.getMessage()]
    assert CONFIGURED["SIDECAR_ADMIN_CLIENT_SECRET"] not in login.headers["location"]


async def test_slack_off_lists_rows_read_only(db_target):
    async with running_server(db_target, env=CONFIGURED) as running:
        pieces = running.server.hosted["admin"]
        assert running.server.hosted["slack"] is None
        pieces["channels"].registry.add(new_row("T0ADMIN01", "C0ADMIN01", "team-a", "2027", fusion_panel=("a/one",),
                                                fusion_analyst="d/an", subagent_model="e/sub"))
        raw_id, _ = pieces["sessions"].create("email|admin", "admin@example.edu")
        async with _http(running) as http:
            http.cookies.set("sidecar_admin", raw_id, domain="127.0.0.1", path="/admin")
            page = await http.get("/admin")
            assert page.status_code == 200
            assert "team-a" in page.text and escape(copy.SLACK_OFF) in page.text
            # With no team models to read, the edit page is a plain refusal, never an error page.
            edit = await http.get("/admin/channels/T0ADMIN01/C0ADMIN01")
            assert edit.status_code == 422 and escape(copy.SLACK_OFF) in edit.text
            # Nothing can be registered without a Slack client: the form is refused, no row appears.
            form = await http.get("/admin/channels/new?channel=C0ADMIN02")
            assert form.status_code == 422 and escape(copy.SLACK_OFF) in form.text
        assert [row.channel_id for row in pieces["channels"].registry.all()] == ["C0ADMIN01"]


async def test_admin_uses_the_slack_apps_registry_when_slack_is_on(db_target):
    from slack_fakes import FakeSlack

    env = {"SIDECAR_SLACK_ENABLED": "1", "SLACK_BOT_TOKEN": "xoxb-" + "1" * 10,
           "SLACK_APP_TOKEN": "xapp-" + "1" * 10} | CONFIGURED
    api = FakeSlack()
    async with running_server(db_target, env=env, slack={"api": api}) as running:
        slack_app = running.server.hosted["slack"]
        channels = running.server.hosted["admin"]["channels"]
        assert channels.registry is slack_app.registry and channels.api is slack_app.api is api
        assert channels.services is slack_app.services


@pytest.mark.parametrize("env", [{}, CONFIGURED, {"SIDECAR_ADMIN_ENABLED": "false"}])
async def test_mcp_unaffected(db_target, env):
    async with running_server(db_target, env=env) as running:
        async with httpx.AsyncClient() as http:
            body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            accept = {"Accept": "application/json, text/event-stream"}
            assert (await http.post(running.url, json=body, headers=accept)).status_code == 401
            ready = await http.get(f"http://127.0.0.1:{running.port}/health")
            assert ready.status_code == 200
        async with running.client(running.token()) as client:
            assert (await client.list_tools()).tools
