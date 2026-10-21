"""Guest and shared-channel checks: Sidecar stays off in a channel that has a guest or is shared outside.

`channel_problem` is the one check (the ops script and the guard both use it). `GuestGuard` deactivates a
registered channel on a guest join or a share event, posting `GUEST_OFF` once, and re-checks members at
most hourly as a backstop for anything the events missed.
"""

from __future__ import annotations

import logging
import time

from ...logs import redact
from .api import SlackApi, SlackCallError
from .registry import ChannelRegistry, ChannelRow
from .welcome import GUEST_OFF

log = logging.getLogger(__name__)

SHARED = "shared outside the organization"
GUEST = "has a guest member"
GUEST_JOINED = "guest joined"
RECHECK_S = 3600.0


def is_guest(user: dict) -> bool:
    return bool(user.get("is_restricted") or user.get("is_ultra_restricted"))


async def channel_problem(api: SlackApi, channel_id: str) -> str | None:
    """Why Sidecar must refuse this channel, or None. Sharing is checked first so it needs no member scan."""
    info = await api.channel_info(channel_id)
    if info.get("is_ext_shared") or info.get("is_pending_ext_shared"):
        return SHARED
    for user_id in await api.members(channel_id):
        if is_guest(await api.user(user_id)):
            return GUEST
    return None


class GuestGuard:
    def __init__(self, registry: ChannelRegistry, api: SlackApi, *, every_s: float = RECHECK_S,
                 clock=time.monotonic):
        self.registry, self.api, self.every_s, self.clock = registry, api, every_s, clock
        self._checked: dict[tuple[str, str], float] = {}

    async def allowed(self, row: ChannelRow) -> bool:
        if not row.active:
            return False
        key = (row.slack_team_id, row.channel_id)
        last = self._checked.get(key)
        if last is not None and self.clock() - last < self.every_s:
            return True
        try:
            problem = await channel_problem(self.api, row.channel_id)
        except SlackCallError as error:
            # Fail open for this call only; with no cache entry the next call retries.
            log.warning("Guest check for %s failed: %s", row.channel_id, redact(str(error)))
            return True
        if problem is None:
            self._checked[key] = self.clock()
            return True
        await self._deactivate(row.slack_team_id, row.channel_id, problem)
        return False

    async def on_member_joined(self, team_id: str, channel_id: str, user_id: str) -> None:
        row = self.registry.get(team_id, channel_id)
        if row is None or not row.active:
            return
        try:
            guest = is_guest(await self.api.user(user_id))
        except SlackCallError as error:
            # Unknown member: drop the cache so the next request re-checks the whole channel.
            log.warning("Guest lookup in %s failed: %s", channel_id, redact(str(error)))
            self._checked.pop((team_id, channel_id), None)
            return
        if guest:
            await self._deactivate(team_id, channel_id, GUEST_JOINED)

    async def on_channel_shared(self, team_id: str, channel_id: str) -> None:
        if self.registry.get(team_id, channel_id) is not None:
            await self._deactivate(team_id, channel_id, SHARED)

    async def _deactivate(self, team_id: str, channel_id: str, reason: str) -> None:
        """`deactivate` is True only on active to inactive, so the notice posts once however many events race."""
        self._checked.pop((team_id, channel_id), None)
        if not self.registry.deactivate(team_id, channel_id, reason):
            return
        try:
            await self.api.post(channel_id, None, GUEST_OFF)
        except SlackCallError as error:
            log.warning("Could not post the guest notice in %s: %s", channel_id, redact(str(error)))
