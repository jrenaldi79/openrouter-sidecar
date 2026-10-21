"""What every /admin route shares: the context, the headers and cookies, and the one guard.

The guard runs before a handler: no or expired session means a redirect to sign-in, and a POST must carry the
session's CSRF token (form field `_csrf`, or the `x-csrf-token` header htmx sends). A handler that raises gets a
plain error page; the exception text goes to the log, never to the page.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from ...logs import redact
from ..slack.channel_admin import ChannelAdmin
from . import copy
from .audit import AdminAudit
from .oidc import Auth0Login
from .sessions import AdminSessions, Session
from .settings import AdminSettings
from .views import Views, security_headers

log = logging.getLogger(__name__)

SESSION_COOKIE = "sidecar_admin"
LOGIN_COOKIE = "sidecar_admin_login"
COOKIE_PATH = "/admin"
LOGIN_LIMIT = 20
LOGIN_SPAN_S = 600
MAX_FORM_BYTES = 100_000
# Sentences for HTTP-level problems; everything about channels comes from copy.py.
BAD_TOKEN = "Your session token didn't match. Reload the page and try again."
BAD_REQUEST = "That request couldn't be read."
TOO_BIG = "That form is too large."
NOT_FOUND = "That page doesn't exist."
FAILED = copy.UNEXPECTED[:1].upper() + copy.UNEXPECTED[1:] + "."

Handler = Callable[["AdminContext", Request, "Session | None", "object | None"], Awaitable[Response]]


@dataclass
class AdminContext:
    settings: AdminSettings
    login: Auth0Login | None
    sessions: AdminSessions
    audit: AdminAudit
    channels: ChannelAdmin
    views: Views
    limits: object  # `Limits.window` only
    # Daily cap and concurrent limit the register form starts from (SIDECAR_SLACK_DAILY_CAP_USD and
    # SIDECAR_SLACK_MAX_CONCURRENT).
    register_defaults: tuple[float, int] = (4.0, 3)
    headers: dict[str, str] = field(init=False)

    def __post_init__(self):
        self.headers = security_headers(self.settings.issuer, self.settings.public_origin)

    def finish(self, response: Response) -> Response:
        """Add the security headers; one a handler already set (the static files' cache lifetime) stays."""
        for name, value in self.headers.items():
            if name not in response.headers:
                response.headers[name] = value
        return response

    def set_cookie(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, path=COOKIE_PATH, httponly=True, samesite="lax",
                            secure=self.settings.secure_cookies)

    def clear_cookie(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path=COOKIE_PATH, httponly=True, samesite="lax",
                               secure=self.settings.secure_cookies)

    def dead_end(self, request: Request, text: str, status: int, session: Session | None = None) -> Response:
        """A problem page; htmx requests get the plain text its error banner shows instead."""
        if is_htmx(request):
            return PlainTextResponse(text, status_code=status)
        return self.views.message(text, session=session, status=status)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status)


def _token_matches(request: Request, form, expected: str) -> bool:
    sent = form.get("_csrf") if form is not None else None
    if not isinstance(sent, str):
        sent = request.headers.get("x-csrf-token", "")
    return hmac.compare_digest(sent.encode(), expected.encode())


async def _read_form(request: Request):
    try:
        if int(request.headers.get("content-length") or 0) > MAX_FORM_BYTES:
            raise HTTPException(413)
    except ValueError:
        raise HTTPException(400) from None
    return await request.form()


def endpoint(ctx: AdminContext, handler: Handler, *, public: bool = False, post: bool = False):
    """A Starlette endpoint that runs `handler(ctx, request, session, form)` behind the guard."""
    async def run(request: Request) -> Response:
        session = None
        try:
            if not public:
                raw = request.cookies.get(SESSION_COOKIE)
                session = ctx.sessions.get(raw) if raw else None
                if session is None:
                    return ctx.finish(redirect("/admin/login", 302))
            form = None
            if post:
                form = await _read_form(request)
                if not _token_matches(request, form, session.csrf_token):
                    return ctx.finish(ctx.dead_end(request, BAD_TOKEN, 403, session))
            return ctx.finish(await handler(ctx, request, session, form))
        except HTTPException as error:
            text = TOO_BIG if error.status_code == 413 else BAD_REQUEST
            return ctx.finish(ctx.dead_end(request, text, error.status_code, session))
        except Exception as error:  # the page says something generic; the log has the rest
            log.exception(redact(f"admin request failed: {type(error).__name__}"))
            return ctx.finish(ctx.dead_end(request, FAILED, 500, session))
    return run
