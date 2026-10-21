"""The channel admin service rig: FakeSlack, a fake model list, fake preferences and key provisioner, and real
registry and audit stores. Shared by the `test_hosted_slack_channel_admin*.py` files."""

from datetime import UTC, datetime
from types import SimpleNamespace

from slack_fakes import FakeSlack

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
        if self.error:
            raise self.error
        return [{"id": i, "name": i.upper(), "created": CREATED} for i in self.ids]


class FakePreferences:
    """`PreferenceService.get`/`update` for one team; records who was acting."""

    def __init__(self):
        self.saved = dict.fromkeys(("google", "openai", "x-ai", "open_weight"))
        self.revision = 0
        self.error: Exception | None = None
        self.calls: list[dict] = []
        self.actors: list = []

    def get(self):
        return {"settings_id": "s1", "revision": self.revision, "family_defaults": dict(self.saved)}

    async def update(self, settings_id, expected_revision, family_defaults=None):
        self.actors.append(_acting.get())
        if self.error:
            raise self.error
        assert (settings_id, expected_revision) == ("s1", self.revision)
        self.saved.update(family_defaults)
        self.revision += 1
        self.calls.append(dict(family_defaults))


class FakeProvisioner:
    def __init__(self):
        self.calls: list[tuple] = []
        self.keys: set[str] = set()
        self.error: Exception | None = None

    async def ensure(self, principal, *, team=None):
        self.calls.append((principal, team))
        if self.error:
            raise self.error
        self.keys.add(principal.key)


class Rig:
    def __init__(self, target):
        self.target = target
        self.registry = ChannelRegistry(target)
        self.audit = AdminAudit(target, clock=iter(range(1000, 2000)).__next__)
        self.slack = FakeSlack()
        self.slack.team = "T1"
        self.slack.channels = {"C1": {"name": "abbott"}, "C2": {"name": "other", "is_private": True}}
        self.slack.bot_in = ["C1", "C2"]
        self.slack.members["C1"] = ["U1", "U2"]
        self.slack.users = {"U1": {"profile": {"display_name": "Zed"}}, "U2": {"profile": {"display_name": "Ana"}}}
        self.client, self.prefs, self.provisioner = FakeClient(), FakePreferences(), FakeProvisioner()
        services = {c: SimpleNamespace(preferences=self.prefs, client=self.client) for c in ("2027", "2028")}
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
