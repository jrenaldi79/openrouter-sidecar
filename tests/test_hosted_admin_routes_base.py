"""The /admin guard: session, CSRF token, form size and unexpected errors."""

import pytest
from admin_routes_rig import EDIT, LOGOUT, NEW, AdminRig

from openrouter_image_mcp.hosted.admin import copy

CHANNEL = EDIT
POSTS = ["/admin/logout", NEW, CHANNEL, CHANNEL + "/finish", CHANNEL + "/off",
         CHANNEL + "/on"]
GETS = ["/admin", "/admin/log", NEW, CHANNEL]


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    yield rig
    await rig.aclose()


async def test_redirects_to_login_without_session(rig):
    for path in GETS:
        response = await rig.client.get(path)
        assert (response.status_code, response.headers["location"]) == (302, "/admin/login"), path
    for path in POSTS[1:]:  # the sign-out has no session to need
        response = await rig.client.post(path, data={"_csrf": "x"})
        assert (response.status_code, response.headers["location"]) == (302, "/admin/login"), path


async def test_logout_without_a_session_still_signs_out_at_auth0(rig):
    response = await rig.client.post("/admin/logout", data={})
    assert (response.status_code, response.headers["location"]) == (302, LOGOUT)
    assert "max-age=0" in response.headers["set-cookie"].lower()
    rig.sign_in()
    assert (await rig.post("/admin/logout", token="wrong")).status_code == 403  # with a session, the token counts
    assert rig.sessions.get(rig.session_id) is not None


async def test_ended_session_redirects_to_login(rig):
    rig.sign_in()
    assert (await rig.client.get("/admin")).status_code == 200
    rig.sessions.delete(rig.session_id)
    assert (await rig.client.get("/admin")).status_code == 302


async def test_csrf_required_on_every_post(rig):
    await rig.register()
    rig.sign_in()
    for path in POSTS:
        for token in ("", "wrong-token", rig.csrf[:-1]):
            response = await rig.post(path, {"reason": "x"}, token=token)
            assert response.status_code == 403, (path, token)
            assert copy.NOT_ADMIN not in response.text
    assert rig.row().active  # no POST changed anything
    assert rig.sessions.get(rig.session_id) is not None  # not even the sign-out
    response = await rig.client.post(CHANNEL + "/on", data={}, headers={"x-csrf-token": rig.csrf})
    assert response.status_code == 422 and copy.NOTHING_CHANGED in response.text  # the header token is accepted
    assert (await rig.client.post(CHANNEL + "/on", data={"_csrf": rig.csrf},
                                  headers={"x-csrf-token": "wrong"})).status_code == 422  # the field wins


async def test_csrf_failure_on_htmx_is_plain_text(rig):
    rig.sign_in()
    response = await rig.client.post(CHANNEL + "/off", data={"reason": "x"}, headers={"hx-request": "true"})
    assert response.status_code == 403 and response.headers["content-type"].startswith("text/plain")
    assert "token" in response.text


async def test_unexpected_error_is_a_plain_500(rig, monkeypatch, caplog):  # the log keeps the detail
    rig.sign_in()

    def explode(limit=200):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(rig.slack_rig.audit, "recent", explode)
    response = await rig.client.get("/admin/log")
    assert response.status_code == 500
    assert "Something unexpected went wrong" in response.text
    assert "secret detail" not in response.text and "RuntimeError" not in response.text
    assert "RuntimeError" in caplog.text


async def test_oversized_form_is_refused(rig):
    rig.sign_in()
    response = await rig.client.post(NEW, data={"_csrf": rig.csrf, "label": "x" * 200_000})
    assert response.status_code == 413




async def test_chunked_body_cannot_get_around_the_form_cap(rig):
    rig.sign_in()

    async def chunks():  # no Content-Length: the body is counted as it arrives
        for _ in range(30):
            yield b"label=" + b"x" * 5000 + b"&"

    response = await rig.client.post(NEW, content=chunks(), headers={
        "content-type": "application/x-www-form-urlencoded", "x-csrf-token": rig.csrf})
    assert response.status_code == 413 and rig.slack_rig.registry.all() == []


async def test_only_urlencoded_utf8_forms_are_read(rig):
    rig.sign_in()
    post = rig.client.post
    multipart = await post(NEW, files={"label": ("x", b"x")}, headers={"x-csrf-token": rig.csrf})
    assert multipart.status_code == 415
    broken = await post(NEW, content=b"label=\xff\xfe", headers={
        "content-type": "application/x-www-form-urlencoded", "x-csrf-token": rig.csrf})
    assert broken.status_code == 400
    many = "&".join(f"f{i}=1" for i in range(500)).encode()
    flood = await post(NEW, content=many, headers={
        "content-type": "application/x-www-form-urlencoded", "x-csrf-token": rig.csrf})
    assert flood.status_code == 400


@pytest.mark.parametrize("path", ["/admin/channels/bad/ids", "/admin/channels/T0123ABCD/c0123abcd",
                                  "/admin/channels/T0123ABCD/C0123ABCD0123", "/admin/channels/new?channel=c1",
                                  "/admin/channels/new?channel=C1", "/admin/channels/T0123ABC%0A/C0123ABCD"])
async def test_ids_that_are_not_slack_ids_are_not_found(rig, path):
    rig.sign_in()
    for send in (rig.client.get, lambda p: rig.post(p, {"reason": "x"})):
        response = await send(path)
        assert response.status_code == 404 and "That page doesn&#39;t exist." in response.text, path
    assert rig.slack_rig.slack.posts == [] and rig.slack_rig.registry.all() == []
