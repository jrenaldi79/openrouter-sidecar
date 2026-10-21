import base64
import hashlib
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt_helpers as jh
import pytest
import respx

from openrouter_image_mcp.hosted.admin.oidc import (
    ADMIN_PERMISSIONS,
    Auth0Login,
    LoginRefused,
)
from openrouter_image_mcp.hosted.admin.sessions import PendingLogins
from openrouter_image_mcp.hosted.admin.settings import AdminSettings

CLIENT_ID = "admin-client"
SECRET = "s" * 40
CODE = "code-" + "c" * 20
TOKEN_URL = jh.ISSUER + "oauth/token"
SETTINGS = AdminSettings(enabled=True, client_id=CLIENT_ID, client_secret=SECRET, issuer=jh.ISSUER,
                         resource=jh.RESOURCE, public_origin="https://sidecar.example")


@pytest.fixture
def login(db_target):
    return Auth0Login(SETTINGS, PendingLogins(db_target), key_resolver=jh.resolver)


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _access(permissions=("sidecar:use", "sidecar:admin"), **overrides):
    return jh.token(permissions=list(permissions), **overrides)


def _start(login, mock, *, access=None, id_token=None, status=200):
    """Begin a login and stub the token endpoint with an ID token carrying the login's nonce."""
    url, state = login.start()
    body = {"access_token": access or _access(),
            "id_token": id_token or jh.id_token(CLIENT_ID, _query(url)["nonce"]), "token_type": "Bearer"}
    mock.post(TOKEN_URL).mock(return_value=httpx.Response(status, json=body))
    return state


async def _refused(login, mock, **kwargs) -> str:
    state = _start(login, mock, **kwargs)
    with pytest.raises(LoginRefused) as refused:
        await login.finish(CODE, state, state)
    return refused.value.reason


def test_start_url_has_pkce_state_nonce_audience(login, db_target):
    url, state = login.start()
    parts = urlsplit(url)
    q = _query(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == jh.ISSUER + "authorize"
    assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID and q["scope"] == "openid"
    assert q["redirect_uri"] == "https://sidecar.example/admin/callback"
    assert q["audience"] == jh.RESOURCE and q["state"] == state and q["code_challenge_method"] == "S256"
    pending = PendingLogins(db_target).take(state)
    assert q["nonce"] == pending.nonce
    digest = hashlib.sha256(pending.verifier.encode()).digest()
    assert q["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def test_good_login(login):
    with respx.mock() as mock:
        state = _start(login, mock)
        identity = await login.finish(CODE, state, state)
        sent = parse_qs(mock.calls.last.request.content.decode())
    assert (identity.subject, identity.email) == ("email|alice", "alice@u.northwestern.edu")
    assert sent["grant_type"] == ["authorization_code"] and sent["client_id"] == [CLIENT_ID]
    assert sent["client_secret"] == [SECRET] and sent["code"] == [CODE]
    assert sent["redirect_uri"] == ["https://sidecar.example/admin/callback"]
    assert len(sent["code_verifier"][0]) >= 43


@pytest.mark.parametrize("cookie", [None, "someone-elses-state"])
async def test_callback_needs_login_cookie(login, cookie):
    with respx.mock(assert_all_called=False) as mock:
        state = _start(login, mock)
        with pytest.raises(LoginRefused) as refused:
            await login.finish(CODE, state, cookie)
        assert mock.calls.call_count == 0
    assert refused.value.reason == "state"


async def test_missing_admin_permission(login):
    with respx.mock() as mock:
        assert await _refused(login, mock, access=_access(["sidecar:use"])) == "not_admin"


async def test_missing_use_permission(login):
    with respx.mock() as mock:
        assert await _refused(login, mock, access=_access(["sidecar:admin"])) == "not_admin"


async def test_no_profile_email(login):
    profile = {"cohort": "2027", "workspace_id": jh.WORKSPACE}
    with respx.mock() as mock:
        assert await _refused(login, mock, access=_access(**{jh.PROFILE: profile})) == "not_admin"


async def test_admin_without_workspace_signs_in(login):
    access = _access(**{jh.PROFILE: {"email": "root@u.northwestern.edu"}})
    with respx.mock() as mock:
        state = _start(login, mock, access=access)
        identity = await login.finish(CODE, state, state)
    assert identity.email == "root@u.northwestern.edu"


async def test_unknown_or_reused_state(login):
    with respx.mock() as mock:
        state = _start(login, mock)
        await login.finish(CODE, state, state)
        for bad in (state, "never-issued"):
            with pytest.raises(LoginRefused) as refused:
                await login.finish(CODE, bad, bad)
            assert refused.value.reason == "state"


async def test_wrong_nonce(login):
    with respx.mock() as mock:
        assert await _refused(login, mock, id_token=jh.id_token(CLIENT_ID, "not-the-nonce")) == "id_token"


async def test_hs256_id_token_refused(login):
    forged = jh.id_token(CLIENT_ID, "n", key="k" * 32, alg="HS256")
    with respx.mock() as mock:
        assert await _refused(login, mock, id_token=forged) == "id_token"


async def test_exchange_failure_logs_status_only(login, caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock() as mock:
        _, state = login.start()
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(403, text=f"denied {SECRET} {CODE}"))
        with pytest.raises(LoginRefused) as refused:
            await login.finish(CODE, state, state)
    assert refused.value.reason == "exchange"
    assert "403" in caplog.text
    assert SECRET not in caplog.text and CODE not in caplog.text and state not in caplog.text


async def test_exchange_with_bad_json_is_refused(login):
    with respx.mock() as mock:
        _, state = login.start()
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, text="not json"))
        with pytest.raises(LoginRefused) as refused:
            await login.finish(CODE, state, state)
    assert refused.value.reason == "exchange"


def test_logout_url(login):
    url = login.logout_url()
    assert url.startswith(jh.ISSUER + "v2/logout?")
    assert _query(url) == {"client_id": CLIENT_ID, "returnTo": "https://sidecar.example/admin"}


def test_admin_permissions():
    assert ADMIN_PERMISSIONS == ("sidecar:use", "sidecar:admin")
