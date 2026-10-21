"""What every /admin route shares: the context, the headers and cookies, and the one guard.

The guard runs before a handler: no or expired session means a redirect to sign-in, and a POST must carry the
session's CSRF token (form field `_csrf`, or the `x-csrf-token` header htmx sends). A handler that raises gets a
plain error page; the exception text goes to the log, never to the page.
"""

from __future__ import annotations

import hmac
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from starlette.datastructures import FormData
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

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
MAX_FORM_FIELDS = 200
FORM_TYPE = "application/x-www-form-urlencoded"
# Slack's team and channel ids; anything else in a URL is not one of ours.
SLACK_ID = re.compile(r"[A-Z0-9]{8,12}\Z", re.ASCII)

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

    def dead_end(self, request: Request, text: str, status: int, session: Session | None = None, *,
                 logout_url: str | None = None) -> Response:
        """A problem page; htmx requests get the plain text its error banner shows instead.

        `logout_url` adds a plain Sign out link for someone signed in at Auth0 but with no session here."""
        if is_htmx(request):
            return PlainTextResponse(text, status_code=status)
        if logout_url:
            page = self.views.env.get_template("message.html").render(text=text, session=None, logout_url=logout_url)
            return HTMLResponse(page, status_code=status)
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


async def read_form(request: Request) -> FormData:
    """The urlencoded form (all ours are), read at most `MAX_FORM_BYTES`, however the body is framed."""
    kind = request.headers.get("content-type", FORM_TYPE).split(";")[0].strip().lower()
    if kind != FORM_TYPE:
        raise HTTPException(415)
    chunks, size = [], 0
    async for chunk in request.stream():  # counted as it arrives: a chunked body has no Content-Length
        size += len(chunk)
        if size > MAX_FORM_BYTES:
            raise HTTPException(413)
        chunks.append(chunk)
    try:
        pairs = parse_qsl(b"".join(chunks).decode(), keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS)
    except ValueError:  # not UTF-8, or too many fields
        raise HTTPException(400) from None
    return FormData(pairs)


def ids_look_right(request: Request) -> bool:
    """True when every Slack id in the path and the `channel` query is shaped like one."""
    found = [request.path_params.get(name) for name in ("team", "channel")] + [request.query_params.get("channel")]
    return all(value is None or SLACK_ID.match(value) for value in found)


def _signed_in(ctx: AdminContext, request: Request) -> Session | None:
    raw = request.cookies.get(SESSION_COOKIE)
    return ctx.sessions.get(raw) if raw else None


def endpoint(ctx: AdminContext, handler: Handler, *, public: bool = False, post: bool = False):
    """A Starlette endpoint that runs `handler(ctx, request, session, form)` behind the guard.

    A page that isn't `public` needs a session (else redirect to sign-in). A POST with a session must carry its
    CSRF token; a public POST without one (sign-out) has nothing to protect and passes."""
    async def run(request: Request) -> Response:
        session = None
        try:
            if post or not public:
                session = _signed_in(ctx, request)
            if session is None and not public:
                return ctx.finish(redirect("/admin/login", 302))
            if not public and not ids_look_right(request):
                return ctx.finish(ctx.dead_end(request, copy.NOT_FOUND, 404, session))
            form = None
            if post:
                form = await read_form(request)
                if session is not None and not _token_matches(request, form, session.csrf_token):
                    return ctx.finish(ctx.dead_end(request, copy.BAD_TOKEN, 403, session))
            return ctx.finish(await handler(ctx, request, session, form))
        except HTTPException as error:
            text = copy.TOO_BIG if error.status_code == 413 else copy.BAD_REQUEST
            return ctx.finish(ctx.dead_end(request, text, error.status_code, session))
        except Exception as error:  # the page says something generic; the log has the rest
            log.exception(redact(f"admin request failed: {type(error).__name__}"))
            return ctx.finish(ctx.dead_end(request, copy.WENT_WRONG, 500, session))
    return run
