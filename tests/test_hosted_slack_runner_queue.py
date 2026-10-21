"""Slack runner queueing: per-chat FIFO locks before team slots, stops that wake queued jobs, cleanup."""

import asyncio

import pytest
from slack_runner_rig import CHANNEL, Rig, answer_item, body, wait_until

from openrouter_image_mcp.hosted.slack import runner as runner_module
from openrouter_image_mcp.hosted.slack.runner_queue import (
    ACQUIRED,
    STOPPED,
    TIMED_OUT,
    Entry,
    Shared,
    wait_turn,
)


@pytest.fixture
def slack_rig(db_target, keys):
    return Rig(db_target, keys)


def _gated(rig, gate, *, block=(1,)):
    async def respond(request):
        number = len(rig.model.requests)
        if number in block:
            await gate.wait()
        return body(answer_item(f"answer {number}"))

    rig.model.respond = respond


def _labels(rig):
    return rig.runner.running(rig.scope.owner, CHANNEL)


# ---- runner_queue units ----

def test_shared_drops_a_key_with_its_last_user():
    shared = Shared(asyncio.Semaphore)
    first = shared.take("team", 1)
    assert shared.take("team", 5) is first and len(shared) == 1
    shared.drop("team")
    shared.drop("team")
    assert len(shared) == 0 and shared.take("team", 5) is not first


async def test_wait_turn_acquires_times_out_and_stops():
    lock = asyncio.Lock()
    loop = asyncio.get_running_loop()
    assert await wait_turn(lock.acquire, lock.release, Entry(None), loop.time() + 1) == ACQUIRED
    assert await wait_turn(lock.acquire, lock.release, Entry(None), loop.time() + 0.001) == TIMED_OUT
    entry = Entry(None)
    waiting = asyncio.create_task(wait_turn(lock.acquire, lock.release, entry, loop.time() + 5))
    await asyncio.sleep(0)
    entry.stop()
    assert await waiting == STOPPED
    lock.release()
    assert not lock.locked()


async def test_wait_turn_releases_a_win_when_already_stopped():
    lock = asyncio.Lock()
    entry = Entry(None)
    entry.stop()
    assert await wait_turn(lock.acquire, lock.release, entry, asyncio.get_running_loop().time() + 1) == STOPPED
    assert not lock.locked()


# ---- the runner ----

async def test_three_asks_on_one_chat_run_in_order(slack_rig):
    rig, gate = slack_rig, asyncio.Event()
    _gated(rig, gate)
    chat = rig.chat()
    tasks = [asyncio.create_task(rig.run(rig.job(chat, brief="one")))]
    await wait_until(lambda: len(rig.model.requests) == 1)
    for count, brief in ((2, "two"), (3, "three")):
        tasks.append(asyncio.create_task(rig.run(rig.job(chat, brief=brief))))
        await wait_until(lambda count=count: len(_labels(rig)) == count)
    gate.set()
    await asyncio.gather(*tasks)
    assert [m["content"] for m in rig.history(chat) if m["role"] == "user"] == ["one", "two", "three"]
    assert len(rig.runner._chats) == 0 and len(rig.runner._slots) == 0  # nothing left behind


async def test_waiting_follow_up_holds_no_team_slot(slack_rig):
    rig, gate = slack_rig, asyncio.Event()
    rig.set_row(max_concurrent=2)
    _gated(rig, gate)
    busy = rig.chat()
    first = asyncio.create_task(rig.run(rig.job(busy, brief="one")))
    await wait_until(lambda: len(rig.model.requests) == 1)
    follow_up = asyncio.create_task(rig.run(rig.job(busy, brief="two")))
    await wait_until(lambda: len(_labels(rig)) == 2)
    # The follow-up waits for its chat, so the team's second slot is free for another chat.
    await rig.run(rig.job(rig.chat(alias="gpt"), alias="gpt", label="ChatGPT", brief="other"))
    assert rig.texts()[-1].startswith("answer 2")
    gate.set()
    await asyncio.gather(first, follow_up)


async def test_chat_wait_timeout_says_the_thread_is_busy(slack_rig, monkeypatch):
    rig, gate = slack_rig, asyncio.Event()
    monkeypatch.setattr(runner_module, "QUEUE_WAIT_S", 0.01)
    _gated(rig, gate)
    chat = rig.chat()
    first = asyncio.create_task(rig.run(rig.job(chat)))
    await wait_until(lambda: len(rig.model.requests) == 1)
    await rig.run(rig.job(chat, brief="again"))
    assert rig.texts()[-1] == runner_module.CHAT_BUSY
    gate.set()
    await first


@pytest.mark.parametrize("same_chat", [True, False])
async def test_stop_wakes_a_queued_job_before_admission(slack_rig, same_chat):
    rig, gate = slack_rig, asyncio.Event()
    rig.set_row(max_concurrent=1)
    _gated(rig, gate)
    admitted = []
    real_admit = rig.limits.admit
    rig.limits.admit = lambda *args, **kwargs: admitted.append(1) or real_admit(*args, **kwargs)
    first = asyncio.create_task(rig.run(rig.job(rig.chat())))
    await wait_until(lambda: len(rig.model.requests) == 1)
    chat = rig.chat() if not same_chat else rig.store.get(rig.scope, rig.job_rows()[0]["chat_id"])
    queued = asyncio.create_task(rig.run(rig.job(chat, thread_ts="9.9", label="Later")))
    await wait_until(lambda: "Later" in _labels(rig))
    assert rig.runner.stop(rig.scope.owner, CHANNEL, "9.9") == 1
    await queued
    assert rig.slack.posts[-1]["text"] == "Stopped." and rig.slack.posts[-1]["username"] == "Sidecar · Later"
    assert len(admitted) == 1 and len(rig.job_rows()) == 1
    gate.set()
    await first


async def test_team_slots_follow_a_changed_row_once_idle(slack_rig):
    rig, gate = slack_rig, asyncio.Event()
    rig.set_row(max_concurrent=1)
    await rig.run(rig.job(rig.chat()))
    rig.set_row(max_concurrent=2)
    _gated(rig, gate, block=(2, 3))
    jobs = [rig.job(rig.chat(alias=alias), alias=alias) for alias in ("gpt", "grok")]
    tasks = [asyncio.create_task(rig.run(job)) for job in jobs]
    await wait_until(lambda: len(rig.model.requests) == 3)  # both run at once under the new size
    gate.set()
    await asyncio.gather(*tasks)
