"""The /admin guard: session, CSRF token, form size and unexpected errors."""

import pytest
from admin_routes_rig import AdminRig

from openrouter_image_mcp.hosted.admin import copy

CHANNEL = "/admin/channels/T1/C1"
POSTS = ["/admin/logout", "/admin/channels/new?channel=C1", CHANNEL, CHANNEL + "/finish", CHANNEL + "/off",
         CHANNEL + "/on"]
GETS = ["/admin", "/admin/log", "/admin/channels/new?channel=C1", CHANNEL]


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    yield rig
    await rig.aclose()


async def test_redirects_to_login_without_session(rig):
    for path in GETS:
        response = await rig.client.get(path)
        assert (response.status_code, response.headers["location"]) == (302, "/admin/login"), path
    for path in POSTS:
        response = await rig.client.post(path, data={"_csrf": "x"})
        assert (response.status_code, response.headers["location"]) == (302, "/admin/login"), path


async def test_ended_session_redirects_to_login(rig):
    rig.sign_in()
    assert (await rig.client.get("/admin")).status_code == 200
    rig.sessions.delete(rig.session_id)
    assert (await rig.client.get("/admin")).status_code == 302


async def test_csrf_required_on_every_post(rig):
    await rig.slack_rig.register()
    rig.sign_in()
    for path in POSTS:
        for token in ("", "wrong-token", rig.csrf[:-1]):
            response = await rig.post(path, {"reason": "x"}, token=token)
            assert response.status_code == 403, (path, token)
            assert copy.NOT_ADMIN not in response.text
    assert rig.slack_rig.registry.get("T1", "C1").active  # no POST changed anything
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
    response = await rig.client.post("/admin/channels/new?channel=C1", data={"_csrf": rig.csrf, "label": "x" * 200_000})
    assert response.status_code == 413


