import asyncio

import pytest
from slack_sdk.socket_mode.request import SocketModeRequest

from openrouter_image_mcp.hosted.slack.gateway import EventLog, Gateway

TOKEN = "xapp-" + "1" * 10  # fake, built at runtime
BOT = "UBOT"


class FakeClient:
    def __init__(self, app_token, web_client):
        self.app_token, self.web_client = app_token, web_client
        self.socket_mode_request_listeners = []
        self.responses, self.calls = [], []

    async def send_socket_mode_response(self, response):
        self.calls.append("ack")
        self.responses.append(response)

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def close(self):
        self.calls.append("close")


class Recorder:
    """Stands in for dispatcher, guard and registry; `gate` holds a handler open, `boom` makes it raise."""

    def __init__(self):
        self.seen, self.gate, self.boom = [], None, False

    async def _record(self, name, *args):
        self.seen.append((name, *args))
        if self.gate is not None:
            await self.gate.wait()
        if self.boom:
            raise RuntimeError("handler failed")

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
    def __init__(self, db_target, **kwargs):
        self.rec = Recorder()
        self.clients = []

        def factory(app_token, web_client):
            self.clients.append(FakeClient(app_token, web_client))
            return self.clients[-1]

        self.gateway = Gateway(TOKEN, object(), self.rec, self.rec, self.rec, EventLog(db_target),
                               bot_user_id=BOT, client_factory=factory, **kwargs)

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


@pytest.fixture
async def rig(db_target):
    made = Rig(db_target)
    await made.gateway.start()
    yield made
    await made.gateway.stop()


async def test_start_registers_listener_and_connects(rig):
    client = rig.clients[0]
    assert client.app_token == TOKEN
    assert client.socket_mode_request_listeners == [rig.gateway.on_request]
    assert client.calls == ["connect"]


async def test_ack_before_handling(rig):
    rig.rec.boom = True
    await rig.send(mention())
    assert rig.clients[0].responses[0].envelope_id == "env1"
    assert rig.rec.seen == [("mention", "T1", "hi")]


async def test_ack_comes_first_in_call_order(rig):
    order = []
    original = rig.rec.on_mention

    async def ack(response):
        order.append("ack")

    async def handler(team, event):
        order.append("handle")
        await original(team, event)

    rig.clients[0].send_socket_mode_response = ack
    rig.rec.on_mention = handler
    await rig.send(mention())
    assert order == ["ack", "handle"]


@pytest.mark.parametrize("payload", [
    {"team_id": "T1", "event": mention()},
    {"event_id": 5, "team_id": "T1", "event": mention()},
    {"event_id": "Ev1", "event": mention()},
    {"event_id": "Ev1", "team_id": 7, "event": mention()},
    {"event_id": "Ev1", "team_id": "T1", "event": "text"},
    {"event_id": "Ev1", "team_id": "T1"},
])
async def test_invalid_envelope_is_acked_and_dropped(rig, payload):
    await rig.push(payload, envelope="env9")
    await rig.settle()
    assert [r.envelope_id for r in rig.clients[0].responses] == ["env9"]
    assert rig.rec.seen == []


async def test_non_events_api_is_acked_and_ignored(rig):
    await rig.send(mention(), type="slash_commands")
    assert len(rig.clients[0].responses) == 1
    assert rig.rec.seen == []


async def test_duplicate_event_id_handled_once(rig):
    await rig.send(mention())
    await rig.send(mention(), envelope="env2")
    assert len(rig.clients[0].responses) == 2
    assert len(rig.rec.seen) == 1


async def test_dedupe_across_two_gateways(db_target):
    first, second = Rig(db_target), Rig(db_target)
    await first.gateway.start()
    await second.gateway.start()
    await first.send(mention())
    await second.send(mention())
    assert len(first.rec.seen) + len(second.rec.seen) == 1
    await first.gateway.stop()
    await second.gateway.stop()


@pytest.mark.parametrize("extra", [
    {"bot_id": "B1"}, {"subtype": "bot_message"}, {"edited": {"user": "U1", "ts": "2.0"}},
    {"subtype": "message_changed"}, {"subtype": "message_deleted"},
])
async def test_bot_posts_and_edits_dropped(rig, extra):
    await rig.send(mention(**extra))
    assert rig.rec.seen == []


async def test_routes_guest_and_shared_and_removed(rig):
    await rig.send({"type": "member_joined_channel", "channel": "C1", "user": "U9"}, event_id="E1")
    await rig.send({"type": "channel_shared", "channel": "C1"}, event_id="E2")
    await rig.send({"type": "member_left_channel", "channel": "C1", "user": BOT}, event_id="E3")
    await rig.send({"type": "member_left_channel", "channel": "C1", "user": "U9"}, event_id="E4")
    assert rig.rec.seen == [("joined", "T1", "C1", "U9"), ("shared", "T1", "C1"),
                            ("deactivate", "T1", "C1", "bot removed")]


async def test_channel_left_deactivates(rig):
    await rig.send({"type": "channel_left", "channel": "C2"})
    assert rig.rec.seen == [("deactivate", "T1", "C2", "bot removed")]


async def test_unknown_event_type_ignored(rig):
    await rig.send({"type": "reaction_added", "channel": "C1"})
    assert rig.rec.seen == []


async def test_malformed_routed_event_never_raises(rig):
    await rig.send({"type": "member_joined_channel", "channel": "C1"})
    await rig.send({"type": "app_mention"}, event_id="E2")
    assert rig.clients[0].responses


async def test_on_request_returns_before_handler_finishes(rig):
    rig.rec.gate = asyncio.Event()
    await rig.push({"event_id": "Ev1", "team_id": "T1", "event": mention()})
    assert len(rig.gateway._tasks) == 1
    rig.rec.gate.set()
    await rig.settle()
    assert not rig.gateway._tasks


async def test_purge_expires_after_24h(db_target):
    log = EventLog(db_target)
    log.clock = lambda: 1000.0
    assert log.first_time("old") is True
    assert log.first_time("old") is False
    log.clock = lambda: 1000.0 + 86400 - 10
    assert log.first_time("new") is True
    assert log.purge(1000.0 + 86400 + 1) == 1  # "old" is past 24h, "new" is not
    assert log.first_time("old") is True
    assert log.first_time("new") is False


async def test_purge_loop_runs_on_interval():
    class Events:
        def __init__(self):
            self.ran = asyncio.Event()
            self.calls = 0

        def first_time(self, event_id):
            return True

        def purge(self, now, older_than_s=86400.0):
            self.calls += 1
            if self.calls == 2:
                self.ran.set()
            return 0

    events = Events()
    gateway = Gateway(TOKEN, object(), Recorder(), Recorder(), Recorder(), events, bot_user_id=BOT,
                      client_factory=FakeClient, purge_interval_s=0.01)
    await gateway.start()
    await asyncio.wait_for(events.ran.wait(), 5)
    await gateway.stop()


async def test_stop_closes_socket(db_target):
    rig = Rig(db_target)
    await rig.gateway.start()
    await rig.gateway.stop()
    assert rig.clients[0].calls == ["connect", "disconnect", "close"]


async def test_stop_is_idempotent(db_target):
    rig = Rig(db_target)
    await rig.gateway.start()
    await rig.gateway.stop()
    await rig.gateway.stop()
    assert rig.clients[0].calls.count("close") == 1


async def test_stop_before_start_is_safe(db_target):
    await Rig(db_target).gateway.stop()


async def test_stop_waits_for_inflight_handlers(db_target):
    rig = Rig(db_target)
    await rig.gateway.start()
    rig.rec.gate = asyncio.Event()
    await rig.push({"event_id": "Ev1", "team_id": "T1", "event": mention()})
    stopping = asyncio.create_task(rig.gateway.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()
    rig.rec.gate.set()
    await asyncio.wait_for(stopping, 2)
    assert not rig.gateway._tasks


async def test_stop_cancels_handlers_after_grace(db_target):
    rig = Rig(db_target)
    rig.gateway.grace_s = 0.05
    await rig.gateway.start()
    rig.rec.gate = asyncio.Event()  # never released
    await rig.push({"event_id": "Ev1", "team_id": "T1", "event": mention()})
    await asyncio.wait_for(rig.gateway.stop(), 2)
    assert not rig.gateway._tasks
