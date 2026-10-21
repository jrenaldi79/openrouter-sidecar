"""Sign-in, callback and sign-out. The Auth0 round trip itself lives in `oidc.py`; these handlers own cookies."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from ..limits import RateLimitedError
from . import copy
from .oidc import LoginRefused
from .routes_base import (
    LOGIN_COOKIE,
    LOGIN_LIMIT,
    LOGIN_SPAN_S,
    SESSION_COOKIE,
    AdminContext,
    redirect,
)
from .sessions import ABSOLUTE_S, LOGIN_S, TooManyLogins


async def login(ctx: AdminContext, request: Request, session, form) -> Response:
    """Start an Auth0 sign-in, unless this address or the server already has too many in progress."""
    host = request.client.host if request.client else "unknown"
    try:
        ctx.limits.window(f"admin-login|{host}", "sign-ins", limit=LOGIN_LIMIT, span_s=LOGIN_SPAN_S)
        url, state = ctx.login.start()
    except (RateLimitedError, TooManyLogins):
        return ctx.dead_end(request, copy.TOO_MANY_LOGINS, 429)
    response = redirect(url, 302)
    ctx.set_cookie(response, LOGIN_COOKIE, state, LOGIN_S)
    return response


async def callback(ctx: AdminContext, request: Request, session, form) -> Response:
    """Finish the sign-in: the code and `state` from Auth0, the login cookie from this browser."""
    code, state = request.query_params.get("code"), request.query_params.get("state")
    try:
        if not code or not state:  # the user declined at Auth0, or the link was edited
            raise LoginRefused("state")
        identity = await ctx.login.finish(code, state, request.cookies.get(LOGIN_COOKIE))
    except LoginRefused as refused:
        not_admin = refused.reason == "not_admin"
        # Someone signed in at Auth0 with the wrong account has no session here to sign out of, so the page
        # links straight to Auth0's sign-out: a plain GET, nothing local to protect.
        response = ctx.dead_end(request, copy.NOT_ADMIN if not_admin else copy.LOGIN_FAILED,
                                403 if not_admin else 400,
                                logout_url=ctx.login.logout_url() if not_admin else None)
        ctx.clear_cookie(response, LOGIN_COOKIE)
        return response
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:  # a new sign-in replaces the session this browser held, rather than leaving it to expire
        ctx.sessions.delete(previous)
    raw_id, _ = ctx.sessions.create(identity.subject, identity.email)
    response = redirect("/admin", 302)
    ctx.clear_cookie(response, LOGIN_COOKIE)
    ctx.set_cookie(response, SESSION_COOKIE, raw_id, ABSOLUTE_S)
    return response


async def logout(ctx: AdminContext, request: Request, session, form) -> Response:
    """End the session here (if there is one), then at Auth0 so the next sign-in asks again. The guard has
    already checked the CSRF token when a session exists."""
    raw_id = request.cookies.get(SESSION_COOKIE)
    if raw_id:
        ctx.sessions.delete(raw_id)
    response = redirect(ctx.login.logout_url(), 302)
    ctx.clear_cookie(response, SESSION_COOKIE)
    return response
