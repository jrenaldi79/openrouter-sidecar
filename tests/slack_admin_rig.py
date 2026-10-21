"""The channel admin service rig: FakeSlack, a fake model list, fake preferences and key provisioner, and real
registry and audit stores. Shared by the `test_hosted_slack_channel_admin*.py` files."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from slack_fakes import FakeSlack

from openrouter_image_mcp.errors import AuthRequiredError, BadRequestError
from openrouter_image_mcp.hosted.admin.audit import AdminAudit
from openrouter_image_mcp.hosted.admin.sessions import Session
from openrouter_image_mcp.hosted.slack.channel_admin import ChannelAdmin
from openrouter_image_mcp.hosted.slack.principal import _acting
from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row

NOW = datetime(2026, 10, 21, tzinfo=UTC)
CREATED = int(NOW.timestamp()) - 86400
ACTOR = Session("auth0|admin", "admin@northwestern.edu", "c" * 32)
PANEL = ["google/gemini-x", "openai/gpt-x"]
FIELDS = {"label": "abbott", "cohort": "2027", "fusion_panel": PANEL, "fusion_analyst": "openai/gpt-x",
          "subagent_model": "google/gemini-flash"}
FAMILY_DEFAULTS = {"google": "google/gemini-x", "openai": "openai/gpt-x"}
GUEST_TEXT = "Sidecar can't be used in #abbott: it has a guest member."


class FakeClient:
    def __init__(self):
        self.error: Exception | None = None
        self.ids = ["google/gemini-x", "google/gemini-flash", "openai/gpt-x", "openai/gpt-y", "x-ai/grok-x"]

    async def public_models(self):
        await asyncio.sleep(0)  # a real request yields, so concurrent callers interleave
        if self.error:
            raise self.error
        return [{"id": i, "name": i.upper(), "created": CREATED} for i in self.ids]


class FakePreferences:
    """`PreferenceService.get`/`update` for one team; records who was acting.

    Like the real service (through `SlackCredentials.resolve`), `get` raises `AuthRequiredError` until the key
    provisioner has made the acting team's key."""

    def __init__(self, provisioner=None):
        self.provisioner = provisioner
        self.saved = dict.fromkeys(("google", "openai", "x-ai", "open_weight"))
        self.revision = 0
        self.error: Exception | None = None
        self.calls: list[dict] = []
        self.actors: list = []
        self.yield_in_update = False  # a real save awaits the catalog before it writes

    def get(self):
        acting = _acting.get()
        if self.provisioner is not None and (acting is None or acting.key not in self.provisioner.keys):
            raise AuthRequiredError("Your Sidecar OpenRouter key isn't ready yet. Try again in a moment.")
        return {"settings_id": "s1", "revision": self.revision, "family_defaults": dict(self.saved)}

    async def update(self, settings_id, expected_revision, family_defaults=None):
        self.actors.append(_acting.get())
        if self.yield_in_update:
            await asyncio.sleep(0)
        if self.error:
            raise self.error
        assert settings_id == "s1"
        if expected_revision != self.revision:  # what the real service says to a stale save
            raise BadRequestError("The settings changed; reload and try again")
        self.saved.update(family_defaults)
        self.revision += 1
        self.calls.append(dict(family_defaults))


class FakeProvisioner:
    """`KeyProvisioner.ensure`: one key per principal; `creations` counts the keys actually made."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.keys: set[str] = set()
        self.creations = 0
        self.error: Exception | None = None

    async def ensure(self, principal, *, team=None):
        self.calls.append((principal, team))
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        if principal.key not in self.keys:
            self.creations += 1
            self.keys.add(principal.key)


class YieldingSlack(FakeSlack):
    """Every user lookup yields, and `peak` records how many ran at once."""

    def __init__(self):
        super().__init__()
        self.running = self.peak = 0

    async def user(self, user_id):
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.running -= 1
        return await super().user(user_id)


class Rig:
    def __init__(self, target):
        self.target = target
        self.registry = ChannelRegistry(target)
        self.audit = AdminAudit(target, clock=iter(range(1000, 2000)).__next__)
        self.slack = YieldingSlack()
        self.slack.team = "T1"
        self.slack.channels = {"C1": {"name": "abbott"}, "C2": {"name": "other", "is_private": True}}
        self.slack.bot_in = ["C1", "C2"]
        self.slack.members["C1"] = ["U1", "U2"]
        self.slack.users = {"U1": {"profile": {"display_name": "Zed"}}, "U2": {"profile": {"display_name": "Ana"}}}
        self.client, self.provisioner = FakeClient(), FakeProvisioner()
        self.prefs = FakePreferences(self.provisioner)
        services = SimpleNamespace(preferences=self.prefs, client=self.client)
        self.admin = ChannelAdmin(self.registry, self.slack, services, self.provisioner, self.audit,
                                  utcnow=lambda: NOW)

    async def register(self, family_defaults=None, **fields):
        return await self.admin.register("T1", "C1", {**FIELDS, **fields},
                                         FAMILY_DEFAULTS if family_defaults is None else family_defaults, ACTOR)

    def actions(self):
        return [e.action for e in reversed(self.audit.recent())]


def registered_row(label="abbott", channel="C1", **optional):
    return new_row("T1", channel, label, "2027", fusion_panel=PANEL, fusion_analyst="openai/gpt-x",
                   subagent_model="google/gemini-flash", **optional)
