"""The /admin routes. `hosted/admin/wiring.py` registers them on the hosted server.

With no `login` (sign-in isn't configured) every path except the static files says so. Otherwise each route runs
behind the guard in `routes_base.endpoint`; the handlers live in `routes_auth`, `routes_channels` and
`routes_actions`.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from . import copy, routes_actions, routes_auth, routes_channels
from .audit import AdminAudit
from .oidc import Auth0Login
from .routes_base import AdminContext, endpoint
from .sessions import AdminSessions
from .settings import AdminSettings
from .views import PUBLIC_DIR, STATIC_FILES, Views

__all__ = ["admin_routes"]

CONTENT_TYPES = {".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2", ".png": "image/png",
                 ".ico": "image/x-icon"}
STATIC_CACHE = "public, max-age=3600"
GET, POST, BOTH = ["GET"], ["POST"], ["GET", "POST"]
ANY = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


async def static(ctx: AdminContext, request: Request, session, form) -> Response:
    """A file from `public/`, for anyone: the name is checked against the allowlist before the disk is touched."""
    name = request.path_params["path"]
    if not STATIC_FILES.match(name):
        return PlainTextResponse(copy.NOT_FOUND, status_code=404)
    root = PUBLIC_DIR.resolve()
    path = (PUBLIC_DIR / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return PlainTextResponse(copy.NOT_FOUND, status_code=404)
    return Response(path.read_bytes(), media_type=CONTENT_TYPES[path.suffix],
                    headers={"Cache-Control": STATIC_CACHE})


async def not_found(ctx: AdminContext, request: Request, session, form) -> Response:
    """Anything under /admin no route took (unknown path, wrong method, trailing slash), with our headers."""
    return ctx.dead_end(request, copy.NOT_FOUND, 404)


async def not_configured(ctx: AdminContext, request: Request, session, form) -> Response:
    return ctx.dead_end(request, copy.NOT_CONFIGURED, 503)


def admin_routes(settings: AdminSettings, *, login: Auth0Login | None, sessions: AdminSessions,
                 audit: AdminAudit, channels, views: Views, limits,
                 register_defaults: tuple[float, int] = (4.0, 3)) -> list[Route]:
    ctx = AdminContext(settings, login, sessions, audit, channels, views, limits, register_defaults)
    routes = [Route("/admin/public/{path:path}", endpoint(ctx, static, public=True), methods=GET)]
    if login is None:
        return routes + [Route(path, endpoint(ctx, not_configured, public=True), methods=BOTH)
                         for path in ("/admin", "/admin/{rest:path}")]
    team = "/admin/channels/{team}/{channel}"
    return routes + [
        Route("/admin/login", endpoint(ctx, routes_auth.login, public=True), methods=GET),
        Route("/admin/callback", endpoint(ctx, routes_auth.callback, public=True), methods=GET),
        Route("/admin/logout", endpoint(ctx, routes_auth.logout, public=True, post=True), methods=POST),
        Route("/admin", endpoint(ctx, routes_channels.index), methods=GET),
        Route("/admin/log", endpoint(ctx, routes_channels.log_page), methods=GET),
        Route("/admin/channels/new", endpoint(ctx, routes_channels.new_form), methods=GET),
        Route("/admin/channels/new", endpoint(ctx, routes_actions.register, post=True), methods=POST),
        Route(team, endpoint(ctx, routes_channels.edit_form), methods=GET),
        Route(team, endpoint(ctx, routes_actions.save, post=True), methods=POST),
        Route(f"{team}/finish", endpoint(ctx, routes_actions.finish, post=True), methods=POST),
        Route(f"{team}/off", endpoint(ctx, routes_actions.turn_off, post=True), methods=POST),
        Route(f"{team}/on", endpoint(ctx, routes_actions.turn_on, post=True), methods=POST),
        Route("/admin/{rest:path}", endpoint(ctx, not_found, public=True), methods=ANY),
    ]
