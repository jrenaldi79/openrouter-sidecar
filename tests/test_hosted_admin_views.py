import re
from dataclasses import replace

import pytest
from admin_views_rig import (
    XSS,
    Entry,
    Sess,
    body,
    channels_html,
    form_ctx,
    nav_current,
    overview,
    row,
    values,
)
from jinja2 import TemplateNotFound
from starlette.responses import HTMLResponse

from openrouter_image_mcp.hosted.admin import copy
from openrouter_image_mcp.hosted.admin.views import (
    PUBLIC_DIR,
    STATIC_FILES,
    Views,
    security_headers,
)

ISSUER = "https://mpd-test.example.auth0.com/"
HTMX = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/"
PICO = "https://cdn.jsdelivr.net/npm/@picocss/pico@2.1.1/"


def test_layout_shell_and_nav():
    response = Views().page("channels", session=Sess(), overview=overview(), no_channels_text=copy.NO_CHANNELS)
    html = body(response)
    assert isinstance(response, HTMLResponse) and response.status_code == 200
    assert response.media_type == "text/html"
    assert 'class="skip" href="#main"' in html
    assert "<strong>MPD Admin</strong><span>Sidecar</span>" in html
    assert 'href="/admin"' in html and 'href="/admin/log"' in html
    assert 'id="main"' in html and 'class="main page"' in html and 'id="htmx-error"' in html
    assert "ada@northwestern.edu" in html
    foot = html.split('class="rail-foot"')[1]
    assert 'action="/admin/logout"' in foot and "Sign out" in foot
    assert 'name="_csrf" value="' + "c" * 32 + '"' in foot
    assert "x-csrf-token" in html and "c" * 32 in html.split("<body")[1].split(">")[0]
    assert nav_current(html) == ["/admin"]
    log = body(Views().page("log", session=Sess(), entries=[]))
    assert nav_current(log) == ["/admin/log"]
    # The edit and register pages belong to Channels.
    assert nav_current(body(Views().page("channel_form", session=Sess(), **form_ctx()))) == ["/admin"]


def test_no_inline_script_style_or_handlers():
    pages = [channels_html(), body(Views().page("log", session=Sess(), entries=[])),
             body(Views().page("channel_form", session=Sess(), **form_ctx()))]
    for html in pages:
        assert not re.search(r"<script(?![^>]*\ssrc=)", html)
        assert "<style" not in html and " style=" not in html
        assert not re.search(r"\son[a-z]+=", html)


def test_layout_loads_pinned_assets_with_integrity():
    html = channels_html()
    assert PICO + "css/pico.min.css" in html and HTMX + "dist/htmx.min.js" in html
    assert len(re.findall(r'integrity="sha384-[A-Za-z0-9+/=]+"', html)) == 2
    assert '"allowEval":false' in html and '"includeIndicatorStyles":false' in html
    for name in re.findall(r'/admin/public/([\w./-]+)"', html):
        assert STATIC_FILES.match(name), name
        assert (PUBLIC_DIR / name).is_file(), name


def test_templates_escape_slack_text():
    entry = Entry(1_700_000_000.0, XSS, XSS, "T1", XSS, XSS, {XSS: [XSS, [XSS]]})
    r = row(label="abbott")
    pages = [
        channels_html(overview=overview([(r, XSS)], [{"id": "C9", "name": XSS}], XSS)),
        body(Views().page("channel_form", session=Sess(), **form_ctx(
            channel={"id": "C1", "name": XSS}, error=XSS, members=[("U1", XSS)],
            values=values(r, fetch_allowlist=[XSS], alias_map={XSS: XSS}, invoker_allowlist=[XSS])))),
        body(Views().page("log", session=Sess(email=XSS), entries=[entry])),
        body(Views().message(XSS, session=Sess(email=XSS))),
    ]
    off = replace(r, active=False, inactive_reason=XSS)
    pages.append(channels_html(overview=overview([(off, "x")])))
    pages.append(body(Views().page("channel_form", session=Sess(), **form_ctx(r=off))))
    for html in pages:
        assert XSS not in html
        assert "&lt;script&gt;" in html


def test_security_headers_exact():
    headers = security_headers(ISSUER, "https://sidecar.example.com")
    assert set(headers) == {"Content-Security-Policy", "Cache-Control", "Referrer-Policy", "X-Content-Type-Options"}
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "same-origin"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "; ".join([
        "default-src 'none'",
        f"script-src {HTMX} https://sidecar.example.com/admin/public/",
        f"style-src 'self' {PICO}",
        "font-src 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "form-action 'self' https://mpd-test.example.auth0.com",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ])


def test_security_headers_scope_scripts_to_the_public_origin():
    csp = security_headers(ISSUER, public_origin="https://sidecar.example.com")["Content-Security-Policy"]
    assert f"script-src {HTMX} https://sidecar.example.com/admin/public/;" in csp
    assert "'unsafe" not in csp


def test_static_allowlist():
    for name in ("tokens.css", "admin.js", "fonts/ArchivoSemiCondensed-Bold.woff2", "brand/mpd-logo.png",
                 "brand/favicon.ico"):
        assert STATIC_FILES.match(name), name
    for name in ("CLAUDE.md", "fonts/CLAUDE.md", "fonts/OFL-Archivo.txt", "../tokens.css", "fonts/../tokens.css",
                 "brand/../../x.png", "fonts/a.css", "brand/x.svg", "x.css\n", "", "x.css/", "a b.css", "%2e%2e.css/x", "\u00e9.css"):
        assert not STATIC_FILES.match(name), name
    served = [p.relative_to(PUBLIC_DIR).as_posix() for p in PUBLIC_DIR.rglob("*")
              if p.is_file() and p.suffix in {".css", ".js", ".woff2", ".png", ".ico"}]
    assert served and all(STATIC_FILES.match(name) for name in served)


def test_message_page_standalone():
    response = Views().message("Admin sign-in isn't configured on this server yet.", status=503)
    html = body(response)
    assert response.status_code == 503 and response.media_type == "text/html"
    assert "isn&#39;t configured" in html
    assert 'class="rail"' not in html and "<form" not in html
    assert "<!doctype html>" in html.lower() and PICO + "css/pico.min.css" in html
    assert '<a href="/admin">' in html
    with_session = body(Views().message(copy.NOT_ADMIN, session=Sess()))
    assert "ada@northwestern.edu" in with_session and 'action="/admin/logout"' in with_session
    assert 'name="_csrf" value="' + "c" * 32 + '"' in with_session


def test_unknown_page_is_an_error():
    with pytest.raises(TemplateNotFound):
        Views().page("nope", session=Sess())
