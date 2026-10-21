"""OwnerLock: the single-owner advisory lock with gain and lose hooks."""

import asyncio

import psycopg
import pytest

from openrouter_image_mcp.hosted.ownership import OWNER_LOCK, OwnerLock

EVERY = 0.05


class Calls:
    """Hooks that record their order; `fail` makes one raise."""

    def __init__(self):
        self.log: list[str] = []

    def hook(self, name: str, *, fail: bool = False):
        async def run():
            self.log.append(name)
            if fail:
                raise RuntimeError("secret detail")
        return run

    def count(self, name: str) -> int:
        return self.log.count(name)


async def eventually(check, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def _lock(target, calls: Calls, **kwargs) -> OwnerLock:
    return OwnerLock(target, every_s=EVERY, on_gain=[calls.hook("gain")],
                     on_lose=[calls.hook("lose")], **kwargs)


def _owner_pids(url: str) -> list[int]:
    with psycopg.connect(url, autocommit=True) as db:
        rows = db.execute("SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
                          "AND objid = %s", (OWNER_LOCK,)).fetchall()
    return [row[0] for row in rows]


async def test_sqlite_always_owner_runs_gain_once(tmp_path):
    calls = Calls()
    lock = _lock(tmp_path / "db.sqlite3", calls)
    await lock.start()
    await asyncio.sleep(EVERY * 3)
    assert lock.held and calls.log == ["gain"]
    await lock.stop()
    assert not lock.held and calls.log == ["gain", "lose"]


async def test_hooks_run_in_order_and_failures_are_isolated(tmp_path, caplog):
    calls = Calls()
    lock = OwnerLock(tmp_path / "db.sqlite3", every_s=EVERY, on_gain=[
        calls.hook("g1", fail=True), calls.hook("g2")], on_lose=[
        calls.hook("l1", fail=True), calls.hook("l2")])
    await lock.start()
    assert lock.held and calls.log == ["g1", "g2"]
    await lock.stop()
    assert calls.log == ["g1", "g2", "l1", "l2"]
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text


async def test_hooks_appended_after_construction_run(tmp_path):
    calls = Calls()
    lock = OwnerLock(tmp_path / "db.sqlite3", every_s=EVERY)
    lock.on_gain.append(calls.hook("late"))
    await lock.start()
    assert calls.log == ["late"]
    await lock.stop()


async def test_stop_is_idempotent_and_lose_runs_once(tmp_path):
    calls = Calls()
    lock = _lock(tmp_path / "db.sqlite3", calls)
    await lock.start()
    await lock.stop()
    await lock.stop()
    assert calls.log == ["gain", "lose"]


async def test_stop_without_start_does_nothing(tmp_path):
    calls = Calls()
    await _lock(tmp_path / "db.sqlite3", calls).stop()
    assert calls.log == []


@pytest.mark.pg
async def test_pg_second_lock_waits_then_gains(pg_target):
    a_calls, b_calls = Calls(), Calls()
    a, b = _lock(pg_target, a_calls), _lock(pg_target, b_calls)
    await a.start()
    await b.start()
    try:
        assert a.held and not b.held and b_calls.log == []
        await asyncio.sleep(EVERY * 3)
        assert not b.held
        await a.stop()
        await eventually(lambda: b.held)
        assert b_calls.log == ["gain"] and a_calls.log == ["gain", "lose"]
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.pg
async def test_pg_connection_loss_runs_on_lose_then_regains(pg_target):
    calls = Calls()
    lock = _lock(pg_target, calls)
    await lock.start()
    try:
        (pid,) = _owner_pids(pg_target)
        with psycopg.connect(pg_target, autocommit=True) as third:
            third.execute("SELECT pg_terminate_backend(%s)", (pid,))
        await eventually(lambda: calls.count("lose") == 1)
        await eventually(lambda: lock.held and calls.count("gain") == 2)
        assert calls.log == ["gain", "lose", "gain"] and len(_owner_pids(pg_target)) == 1
    finally:
        await lock.stop()


@pytest.mark.pg
async def test_pg_stop_releases_the_lock_and_connection(pg_target):
    calls = Calls()
    lock = _lock(pg_target, calls)
    await lock.start()
    assert len(_owner_pids(pg_target)) == 1
    await lock.stop()
    assert _owner_pids(pg_target) == [] and calls.log == ["gain", "lose"]


@pytest.mark.pg
async def test_pg_waiting_lock_closes_its_connection_on_stop(pg_target):
    a = _lock(pg_target, Calls())
    waiting = _lock(pg_target, Calls())
    await a.start()
    await waiting.start()
    await asyncio.sleep(EVERY * 3)
    with psycopg.connect(pg_target, autocommit=True) as db:
        before = db.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = "
                            "current_database()").fetchone()[0]
        await waiting.stop()
        after = db.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = "
                           "current_database()").fetchone()[0]
    await a.stop()
    assert after == before - 1


@pytest.mark.pg
async def test_pg_unreachable_database_never_escapes_or_leaks_the_dsn(caplog):
    url = "postgresql://nobody:hunter2@127.0.0.1:1/none?connect_timeout=1"
    lock = _lock(url, Calls())
    await lock.start()
    await asyncio.sleep(EVERY * 3)
    await lock.stop()
    assert not lock.held and "hunter2" not in caplog.text


@pytest.mark.pg
async def test_pg_unexpected_errors_keep_the_loop_running(pg_target):
    calls = Calls()
    lock = _lock(pg_target, calls)
    await lock.start()
    real, failures = lock._bounded, []

    async def flaky(fn, *args):
        if len(failures) < 2 and getattr(fn, "__name__", "") == "execute":
            failures.append(1)
            raise RuntimeError("secret detail")
        return await real(fn, *args)

    lock._bounded = flaky
    try:
        await eventually(lambda: calls.count("lose") == 2 and lock.held)
        assert calls.log == ["gain", "lose", "gain", "lose", "gain"]  # retaken in the same tick
        assert len(_owner_pids(pg_target)) == 1
    finally:
        await lock.stop()


@pytest.mark.pg
async def test_pg_stop_during_a_slow_check_does_not_leak(pg_target, monkeypatch):
    lock = _lock(pg_target, Calls())
    await lock.start()
    assert len(_owner_pids(pg_target)) == 1
    real = lock._conn.execute

    def slow(*args):
        import time
        time.sleep(0.3)
        return real(*args)

    monkeypatch.setattr(lock._conn, "execute", slow)
    await asyncio.sleep(EVERY * 2)  # a check is now in flight on the owner connection
    await lock.stop()
    await eventually(lambda: _owner_pids(pg_target) == [])


async def test_cancelled_lose_still_runs_every_hook(tmp_path):
    calls = Calls()
    gate = asyncio.Event()

    async def slow_first():
        calls.log.append("l1")
        await gate.wait()

    lock = OwnerLock(tmp_path / "db.sqlite3", every_s=EVERY, on_lose=[slow_first, calls.hook("l2")])
    await lock.start()
    stopper = asyncio.ensure_future(lock.stop())
    await asyncio.sleep(0.05)
    stopper.cancel()  # cuts stop() short, not the hooks
    gate.set()
    await asyncio.gather(stopper, return_exceptions=True)
    await eventually(lambda: calls.log == ["l1", "l2"])
