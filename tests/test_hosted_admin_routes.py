"""The /admin headers, static files and not-configured mode. The guard, sign-in and channels have their own files."""

import pytest
from admin_routes_rig import ISSUER, AdminRig, register_form
from markupsafe import escape

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.views import PUBLIC_DIR, security_headers

CHANNEL = "/admin/channels/T1/C1"
POSTS = ["/admin/logout", "/admin/channels/new?channel=C1", CHANNEL, CHANNEL + "/finish", CHANNEL + "/off",
         CHANNEL + "/on"]
GETS = ["/admin", "/admin/log", "/admin/channels/new?channel=C1", CHANNEL]


@pytest.fixture
async def rig(db_target):
    rig = AdminRig(db_target)
    yield rig
    await rig.aclose()


async def test_headers_on_every_response(rig):
    expected = security_headers(ISSUER, rig.settings.public_origin)
    await rig.slack_rig.register()
    rig.sign_in()
    responses = [
        await rig.client.get("/admin"),  # a page
        await rig.client.get("/admin/login"),  # a redirect
        await rig.post(CHANNEL + "/off", {"reason": ""}),  # a refusal
        await rig.post("/admin/logout", token="bad"),  # a 403
        await rig.client.get("/admin/channels/T1/NOPE"),  # a 404 page
        await rig.client.get("/admin/public/nothing.css"),  # a static 404
    ]
    for response in responses:
        for name, value in expected.items():
            assert response.headers[name] == value, (response.url, name)
    static = await rig.client.get("/admin/public/admin.css")
    assert static.headers["content-security-policy"] == expected["Content-Security-Policy"]


async def test_not_configured_mode(db_target):
    rig = AdminRig(db_target, login=None)
    try:
        for path in ("/admin", "/admin/login", "/admin/callback?code=x", "/admin/log", CHANNEL):
            response = await rig.client.get(path)
            assert response.status_code == 503 and escape(copy.NOT_CONFIGURED) in response.text, path
            assert response.headers["content-security-policy"]
        assert (await rig.client.post("/admin/logout", data={})).status_code == 503
        assert (await rig.client.post("/admin/channels/new?channel=C1", data=register_form())).status_code == 503
        assert (await rig.client.get("/admin/public/admin.css")).status_code == 200
        assert rig.slack_rig.registry.all() == []
    finally:
        await rig.aclose()


@pytest.mark.parametrize("name,kind", [("admin.css", "text/css"), ("tokens.css", "text/css"),
                                       ("admin.js", "text/javascript")])
async def test_static_files_and_404(rig, name, kind):
    response = await rig.client.get(f"/admin/public/{name}")  # no session
    assert response.status_code == 200 and response.headers["content-type"].startswith(kind)
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content == (PUBLIC_DIR / name).read_bytes()


async def test_static_fonts_and_images(rig):
    font = next((PUBLIC_DIR / "fonts").glob("*.woff2"))
    response = await rig.client.get(f"/admin/public/fonts/{font.name}")
    assert response.status_code == 200 and response.headers["content-type"] == "font/woff2"
    icon = next(p for p in (PUBLIC_DIR / "brand").iterdir() if p.suffix in (".png", ".ico"))
    kinds = {".png": "image/png", ".ico": "image/x-icon"}
    response = await rig.client.get(f"/admin/public/brand/{icon.name}")
    assert response.status_code == 200 and response.headers["content-type"] == kinds[icon.suffix]


@pytest.mark.parametrize("path", [
    "nothing.css", "fonts/nothing.woff2", "CLAUDE.md", "fonts", "../admin.css", "%2e%2e/admin.css",
    "fonts/%2e%2e/admin.css", "..%2fadmin.css", "brand/..%2fadmin.css", "admin.css%00.png",
    "/etc/passwd", "admin.py", ".env", "fonts/a.woff2/", "admin.css/",
])
async def test_static_refuses_everything_else(rig, path):
    response = await rig.client.get(f"/admin/public/{path}")
    assert response.status_code == 404
    assert "root:" not in response.text
