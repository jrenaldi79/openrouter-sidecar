import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from slack_gateway_rig import BOT, TOKEN, FakeClient, Recorder, Rig, envelope, mention

from openrouter_image_mcp.hosted.slack.gateway import EventLog, Gateway

SECRET = "sentinel-" + "z" * 8  # must never reach a log line


@pytest.fixture
async def rig(db_target):
    made = Rig.on(db_target)
    await made.start()
    yield made
    await made.gateway.stop()


class BrokenEvents:
    """An event log whose database is down."""

    def __init__(self):
        self.calls = 0

    def first_time(self, event_id):
        self.calls += 1
        raise ConnectionError(SECRET)

    def purge(self, now, older_than_s=86400.0):
        return 0


async def test_start_registers_listener_and_connects(rig):
    client = rig.clients[0]
    assert client.app_token == TOKEN
    assert client.socket_mode_request_listeners == [rig.gateway.on_request]
    assert client.calls == ["connect"]


async def test_ack_before_handling(rig):
    rig.rec.boom = RuntimeError("handler failed")
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
    first, second = Rig.on(db_target), Rig.on(db_target)
    await first.start()
    await second.start()
    await first.send(mention())
    await second.send(mention())
    assert len(first.rec.seen) + len(second.rec.seen) == 1
    await first.gateway.stop()
    await second.gateway.stop()


async def test_first_time_race_has_one_winner(db_target):
    log = EventLog(db_target)
    log.first_time("warm-up")  # create the table before the threads race
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: log.first_time("Ev-race"), range(16)))
    assert results.count(True) == 1


async def test_db_failure_fails_open(caplog):
    rig = Rig(BrokenEvents())
    await rig.start()
    with caplog.at_level(logging.WARNING):
        await rig.send(mention())
    assert rig.rec.seen == [("mention", "T1", "hi")]
    assert "ConnectionError" in caplog.text and SECRET not in caplog.text
    await rig.gateway.stop()


async def test_db_failure_still_drops_a_repeat_seen_in_memory():
    events = BrokenEvents()
    rig = Rig(events)
    await rig.start()
    await rig.send(mention())
    await rig.send(mention(), envelope="env2")
    assert len(rig.rec.seen) == 1
    assert events.calls == 1  # the repeat never reached the database
    await rig.gateway.stop()


async def test_recent_ids_are_bounded():
    rig = Rig(BrokenEvents())
    await rig.start()
    for number in range(1005):
        await rig.send(mention(), event_id=f"Ev{number}")
    assert len(rig.gateway._recent) == 1000
    await rig.send(mention(), event_id="Ev0")  # long evicted, so it is handled again
    assert len(rig.rec.seen) == 1006
    await rig.gateway.stop()


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


async def test_event_missing_its_ids_is_acked_and_never_handled(rig):
    await rig.send({"type": "member_joined_channel", "channel": "C1"}, event_id="E1")
    await rig.send({"type": "channel_shared"}, event_id="E2")
    await rig.send({"type": "channel_left"}, event_id="E3")
    assert rig.rec.seen == []
    assert len(rig.clients[0].responses) == 3
    assert not rig.gateway._tasks


async def test_handler_failure_logs_only_the_type(rig, caplog):
    rig.rec.boom = KeyError(SECRET)
    with caplog.at_level(logging.WARNING):
        await rig.send(mention())
    assert "KeyError" in caplog.text
    assert SECRET not in caplog.text
    rig.rec.boom = None
    await rig.send(mention("again"), event_id="Ev2")  # the gateway keeps working
    assert rig.rec.seen[-1] == ("mention", "T1", "again")


async def test_on_request_returns_before_handler_finishes(rig):
    rig.rec.gate = asyncio.Event()
    await rig.push(envelope(mention()))
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
