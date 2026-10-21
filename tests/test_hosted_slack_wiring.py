"""Slack wiring: built from settings, started and stopped by the owner lock, restart recovery on gain."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from slack_fakes import FakeSlack
from slack_gateway_rig import FakeClient

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.config import SLACK_WORKSPACE
from openrouter_image_mcp.hosted.app import build_hosted_server
from openrouter_image_mcp.hosted.limits import Limits
from openrouter_image_mcp.hosted.settings import load_hosted_settings
from openrouter_image_mcp.hosted.slack.api import SdkSlackApi
from openrouter_image_mcp.hosted.slack.principal import SlackCredentials
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.slack.wiring import SlackApp, build_slack
from openrouter_image_mcp.hosted.student_keys import StudentKeyStore
from openrouter_image_mcp.hosted.vault import Vault
from tests import jwt_helpers as h
from tests.hosted_harness import hosted_env

THREAD = "1700000000.000001"


def slack_env():
    return {"SIDECAR_SLACK_ENABLED": "1", "SLACK_BOT_TOKEN": "xoxb-" + "1" * 10,
            "SLACK_APP_TOKEN": "xapp-" + "1" * 10}


def settings_for(target, extra=None):
    return load_hosted_settings(hosted_env(8123, str(target)) | (extra or {}))


class Provisioner:
    async def ensure(self, principal, *, team=None):
        return None


class World:
    def __init__(self, target, *, api="fake"):
        self.target, self.settings = target, settings_for(target, slack_env())
        self.keys = StudentKeyStore(target, Vault(self.settings.vault_keys))
        self.slack = FakeSlack() if api == "fake" else None
        self.clients: list[FakeClient] = []

        def factory(app_token, web_client):
            self.clients.append(FakeClient(app_token, web_client))
            return self.clients[-1]

        self.app: SlackApp = build_slack(self.settings, target, self.keys, Provisioner(),
                                         Limits(AdvisorStore(target)), api=self.slack, client_factory=factory)

    def orphan(self):
        """A finished answer another process never posted, in a registered channel."""
        row = new_row("T1", "C1", "team-a", "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                      subagent_model="e/sub")
        self.app.dispatcher.deps.registry.add(row)
        scope = Scope("slack|T1:C1", row.workspace_id)
        store = AdvisorStore(self.target)
        chat = store.create(scope, "g/model", "t")
        self.app.dispatcher.deps.threads.put(scope, "C1", THREAD, "gemini", "inline", chat.id)
        results = AdvisorResults(store)
        job = results.create(scope, chat, "p", "proc-dead", slack_user_id="U1", slack_channel_id="C1")
        with results.connection() as db:
            db.execute("UPDATE advisor_jobs SET status='completed', answer='Saved answer.', cost_usd=0.01, "
                       "created_at=? WHERE id=?", (datetime.now(UTC).isoformat(), job["id"]))
        return job["id"]


async def gain(app):
    """A lock gain, then wait for the start task it began (auth.test, recovery start, gateway start)."""
    await app.on_gain()
    if app.start_task is not None:
        await app.start_task


class Sleeps:
    """Injected `SlackApp.sleep`: records each backoff, and can block until released."""

    def __init__(self, block=False):
        self.delays, self.block = [], block

    async def __call__(self, delay):
        self.delays.append(delay)
        if self.block:
            await asyncio.Event().wait()


def recorder(calls, gate=None):
    async def recover():
        calls.append(1)
        if gate is not None:
            await gate.wait()
        return {}
    return recover


@pytest.fixture
async def world(db_target):
    made = World(db_target)
    yield made
    await made.app.close()


def test_not_built_when_disabled(db_target):
    server = build_hosted_server(settings_for(db_target), key_resolver=h.resolver)
    assert server.hosted.get("slack") is None
    assert len(server.hosted["health"].lock.on_gain) == 1  # only Health's own gate hook


def test_built_when_enabled_and_hooked_to_the_lock(db_target):
    server = build_hosted_server(settings_for(db_target, slack_env()), key_resolver=h.resolver,
                                 slack={"api": FakeSlack()})
    app = server.hosted["slack"]
    assert isinstance(app, SlackApp)
    lock = server.hosted["health"].lock
    assert lock.on_gain[-1] == app.on_gain and lock.on_lose[-1] == app.on_lose


def test_one_service_set_in_the_slack_workspace(world):
    services = world.app.services
    advisors = services.advisors
    assert advisors.workspace_id == advisors.client.workspace_id == SLACK_WORKSPACE
    assert isinstance(advisors.credentials, SlackCredentials)
    assert advisors.credentials.workspace_id == SLACK_WORKSPACE
    assert advisors.sandbox_default is False
    assert advisors.sandbox_allowed is world.settings.sandbox_enabled
    assert services.client is advisors.client and services.files is advisors.files
    assert world.app.dispatcher.deps.services is world.app.services


async def test_built_and_started_on_gain(world):
    job_id = world.orphan()
    await world.app.on_gain()  # returns at once: the start runs in its own task
    await world.app.start_task
    assert world.app.gateway.bot_user_id == world.app.dispatcher.bot_user_id == world.slack.bot_user
    assert world.app.dispatcher.deps.builder.bot_user_id == world.slack.bot_user  # its links never fetchable
    await asyncio.sleep(0)
    assert [c.calls for c in world.clients] == [["connect"]]
    assert world.clients[0].app_token == world.settings.slack.app_token
    assert await world.app.recovery_task == {"posted": 1, "closed": 0, "waiting": 0}
    assert world.slack.posts[-1]["text"].startswith("Saved answer.")
    with AdvisorResults(AdvisorStore(world.target)).connection() as db:
        assert db.execute("SELECT slack_posted_at FROM advisor_jobs WHERE id=?", (job_id,)).fetchone()["slack_posted_at"]


async def test_auth_test_failure_is_retried_with_backoff(world, caplog):
    for _ in range(8):
        world.slack.fail_next("bot_user_id", "invalid_auth")
    calls, world.app.sleep = [], Sleeps()
    world.app.recover_once = recorder(calls)
    with caplog.at_level(logging.ERROR):
        await gain(world.app)
    assert world.app.sleep.delays == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]
    assert "SlackCallError" in caplog.text and "invalid_auth" not in caplog.text
    await world.app.recovery_task
    assert len(world.clients) == 1 and calls == [1]


async def test_a_second_gain_while_retrying_starts_no_second_task(world):
    world.slack.fail_next("bot_user_id", "invalid_auth")
    world.app.sleep = Sleeps(block=True)
    await world.app.on_gain()
    first = world.app.start_task
    await asyncio.sleep(0)
    await world.app.on_gain()
    assert world.app.start_task is first and world.app.sleep.delays == [5.0]
    await world.app.on_lose()  # cancels the waiting start
    assert first.cancelled() and world.app.start_task is None and world.clients == []


async def test_recover_is_not_doubled_while_running(world):
    gate, calls = asyncio.Event(), []
    world.app.recover_once = recorder(calls, gate)
    await gain(world.app)
    first = world.app.recovery_task
    await asyncio.sleep(0)
    await gain(world.app)  # a second gain while the first sweep runs
    await asyncio.sleep(0)
    assert world.app.recovery_task is first and calls == [1]
    gate.set()
    await first
    await gain(world.app)
    await world.app.recovery_task
    assert calls == [1, 1]


async def test_on_lose_stops_the_gateway_and_cancels_recovery(world):
    world.app.recover_once = recorder([], asyncio.Event())
    await gain(world.app)
    task = world.app.recovery_task
    await asyncio.sleep(0)
    await world.app.on_lose()
    assert task.cancelled() and world.app.recovery_task is None
    assert "close" in world.clients[0].calls
    await world.app.on_lose()  # idempotent
    await gain(world.app)  # a regain starts it again
    assert len(world.clients) == 2


async def test_a_gain_after_close_starts_nothing(world):
    calls = []
    world.app.recover_once = recorder(calls)
    await world.app.close()
    await world.app.on_gain()
    assert world.app.start_task is None and world.app.recovery_task is None
    assert world.clients == [] and calls == []


async def test_close_during_auth_test_starts_nothing_after_it(world):
    calls, in_flight, release = [], asyncio.Event(), asyncio.Event()
    world.app.recover_once = recorder(calls)

    async def held_auth_test():
        in_flight.set()
        await release.wait()
        return "UBOT"

    world.slack.bot_user_id = held_auth_test
    await world.app.on_gain()
    start = world.app.start_task
    await in_flight.wait()
    closing = asyncio.create_task(world.app.close())
    await asyncio.sleep(0)
    release.set()
    await closing
    assert start.done() and world.app.start_task is None
    assert world.clients == [] and calls == [] and world.app.recovery_task is None


async def test_a_failing_start_is_logged_by_type_only(world, caplog):
    def broken_factory(app_token, web_client):
        raise ValueError("detail that must stay out of the log")

    world.app.gateway._factory = broken_factory
    with caplog.at_level(logging.ERROR):
        await gain(world.app)
    start = world.app.start_task
    assert start.done() and not start.cancelled() and start.exception() is None  # nothing left unretrieved
    [record] = [r for r in caplog.records if "Slack start failed" in r.getMessage()]
    assert "ValueError" in record.getMessage() and record.exc_info is None
    assert "detail that must stay out" not in caplog.text


async def test_close_drains_jobs_then_cancels_the_rest(world):
    world.app.drain_s = 0.05
    quick_done, slow_cancelled = asyncio.Event(), asyncio.Event()

    async def quick():
        quick_done.set()

    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            slow_cancelled.set()

    for job in (quick(), slow()):
        task = asyncio.create_task(job)
        world.app.dispatcher._tasks.add(task)
        task.add_done_callback(world.app.dispatcher._tasks.discard)
    await gain(world.app)
    await world.app.close()
    assert quick_done.is_set() and slow_cancelled.is_set()
    assert not world.app.dispatcher._tasks
    assert "close" in world.clients[0].calls


async def test_close_closes_the_clients_it_built(db_target):
    made = World(db_target, api=None)
    assert isinstance(made.app.api, SdkSlackApi)
    await made.app.close()
    assert made.app.api._http.is_closed
    assert made.app.services.client._http.is_closed
