"""The Slack runner rig plus a `Dispatcher`: real stores, runner and context builder, FakeSlack, a scripted
model catalog, fake preferences and a fake key provisioner. Mentions go in as `app_mention` event dicts."""

from slack_runner_rig import CHANNEL, THREAD, Rig

from openrouter_image_mcp.errors import BadRequestError
from openrouter_image_mcp.hosted.slack.context import ContextBuilder
from openrouter_image_mcp.hosted.slack.dispatch import Deps, Dispatcher
from openrouter_image_mcp.hosted.slack.guests import GuestGuard
from openrouter_image_mcp.hosted.slack.settings import SlackSettings

BOT = "UBOT"
CATALOG = {"g/model": "Gemini Pro", "o/gpt": "GPT Five", "x/grok": "Grok Four", "w/open": "Open One",
           "d/analyst": "Analyst", "z/exact": "Exact Model"}
BRANDS = {"gemini": "g/model", "chatgpt": "o/gpt", "grok": "x/grok", "open weight": "w/open"}


class FakePreferences:
    """`PreferenceService.resolve` over a fixed catalog: brands map to a family model, ids must be listed."""

    def __init__(self):
        self.calls: list[str] = []

    async def resolve(self, model=None):
        self.calls.append(model)
        selected = BRANDS.get(model.casefold(), model)
        if selected not in CATALOG:
            raise BadRequestError("Choose an available exact advisor model ID; reopen sidecar settings.")
        return {"model": selected, "name": CATALOG[selected], "source": "explicit"}


class FakeProvisioner:
    def __init__(self):
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    async def ensure(self, principal, *, team=None):
        self.calls.append((principal, team))
        if self.error is not None:
            raise self.error


class DispatchRig(Rig):
    def __init__(self, target, keys, *, sandbox_enabled=True, welcomed=True):
        super().__init__(target, keys)
        self.model.catalog = [{"id": model_id, "name": name, "architecture": {"output_modalities": ["text"]},
                               "supported_parameters": ["tools"]} for model_id, name in CATALOG.items()]
        self.services.preferences = FakePreferences()
        self.registry.add(self.row)
        if welcomed:
            self.registry.mark_welcomed("T1", CHANNEL)
        self.slack.channels[CHANNEL] = {}
        self.slack.members[CHANNEL] = ["U1"]
        self.slack.users["U1"] = {"profile": {"display_name": "Ana"}}
        self.provisioner = FakeProvisioner()
        self.deps = Deps(
            registry=self.registry, guard=GuestGuard(self.registry, self.slack), provisioner=self.provisioner,
            runner=self.runner, builder=ContextBuilder(self.slack, self.runner.names), threads=self.threads,
            memory=self.memory, jobs=self.jobs, api=self.slack, limits=self.limits,
            services={"2027": self.services}, settings=SlackSettings(enabled=False),
            sandbox_enabled=sandbox_enabled)
        self.dispatcher = Dispatcher(self.deps, bot_user_id=BOT)
        self._n = 100

    def event(self, text, *, thread_ts=THREAD, user="U1", channel=CHANNEL, files=()):
        self._n += 1
        event = {"type": "app_mention", "channel": channel, "user": user, "ts": f"1700000100.{self._n:06d}",
                 "text": text, "files": list(files)}
        if thread_ts is not None:
            event["thread_ts"] = thread_ts
        return event

    async def mention(self, text, *, drain=True, team="T1", **fields):
        event = self.event(f"<@{BOT}> {text}", **fields)
        await self.dispatcher.on_mention(team, event)
        if drain:
            await self.dispatcher.drain()
        return event

    def chat_rows(self):
        return self.threads.chats_in(self.scope, CHANNEL)

    def models_asked(self) -> list[str]:
        return [request["model"] for request in self.model.requests]
