"""Restart recovery looks again once a dead owner's live-looking rows have lapsed (final review I2)."""

import asyncio
import time

import pytest
from test_hosted_slack_wiring import World, gain

from openrouter_image_mcp.advisor_results import AdvisorResults
from openrouter_image_mcp.advisor_store import AdvisorStore, Scope
from openrouter_image_mcp.hosted.limits import Limits, RateLimitedError
from openrouter_image_mcp.hosted.slack.recovery import INTERRUPTED_EDIT
from openrouter_image_mcp.hosted.slack.registry import new_row
from openrouter_image_mcp.hosted.slack.wiring import RECOVERY_RECHECK_S


class World3(World):
    """A registered team at its three-answer cap, with three rows a crashed process left running."""

    def stuck_rows(self):
        row = new_row("T1", "C1", "team-a", "2027", fusion_panel=("a/one",), fusion_analyst="d/an",
                      subagent_model="e/sub", max_concurrent=3)
        self.app.dispatcher.deps.registry.add(row)
        self.scope = Scope("slack|T1:C1", row.workspace_id)
        self.results = AdvisorResults(AdvisorStore(self.target))
        ids = []
        for n in range(3):
            chat = self.results.store.create(self.scope, "g/model", "t")
            job = self.results.create(self.scope, chat, "p", "proc-dead", slack_user_id="U1",
                                      slack_channel_id="C1")
            self.set_rows([job["id"]], status="running", heartbeat_until=time.time() + 60,
                          slack_working_ts=f"1700000000.00001{n}")
            ids.append(job["id"])
        return ids

    def set_rows(self, ids, **fields):
        with self.results.connection() as db:
            for job_id in ids:
                db.execute(f"UPDATE advisor_jobs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                           (*fields.values(), job_id))

    def statuses(self, ids):
        with self.results.connection() as db:
            return [db.execute("SELECT status FROM advisor_jobs WHERE id=?", (i,)).fetchone()["status"] for i in ids]


@pytest.fixture
async def world(db_target):
    made = World3(db_target)
    yield made
    await made.app.close()


async def test_second_pass_closes_rows_whose_owner_died(world):
    ids = world.stuck_rows()
    limits = Limits(AdvisorStore(world.target))
    seen = []

    async def heartbeat_window_passes(delay):
        seen.append((delay, world.statuses(ids), list(world.slack.updates)))
        with pytest.raises(RateLimitedError):  # until then the team reads as "3 answers running"
            limits.admit(world.scope, max_concurrent=3, daily_usd=4.0)
        world.set_rows(ids, heartbeat_until=time.time() - 1)  # the dead process never beats again

    world.app.sleep = heartbeat_window_passes
    await gain(world.app)
    counts = await world.app.recovery_task
    # The first pass left them alone (they looked live) and blocked the team ...
    assert seen == [(RECOVERY_RECHECK_S, ["running"] * 3, [])]
    # ... the second pass closed them, edited their working messages and freed the team's slots.
    assert world.statuses(ids) == ["interrupted"] * 3 and counts["closed"] == 3
    assert sorted(u["ts"] for u in world.slack.updates) == [f"1700000000.00001{n}" for n in range(3)]
    assert {u["text"] for u in world.slack.updates} == {INTERRUPTED_EDIT}
    limits.admit(world.scope, max_concurrent=3, daily_usd=4.0)  # no longer "3 answers running"


async def test_no_second_pass_when_nothing_waited(world):
    slept = []

    async def record(delay):
        slept.append(delay)

    world.app.sleep = record
    await gain(world.app)
    assert (await world.app.recovery_task)["waiting"] == 0 and slept == []


async def test_lose_cancels_the_pending_second_pass(world):
    ids = world.stuck_rows()
    waiting = asyncio.Event()

    async def blocked(delay):
        waiting.set()
        await asyncio.Event().wait()

    world.app.sleep = blocked
    await gain(world.app)
    task = world.app.recovery_task
    await waiting.wait()
    await world.app.on_lose()
    assert task.cancelled() and world.statuses(ids) == ["running"] * 3
    with pytest.raises(RateLimitedError):
        Limits(AdvisorStore(world.target)).admit(world.scope, max_concurrent=3, daily_usd=4.0)
