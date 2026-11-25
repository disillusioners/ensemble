"""Engine due-ness / skip / backoff / shadow / metrics pins (Engine Arc P2).

File name per ``test-strategy.md`` §1.1 T3
(``tests/unit/services/test_engine_due_skip_backoff.py``).

Covers:
* AC5 — shadow: zero execute_fn invocations across intervals;
  skips_by_reason["SHADOW"] == due evaluations. Mutation: guard removed ⇒ RED.
* AC3/AC7 — due-ness boundaries + hydration.
* metrics snapshot key set (D2.14) + dead computed (D2.15/CS-9).
* project_merged (T2.9) purity (F-2).
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine import dispatch as D
from daemon.services.engine.engine import EngineService
from daemon.services.engine.metrics import project_merged
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta
from daemon.services.engine.state import now_iso, parse_iso


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
        trigger=IntervalTrigger(seconds=10),
        max_runtime_s=60.0,
        timeout_policy="cancel",
        concurrency_class="DB_OFFLOAD",
        priority=5,
    )
    base.update(overrides)
    return JobSpec(**base)


@pytest.mark.asyncio
async def test_shadow_zero_execution():
    """AC5: under shadow_mode, execute_fn is never invoked."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    spec = _spec()
    await reg.aregister(spec)
    svc = EngineService(repo, reg, None, shadow_mode=True)
    calls = {"n": 0}

    def exec_fn():
        calls["n"] += 1
        return "ran"

    svc.set_executor("orphan-watcher", exec_fn)
    await svc.start()
    # run a few ticks manually
    loop = svc._execution_loop
    for _ in range(3):
        await loop._tick()
        await asyncio.sleep(0)
    await svc.stop()
    assert calls["n"] == 0
    snap = svc.metrics.snapshot()
    assert snap["orphan-watcher"]["skips_by_reason"].get("SHADOW", 0) >= 1


@pytest.mark.asyncio
async def test_due_boundary():
    """AC7: due ⇔ now-last_run_at >= interval."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    spec = _spec()
    await reg.aregister(spec)
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    cache = svc._cache["orphan-watcher"]
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    # last_run_at = now - interval/2 -> not due
    half = (now - __import__("datetime").timedelta(seconds=5)).isoformat()
    cache["last_run_at"] = half
    assert svc._execution_loop._is_due("orphan-watcher", cache, now) is False
    # now - 2*interval -> due
    far = (now - __import__("datetime").timedelta(seconds=25)).isoformat()
    cache["last_run_at"] = far
    assert svc._execution_loop._is_due("orphan-watcher", cache, now) is True


@pytest.mark.asyncio
async def test_metrics_snapshot_key_set():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    snap = svc.metrics.snapshot()
    assert "orphan-watcher" in snap
    expected = {"next_due", "last_duration_seconds", "last_outcome", "skips_by_reason",
                "consecutive_skips", "dead", "running", "would_fire"}
    assert set(snap["orphan-watcher"].keys()) == expected


@pytest.mark.asyncio
async def test_dead_observer_carveout():
    """CS-9/L-8: observer dead ⇔ zero SHADOW rows in 2x interval (windowed)."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    # no shadow evidence yet -> dead True (observer)
    snap = svc.metrics.snapshot()
    assert snap["orphan-watcher"]["dead"] is True
    # a FRESH shadow timestamp -> dead False
    svc._cache["orphan-watcher"]["shadow_evidence_ts"] = [now_iso()]
    snap = svc.metrics.snapshot()
    assert snap["orphan-watcher"]["dead"] is False


@pytest.mark.asyncio
async def test_dead_observer_stale_evidence_outside_window():
    """MINOR-1 mutation pin: shadow evidence older than 2x interval is STALE
    and must NOT keep the observer alive. Removing the window check REDs.
    """
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())  # interval 10s -> window 20s
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()

    stale = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
             - __import__("datetime").timedelta(seconds=25)).isoformat()
    # old count-only logic would read this as "has shadow" -> dead False;
    # windowed logic sees zero fresh evidence -> dead True.
    svc._cache["orphan-watcher"]["skips_by_reason"]["SHADOW"] = 1
    svc._cache["orphan-watcher"]["shadow_evidence_ts"] = [stale]
    assert svc.metrics.snapshot()["orphan-watcher"]["dead"] is True

    # fresh evidence inside the window -> alive
    svc._cache["orphan-watcher"]["shadow_evidence_ts"].append(now_iso())
    assert svc.metrics.snapshot()["orphan-watcher"]["dead"] is False


@pytest.mark.asyncio
async def test_shadow_stamp_records_timestamped_evidence():
    """The shadow skip path appends a timestamp to the RAM shadow evidence."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    cache = svc._cache["orphan-watcher"]
    assert cache["shadow_evidence_ts"] == []
    await svc._execution_loop._tick()
    assert len(cache["shadow_evidence_ts"]) == 1
    assert parse_iso(cache["shadow_evidence_ts"][0]) is not None


@pytest.mark.asyncio
async def test_project_merged_purity():
    """T2.9: project_merged is pure and does not touch job_metrics shape."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()

    class FakeMaint:
        def job_metrics(self):
            return {"orphan-watcher": {"last_success_ts": "2026-01-01T00:00:00+00:00", "consecutive_skips": 3}}

    merged = project_merged(FakeMaint(), svc)
    assert "orphan-watcher" in merged
    assert merged["orphan-watcher"]["last_success_ts"] == "2026-01-01T00:00:00+00:00"
    # engine snapshot fields present
    assert "would_fire" in merged["orphan-watcher"] or "next_due" in merged["orphan-watcher"]


@pytest.mark.asyncio
async def test_hydration_restart_clock():
    """AC7: persisted last_run_at survives hydration."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    from daemon.services.engine.state import TerminalStamp, stamp_terminal

    svc0 = EngineService(repo, reg, None, shadow_mode=True)
    await svc0._hydrate()
    # stamp a run with far-past last_run_at? just stamp now
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=now_iso(),
        finished_at=now_iso(),
        outcome="SUCCEEDED",
        consecutive_skips=0,
    )
    await __import__("asyncio").to_thread(stamp_terminal, repo, stamp)
    persisted = repo.get_job("orphan-watcher").last_run_at

    # restart
    svc1 = EngineService(repo, reg, None, shadow_mode=True)
    await svc1._hydrate()
    assert svc1._cache["orphan-watcher"]["last_run_at"] == persisted


@pytest.mark.asyncio
async def test_ingest_event_sets_pending_latch():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    spec = _spec(trigger=__import__("daemon.services.engine.spec", fromlist=["EventTrigger"]).EventTrigger(kind="foo"))
    await reg.aregister(spec)
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    svc.ingest_event("foo", {"x": 1})
    assert svc._cache["orphan-watcher"]["pending_event"] is True


@pytest.mark.asyncio
async def test_loop_does_not_silently_return_on_unexpected_error():
    """F-7 (re-baselined for P3 D3.9 supervisor): unexpected loop error is
    supervised — the loop re-enters after a bounded backoff, never returns,
    and the restart counter increments (P4 watchdog input)."""
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    svc = EngineService(repo, reg, None, shadow_mode=True)
    # fast resolution/backoff
    if svc.config is None:
        from types import SimpleNamespace
        svc.config = SimpleNamespace(tick_resolution_seconds=0.05)
    else:
        svc.config.tick_resolution_seconds = 0.05
    await svc._hydrate()

    async def boom():
        raise ValueError("unexpected")

    svc._execution_loop._tick = boom
    svc._stopping = False
    task = asyncio.create_task(svc._execution_loop.run())
    # give the supervised loop time to hit the error + backoff + re-enter
    await asyncio.sleep(0.3)
    assert getattr(svc, "_supervisor_restarts", 0) >= 1
    assert not task.done()
    svc._stopping = True
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2)
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_consecutive_skips_survives_hydration():
    eng = _engine()
    repo = EngineRepository(eng)
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    from daemon.services.engine.state import TerminalStamp, stamp_terminal

    # stamp a skip with consecutive_skips=3
    stamp = TerminalStamp(
        job_id="orphan-watcher",
        service_tag="orphan-watcher-sweep",
        started_at=now_iso(),
        finished_at=now_iso(),
        outcome="SKIPPED_IDLE_GATED",
        skip_reason="IDLE_GATED",
        consecutive_skips=3,
    )
    await __import__("asyncio").to_thread(stamp_terminal, repo, stamp)
    svc = EngineService(repo, reg, None, shadow_mode=True)
    await svc._hydrate()
    assert svc._cache["orphan-watcher"]["consecutive_skips"] == 3
