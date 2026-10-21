"""Fakes for the gateway tests: a `SocketModeClient` stand-in, a recording handler and a rig that wires them."""

import asyncio

from slack_sdk.socket_mode.request import SocketModeRequest

from openrouter_image_mcp.hosted.slack.gateway import EventLog, Gateway

TOKEN = "xapp-" + "1" * 10  # fake, built at runtime
BOT = "UBOT"


class FakeClient:
    """The slice of `SocketModeClient` the gateway uses. `connect_forever` makes `connect` never return;
    `on_close` runs inside `close`, where a real client may still deliver a late envelope."""

    def __init__(self, app_token, web_client):
        self.app_token, self.web_client = app_token, web_client
        self.socket_mode_request_listeners = []
        self.responses, self.calls = [], []
        self.connect_forever, self.connect_error, self.on_close = False, None, None

    async def deliver(self, request):
        """What a reconnected socket does: hand a request to every listener."""
        for listener in list(self.socket_mode_request_listeners):
            await listener(self, request)

    async def send_socket_mode_response(self, response):
        self.calls.append("ack")
        self.responses.append(response)

    async def connect(self):
        self.calls.append("connect")
        if self.connect_error is not None:
            raise self.connect_error
        if self.connect_forever:
            await asyncio.Event().wait()

    async def disconnect(self):
        self.calls.append("disconnect")

    async def close(self):
        self.calls.append("close")
        if self.on_close is not None:
            await self.on_close(self)


class Recorder:
    """Stands in for dispatcher, guard and registry; `gate` holds a handler open, `boom` makes it raise."""

    def __init__(self):
        self.seen, self.gate, self.boom = [], None, None

    async def _record(self, name, *args):
        self.seen.append((name, *args))
        if self.gate is not None:
            await self.gate.wait()
        if self.boom is not None:
            raise self.boom

    async def on_mention(self, team_id, event):
        await self._record("mention", team_id, event["text"])

    async def on_member_joined(self, team_id, channel_id, user_id):
        await self._record("joined", team_id, channel_id, user_id)

    async def on_channel_shared(self, team_id, channel_id):
        await self._record("shared", team_id, channel_id)

    def deactivate(self, team_id, channel_id, reason):
        self.seen.append(("deactivate", team_id, channel_id, reason))
        return True


class Rig:
    def __init__(self, events, **kwargs):
        self.rec = Recorder()
        self.clients = []

        def factory(app_token, web_client):
            self.clients.append(FakeClient(app_token, web_client))
            return self.clients[-1]

        self.gateway = Gateway(TOKEN, object(), self.rec, self.rec, self.rec, events, bot_user_id=BOT,
                               client_factory=kwargs.pop("client_factory", factory), **kwargs)

    @classmethod
    def on(cls, db_target, **kwargs):
        return cls(EventLog(db_target), **kwargs)

    async def start(self):
        await self.gateway.start()
        await asyncio.sleep(0)  # connect runs in its own task

    async def push(self, payload, *, type="events_api", envelope="env1"):
        await self.gateway.on_request(self.clients[0], SocketModeRequest(type, envelope, payload))

    async def send(self, event, *, event_id="Ev1", team_id="T1", **kwargs):
        await self.push({"event_id": event_id, "team_id": team_id, "event": event}, **kwargs)
        await self.settle()

    async def settle(self):
        while self.gateway._tasks:
            await asyncio.gather(*list(self.gateway._tasks), return_exceptions=True)


def mention(text="hi", **extra):
    return {"type": "app_mention", "channel": "C1", "user": "U1", "ts": "1.0", "text": text, **extra}


def envelope(event, event_id="Ev1"):
    return {"event_id": event_id, "team_id": "T1", "event": event}
