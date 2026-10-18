"""Browser pages for linking an OpenRouter key to a hosted Sidecar principal."""

from __future__ import annotations

import html
import logging

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from ..auth import AUTH_URL, build_auth_url, challenge_for
from ..config import APP_TITLE
from ..errors import OpenRouterError
from .links import ATTEMPT_TTL_S, LinkError, LinkStore

log = logging.getLogger(__name__)
COOKIE = "sidecar_link"
BASE = "/link/openrouter"


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    text = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font:16px system-ui;max-width:36rem;"
            f"margin:3rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1>{body}</body>")
    return HTMLResponse(text, status_code=status, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


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
        url = build_auth_url(0, challenge_for(attempt["verifier"]), attempt["id"],
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
                f"<p><b>If {account} is not you, do not continue</b>: someone may have "
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
        response = _page("Linked", f"<p>Linked {html.escape(label)}. Return to Claude and retry.</p>")
        response.delete_cookie(COOKIE, path=BASE)
        return response

    return [Route(f"{BASE}/start", start, methods=["GET"]),
            Route(f"{BASE}/callback", callback, methods=["GET"]),
            Route(f"{BASE}/confirm", confirm, methods=["POST"])]
