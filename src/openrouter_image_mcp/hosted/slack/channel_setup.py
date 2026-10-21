"""The pieces of the channel admin service that touch a team's key and models: finishing setup, audit entries.

`ChannelAdmin` (channel_admin.py) validates and writes the registry row; `ChannelSetup.finish` then creates the
team's key, saves its per-family models and turns the row on. Every step is safe to repeat, so a failed setup is
retried with the same call. Page text comes from `hosted/admin/copy.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from typing import TYPE_CHECKING

from ...errors import (
    AuthRequiredError,
    BadRequestError,
    NoGuardrailError,
    OpenRouterError,
)
from ...logs import redact
from ...preferences import ALIASES
from ..admin import audit as audit_log
from ..admin import copy
from ..admin.audit import AdminAudit
from ..admin.sessions import Session
from ..provisioning import KeyProvisioner, TeamKey
from .principal import acting_as, team_principal
from .registry import ChannelRegistry, ChannelRow

if TYPE_CHECKING:
    from .runner import SlackServices

log = logging.getLogger(__name__)

SETUP_REASONS = (copy.SETTING_UP, copy.SETUP_UNFINISHED)
# What the change log records of a row. The ids, creation time and welcome/warning marks are bookkeeping, and
# the workspace follows the cohort.
UNTRACKED = {"slack_team_id", "channel_id", "created_at", "workspace_id", "welcomed_at", "warned_month"}
TRACKED = tuple(f.name for f in fields(ChannelRow) if f.name not in UNTRACKED)


class ChannelRefused(Exception):
    """The operation did not happen; the message is one plain sentence the page can show."""


def same(a, b) -> bool:
    """Equal ignoring list versus tuple (forms send lists, rows hold tuples)."""
    def plain(value):
        return tuple(value) if isinstance(value, list | tuple) else value
    return plain(a) == plain(b)


def _listed(value):
    return list(value) if isinstance(value, tuple) else value


def diff(before: ChannelRow | None, after: ChannelRow) -> dict[str, list]:
    """`{field: [before, after]}` for the tracked fields that differ; `before` None means every field is new."""
    changes: dict[str, list] = {}
    for name in TRACKED:
        old = None if before is None else getattr(before, name)
        new = getattr(after, name)
        if before is None or not same(old, new):
            changes[name] = [_listed(old), _listed(new)]
    return changes


def audit_callback(audit: AdminAudit, actor: Session, action: str, *, capture: dict | None = None) -> Callable:
    """The registry's `audit` hook: one entry on the write's own connection, so both commit or neither does.

    `capture`, when given, receives the changes of the transaction's own before and after rows."""
    def write(db, before: ChannelRow | None, after: ChannelRow) -> None:
        changes = diff(before, after)
        audit_log.insert(db, at=audit.clock(), actor=actor, action=action, row=after, changes=changes)
        if capture is not None:
            capture.update(changes)
    return write


def model_ids(values: Mapping) -> set[str]:
    """The exact model ids among a row's model fields; aliases that name a brand need no catalog entry."""
    ids: set[str] = set(values.get("fusion_panel") or ())
    ids.update(values[name] for name in ("fusion_analyst", "subagent_model") if values.get(name))
    ids.update(model for model in (values.get("alias_map") or {}).values() if model.casefold() not in ALIASES)
    return ids


def failure_reason(error: Exception, row: ChannelRow) -> str:
    """A plain reason for a failed setup or model save; raw error text never reaches the page."""
    if isinstance(error, NoGuardrailError):
        return copy.NO_GUARDRAIL.format(cohort=row.cohort).rstrip(".")
    if isinstance(error, AuthRequiredError | BadRequestError):  # fixed provisioning and model-check sentences
        return redact(error.message).rstrip(".")
    if isinstance(error, OpenRouterError):
        return copy.OPENROUTER_DOWN
    return copy.UNEXPECTED


def team_of(row: ChannelRow):
    """The team's principal even while the row is off: setup creates the key before turning it on."""
    return team_principal(row.slack_team_id, row.channel_id, replace(row, active=True))


class TeamModels:
    """The team's per-family model defaults, read and saved through the cohort's `PreferenceService`."""

    def __init__(self, services: Mapping[str, SlackServices] | None):
        self.services = services

    def _preferences(self, row: ChannelRow):
        found = (self.services or {}).get(row.cohort)
        if found is None:
            raise LookupError(f"no advisor services for cohort {row.cohort}")
        return found.preferences

    def saved(self, row: ChannelRow) -> dict[str, str | None]:
        with acting_as(team_of(row)):
            return dict(self._preferences(row).get()["family_defaults"])

    async def save(self, row: ChannelRow, wanted: Mapping[str, str]) -> dict[str, list]:
        """Save the families whose model differs; returns `{"<family> model": [before, after]}` for those."""
        preferences = self._preferences(row)
        with acting_as(team_of(row)):
            current = preferences.get()
            before = current["family_defaults"]
            differing = {f: m for f, m in wanted.items() if m and before.get(f) != m}
            if differing:
                await preferences.update(current["settings_id"], current["revision"], family_defaults=differing)
        return {f"{family} model": [before.get(family), model] for family, model in differing.items()}


class ChannelSetup:
    def __init__(self, registry: ChannelRegistry, provisioner: KeyProvisioner, models: TeamModels,
                 audit: AdminAudit):
        self.registry, self.provisioner, self.models, self.audit = registry, provisioner, models, audit

    async def finish(self, team_id: str, channel_id: str, family_defaults: Mapping[str, str],
                     actor: Session) -> ChannelRow:
        """Create the team's key, save its models, turn the row on. Repeating it completes what is missing.

        Turning on is a guarded claim, so a double submit makes one `turned on` entry and the loser gets the row
        as the winner left it."""
        row = self.registry.get(team_id, channel_id)
        if row is None:
            raise ChannelRefused(copy.NOT_REGISTERED)
        if not row.active and row.inactive_reason not in SETUP_REASONS:
            raise ChannelRefused(copy.NOT_IN_SETUP)
        try:
            await self.provisioner.ensure(team_of(row), team=TeamKey(row.team_label))
            changes = await self.models.save(row, family_defaults)
        except Exception as error:  # noqa: BLE001 - whatever failed, the row stays off and Finish setup retries
            return self._unfinished(row, error)
        if changes:  # outside the try: a failed audit write after a good save is a server error, not a setup failure
            self.audit.record(actor, audit_log.MODELS_CHANGED, row, changes)
        try:
            self.registry.activate(team_id, channel_id,
                                   audit=audit_callback(self.audit, actor, audit_log.TURNED_ON))
        except Exception as error:  # noqa: BLE001
            return self._unfinished(row, error)
        return self.registry.get(team_id, channel_id) or row

    def _unfinished(self, row: ChannelRow, error: Exception) -> ChannelRow:
        """Report a failed attempt, unless a concurrent attempt finished the row meanwhile."""
        log.warning("slack admin: setup of %s did not finish (%s): %s", row.team_label,
                    type(error).__name__, redact(str(error)))
        current = self.registry.get(row.slack_team_id, row.channel_id)
        if current is not None and current.active:
            return current
        try:
            self.registry.set_inactive_reason(row.slack_team_id, row.channel_id, copy.SETUP_UNFINISHED)
        except Exception as write_error:  # noqa: BLE001 - the refusal matters more than a second failed write
            log.warning("slack admin: could not mark %s unfinished: %s", row.team_label, redact(str(write_error)))
        raise ChannelRefused(copy.SETUP_UNFINISHED_TEXT.format(reason=failure_reason(error, row))) from None
