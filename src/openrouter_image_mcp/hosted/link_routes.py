"""Browser pages for linking an OpenRouter key to a hosted Sidecar principal."""

from __future__ import annotations

import html
import logging
from functools import cache

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from ..apps import logo_data_uri
from ..auth import AUTH_URL, build_auth_url, challenge_for
from ..config import APP_TITLE
from ..errors import OpenRouterError
from .links import ATTEMPT_TTL_S, LinkError, LinkStore

log = logging.getLogger(__name__)
COOKIE = "sidecar_link"
BASE = "/link/openrouter"


# Matches the "MPD Class Tools" Auth0 sign-in page and code email (mpd-auth/tenant).
_STYLE = """
body{margin:0;background:#F4F1F8;color:#1F1F1F;font:15px/1.5 Arial,Helvetica,sans-serif}
main{max-width:480px;margin:40px auto;padding:0 16px}
.card{background:#FFF;border:1px solid #E4DCEF;border-radius:8px;overflow:hidden}
.inner{padding:32px 32px 24px}
header{text-align:center;margin-bottom:24px}
header img{width:64px;height:64px;border-radius:4px;background:#4E2A84;display:block;margin:0 auto 12px}
.brand{margin:0;font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:#4E2A84;font-weight:bold}
h1{margin:4px 0 0;font-size:22px;line-height:30px}
p{margin:0 0 14px;color:#4A4A4A} b{color:#1F1F1F}
.warn{background:#F4F1F8;border-left:3px solid #4E2A84;padding:10px 12px;border-radius:4px}
button{background:#4E2A84;color:#FFF;border:0;border-radius:6px;padding:12px 20px;
font:bold 15px Arial,Helvetica,sans-serif;width:100%;cursor:pointer;margin-top:8px}
button:hover{background:#401F75} button:focus-visible{outline:3px solid #B6ACD1;outline-offset:2px}
.bar{height:6px;background:#4E2A84}
footer{text-align:center;font-size:13px;color:#6B6B6B;margin-top:16px}
footer a{color:#4E2A84}
"""


@cache
def _logo() -> str:
    return logo_data_uri()


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    text = (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} · MPD Class Tools</title><style>{_STYLE}</style>"
            f"<main><div class=card><div class=inner><header>"
            f"<img src=\"{_logo()}\" alt=\"Northwestern MPD\">"
            f"<p class=brand>MPD Class Tools</p><h1>{html.escape(title)}</h1></header>"
            f"{body}</div><div class=bar></div></div>"
            f"<footer>Trouble linking? Contact John Renaldi at "
            f"<a href='mailto:jrenaldi@northwestern.edu'>jrenaldi@northwestern.edu</a>.</footer>"
            f"</main></html>")
    return HTMLResponse(text, status_code=status, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                   "form-action 'self'"})


def _fail(message: str) -> HTMLResponse:
    return _page("Link not completed", f"<p>{html.escape(message)}</p>", 400)


def link_routes(links: LinkStore, *, exchange, verify, auth_url: str = AUTH_URL) -> list[Route]:
    """exchange(code, verifier) -> key; verify(key, workspace_id) -> key label.

    auth_url replaces OpenRouter's sign-in page only for local development fakes.
    """
    secure = links.origin.startswith("https://")

    async def start(request: Request):
        try:
            attempt, nonce = links.begin(request.query_params.get("t", ""))
        except LinkError as exc:
            return _fail(str(exc))
        url = build_auth_url(challenge_for(attempt["verifier"]), attempt["id"],
                             attempt["workspace"], f"{APP_TITLE} Sidecar",
                             callback_url=f"{links.origin}{BASE}/callback")
        url = auth_url + url[len(AUTH_URL):]
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(COOKIE, nonce, max_age=ATTEMPT_TTL_S, httponly=True,
                            secure=secure, samesite="lax", path=BASE)
        return response

    async def callback(request: Request):
        state = request.query_params.get("state", "")
        nonce = request.cookies.get(COOKIE)
        try:
            attempt = links.attempt(state, nonce)
            if request.query_params.get("error") or not request.query_params.get("code"):
                return _fail("Sign-in to OpenRouter was cancelled. Ask Claude for a new link.")
            key = await exchange(request.query_params["code"], attempt["verifier"])
            label = await verify(key, attempt["workspace"])
            links.stage(state, nonce, key, label)
        except LinkError as exc:
            return _fail(str(exc))
        except OpenRouterError as exc:
            log.info("link callback rejected: %s", type(exc).__name__)
            return _fail("OpenRouter could not confirm this key for your class workspace.")
        csrf = links.csrf_for(state, nonce)
        account = html.escape(attempt.get("account") or "unknown")
        form = (f"<p>Link the OpenRouter key <b>{html.escape(label)}</b> to the Sidecar "
                f"account <b>{account}</b>? Claude will use it for that account's advisor "
                f"chats and images, and charge this key.</p>"
                f"<p class=warn><b>If {account} is not you, do not continue</b>: someone may have "
                f"forwarded you their link. Close this page instead.</p>"
                f"<form method=post action='{BASE}/confirm'>"
                f"<input type=hidden name=state value='{html.escape(state)}'>"
                f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
                f"<button type=submit>Link this key</button></form>")
        return _page("Confirm OpenRouter link", form)

    async def confirm(request: Request):
        form = await request.form()
        try:
            label = links.confirm(str(form.get("state", "")), request.cookies.get(COOKIE),
                                  str(form.get("csrf", "")))
        except LinkError as exc:
            return _fail(str(exc))
        response = _page("Linked", f"<p>Linked {html.escape(label)}. You can close this tab and go back to "
                                    f"Claude.</p>")
        response.delete_cookie(COOKIE, path=BASE)
        return response

    return [Route(f"{BASE}/start", start, methods=["GET"]),
            Route(f"{BASE}/callback", callback, methods=["GET"]),
            Route(f"{BASE}/confirm", confirm, methods=["POST"])]
