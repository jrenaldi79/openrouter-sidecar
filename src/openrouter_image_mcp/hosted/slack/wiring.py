"""Builds the Slack bot from the hosted settings and runs it under the single-owner lock.

`build_slack` makes one `SlackServices` per cohort (an `AdvisorService` whose credentials are the team in
context), the shared stores, the runner, the dispatcher and the Socket Mode gateway. `SlackApp` is what the
hosted server holds: its `on_gain`/`on_lose` hooks follow `Health.lock` (appended after Health's own gate
hooks), and `close` runs at shutdown. Only `app.py` imports this module, and only when Slack is enabled.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from functools import partial

import httpx
from slack_sdk.web.async_client import AsyncWebClient

from ...advisor import AdvisorService
from ...advisor_client import AdvisorClient
from ...advisor_results import AdvisorResults
from ...advisor_store import AdvisorStore
from ...config import ADVISOR_MAX_TIMEOUT_S, COHORT_WORKSPACES
from ...preferences import PreferenceService
from ..openrouter_keys import current_key
from .api import DOWNLOAD_TIMEOUT_S, SdkSlackApi, SlackApi
from .context import ContextBuilder, NameCache
from .dispatch import Deps, Dispatcher
from .gateway import EventLog, Gateway
from .guests import GuestGuard
from .jobs import SlackJobs
from .memory import ChannelMemory
from .outputs import Outputs
from .principal import SlackCredentials
from .recovery import recover
from .registry import ChannelRegistry
from .runner import SlackRunner, SlackServices
from .threads import ThreadMap

log = logging.getLogger(__name__)

AUTH_TEST_S = 10.0  # cap on one auth.test attempt
START_RETRY_S = 5.0  # first wait after a failed auth.test; doubles each time
START_RETRY_MAX_S = 300.0  # the longest wait between attempts
DRAIN_S = 10.0  # how long shutdown waits for running answers before cancelling them

Closer = Callable[[], Awaitable[None]]


async def _cancel(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class SlackApp:
    """The running bot: `gateway`, `dispatcher` and `services` (by cohort), started and stopped by the lock.

    `on_gain` only starts one held start task, so the lock hook returns at once. That task retries
    `auth.test` with backoff until it learns the bot's user id, then starts restart recovery (one task at a
    time) and the gateway. After `close()` nothing starts again.
    """

    def __init__(self, gateway: Gateway, dispatcher: Dispatcher, services: dict[str, SlackServices],
                 api: SlackApi, recover_once: Callable[[], Awaitable[dict]], closers: list[Closer]):
        self.gateway, self.dispatcher, self.services, self.api = gateway, dispatcher, services, api
        self.recover_once, self.closers = recover_once, closers
        self.drain_s = DRAIN_S
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep  # tests inject a fake
        self.start_task: asyncio.Task | None = None
        self.recovery_task: asyncio.Task | None = None
        self._closed = False

    async def on_gain(self) -> None:
        """Start the start task unless one is running or the app is closed; returns promptly."""
        if self._closed:
            return
        if self.start_task is None or self.start_task.done():
            self.start_task = asyncio.create_task(self._start())

    async def _bot_user_id(self) -> str | None:
        """auth.test until it answers (None once closed); failures are logged by type only."""
        delay = START_RETRY_S
        while not self._closed:
            try:
                return await asyncio.wait_for(self.api.bot_user_id(), AUTH_TEST_S)
            except Exception as error:  # noqa: BLE001 - retried; the message could carry Slack detail
                log.error("Slack auth.test failed (%s); retrying in %gs", type(error).__name__, delay)
            await self.sleep(delay)
            delay = min(delay * 2, START_RETRY_MAX_S)
        return None

    async def _start(self) -> None:
        bot = await self._bot_user_id()
        if bot is None or self._closed:
            return
        self.gateway.bot_user_id = self.dispatcher.bot_user_id = bot
        if self.recovery_task is None or self.recovery_task.done():
            self.recovery_task = asyncio.create_task(self._recover())
        await self.gateway.start()

    async def _recover(self) -> dict:
        counts = await self.recover_once()
        log.info("Slack restart recovery: %s", counts)
        return counts

    async def on_lose(self) -> None:
        """Stop the start task, close the socket and stop recovery; idempotent (the lock also calls it at
        shutdown)."""
        task, self.start_task = self.start_task, None
        await _cancel(task)
        await self.gateway.stop()
        task, self.recovery_task = self.recovery_task, None
        await _cancel(task)

    async def close(self) -> None:
        """Shutdown: stop taking events, let running answers finish within `drain_s`, then close clients."""
        self._closed = True  # first: a gain from here on starts nothing
        await self.on_lose()
        await self.dispatcher.shutdown(self.drain_s)
        for closer in self.closers:
            try:
                await closer()
            except Exception as error:  # noqa: BLE001 - one failed close must not skip the others
                log.warning("Slack client close failed (%s)", type(error).__name__)


def _cohort_services(settings, target, keys, transport) -> dict[str, SlackServices]:
    services = {}
    for cohort, workspace in COHORT_WORKSPACES.items():
        store = AdvisorStore(target)
        client = AdvisorClient(workspace, ADVISOR_MAX_TIMEOUT_S, transport=transport)
        advisors = AdvisorService(workspace, store, client, credentials=SlackCredentials(workspace, keys),
                                  sandbox_default=False, sandbox_allowed=settings.sandbox_enabled)
        services[cohort] = SlackServices(advisors, PreferenceService(advisors), store, AdvisorResults(store),
                                         advisors.files, client)
    return services


def _recover_once(api, registry, threads, jobs, services, process_id) -> Callable[[], Awaitable[dict]]:
    by_workspace = {s.advisors.workspace_id: s.results for s in services.values()}
    return partial(recover, jobs, by_workspace.__getitem__, api, registry, threads, process_id=process_id)


def build_slack(settings, target, keys, provisioner, limits, *, transport=None, api: SlackApi | None = None,
                client_factory: Callable | None = None) -> SlackApp:
    """The Slack bot for `settings.slack` (enabled). `api` and `client_factory` replace Slack I/O in tests."""
    slack = settings.slack
    web = AsyncWebClient(token=slack.bot_token)
    closers: list[Closer] = []
    if api is None:
        api = SdkSlackApi(web, http=httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S))
        closers.append(api.aclose)
    services = _cohort_services(settings, target, keys, transport)
    closers += [s.client.aclose for s in services.values()]
    registry, threads, memory, jobs = ChannelRegistry(target), ThreadMap(target), ChannelMemory(target), SlackJobs(target)
    names, process_id = NameCache(api), "slack-" + secrets.token_hex(8)
    runner = SlackRunner(api, limits, threads, memory, jobs, Outputs(api), process_id=process_id, names=names,
                         registry=registry, key_info=partial(current_key, transport=transport))
    guard = GuestGuard(registry, api)
    deps = Deps(registry=registry, guard=guard, provisioner=provisioner, runner=runner,
                builder=ContextBuilder(api, names, channel_chars=slack.context_chars), threads=threads,
                memory=memory, jobs=jobs, api=api, limits=limits, services=services, settings=slack,
                sandbox_enabled=settings.sandbox_enabled)
    dispatcher = Dispatcher(deps, bot_user_id="")  # the real id comes from auth.test on each lock gain
    gateway = Gateway(slack.app_token, web, dispatcher, guard, registry, EventLog(target), bot_user_id="",
                      **({"client_factory": client_factory} if client_factory else {}))
    return SlackApp(gateway, dispatcher, services, api,
                    _recover_once(api, registry, threads, jobs, services, process_id), closers)
