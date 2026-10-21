"""Renders the /admin pages: Jinja2 templates in the Interview Coach admin design system, plus the headers
and the static-file allowlist that go with them. Pages take plain data and return a Starlette response, so
routes stay thin and tests render without a server."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.responses import HTMLResponse

from ... import config
from . import copy

TEMPLATES_DIR = Path(__file__).parent / "templates"
PUBLIC_DIR = Path(__file__).parent / "public"
# Only these files under /admin/public are served, and without a session: never put data in them.
STATIC_FILES = re.compile(r"^(?:[\w.-]+\.(?:css|js)|fonts/[\w-]+\.woff2|brand/[\w.-]+\.(?:png|ico))\Z", re.ASCII)

# Must match the pinned URLs and SRI hashes in templates/layout.html and message.html.
HTMX_SRC = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/"
PICO_SRC = "https://cdn.jsdelivr.net/npm/@picocss/pico@2.1.1/"
_CHICAGO = ZoneInfo("America/Chicago")
# The page a layout page highlights in the rail.
_CURRENT = {"channels": "/admin", "channel_form": "/admin", "log": "/admin/log"}
_DEFAULTS = {"error": None, "setup_unfinished": False, "members": None, "row": None}


def security_headers(issuer: str, public_origin: str) -> dict[str, str]:
    """The headers every /admin response carries. The CSP allows only the pinned htmx and Pico packages and
    our own files; form-action also names the Auth0 origin because browsers apply it to the redirect chain
    after the sign-out POST. Scripts of our own load only from `<public_origin>/admin/public/` (CSP has no
    path-only source), never from 'self', which would also cover the upload and download routes."""
    parts = urlsplit(issuer)
    own_scripts = f"{public_origin.rstrip('/')}/admin/public/"
    csp = "; ".join([
        "default-src 'none'",
        f"script-src {HTMX_SRC} {own_scripts}",
        f"style-src 'self' {PICO_SRC}",
        "font-src 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
        f"form-action 'self' {parts.scheme}://{parts.netloc}",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ])
    return {
        "Content-Security-Policy": csp,
        "Cache-Control": "no-store",
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    }


def chicago(at) -> str:
    """A stored time (epoch seconds, datetime or text) as `YYYY-MM-DD HH:MM` in Chicago."""
    if isinstance(at, int | float):
        return datetime.fromtimestamp(at, _CHICAGO).strftime("%Y-%m-%d %H:%M")
    if isinstance(at, datetime):
        return at.astimezone(_CHICAGO).strftime("%Y-%m-%d %H:%M")
    return str(at)


def plain(value) -> str:
    """A changed field's before or after value as one line of text; the template escapes it."""
    if isinstance(value, dict):
        return ", ".join(f"{k} → {v}" for k, v in value.items()) or "none"
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value) or "none"
    return "none" if value is None or value == "" else str(value)


class Views:
    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True,
                               undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(chicago=chicago, plain=plain)
        self.env.globals.update(copy=copy, cohorts=list(config.COHORT_WORKSPACES))

    def page(self, name: str, *, session, status: int = 200, **context) -> HTMLResponse:
        """A page inside the layout. `session` has `.email` and `.csrf_token`."""
        template = self.env.get_template(f"{name}.html")
        data = {**_DEFAULTS, **context, "email": session.email, "csrf_token": session.csrf_token,
                "current": _CURRENT.get(name, "")}
        return HTMLResponse(template.render(data), status_code=status)

    def message(self, text: str, *, session=None, status: int = 200) -> HTMLResponse:
        """A standalone page (no rail) for sign-in problems and other dead ends."""
        html = self.env.get_template("message.html").render({"text": text, "session": session})
        return HTMLResponse(html, status_code=status)
