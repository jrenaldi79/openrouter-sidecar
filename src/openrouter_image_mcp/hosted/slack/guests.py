"""Guest and shared-channel checks: Sidecar stays off in a channel that has a guest or is shared outside.

`channel_problem` is the one check, used by the guard. `GuestGuard` deactivates a
registered channel on a guest join or a share event, posting `GUEST_OFF` once, and re-checks members at
most hourly as a backstop for anything the events missed. A failed check fails open only within two
intervals of the last success; a failed join lookup denies until a check succeeds.
`scripts/ops/slack_channels.py` calls `channel_problem` before `add` and `update --active`.
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
        self._verified: dict[tuple[str, str], float] = {}  # last successful check per channel
        self._recheck: set[tuple[str, str]] = set()  # a join lookup failed: deny until a check succeeds

    async def allowed(self, row: ChannelRow) -> bool:
        # Re-read the row: the caller's copy may predate a deactivation.
        current = self.registry.get(row.slack_team_id, row.channel_id)
        if current is None or not current.active:
            return False
        key = (row.slack_team_id, row.channel_id)
        verified = self._verified.get(key)
        if key not in self._recheck and verified is not None and self.clock() - verified < self.every_s:
            return True
        try:
            problem = await channel_problem(self.api, row.channel_id)
        except Exception as error:  # noqa: BLE001 - any failed check counts, not only Slack errors
            log.warning("Guest check for %s failed (%s): %s", row.channel_id, type(error).__name__,
                        redact(str(error)))
            return self._fail_open(key, verified)
        self._verified[key] = self.clock()
        self._recheck.discard(key)
        if problem is None:
            return True
        await self._deactivate(row.slack_team_id, row.channel_id, problem)
        return False

    def _fail_open(self, key: tuple[str, str], verified: float | None) -> bool:
        """A failed check passes only on recent evidence, and never while a join lookup is unresolved."""
        if key in self._recheck or verified is None:
            return False
        return self.clock() - verified < 2 * self.every_s

    async def on_member_joined(self, team_id: str, channel_id: str, user_id: str) -> None:
        row = self.registry.get(team_id, channel_id)
        if row is None or not row.active:
            return
        try:
            guest = is_guest(await self.api.user(user_id))
        except Exception as error:  # noqa: BLE001 - any failed lookup is an unknown member
            # Unknown member: deny until a full check of the channel succeeds.
            detail = redact(str(error)) if isinstance(error, SlackCallError) else type(error).__name__
            log.warning("Guest lookup in %s failed: %s", channel_id, detail)
            self._recheck.add((team_id, channel_id))
            return
        if guest:
            await self._deactivate(team_id, channel_id, GUEST_JOINED)

    async def on_channel_shared(self, team_id: str, channel_id: str) -> None:
        if self.registry.get(team_id, channel_id) is not None:
            await self._deactivate(team_id, channel_id, SHARED)

    async def _deactivate(self, team_id: str, channel_id: str, reason: str) -> None:
        """`deactivate` is True only on active to inactive, so the notice posts once however many events race."""
        self._verified.pop((team_id, channel_id), None)
        self._recheck.discard((team_id, channel_id))
        if not self.registry.deactivate(team_id, channel_id, reason):
            return
        try:
            await self.api.post(channel_id, None, GUEST_OFF)
        except SlackCallError as error:
            log.warning("Could not post the guest notice in %s: %s", channel_id, redact(str(error)))
