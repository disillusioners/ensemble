"""Engine single-flight + timeout + off-loop discipline pins (Engine Arc P2).

File name per ``test-strategy.md`` §1.1 T4
(``tests/unit/services/test_engine_single_flight.py``).

Covers:
* AC2 — two concurrent ticks → one RUNNING + one SKIPPED_OVERLAP.
* AC3 — cancel policy yields TIMED_OUT; extend yields TIMEOUT_PENDING_EXTEND
  and keeps lock held.
* AC6 — off-loop thread discipline: DB_OFFLOAD on non-loop thread,
  DEDICATED_EXECUTOR on pooled thread, MAIN_LOOP on loop thread.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from daemon.repositories.engine.models import EngineRun
from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine import dispatch as D
from daemon.services.engine.engine import EngineService
from daemon.services.engine.locks import JobLockTable
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta
from daemon.services.engine.state import parse_iso


def _engine():
    from sqlmodel.pool import StaticPool

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _spec(**overrides):
    base = dict(
        id="orphan-watcher",
        meta=ServiceMeta(
            service_tag="orphan-watcher-sweep",
            category="reconciler",
            service_name="Orphan Watcher Sweep",
            module_info="daemon.services.orphan_watcher_sweep",
            description="x",
        ),
        trigger=IntervalTrigger(seconds=90),
        max_runtime_s=60.0,
        timeout_policy="cancel",
        concurrency_class="DB_OFFLOAD",
        priority=5,
    )
    base.update(overrides)
    return JobSpec(**base)


def _count_runs(repo, job_id):
    with Session(repo.engine) as s:
        return list(s.exec(select(EngineRun).where(EngineRun.job_id == job_id)).all())


@pytest.mark.asyncio
async def test_single_flight_overlap_returns_false_without_awaiting():
    table = JobLockTable()
    assert await table.try_acquire("j") is True
    # held -> False immediately
    assert await table.try_acquire("j") is False
    table.release("j")
    assert await table.try_acquire("j") is True
    table.release("j")


@pytest.mark.asyncio
async def test_lock_released_in_finally_on_exception():
    table = JobLockTable()
    await table.try_acquire("j")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        pass
    finally:
        table.release("j")
    assert not table.locked("j")
    assert await table.try_acquire("j") is True
    table.release("j")


@pytest.mark.asyncio
async def test_two_concurrent_ticks_produce_one_overlap():
    """AC2 (real concurrency): two ticks racing on one job produce exactly
    one execution and one SKIPPED_OVERLAP run row.

    Deterministic by event-controlled interleaving: the first tick's executor
    blocks on a threading.Event so the lock is provably held before the
    second tick is started — no sleep-racing.
    """
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()

    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def executor():
        calls["n"] += 1
        entered.set()
        assert release.wait(timeout=10), "executor was never released"
        return "done"

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop

    first = asyncio.create_task(loop._tick())
    # Wait OFF-loop for the executor to enter: proves the lock is held before
    # the second tick starts, without racing sleeps on the event loop.
    assert await asyncio.to_thread(entered.wait, 10)
    assert loop.locks.locked("orphan-watcher")

    second = asyncio.create_task(loop._tick())
    await asyncio.wait_for(second, timeout=10)

    release.set()
    await asyncio.wait_for(first, timeout=10)

    assert calls["n"] == 1, "single-flight: executor must run exactly once"
    outcomes = [r.outcome for r in _count_runs(repo, "orphan-watcher")]
    assert outcomes.count("SKIPPED_OVERLAP") == 1
    assert outcomes.count("SUCCEEDED") == 1
    assert svc._cache["orphan-watcher"]["skips_by_reason"].get("OVERLAP") == 1


@pytest.mark.asyncio
async def test_timeout_cancel_policy():
    async def slow():
        await asyncio.sleep(10)
        return "done"

    outcome, exc = await D.run_with_timeout(lambda: slow(), 0.05, "cancel")
    assert outcome == "TIMED_OUT"
    assert exc is not None


@pytest.mark.asyncio
async def test_timeout_extend_policy_records_pending_and_awaits_completion():
    completed = {"v": False}

    async def slow():
        await asyncio.sleep(0.2)
        completed["v"] = True
        return "done"

    ticks = {"n": 0}

    def on_tick():
        ticks["n"] += 1

    outcome, exc = await D.run_with_timeout(lambda: slow(), 0.05, "extend", on_extend_tick=on_tick)
    assert outcome == "TIMEOUT_PENDING_EXTEND"
    assert completed["v"] is True
    assert ticks["n"] == 1


async def _start_extended_dispatch(repo, reg):
    """Drive _dispatch_one into the extend-fire state; return the live harness.

    The executor blocks on a threading.Event so the lock is provably held with
    the TPE flag set when this returns (event-controlled, no sleep-racing).
    """
    await reg.aregister(_spec(timeout_policy="extend", max_runtime_s=0.05,
                              concurrency_class="DB_OFFLOAD"))
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()
    release = threading.Event()

    def executor():
        assert release.wait(timeout=10), "executor was never released"
        return "done"

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop
    fire_seen = asyncio.Event()
    original_fire = loop._stamp_extend_fire

    async def spy_fire(*a, **kw):
        await original_fire(*a, **kw)
        fire_seen.set()

    loop._stamp_extend_fire = spy_fire
    task = asyncio.create_task(
        loop._dispatch_one("orphan-watcher", datetime.now(timezone.utc))
    )
    await asyncio.wait_for(fire_seen.wait(), timeout=10)
    return svc, loop, release, task


@pytest.mark.asyncio
async def test_extend_then_eventual_success_honest_state():
    """MAJOR pin (a): extend fire + eventual success -> truthful terminal,
    one TPE run row, TPE counter == 1, lock released, next-due from terminal.
    """
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    svc, loop, release, task = await _start_extended_dispatch(repo, reg)

    # fire-time observables (not the terminal outcome)
    assert loop.locks.is_extended("orphan-watcher")
    assert svc._cache["orphan-watcher"]["last_skip_reason"] == "TIMEOUT_PENDING_EXTEND"

    release.set()
    await asyncio.wait_for(task, timeout=10)

    job = repo.get_job("orphan-watcher")
    assert job.last_outcome == "SUCCEEDED"
    assert job.last_skip_reason is None
    rows = _count_runs(repo, "orphan-watcher")
    assert sum(1 for r in rows if r.outcome == "TIMEOUT_PENDING_EXTEND") == 1
    assert sum(1 for r in rows if r.outcome == "SUCCEEDED") == 1
    assert svc._cache["orphan-watcher"]["skips_by_reason"]["TIMEOUT_PENDING_EXTEND"] == 1
    assert not loop.locks.locked("orphan-watcher")
    assert not loop.locks.is_extended("orphan-watcher")
    assert svc._cache["orphan-watcher"]["consecutive_skips"] == 0
    snap = svc.metrics.snapshot()["orphan-watcher"]
    # next-due is computed from the terminal stamp's last_run_at (the cache
    # copy metrics reads), NOT the fire-time stamp. The DB copy is the same
    # terminal instant within the now_iso()/finished_dt microsecond skew.
    terminal_cache_run = parse_iso(svc._cache["orphan-watcher"]["last_run_at"])
    assert parse_iso(snap["next_due"]) == terminal_cache_run + timedelta(seconds=90.0)
    assert abs((terminal_cache_run - parse_iso(job.last_run_at)).total_seconds()) < 1.0


@pytest.mark.asyncio
async def test_extend_held_across_ticks_stamps_tpe_not_overlap():
    """MAJOR pin (b): while the extended dispatch holds the lock, further
    ticks stamp the SPECIFIC TPE member (never generic OVERLAP).
    """
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    svc, loop, release, task = await _start_extended_dispatch(repo, reg)

    for _ in range(2):
        await asyncio.wait_for(loop._tick(), timeout=10)

    rows = _count_runs(repo, "orphan-watcher")
    held = [r for r in rows if (r.outcome or "").startswith("SKIPPED_")]
    assert len(held) == 2
    assert all(r.outcome == "SKIPPED_TIMEOUT_PENDING_EXTEND" for r in held)
    assert all(r.skip_reason == "TIMEOUT_PENDING_EXTEND" for r in held)
    assert not any(r.outcome == "SKIPPED_OVERLAP" for r in rows)
    assert svc._cache["orphan-watcher"]["skips_by_reason"]["TIMEOUT_PENDING_EXTEND"] == 3

    release.set()
    await asyncio.wait_for(task, timeout=10)
    assert repo.get_job("orphan-watcher").last_outcome == "SUCCEEDED"
    assert not loop.locks.locked("orphan-watcher")


@pytest.mark.asyncio
async def test_extend_final_state_has_no_self_contradiction():
    """MAJOR pin (c): last_outcome / last_skip_reason / skips_by_reason /
    engine_runs outcomes never contradict each other about the same event.
    """
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    svc, loop, release, task = await _start_extended_dispatch(repo, reg)
    await asyncio.wait_for(loop._tick(), timeout=10)  # one held-tick TPE stamp
    release.set()
    await asyncio.wait_for(task, timeout=10)

    job = repo.get_job("orphan-watcher")
    cache = svc._cache["orphan-watcher"]
    rows = _count_runs(repo, "orphan-watcher")
    outcomes = [r.outcome for r in rows]

    assert job.last_outcome == "SUCCEEDED"
    assert job.last_skip_reason is None
    assert cache["last_outcome"] == "SUCCEEDED"
    assert cache["last_skip_reason"] is None
    tpe_rows = [r for r in rows if "TIMEOUT_PENDING_EXTEND" in (r.outcome or "")]
    assert len(tpe_rows) == 2  # fire + one held tick
    assert all(r.skip_reason == "TIMEOUT_PENDING_EXTEND" for r in tpe_rows)
    assert cache["skips_by_reason"]["TIMEOUT_PENDING_EXTEND"] == len(tpe_rows)
    assert "SKIPPED_OVERLAP" not in outcomes
    assert not (job.last_outcome == "SUCCEEDED" and job.last_skip_reason)


@pytest.mark.asyncio
async def test_off_loop_thread_discipline_db_offload():
    loop_thread = threading.get_ident()
    observed = {"ident": None}

    def fn():
        observed["ident"] = threading.get_ident()
        return "ok"

    res = await D.run_db_offload(fn)
    assert res == "ok"
    assert observed["ident"] is not None
    assert observed["ident"] != loop_thread


@pytest.mark.asyncio
async def test_db_offload_refuses_coroutine_function():
    """W-4: DB_OFFLOAD exists to keep blocking work off the loop; handing it
    a coroutine function is a registration-class discipline violation and is
    REFUSED (TypeError) rather than silently awaited inline."""
    async def coro_fn():
        return "ok"

    with pytest.raises(TypeError, match="DB_OFFLOAD refuses coroutine"):
        await D.run_db_offload(coro_fn)


@pytest.mark.asyncio
async def test_off_loop_thread_discipline_dedicated_executor():
    loop_thread = threading.get_ident()
    observed = {"name": None, "ident": None}
    ex = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="engine-dedicated"
    )
    try:

        def fn():
            observed["name"] = threading.current_thread().name
            observed["ident"] = threading.get_ident()
            return "ok"

        res = await D.run_dedicated_executor(ex, fn)
        assert res == "ok"
        assert observed["name"].startswith("engine-dedicated")
        assert observed["ident"] != loop_thread
    finally:
        ex.shutdown(wait=False)


@pytest.mark.asyncio
async def test_main_loop_runs_on_loop_thread():
    loop_thread = threading.get_ident()
    observed = {"ident": None}

    def fn():
        observed["ident"] = threading.get_ident()
        return "ok"

    res = await D.run_main_loop(fn)
    assert res == "ok"
    assert observed["ident"] == loop_thread


@pytest.mark.asyncio
async def test_engine_start_stop_idempotent():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc.start()
    await svc.start()  # idempotent
    assert svc._task is not None
    await svc.stop()
    assert svc._task is None


@pytest.mark.asyncio
async def test_engine_task_name():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc.start()
    assert svc._task.get_name() == "engine-tick"
    await svc.stop()


@pytest.mark.asyncio
async def test_engine_stop_bounded():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc.start()
    await svc.stop(timeout=5.0)
    assert svc._task is None


@pytest.mark.asyncio
async def test_extend_then_raise_records_failed_backoff():
    """C-1 pin: extend fire + late RuntimeError -> FAILED_BACKOFF with
    error_class=RuntimeError; one TPE fire row; last_outcome FAILED_BACKOFF."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec(timeout_policy="extend", max_runtime_s=0.05,
                              concurrency_class="DB_OFFLOAD"))
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()
    release = threading.Event()

    def executor():
        assert release.wait(timeout=10), "executor was never released"
        raise RuntimeError("late boom")

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop
    fire_seen = asyncio.Event()
    original_fire = loop._stamp_extend_fire

    async def spy_fire(*a, **kw):
        await original_fire(*a, **kw)
        fire_seen.set()

    loop._stamp_extend_fire = spy_fire
    task = asyncio.create_task(
        loop._dispatch_one("orphan-watcher", datetime.now(timezone.utc))
    )
    await asyncio.wait_for(fire_seen.wait(), timeout=10)

    release.set()
    await asyncio.wait_for(task, timeout=10)

    job = repo.get_job("orphan-watcher")
    assert job.last_outcome == "FAILED_BACKOFF"
    rows = _count_runs(repo, "orphan-watcher")
    tpe_rows = [r for r in rows if r.outcome == "TIMEOUT_PENDING_EXTEND"]
    failed_rows = [r for r in rows if r.outcome == "FAILED_BACKOFF"]
    # Strict row-SET pin (🟢b): exact sequence [TPE fire row, FAILED_BACKOFF].
    assert [r.outcome for r in rows] == ["TIMEOUT_PENDING_EXTEND", "FAILED_BACKOFF"], [
        r.outcome for r in rows
    ]
    assert len(tpe_rows) == 1
    assert len(failed_rows) == 1
    assert failed_rows[-1].error_class == "RuntimeError"
