"""Puts the /admin page on the hosted server. `app.py` calls `register_admin`; nothing else imports this module.

Any replica serves the page: it reads and writes the shared database and holds no owner-lock hook. Without the
Auth0 client values the routes still exist but answer "not configured" (and the startup log says which are unset);
with `SIDECAR_ADMIN_ENABLED` off nothing is registered, so every /admin path is a 404.
"""

from __future__ import annotations

import logging

from ..slack.channel_admin import ChannelAdmin
from ..slack.registry import ChannelRegistry
from .audit import AdminAudit
from .oidc import Auth0Login
from .routes import admin_routes
from .sessions import AdminSessions, PendingLogins
from .views import Views

log = logging.getLogger(__name__)


def _channels(target, slack_app, provisioner, audit) -> ChannelAdmin:
    """The channel admin service: over the live bot's registry and Slack client, or, with Slack off, over the
    registry rows alone (no Slack client, so the page lists and shows them but registers nothing)."""
    if slack_app is not None:
        return ChannelAdmin(slack_app.registry, slack_app.api, slack_app.services, provisioner, audit)
    return ChannelAdmin(ChannelRegistry(target), None, None, provisioner, audit)


def _login(settings, target, options: dict) -> Auth0Login | None:
    admin = settings.admin
    if not admin.configured:
        log.warning("Admin sign-in is not configured; /admin shows a notice. Unset: %s", ", ".join(admin.missing()))
        return None
    return Auth0Login(admin, PendingLogins(target), key_resolver=options.get("key_resolver"),
                      transport=options.get("transport"))


def _register_defaults(settings) -> tuple[float, int]:
    """The register form's starting cap and concurrency; read from the Slack settings even when Slack is off."""
    slack = settings.slack
    return (slack.daily_cap_usd, slack.max_concurrent) if slack is not None else (4.0, 3)


def register_admin(server, settings, target, slack_app, provisioner, limits, *,
                   options: dict | None = None) -> dict | None:
    """Register the admin routes; returns the pieces for `server.hosted["admin"]`, None when disabled.

    `options` passes test doubles to `Auth0Login` (`key_resolver`, `transport`)."""
    if settings.admin is None or not settings.admin.enabled:
        return None
    audit, sessions, views = AdminAudit(target), AdminSessions(target), Views()
    channels = _channels(target, slack_app, provisioner, audit)
    login = _login(settings, target, options or {})
    routes = admin_routes(settings.admin, login=login, sessions=sessions, audit=audit, channels=channels,
                          views=views, limits=limits, register_defaults=_register_defaults(settings))
    for route in routes:
        server.custom_route(route.path, sorted(route.methods - {"HEAD"}))(route.endpoint)
    return {"login": login, "sessions": sessions, "audit": audit, "channels": channels, "views": views}
