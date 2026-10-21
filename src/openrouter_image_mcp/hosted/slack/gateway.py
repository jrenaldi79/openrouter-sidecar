"""Socket Mode gateway: acks Slack envelopes, dedupes events across replicas and routes them.

Slack redelivers an event until it is acked and can deliver one to several connections, so `EventLog`
(a shared table) lets exactly one replica handle each `event_id`. `Gateway.on_request` acks first, always,
then validates, dedupes, filters bot posts and edits, and starts the handler as a task so it returns fast.
Handler failures are logged by type and never escape. The owner lock (Task 21) starts and stops it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from ... import storage
from ...logs import redact_log

log = logging.getLogger(__name__)

_SCHEMA = "CREATE TABLE IF NOT EXISTS slack_events (event_id TEXT PRIMARY KEY, received_at REAL NOT NULL)"
RETENTION_S = 86400.0
PURGE_EVERY_S = 3600.0
GRACE_S = 5.0
BOT_REMOVED = "bot removed"
_NOT_EDITS = {"message_changed", "message_deleted", "bot_message"}


class EventLog:
    """Event ids Slack has delivered, shared by every replica through the database."""

    def __init__(self, target):
        self.target = target
        self.clock = time.time

    def first_time(self, event_id: str) -> bool:
        """True only for the caller whose insert created the row, on SQLite and Postgres alike."""
        with storage.connect(self.target) as db:
            db.execute(_SCHEMA)
            cursor = db.execute("INSERT INTO slack_events (event_id, received_at) VALUES (?, ?) "
                                "ON CONFLICT DO NOTHING", (event_id, self.clock()))
            db.commit()
            return cursor.rowcount == 1

    def purge(self, now: float, older_than_s: float = RETENTION_S) -> int:
        with storage.connect(self.target) as db:
            db.execute(_SCHEMA)
            cursor = db.execute("DELETE FROM slack_events WHERE received_at < ?", (now - older_than_s,))
            db.commit()
            return cursor.rowcount


def _text(value) -> bool:
    return isinstance(value, str) and bool(value)


def _skipped(event: dict) -> bool:
    """Bot posts and edits never reach a handler."""
    return bool(event.get("bot_id") or event.get("edited") or event.get("subtype") in _NOT_EDITS)


class Gateway:
    def __init__(self, app_token: str, web_client, dispatcher, guard, registry, events, *, bot_user_id: str,
                 client_factory: Callable = SocketModeClient, purge_interval_s: float = PURGE_EVERY_S):
        self._app_token, self._web_client = app_token, web_client
        self.dispatcher, self.guard, self.registry, self.events = dispatcher, guard, registry, events
        self.bot_user_id = bot_user_id
        self._factory, self._purge_every = client_factory, purge_interval_s
        self.grace_s = GRACE_S
        self._client = None
        self._purger: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._client is not None:
            return
        client = self._factory(app_token=self._app_token, web_client=self._web_client)
        client.socket_mode_request_listeners.append(self.on_request)
        self._client = client
        self._purger = asyncio.create_task(self._purge_loop())
        await client.connect()

    async def stop(self) -> None:
        """Idempotent: stop purging and new events, give handlers `grace_s` to finish, cancel the rest."""
        client, self._client = self._client, None
        purger, self._purger = self._purger, None
        if purger is not None:
            purger.cancel()
            await asyncio.gather(purger, return_exceptions=True)
        if client is not None:
            await self._quietly("disconnect", client.disconnect())
        await self._drain()
        if client is not None:
            await self._quietly("close", client.close())

    async def _drain(self) -> None:
        pending = list(self._tasks)
        if not pending:
            return
        _, still = await asyncio.wait(pending, timeout=self.grace_s)
        for task in still:
            task.cancel()
        await asyncio.gather(*still, return_exceptions=True)

    async def _quietly(self, step: str, call: Awaitable) -> None:
        try:
            await call
        except Exception as error:  # noqa: BLE001 - a failed socket step must not stop the others
            log.warning("Slack socket %s failed: %s", step, redact_log(type(error).__name__))

    async def _purge_loop(self) -> None:
        while True:
            try:
                removed = await asyncio.to_thread(self.events.purge, time.time())
                log.debug("Purged %s old Slack event ids", removed)
            except Exception as error:  # noqa: BLE001 - a failed sweep is retried next interval
                log.warning("Slack event purge failed: %s", redact_log(type(error).__name__))
            await asyncio.sleep(self._purge_every)

    async def on_request(self, client, req: SocketModeRequest) -> None:
        await self._ack(client, req)
        if req.type != "events_api":
            return
        payload = req.payload if isinstance(req.payload, dict) else {}
        event_id, team_id, event = payload.get("event_id"), payload.get("team_id"), payload.get("event")
        if not (_text(event_id) and _text(team_id) and isinstance(event, dict)):
            log.warning("Dropped a malformed Slack envelope")
            return
        if not await self._first_time(event_id) or _skipped(event):
            return
        handler = self._handler(team_id, event)
        if handler is not None:
            self._spawn(handler)

    async def _ack(self, client, req: SocketModeRequest) -> None:
        try:
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        except Exception as error:  # noqa: BLE001 - a failed ack must not stop handling
            log.warning("Slack ack failed: %s", redact_log(type(error).__name__))

    async def _first_time(self, event_id: str) -> bool:
        try:
            return await asyncio.to_thread(self.events.first_time, event_id)
        except Exception as error:  # noqa: BLE001 - dedupe failure fails open
            # Only the owner replica handles events, so a lost mention costs more than a rare repeat.
            log.warning("Slack event dedupe failed, handling anyway: %s", redact_log(type(error).__name__))
            return True

    def _handler(self, team_id: str, event: dict) -> Awaitable | None:
        kind, channel, user = event.get("type"), event.get("channel"), event.get("user")
        if kind == "app_mention":
            return self.dispatcher.on_mention(team_id, event)
        if kind == "member_joined_channel" and _text(channel) and _text(user):
            return self.guard.on_member_joined(team_id, channel, user)
        if kind == "channel_shared" and _text(channel):
            return self.guard.on_channel_shared(team_id, channel)
        removed = (kind == "member_left_channel" and user == self.bot_user_id) or kind == "channel_left"
        if removed and _text(channel):
            return self._deactivate(team_id, channel)
        return None

    async def _deactivate(self, team_id: str, channel: str) -> None:
        self.registry.deactivate(team_id, channel, BOT_REMOVED)

    def _spawn(self, handler: Awaitable) -> None:
        task = asyncio.ensure_future(handler)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.warning("Slack event handler failed: %s", redact_log(type(error).__name__))
