"""Sign-in, callback and sign-out through the routes, with a fake Auth0 login."""

import httpx
import pytest
from admin_routes_rig import AUTHORIZE, IDENTITY, LOCAL, LOGOUT, AdminRig
from markupsafe import escape

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.routes_base import LOGIN_LIMIT
from openrouter_image_mcp.hosted.admin.sessions import (
    ABSOLUTE_S,
    LOGIN_S,
    TooManyLogins,
)


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    yield rig
    await rig.aclose()


def _cookie(response, name):
    """The Set-Cookie line for `name`, lower-cased, or None."""
    return next((v.lower() for v in response.headers.get_list("set-cookie") if v.startswith(f"{name}=")), None)


async def test_login_sets_login_cookie(rig):
    response = await rig.client.get("/admin/login")
    assert (response.status_code, response.headers["location"]) == (302, AUTHORIZE)
    cookie = _cookie(response, "sidecar_admin_login")
    assert cookie.startswith("sidecar_admin_login=state-1;")
    for part in (f"max-age={LOGIN_S}", "path=/admin", "httponly", "samesite=lax", "secure"):
        assert part in cookie
    assert LOGIN_S == 600
    assert rig.client.cookies.get("sidecar_admin") is None


async def test_cookie_secure_only_off_localhost(db_target):
    local = AdminRig(db_target, origin=LOCAL)
    try:
        started = await local.client.get("/admin/login")
        assert "secure" not in _cookie(started, "sidecar_admin_login")
        assert "httponly" in _cookie(started, "sidecar_admin_login")
        called = await local.client.get("/admin/callback?code=c&state=state-1")
        assert "secure" not in _cookie(called, "sidecar_admin")
    finally:
        await local.aclose()
    hosted = AdminRig(db_target)
    try:
        called = await hosted.client.get("/admin/callback?code=c&state=state-1")
        assert "secure" in _cookie(called, "sidecar_admin") and "secure" in _cookie(called, "sidecar_admin_login")
    finally:
        await hosted.aclose()


async def test_callback_sets_session_and_clears_login_cookie(rig):
    rig.client.cookies.set("sidecar_admin_login", "state-1", domain="sidecar.example", path="/admin")
    response = await rig.client.get("/admin/callback?code=the-code&state=state-1")
    assert (response.status_code, response.headers["location"]) == (302, "/admin")
    assert rig.login.finished == [("the-code", "state-1", "state-1")]  # code, query state, the browser's cookie
    session_cookie = _cookie(response, "sidecar_admin")
    for part in (f"max-age={ABSOLUTE_S}", "path=/admin", "httponly", "samesite=lax", "secure"):
        assert part in session_cookie
    assert "max-age=0" in _cookie(response, "sidecar_admin_login")
    raw_id = rig.client.cookies.get("sidecar_admin", path="/admin")
    session = rig.sessions.get(raw_id)
    assert (session.subject, session.email) == (IDENTITY.subject, IDENTITY.email)
    assert (await rig.client.get("/admin")).status_code == 200  # the jar now holds the session


async def test_callback_passes_a_missing_login_cookie_on(rig):
    await rig.client.get("/admin/callback?code=c&state=s")
    assert rig.login.finished == [("c", "s", None)]  # `Auth0Login.finish` refuses it; the route doesn't decide


async def test_not_admin_page(rig):
    rig.login.identity, rig.login.reason = None, "not_admin"
    response = await rig.client.get("/admin/callback?code=c&state=s")
    assert response.status_code == 403 and str(escape(copy.NOT_ADMIN)) in response.text
    assert _cookie(response, "sidecar_admin") is None
    assert "max-age=0" in _cookie(response, "sidecar_admin_login")
    # Signed in at Auth0 with the wrong account and no session here: a plain link to sign out there.
    assert f'<a href="{escape(LOGOUT)}">Sign out</a>' in response.text
    assert "<form" not in response.text


@pytest.mark.parametrize("reason", ["state", "exchange", "id_token"])
async def test_login_failed_page(rig, reason):
    rig.login.identity, rig.login.reason = None, reason
    response = await rig.client.get("/admin/callback?code=c&state=s")
    assert response.status_code == 400 and str(escape(copy.LOGIN_FAILED)) in response.text
    assert _cookie(response, "sidecar_admin") is None
    assert 'href="/admin"' in response.text  # the way back to try again


@pytest.mark.parametrize("query", ["", "?state=s", "?code=c", "?error=access_denied&state=s", "?code=&state="])
async def test_callback_without_code_and_state_is_a_failed_login(rig, query):
    response = await rig.client.get("/admin/callback" + query)
    assert response.status_code == 400 and str(escape(copy.LOGIN_FAILED)) in response.text
    assert rig.login.finished == []


async def test_login_rate_limit(rig):
    for _ in range(LOGIN_LIMIT):
        assert (await rig.client.get("/admin/login")).status_code == 302
    response = await rig.client.get("/admin/login")
    assert response.status_code == 429 and copy.TOO_MANY_LOGINS in response.text
    assert _cookie(response, "sidecar_admin_login") is None
    assert rig.login.started == LOGIN_LIMIT  # the refused ones never started a pending login


async def test_login_rate_limit_is_per_address(rig):
    for _ in range(LOGIN_LIMIT + 1):
        await rig.client.get("/admin/login")
    other = httpx.AsyncClient(transport=httpx.ASGITransport(rig.app, client=("203.0.113.77", 1)),
                              base_url="https://sidecar.example")
    try:
        assert (await other.get("/admin/login")).status_code == 302
    finally:
        await other.aclose()


async def test_too_many_pending_logins(rig):
    rig.login.start_error = TooManyLogins("full")
    response = await rig.client.get("/admin/login")
    assert response.status_code == 429 and copy.TOO_MANY_LOGINS in response.text
    assert _cookie(response, "sidecar_admin_login") is None


async def test_logout_clears_session(rig):
    rig.sign_in()
    response = await rig.post("/admin/logout")
    assert (response.status_code, response.headers["location"]) == (302, LOGOUT)
    cookie = _cookie(response, "sidecar_admin")
    assert "max-age=0" in cookie and "path=/admin" in cookie
    assert rig.sessions.get(rig.session_id) is None
    rig.client.cookies.clear()
    assert (await rig.client.get("/admin")).status_code == 302


async def test_callback_ends_the_session_the_browser_held(rig):
    first = rig.sign_in()
    assert first and rig.sessions.get(rig.session_id) is not None
    old = rig.session_id
    response = await rig.client.get("/admin/callback?code=c&state=state-1")
    assert response.status_code == 302
    assert rig.sessions.get(old) is None  # replaced, not left to expire
    assert (await rig.client.get("/admin")).status_code == 200  # the new session works


async def test_other_failures_have_no_sign_out_link(rig):
    rig.login.identity, rig.login.reason = None, "exchange"
    response = await rig.client.get("/admin/callback?code=c&state=s")
    assert "Sign out" not in response.text
