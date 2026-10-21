"""End to end on the running hosted server: an `app_mention` envelope through the real gateway, dispatcher,
runner and key provisioner, with FakeSlack and the fake OpenRouter."""

import asyncio

import pytest
from slack_fakes import FakeSlack
from slack_gateway_rig import FakeClient
from slack_sdk.socket_mode.request import SocketModeRequest

from openrouter_image_mcp.hosted.openrouter_keys import TEAM_GUARDRAIL
from openrouter_image_mcp.hosted.provisioning import TeamKey
from openrouter_image_mcp.hosted.slack.principal import acting_as, team_principal
from openrouter_image_mcp.hosted.slack.registry import new_row
from tests.hosted_harness import MODEL, running_server

THREAD = "1700000100.000001"


def slack_env():
    return {"SIDECAR_SLACK_ENABLED": "1", "SLACK_BOT_TOKEN": "xoxb-" + "1" * 10,
            "SLACK_APP_TOKEN": "xapp-" + "1" * 10}


class LoopClient(FakeClient):
    """Remembers the server's event loop, so the test can run work there."""

    loop: asyncio.AbstractEventLoop | None = None

    async def connect(self):
        LoopClient.loop = asyncio.get_running_loop()
        await super().connect()


async def on_server(coro):
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, LoopClient.loop))


async def wait_for_loop():
    for _ in range(500):
        if LoopClient.loop is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the gateway never connected")


@pytest.fixture
async def running(db_target):
    LoopClient.loop, clients = None, []

    def factory(app_token, web_client):
        clients.append(LoopClient(app_token, web_client))
        return clients[-1]

    slack = FakeSlack()
    async with running_server(db_target, env=slack_env(),
                              slack={"api": slack, "client_factory": factory}) as server:
        server.fake.guardrails.append({"id": "g-team", "name": TEAM_GUARDRAIL})
        await wait_for_loop()
        yield server, slack, clients[0]


async def setup_team(app, slack):
    """What the ops scripts do: register the channel, provision the team key, seed its model defaults."""
    row = new_row("T1", "C1", "abbott", "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                  subagent_model="e/sub", default_model="gpt")
    deps = app.dispatcher.deps
    deps.registry.add(row)
    slack.channels["C1"], slack.members["C1"] = {}, ["U1"]
    slack.users["U1"] = {"profile": {"display_name": "Ana"}}
    principal = team_principal("T1", "C1", row)
    await deps.provisioner.ensure(principal, team=TeamKey(row.team_label))
    preferences = app.services.preferences
    with acting_as(principal):
        saved = preferences.get()
        await preferences.update(saved["settings_id"], saved["revision"], general_default=MODEL,
                                 family_defaults={"openai": MODEL})
    return row


async def deliver(app, client, event, event_id="Ev1"):
    request = SocketModeRequest("events_api", "env-" + event_id,
                                {"event_id": event_id, "team_id": "T1", "event": event})
    await app.gateway.on_request(client, request)
    while app.gateway._tasks:
        await asyncio.gather(*list(app.gateway._tasks), return_exceptions=True)
    await app.dispatcher.drain()


async def test_end_to_end_mention_to_answer(running):
    server, slack, client = running
    app = server.server.hosted["slack"]
    await on_server(setup_team(app, slack))
    event = {"type": "app_mention", "channel": "C1", "user": "U1", "ts": THREAD,
             "text": "<@UBOT> what should we test first?"}
    await on_server(deliver(app, client, event))

    team_keys = [b for b in server.fake.created_bodies() if b["name"].startswith("Sidecar · abbott")]
    assert [b["name"] for b in team_keys] == ["Sidecar · abbott · 2027"]
    assert server.fake.guardrails[-1]["name"] == TEAM_GUARDRAIL
    assert [guardrail for guardrail, _ in server.fake.assignments] == ["g-team"]
    [answer] = [p for p in slack.posts if p["text"].startswith("Final advice")]
    assert answer["username"] == "Sidecar · ChatGPT" and answer["thread_ts"] == THREAD
    assert answer["text"] == f"Final advice\n\nChatGPT · {MODEL} · $0.0020"
    with app.dispatcher.deps.jobs.results.connection() as db:
        [row] = [dict(r) for r in db.execute("SELECT * FROM advisor_jobs")]
    assert row["owner"] == "slack|T1:C1" and row["slack_user_id"] == "U1" and row["status"] == "completed"
    assert row["slack_posted_at"] is not None
    # The model ran on the team's own key. Compared as one boolean so a failure never prints a key.
    team_key = server.keys.key_for(team_principal("T1", "C1", app.dispatcher.deps.registry.get("T1", "C1")))
    calls = [r for r in server.fake.requests if r.url.path.endswith("/responses")]
    on_team_key = bool(team_key) and all(r.headers.get("Authorization") == "Bearer " + team_key for r in calls)
    assert calls and on_team_key
