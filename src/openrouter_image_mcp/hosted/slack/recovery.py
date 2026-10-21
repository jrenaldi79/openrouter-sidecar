"""Restart recovery: a replica that gains the owner lock finishes what the dead owner left behind.

Finished answers that were never posted are posted; jobs still active after their heartbeat lapsed are
closed with a note in Slack, and those whose heartbeat still looks live are counted as `waiting`, so the
caller can look again once it would have lapsed (`SlackApp` does, once). Nothing is retried. Every row is handled under its own team (the sweep is
cross-team), and a row that is not a registered Slack team's is skipped and logged. `recover` never raises.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from ...advisor_results import AdvisorResults
from ...advisor_store import Scope
from ..principal import SLACK_ISSUER
from .api import SlackApi
from .asks import COMPARE_ALIAS, COMPARE_LABEL
from .jobs import SlackJobs
from .principal import acting_as, team_principal
from .registry import ChannelRegistry, ChannelRow
from .replies import footer, post_answer
from .threads import ThreadMap

log = logging.getLogger(__name__)

RESTART_ERROR = "Interrupted by a server restart"
INTERRUPTED_EDIT = "Interrupted by a server restart. Ask again."
MAX_AGE_S = 24 * 3600.0  # an older answer would arrive out of the blue: close it quietly
ACTIVE = ("queued", "running")


class Skipped(Exception):
    """The row is not a registered, active Slack team's; it is left alone."""


def _identify(row: dict, registry: ChannelRegistry):
    """(scope, principal, registry row) for a row owned by `slack|<team>:<channel>` that matches its registry row."""
    prefix = SLACK_ISSUER + "|"
    owner = row["owner"]
    team, _, channel = owner[len(prefix):].partition(":") if owner.startswith(prefix) else ("", "", "")
    if not (team and channel) or channel != row["slack_channel_id"]:
        raise Skipped("the owner is not the Slack team of its channel")
    channel_row = registry.get(team, channel)
    if channel_row is None:
        raise Skipped("the channel is not registered")
    try:
        principal = team_principal(team, channel, channel_row)
    except ValueError as error:
        raise Skipped(str(error)) from None
    if (principal.key, principal.workspace_id) != (owner, row["workspace"]):
        raise Skipped("the workspace does not match the channel")
    return Scope(owner, row["workspace"]), principal, channel_row


def _label(channel_row: ChannelRow, alias: str, model: str) -> str:
    """The label a live answer wears: Fusion for a compare, else the channel's brand for the alias.

    The live runner falls back to the catalog name; recovery has no catalog, so it uses the model id.
    """
    if alias == COMPARE_ALIAS:
        return COMPARE_LABEL
    brand = channel_row.alias_map.get(alias)
    return brand if isinstance(brand, str) and brand and "/" not in brand else model


def _older_than(created_at: str, now: float) -> bool:
    try:
        return now - datetime.fromisoformat(created_at).timestamp() > MAX_AGE_S
    except ValueError:
        return False


class _Recovery:
    def __init__(self, jobs, results_for, api, registry, threads, process_id, clock):
        self.jobs, self.results_for, self.api = jobs, results_for, api
        self.registry, self.threads, self.process_id, self.clock = registry, threads, process_id, clock
        self.counts = {"posted": 0, "closed": 0, "waiting": 0}

    async def row(self, row: dict) -> None:
        try:
            scope, principal, channel_row = _identify(row, self.registry)
            with acting_as(principal):
                if row["status"] in ACTIVE:
                    await self._close(row, scope)
                else:
                    await self._post(row, scope, channel_row)
        except Skipped as why:
            log.warning("slack recovery skipped a job: %s", why)
        except Exception as error:  # noqa: BLE001 - one bad row must not stop the sweep
            log.warning("slack recovery failed for a job: %s", type(error).__name__)

    async def _close(self, row: dict, scope: Scope) -> None:
        if row["heartbeat_until"] > self.clock():
            self.counts["waiting"] += 1  # a live owner may still run it (takeover window), or a dead one
            return
        # Acts only when the row's own process still holds it: that is the orphan's process id.
        self.results_for(row["workspace"]).interrupt(scope, row["id"], row["process_id"], error=RESTART_ERROR)
        self.counts["closed"] += 1
        if row["slack_working_ts"]:
            try:
                await self.api.update(row["slack_channel_id"], row["slack_working_ts"], INTERRUPTED_EDIT)
            except Exception as error:  # noqa: BLE001 - the row is closed either way
                log.warning("slack recovery could not edit the working message: %s", type(error).__name__)

    async def _post(self, row: dict, scope: Scope, channel_row: ChannelRow) -> None:
        if _older_than(row["created_at"], self.clock()):
            self.jobs.mark_posted(scope, row["id"])
            return
        found = self.threads.thread_chat(scope, row["chat_id"])
        if found is None:
            log.warning("slack recovery found no thread for a finished answer; marking it posted")
            self.jobs.mark_posted(scope, row["id"])
            return
        thread_ts, alias = found
        label = _label(channel_row, alias, row["model"])
        await post_answer(self.api, row["slack_channel_id"], thread_ts, label, row["answer"],
                          footer(f"{label} · {row['model']}", row["cost_usd"]))  # a Slack failure leaves the row
        self.jobs.mark_posted(scope, row["id"])
        self.counts["posted"] += 1
        try:
            self.threads.answered(scope, row["chat_id"])
        except Exception as error:  # noqa: BLE001 - the answer is out; bookkeeping must not undo that
            log.warning("slack recovery could not mark the thread answered: %s", type(error).__name__)


async def recover(jobs: SlackJobs, results_for: Callable[[str], AdvisorResults], api: SlackApi,
                  registry: ChannelRegistry, threads: ThreadMap, *, process_id: str,
                  clock: Callable[[], float] = time.time) -> dict:
    """Finish what other processes left behind; returns `{"posted": n, "closed": m, "waiting": w}`.

    `results_for(workspace_id)` gives the advisor results of that cohort. An active row counts as
    orphaned only once its `heartbeat_until` has lapsed by `clock`; until then it is `waiting`.
    """
    work = _Recovery(jobs, results_for, api, registry, threads, process_id, clock)
    try:
        rows = jobs.orphans(process_id)
    except Exception as error:  # noqa: BLE001 - recovery runs in a lock hook and must never raise
        log.warning("slack recovery could not list orphans: %s", type(error).__name__)
        return work.counts
    for row in rows:
        await work.row(row)
    return work.counts
