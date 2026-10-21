"""The service behind the /admin page: list, register, edit, turn off and on Slack channels.

Routes stay thin: they parse a form and call one operation. Registry writes share a transaction with their audit
entry (`channel_setup.audit_callback`). Key creation and model saves can't, so they run after it, are safe to
repeat and are audited only once they succeed. Every refusal is a `ChannelRefused` holding one plain sentence.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ...errors import OpenRouterError
from ...logs import redact
from ...preferences import filter_catalog
from ..admin import audit as audit_log
from ..admin import copy
from ..admin.audit import AdminAudit
from ..admin.sessions import Session
from ..provisioning import KeyProvisioner, TeamKey
from .api import SlackApi, SlackCallError
from .channel_setup import (
    SETUP_REASONS,
    ChannelRefused,
    ChannelSetup,
    TeamModels,
    audit_callback,
    failure_reason,
    model_ids,
    same,
    team_of,
)
from .context import NameCache
from .guests import channel_problem
from .registry import ChannelRegistry, ChannelRow, _validate, new_row

if TYPE_CHECKING:
    from .runner import SlackServices

log = logging.getLogger(__name__)

__all__ = ["ChannelAdmin", "ChannelRefused", "Overview"]

MAX_MEMBERS = 50
LOOKUPS_AT_ONCE = 5
# The registry fields an edit may change. The label, cohort and ids are fixed once the row exists, and `active`
# moves only through turn_off and turn_on (which run the guest check).
EDITABLE = frozenset({"default_model", "alias_map", "fusion_panel", "fusion_analyst", "subagent_model",
                      "fetch_allowlist", "daily_cap_usd", "max_concurrent", "invoker_allowlist"})


@dataclass
class Overview:
    registered: list[tuple[ChannelRow, str]]  # each row with its #channel name (the id when Slack won't say)
    unregistered: list[dict]  # channels the bot is in that have no row: id, name, is_private
    slack_note: str | None


class ChannelAdmin:
    def __init__(self, registry: ChannelRegistry, api: SlackApi | None,
                 services: Mapping[str, SlackServices] | None, provisioner: KeyProvisioner, audit: AdminAudit,
                 *, names: NameCache | None = None, utcnow: Callable[[], datetime] | None = None):
        self.registry, self.api, self.services, self.provisioner = registry, api, services, provisioner
        self.audit = audit
        self.names = names or (NameCache(api) if api is not None else None)
        self._utcnow = utcnow or (lambda: datetime.now(UTC))
        self._models = TeamModels(services)
        self._setup = ChannelSetup(registry, provisioner, self._models, audit)

    # ---- reading ----

    async def _channel_name(self, channel_id: str) -> str:
        try:
            return (await self.api.channel_info(channel_id)).get("name") or channel_id
        except SlackCallError as error:
            log.warning("slack admin: channel lookup failed: %s", error.code)
            return channel_id

    async def overview(self) -> Overview:
        rows = self.registry.all()
        if self.api is None:
            return Overview([(row, row.channel_id) for row in rows], [], copy.SLACK_OFF)
        try:
            in_slack = await self.api.bot_channels()
        except SlackCallError as error:
            log.warning("slack admin: channel list failed: %s", error.code)
            return Overview([(row, row.channel_id) for row in rows], [], copy.SLACK_DOWN)
        names = await _limited(self._channel_name(row.channel_id) for row in rows)
        known = {row.channel_id for row in rows}
        return Overview(list(zip(rows, names, strict=True)), [c for c in in_slack if c["id"] not in known], None)

    async def check(self, channel_id: str) -> str | None:
        """Why Sidecar can't be used in this channel (a guest, or shared outside), as page text; None if fine."""
        api = self._slack()
        try:
            problem = await channel_problem(api, channel_id)
            if problem is None:
                return None
            name = await self._channel_name(channel_id)
        except SlackCallError as error:
            log.warning("slack admin: guest check failed: %s", error.code)
            raise ChannelRefused(copy.SLACK_DOWN) from None
        return copy.GUEST_BLOCKED.format(name=name, reason=problem)

    async def catalog(self) -> list[dict]:
        """The models a team may use: the public list, filtered like the students' lists."""
        try:
            client = next(iter((self.services or {}).values())).client
            return filter_catalog(await client.public_models(), self._utcnow())
        except (StopIteration, OpenRouterError) as error:
            log.warning("slack admin: model list failed: %s", redact(str(error)) or type(error).__name__)
            raise ChannelRefused(copy.CATALOG_DOWN) from None

    async def members(self, channel_id: str) -> list[tuple[str, str]] | None:
        """`(user id, name)` by name; None when there are over 50 members or Slack won't say."""
        if self.api is None or self.names is None:
            return None
        try:
            ids = await self.api.members(channel_id)
        except SlackCallError as error:
            log.warning("slack admin: member list failed: %s", error.code)
            return None
        if len(ids) > MAX_MEMBERS:
            return None
        found = list(zip(ids, await _limited(self.names.name(i) for i in ids), strict=True))
        return sorted(found, key=lambda pair: (pair[1].casefold(), pair[0]))

    async def is_registered(self, team_id: str, channel_id: str) -> bool:
        return self.registry.get(team_id, channel_id) is not None

    async def channel(self, team_id: str, channel_id: str) -> tuple[ChannelRow, dict[str, str | None] | None] | None:
        """The registered row and the team's saved per-family models, for the edit page; None when unregistered.

        The models are None when they can't be read (no services for the cohort, or the preferences failed): the
        page then leaves the model selects off, since a form can't tell "nothing saved" from "couldn't look"."""
        row = self.registry.get(team_id, channel_id)
        if row is None:
            return None
        try:
            return row, self._models.saved(row)
        except Exception as error:  # noqa: BLE001 - an unreadable model list must not hide the channel
            log.warning("slack admin: reading %s's models failed (%s)", row.team_label, type(error).__name__)
            return row, None

    def _slack(self) -> SlackApi:
        if self.api is None:
            raise ChannelRefused(copy.SLACK_OFF)
        return self.api

    def _row(self, team_id: str, channel_id: str) -> ChannelRow:
        row = self.registry.get(team_id, channel_id)
        if row is None:
            raise ChannelRefused(copy.NOT_REGISTERED)
        return row

    # ---- registering ----

    async def register(self, team_id: str, channel_id: str, fields: dict, family_defaults: dict,
                       actor: Session) -> ChannelRow:
        """Validate, write the row turned off with its audit entry, then finish setup (key, models, on)."""
        self._slack()
        fields = dict(fields)
        try:
            row = new_row(team_id, channel_id, fields.pop("label"), fields.pop("cohort"), **fields)
            _validate(row)
        except ValueError as error:
            raise ChannelRefused(str(error)) from None
        wanted = {family: model for family, model in family_defaults.items() if model}
        await self._check_models(model_ids(fields), wanted)
        problem = await self.check(channel_id)
        if problem:
            raise ChannelRefused(problem)
        try:
            self.registry.add(replace(row, active=False, inactive_reason=copy.SETTING_UP),
                              audit=audit_callback(self.audit, actor, audit_log.REGISTERED))
        except ValueError as error:  # a double submit, or a label already in use: no second row, no second key
            raise ChannelRefused(str(error)) from None
        return await self.finish_setup(team_id, channel_id, wanted, actor)

    async def finish_setup(self, team_id: str, channel_id: str, family_defaults: dict, actor: Session) -> ChannelRow:
        return await self._setup.finish(team_id, channel_id, family_defaults, actor)

    async def _check_models(self, ids: set[str], family_defaults: Mapping[str, str]) -> None:
        if not ids and not family_defaults:
            return  # nothing to look up, so a model list outage doesn't block the edit
        listed = {model["id"]: model for model in await self.catalog()}
        for model in sorted(ids | set(family_defaults.values())):
            if model not in listed:
                raise ChannelRefused(copy.MODEL_NOT_LISTED.format(model=model))
        for family, model in family_defaults.items():
            if listed[model]["family"] != family:
                raise ChannelRefused(copy.MODEL_WRONG_FAMILY.format(model=model, family=family))

    # ---- editing ----

    async def change(self, team_id: str, channel_id: str, changes: dict, family_defaults: dict,
                     actor: Session) -> dict[str, list]:
        """Write only what differs from the row now: registry fields in one transaction with their entry, then
        the team's models. Returns `{field: [before, after]}` for what was saved.

        Protection against overwriting another admin's edit depends on the caller: the route must send only the
        fields this admin changed against the snapshot the form was rendered from. Here, values already equal to
        the row's are dropped, but a stale value that differs from it would be written."""
        row = self._row(team_id, channel_id)
        unknown = set(changes) - EDITABLE
        if unknown:
            raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}.")
        registry_changes = {k: v for k, v in changes.items() if not same(getattr(row, k), v)}
        wanted = await self._differing_models(row, family_defaults)
        if not registry_changes and not wanted:
            raise ChannelRefused(copy.NOTHING_CHANGED)
        await self._check_models(model_ids(registry_changes), wanted)
        saved: dict[str, list] = {}  # filled from the transaction's own before and after rows
        if registry_changes:
            try:
                self.registry.update(team_id, channel_id, **registry_changes,
                                     audit=audit_callback(self.audit, actor, audit_log.CHANGED, capture=saved))
            except ValueError as error:
                raise ChannelRefused(str(error)) from None
        if wanted:
            saved |= await self._save_models(row, wanted, actor, kept_registry=bool(registry_changes))
        return saved

    async def _differing_models(self, row: ChannelRow, family_defaults: Mapping[str, str]) -> dict[str, str]:
        wanted = {family: model for family, model in family_defaults.items() if model}
        if not wanted:
            return {}
        try:
            await self.provisioner.ensure(team_of(row), team=TeamKey(row.team_label))
            current = self._models.saved(row)
        except Exception as error:  # noqa: BLE001 - unknown means "try them all"; the save reports the failure
            log.warning("slack admin: reading %s's models failed (%s)", row.team_label, type(error).__name__)
            return wanted
        return {family: model for family, model in wanted.items() if current.get(family) != model}

    async def _save_models(self, row: ChannelRow, wanted: dict[str, str], actor: Session, *,
                           kept_registry: bool) -> dict[str, list]:
        try:
            await self.provisioner.ensure(team_of(row), team=TeamKey(row.team_label))
            saved = await self._models.save(row, wanted)
        except Exception as error:  # noqa: BLE001 - the registry change stays; the page says what didn't happen
            log.warning("slack admin: model save for %s failed (%s): %s", row.team_label,
                        type(error).__name__, redact(str(error)))
            reason = failure_reason(error, row)
            text = copy.MODELS_NOT_SAVED if kept_registry else copy.MODELS_ONLY_FAILED
            raise ChannelRefused(text.format(reason=reason)) from None
        if saved:  # outside the try: the models are saved, so a failed audit write must not say they weren't
            self.audit.record(actor, audit_log.MODELS_CHANGED, row, saved)
        return saved

    # ---- turning off and on ----

    async def turn_off(self, team_id: str, channel_id: str, reason: str, actor: Session) -> None:
        reason = reason.strip()
        if not reason:
            raise ChannelRefused(copy.REASON_REQUIRED)
        self._row(team_id, channel_id)
        if not self.registry.deactivate(team_id, channel_id, reason,
                                        audit=audit_callback(self.audit, actor, audit_log.TURNED_OFF)):
            raise ChannelRefused(copy.NOTHING_CHANGED)  # already off

    async def turn_on(self, team_id: str, channel_id: str, actor: Session) -> None:
        row = self._row(team_id, channel_id)
        if row.active:
            raise ChannelRefused(copy.NOTHING_CHANGED)
        if row.inactive_reason in SETUP_REASONS:
            raise ChannelRefused(copy.FINISH_FIRST)  # a row with no key or models yet is finished, not just switched on
        problem = await self.check(channel_id)
        if problem:
            raise ChannelRefused(problem)
        if not self.registry.activate(team_id, channel_id,
                                      audit=audit_callback(self.audit, actor, audit_log.TURNED_ON)):
            raise ChannelRefused(copy.NOTHING_CHANGED)  # a double submit: the other call turned it on


async def _limited(calls: Iterable) -> list:
    """Run the awaitables with at most five in flight, results in order."""
    gate = asyncio.Semaphore(LOOKUPS_AT_ONCE)

    async def run(call):
        async with gate:
            return await call

    return list(await asyncio.gather(*(run(call) for call in calls)))
