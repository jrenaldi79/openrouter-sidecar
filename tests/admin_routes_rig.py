"""The /admin routes rig: a Starlette app over the real routes, with a fake Auth0 login and the channel admin
service from `slack_admin_rig` (real registry, audit and sessions on `db_target`).
Shared by the `test_hosted_admin_routes*.py` files."""

from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from slack_admin_rig import ACTOR, FAMILY_DEFAULTS, FIELDS, PANEL, Rig
from starlette.applications import Starlette

from openrouter_image_mcp.advisor_store import AdvisorStore
from openrouter_image_mcp.hosted.admin.oidc import AdminIdentity, LoginRefused
from openrouter_image_mcp.hosted.admin.routes import admin_routes
from openrouter_image_mcp.hosted.admin.sessions import AdminSessions, PendingLogins
from openrouter_image_mcp.hosted.admin.settings import AdminSettings
from openrouter_image_mcp.hosted.admin.views import Views
from openrouter_image_mcp.hosted.limits import Limits

ISSUER = "https://mpd-test.example.auth0.com/"
ORIGIN = "https://sidecar.example"
LOCAL = "http://localhost:8000"
AUTHORIZE = ISSUER + "authorize?client_id=cid"
LOGOUT = ISSUER + "v2/logout?client_id=cid"
IDENTITY = AdminIdentity("auth0|admin", "admin@northwestern.edu")
# Slack-shaped ids: the routes refuse anything else in a URL.
TEAM, CHAN, OTHER = "T0123ABCD", "C0123ABCD", "C0456EFGH"
EDIT = f"/admin/channels/{TEAM}/{CHAN}"
NEW = f"/admin/channels/new?channel={CHAN}"


class FakeLogin:
    """`Auth0Login` with no Auth0: `start` hands out numbered states, `finish` returns `identity` or refuses."""

    def __init__(self):
        self.identity: AdminIdentity | None = IDENTITY
        self.reason = "state"  # the refusal when `identity` is None
        self.start_error: Exception | None = None
        self.started = 0
        self.finished: list[tuple[str, str, str | None]] = []

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started += 1
        return AUTHORIZE, f"state-{self.started}"

    async def finish(self, code, state, cookie_state):
        self.finished.append((code, state, cookie_state))
        if self.identity is None:
            raise LoginRefused(self.reason)
        return self.identity

    def logout_url(self):
        return LOGOUT


def settings(origin: str = ORIGIN) -> AdminSettings:
    return AdminSettings(enabled=True, client_id="cid", client_secret="s" * 40, issuer=ISSUER,
                         resource=origin + "/mcp", public_origin=origin, profile_claim="https://example/profile")


class AdminRig:
    def __init__(self, target, *, origin: str = ORIGIN, login: FakeLogin | None | bool = True):
        self.target = target
        self.slack_rig = Rig(target)  # registry, audit, slack, provisioner and the ChannelAdmin
        slack = self.slack_rig.slack
        slack.team, slack.bot_in = TEAM, [CHAN, OTHER]
        slack.channels = {CHAN: {"name": "abbott"}, OTHER: {"name": "other", "is_private": True}}
        slack.members[CHAN] = ["U1", "U2"]
        self.login = FakeLogin() if login is True else login
        self.sessions = AdminSessions(target)
        self.logins = PendingLogins(target)
        self.limits = Limits(AdvisorStore(target))
        self.settings = settings(origin)
        routes = admin_routes(self.settings, login=self.login, sessions=self.sessions, audit=self.slack_rig.audit,
                              channels=self.slack_rig.admin, views=Views(), limits=self.limits)
        self.app = Starlette(routes=routes)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app, client=("203.0.113.9", 4000)),
                                        base_url=origin)
        self.csrf = ""
        self.session_id = ""

    async def register(self, family_defaults=None, **fields):
        """Register the Slack rig's channel through the service (not the page)."""
        wanted = FAMILY_DEFAULTS if family_defaults is None else family_defaults
        return await self.slack_rig.admin.register(TEAM, CHAN, {**FIELDS, **fields}, wanted, ACTOR)

    def row(self):
        return self.slack_rig.registry.get(TEAM, CHAN)

    def sign_in(self, email: str = IDENTITY.email) -> str:
        """Start a session the way the callback would, and send its cookie from now on."""
        self.session_id, session = self.sessions.create(IDENTITY.subject, email)
        self.client.cookies.set("sidecar_admin", self.session_id, domain=urlsplit(str(self.client.base_url)).hostname,
                                path="/admin")
        self.csrf = session.csrf_token
        return self.csrf

    async def post(self, path: str, data=None, *, token: str | None = None, **kwargs):
        body: dict[str, list[str]] = {}
        sent = pairs(data) if isinstance(data, dict) else list(data or [])
        for name, value in [*sent, ("_csrf", self.csrf if token is None else token)]:
            body.setdefault(name, []).append(value)
        return await self.client.post(path, data=body, **kwargs)

    async def aclose(self):
        await self.client.aclose()


def register_form(**over) -> dict:
    """What the register page posts for the Slack rig's channel, with `over` on top."""
    data = {"label": FIELDS["label"], "cohort": FIELDS["cohort"], "default_model": "gemini",
            "fusion_panel": list(PANEL), "fusion_analyst": FIELDS["fusion_analyst"],
            "subagent_model": FIELDS["subagent_model"], "daily_cap_usd": "4", "max_concurrent": "3",
            "alias_name": ["gpt", "gemini"], "alias_target": ["ChatGPT", "Gemini"],
            **{f"model_{family}": model for family, model in FAMILY_DEFAULTS.items()}}
    data.update(over)
    return data


def pairs(data: dict) -> list[tuple[str, str]]:
    return [(name, v) for name, value in data.items() for v in (value if isinstance(value, list) else [value])]


class _Form(HTMLParser):
    """The fields a browser would post for one form: inputs, the selected option of each select, textareas."""

    def __init__(self, action: str):
        super().__init__()
        self.action, self.pairs, self.inside = action, [], False
        self._select = self._options = self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.inside = self.inside or (a.get("method") == "post" and self.action == a.get("action"))
        elif not self.inside:
            return
        elif tag == "input" and a.get("name"):
            if a.get("type") == "checkbox" and "checked" not in a:
                return
            if a.get("type") != "submit":
                self.pairs.append((a["name"], a.get("value") or ""))
        elif tag == "select":
            self._select, self._options = (None if "disabled" in a else a["name"]), []
        elif tag == "option" and self._options is not None:
            self._options.append((a.get("value") or "", "selected" in a))
        elif tag == "textarea":
            self._textarea, self._text = a["name"], ""

    def handle_data(self, data):
        if self._textarea and self.inside:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select":
            if self._select:  # a disabled select is never posted
                chosen = next((v for v, picked in self._options if picked),
                              self._options[0][0] if self._options else "")
                self.pairs.append((self._select, chosen))
            self._select = self._options = None
        elif tag == "textarea" and self._textarea:
            self.pairs.append((self._textarea, self._text.removeprefix("\n")))
            self._textarea = None


def form_pairs(page: str, action: str) -> list[tuple[str, str]]:
    """What the browser posts from the POST form with exactly this `action`."""
    parser = _Form(action)
    parser.feed(page)
    return parser.pairs


def edited(found: list[tuple[str, str]], **changes) -> list[tuple[str, str]]:
    """`found` with each named field replaced by the new value (the first occurrence; list values replace all)."""
    kept = [(n, v) for n, v in found if n not in changes]
    return kept + pairs(changes)
