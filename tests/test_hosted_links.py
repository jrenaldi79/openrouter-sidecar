"""OpenRouter link flow: single-use, browser-bound, confirmed, never leaks the key."""

import base64
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from openrouter_image_mcp.errors import AuthRequiredError
from openrouter_image_mcp.hosted.link_routes import link_routes
from openrouter_image_mcp.hosted.links import LinkRequiredError, LinkStore
from openrouter_image_mcp.hosted.principal import Principal
from openrouter_image_mcp.hosted.vault import Vault

ORIGIN = "https://sidecar.example"
KEY = "sk-or-v1-" + "s" * 40
ALICE = Principal("https://iss/", "email|alice", "ws-2027", "2027", "alice@u.northwestern.edu")
BOB = Principal("https://iss/", "email|bob", "ws-2027", "2027")


@pytest.fixture
def links(db_target):
    vault = Vault("v1:" + base64.b64encode(os.urandom(32)).decode())
    return LinkStore(db_target, vault, "k" * 32, ORIGIN)


@pytest.fixture
def exchanged():
    return {"calls": [], "workspace": "ws-2027"}


@pytest.fixture
def client(links, exchanged):
    async def exchange(code, verifier):
        exchanged["calls"].append((code, verifier))
        return KEY

    async def verify(key, workspace_id):
        if workspace_id != exchanged["workspace"]:
            raise AuthRequiredError("wrong workspace")
        return "Sidecar key"

    app = Starlette(routes=link_routes(links, exchange=exchange, verify=verify))
    return TestClient(app, base_url=ORIGIN)


def _token(url):
    return parse_qs(urlsplit(url).query)["t"][0]


def _complete(client, links, principal=ALICE):
    url = links.issue(principal)
    start = client.get("/link/openrouter/start", params={"t": _token(url)}, follow_redirects=False)
    assert start.status_code == 302
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get("/link/openrouter/callback", params={"code": "c1", "state": state})
    return start, state, callback


def test_unlinked_principal_gets_link_required(links):
    with pytest.raises(LinkRequiredError) as caught:
        links.key_for(ALICE)
    assert caught.value.code == "openrouter_link_required"
    assert caught.value.link_url.startswith(ORIGIN + "/link/openrouter/start?t=")


def test_full_flow_links_key_after_confirmation(client, links):
    start, state, callback = _complete(client, links)
    location = urlsplit(start.headers["location"])
    params = parse_qs(location.query)
    assert location.netloc == "openrouter.ai"
    assert params["callback_url"] == [ORIGIN + "/link/openrouter/callback"]
    assert params["required_workspace_id"] == ["ws-2027"]
    assert "sidecar_link" in start.headers["set-cookie"]
    assert "HttpOnly" in start.headers["set-cookie"] and "SameSite=lax" in start.headers["set-cookie"]
    assert callback.status_code == 200 and "Sidecar key" in callback.text
    assert KEY not in callback.text
    assert not links.is_active(ALICE.key, ALICE.workspace_id)  # not until confirmed
    csrf = links.csrf_for(state, client.cookies["sidecar_link"])
    done = client.post("/link/openrouter/confirm", data={"state": state, "csrf": csrf})
    assert done.status_code == 200 and "Linked" in done.text
    assert links.key_for(ALICE) == KEY


def test_link_url_is_single_use(client, links):
    url = links.issue(ALICE)
    assert client.get("/link/openrouter/start", params={"t": _token(url)},
                      follow_redirects=False).status_code == 302
    again = client.get("/link/openrouter/start", params={"t": _token(url)}, follow_redirects=False)
    assert again.status_code == 400


def test_tampered_or_expired_token_rejected(client, links):
    url = links.issue(ALICE)
    token = _token(url)
    assert client.get("/link/openrouter/start", params={"t": token[:-2] + "xx"}).status_code == 400
    links.clock = lambda: 10**12
    assert client.get("/link/openrouter/start", params={"t": token}).status_code == 400


def test_callback_requires_the_starting_browser(client, links):
    url = links.issue(ALICE)
    start = client.get("/link/openrouter/start", params={"t": _token(url)}, follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    client.cookies.clear()  # a different browser (the login-CSRF victim) has no cookie
    victim = client.get("/link/openrouter/callback", params={"code": "c1", "state": state})
    assert victim.status_code == 400
    assert not links.is_active(ALICE.key, ALICE.workspace_id)


def test_wrong_workspace_key_is_not_linked(client, links, exchanged):
    exchanged["workspace"] = "ws-other"
    _, _, callback = _complete(client, links)
    assert callback.status_code == 400
    assert not links.is_active(ALICE.key, ALICE.workspace_id)


def test_confirm_needs_valid_csrf(client, links):
    _, state, _ = _complete(client, links)
    bad = client.post("/link/openrouter/confirm", data={"state": state, "csrf": "forged"})
    assert bad.status_code == 400
    assert not links.is_active(ALICE.key, ALICE.workspace_id)


def test_provider_denial_shows_cancelled(client, links):
    url = links.issue(ALICE)
    start = client.get("/link/openrouter/start", params={"t": _token(url)}, follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    page = client.get("/link/openrouter/callback", params={"error": "access_denied", "state": state})
    assert page.status_code == 400 and "cancelled" in page.text.lower()


def test_relink_replaces_key_and_revoke_disables(links):
    links.save(ALICE, "sk-old", "old")
    links.save(ALICE, "sk-new", "new")
    assert links.key_for(ALICE) == "sk-new"
    links.revoke(ALICE.key, ALICE.workspace_id)
    assert not links.is_active(ALICE.key, ALICE.workspace_id)
    with pytest.raises(LinkRequiredError):
        links.key_for(ALICE)


def test_principals_are_isolated(links):
    links.save(ALICE, "sk-alice", "a")
    with pytest.raises(LinkRequiredError):
        links.key_for(BOB)
    assert not links.is_active(ALICE.key, "ws-2028")


def test_key_never_stored_in_plaintext(links, db_target):
    links.save(ALICE, KEY, "label")
    from openrouter_image_mcp import storage
    with storage.connect(db_target) as db:
        rows = db.execute("SELECT * FROM openrouter_links").fetchall()
    assert rows and all(KEY not in str(dict(row)) for row in rows)


def test_confirmation_names_the_target_account_and_warns(client, links):
    _, _, callback = _complete(client, links)
    assert "alice@u.northwestern.edu" in callback.text
    assert "not you" in callback.text.lower()


def test_rotated_vault_reseals_on_read(db_target):
    old = "v1:" + base64.b64encode(os.urandom(32)).decode()
    new = "v2:" + base64.b64encode(os.urandom(32)).decode()
    LinkStore(db_target, Vault(old), "k" * 32, ORIGIN).save(ALICE, KEY, "Alice key")
    rotated = LinkStore(db_target, Vault(f"{new},{old}"), "k" * 32, ORIGIN)
    assert rotated.key_for(ALICE) == KEY
    # After one read the row is sealed with v2, so the old key can be retired.
    assert LinkStore(db_target, Vault(new), "k" * 32, ORIGIN).key_for(ALICE) == KEY
