import asyncio
import logging

from slack_gateway_rig import TOKEN, FakeClient, Rig, envelope, mention
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web.async_client import AsyncWebClient

from openrouter_image_mcp.hosted.slack.gateway import Gateway


async def test_stop_closes_the_client_once(db_target):
    rig = Rig.on(db_target)
    await rig.start()
    await rig.gateway.stop()
    assert rig.clients[0].calls == ["connect", "close"]


async def test_stop_is_idempotent(db_target):
    rig = Rig.on(db_target)
    await rig.start()
    await rig.gateway.stop()
    await rig.gateway.stop()
    assert rig.clients[0].calls.count("close") == 1


async def test_stop_before_start_is_safe(db_target):
    await Rig.on(db_target).gateway.stop()


async def test_stop_waits_for_inflight_handlers(db_target):
    rig = Rig.on(db_target)
    await rig.start()
    rig.rec.gate = asyncio.Event()
    await rig.push(envelope(mention()))
    stopping = asyncio.create_task(rig.gateway.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()
    rig.rec.gate.set()
    await asyncio.wait_for(stopping, 2)
    assert not rig.gateway._tasks


async def test_stop_cancels_handlers_after_grace(db_target):
    rig = Rig.on(db_target)
    rig.gateway.grace_s = 0.05
    await rig.start()
    rig.rec.gate = asyncio.Event()  # never released
    await rig.push(envelope(mention()))
    await asyncio.wait_for(rig.gateway.stop(), 2)
    assert not rig.gateway._tasks


async def test_late_event_during_drain_is_acked_and_never_handled(db_target):
    rig = Rig.on(db_target)
    await rig.start()
    rig.rec.gate = asyncio.Event()
    await rig.push(envelope(mention("first")))
    stopping = asyncio.create_task(rig.gateway.stop())
    await asyncio.sleep(0.05)
    late = SocketModeRequest("events_api", "late", envelope(mention("late"), "Ev2"))
    await rig.clients[0].deliver(late)  # a reconnected socket delivering mid-drain
    assert [r.envelope_id for r in rig.clients[0].responses] == ["env1", "late"]
    rig.rec.gate.set()
    await asyncio.wait_for(stopping, 2)
    assert rig.rec.seen == [("mention", "T1", "first")]
    assert not rig.gateway._tasks


async def test_event_delivered_while_closing_is_never_handled(db_target):
    rig = Rig.on(db_target)
    await rig.start()

    async def late_delivery(client):
        await client.deliver(SocketModeRequest("events_api", "late", envelope(mention("late"))))

    rig.clients[0].on_close = late_delivery
    await rig.gateway.stop()
    assert rig.rec.seen == []
    assert rig.clients[0].responses[0].envelope_id == "late"
    assert not rig.gateway._tasks


async def test_start_returns_while_connect_never_completes(db_target):
    clients = []

    def factory(app_token, web_client):
        clients.append(FakeClient(app_token, web_client))
        clients[0].connect_forever = True
        return clients[0]

    rig = Rig.on(db_target, client_factory=factory)
    rig.clients = clients
    await asyncio.wait_for(rig.gateway.start(), 1)
    await asyncio.sleep(0)
    assert rig.clients[0].calls == ["connect"]
    connector = rig.gateway._connector
    assert not connector.done()
    await asyncio.wait_for(rig.gateway.stop(), 2)
    assert connector.done()
    assert rig.clients[0].calls[-1] == "close"


async def test_connect_failure_is_logged_by_type(db_target, caplog):
    rig = Rig.on(db_target)
    await rig.gateway.start()  # builds the client; the connect task has not run yet
    rig.clients[0].connect_error = ConnectionError("xapp-" + "9" * 10)
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.01)
    assert "ConnectionError" in caplog.text
    assert "xapp-" + "9" * 10 not in caplog.text
    await rig.gateway.stop()


async def test_second_start_is_a_logged_noop(db_target, caplog):
    rig = Rig.on(db_target)
    await rig.start()
    with caplog.at_level(logging.INFO):
        await rig.gateway.start()
    assert len(rig.clients) == 1
    assert "already started" in caplog.text
    await rig.gateway.stop()


async def test_restart_after_stop_accepts_events_again(db_target):
    rig = Rig.on(db_target)
    await rig.start()
    await rig.gateway.stop()
    await rig.start()
    await rig.clients[1].deliver(SocketModeRequest("events_api", "e", envelope(mention())))
    await rig.settle()
    assert rig.rec.seen == [("mention", "T1", "hi")]
    await rig.gateway.stop()


async def test_real_socket_mode_client_accepts_the_gateway_kwargs():
    gateway = Gateway(TOKEN, AsyncWebClient(), object(), object(), object(), object(), bot_user_id="UBOT")
    client = gateway._new_client()
    try:
        assert isinstance(client, SocketModeClient)
        assert client.app_token == TOKEN
        assert gateway.on_request in client.socket_mode_request_listeners
    finally:
        await client.close()
