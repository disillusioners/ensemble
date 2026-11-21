"""P3 T5 — dependency getter-at-tick + dead escalation + boot wiring assertion."""

from __future__ import annotations

import asyncio
import time

import pytest

from daemon.services.engine.guardrails import (
    DEP_MISSING_DEAD_FACTOR,
    EXTEND_WEDGE_FACTOR,
    DepMissingTracker,
    DependencyRef,
    ExtendWedgeTracker,
    WiringReport,
    assert_dependencies_resolvable,
    escalate_dead_log_once,
    resolve_at_due,
)


def test_resolve_at_due_calls_getter_at_due_time():
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return "dep"

    ref = DependencyRef(label="bus", getter=getter)
    r1 = resolve_at_due([ref])
    r2 = resolve_at_due([ref])
    assert r1.resolved == {"bus": "dep"}
    assert r2.resolved == {"bus": "dep"}
    assert calls["n"] == 2  # once per due-evaluation


def test_resolve_at_due_none_is_missing():
    ref = DependencyRef(label="bus", getter=lambda: None)
    r = resolve_at_due([ref])
    assert r.missing == ["bus"]
    assert r.resolved == {}
    assert not r.ok


def test_dependency_becomes_available_after_start_no_restart():
    state = {"bus": None}

    def getter():
        return state["bus"]

    ref = DependencyRef(label="bus", getter=getter)
    r1 = resolve_at_due([ref])
    assert r1.missing == ["bus"]
    state["bus"] = "live"
    r2 = resolve_at_due([ref])
    assert r2.resolved == {"bus": "live"}


def test_assert_dependencies_resolvable_pure_and_names():
    ref = DependencyRef(label="dep-a", getter=lambda: None)
    import copy

    before = copy.deepcopy  # just a marker
    report = assert_dependencies_resolvable([("job-1", [ref])])
    assert isinstance(report, WiringReport)
    assert report.unresolved == {"job-1": ["dep-a"]}
    assert not report.ok
    # pure: calling again is deterministic
    report2 = assert_dependencies_resolvable([("job-1", [ref])])
    assert report2.unresolved == report.unresolved


def test_assert_dependencies_resolvable_ok_when_resolved():
    ref = DependencyRef(label="dep-a", getter=lambda: "ok")
    report = assert_dependencies_resolvable([("job-1", [ref])])
    assert report.ok
    assert report.unresolved == {}


def test_dep_missing_tracker_escalates_after_2x_interval():
    tr = DepMissingTracker()
    interval = 10.0
    t0 = 1000.0
    tr.record_missing("j", t0)
    assert not tr.should_escalate("j", interval, t0 + 5.0)
    assert not tr.should_escalate("j", interval, t0 + 19.0)
    assert tr.should_escalate("j", interval, t0 + 20.0)
    assert tr.is_escalated("j")
    # once escalated, stays
    assert not tr.should_escalate("j", interval, t0 + 100.0)


def test_dep_missing_tracker_resets_on_resolve():
    tr = DepMissingTracker()
    t0 = 1000.0
    tr.record_missing("j", t0)
    tr.record_resolved("j")
    assert not tr.is_escalated("j")
    assert not tr.should_escalate("j", 10.0, t0 + 100.0)


def test_escalate_dead_log_once_single():
    esc = set()
    assert escalate_dead_log_once("j", esc, level="error", message="boom") is True
    assert escalate_dead_log_once("j", esc, level="error", message="boom") is False
    assert "j" in esc


def test_extend_wedge_tracker_escalates_after_2x():
    tr = ExtendWedgeTracker()
    t0 = 1000.0
    tr.record_hold_start("j", t0, max_runtime_s=5.0)
    assert not tr.check_escalate("j", t0 + 9.0)
    assert tr.check_escalate("j", t0 + 10.0)
    # once escalated
    assert not tr.check_escalate("j", t0 + 100.0)


def test_extend_wedge_tracker_clears_on_release():
    tr = ExtendWedgeTracker()
    t0 = 1000.0
    tr.record_hold_start("j", t0, max_runtime_s=5.0)
    tr.clear_hold("j")
    assert not tr.check_escalate("j", t0 + 100.0)


def test_factors():
    assert DEP_MISSING_DEAD_FACTOR == 2
    assert EXTEND_WEDGE_FACTOR == 2


@pytest.mark.asyncio
async def test_engine_due_evaluation_stamps_dep_missing():
    """Engine integration (P3 T3.1): set_dependencies feeds the ENGINE's due
    evaluation — a due tick with a missing dependency stamps
    SKIPPED_DEP_MISSING (engine_runs row + skips counter) and never invokes
    the executor. Not a tracker-in-isolation pin."""
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel, Session, select
    from sqlmodel.pool import StaticPool

    from daemon.repositories.engine.models import EngineRun
    from daemon.repositories.engine.repository import EngineRepository
    from daemon.services.engine.engine import EngineService
    from daemon.services.engine.registry import EngineRegistry
    from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    repo = EngineRepository(engine)
    reg = EngineRegistry(repo)
    spec = JobSpec(
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
    await reg.aregister(spec)
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()
    svc.set_dependencies(
        "orphan-watcher",
        [DependencyRef(label="bus", getter=lambda: None)],
    )
    calls = {"n": 0}

    def executor():
        calls["n"] += 1

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop
    await loop._tick()

    with Session(repo.engine) as s:
        rows = list(
            s.exec(select(EngineRun).where(EngineRun.job_id == "orphan-watcher")).all()
        )
    stamped = [r for r in rows if r.outcome == "SKIPPED_DEP_MISSING"]
    assert len(stamped) == 1 and stamped[0].skip_reason == "DEP_MISSING"
    cache = svc._cache["orphan-watcher"]
    assert cache["skips_by_reason"]["DEP_MISSING"] == 1
    assert calls["n"] == 0  # skip path, not a run


@pytest.mark.asyncio
async def test_engine_dep_missing_escalates_to_dead(caplog):
    """F1 engine-level escalation pin: a missing dependency aged past
    DEP_MISSING_DEAD_FACTOR * interval escalates to dead=1, logs exactly ONE
    ERROR line, and persists one SKIPPED_DEP_MISSING row. Driven through the
    ENGINE dispatch path (not the tracker in isolation)."""
    import logging

    from sqlalchemy import create_engine
    from sqlmodel import SQLModel, Session, select
    from sqlmodel.pool import StaticPool

    from daemon.repositories.engine.models import EngineRun
    from daemon.repositories.engine.repository import EngineRepository
    from daemon.services.engine.engine import EngineService
    from daemon.services.engine.registry import EngineRegistry
    from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    repo = EngineRepository(engine)
    reg = EngineRegistry(repo)
    spec = JobSpec(
        id="orphan-watcher",
        meta=ServiceMeta(
            service_tag="orphan-watcher-sweep",
            category="reconciler",
            service_name="Orphan Watcher Sweep",
            module_info="daemon.services.orphan_watcher_sweep",
            description="x",
        ),
        trigger=IntervalTrigger(seconds=1.0),
        max_runtime_s=60.0,
        timeout_policy="cancel",
        concurrency_class="DB_OFFLOAD",
        priority=5,
    )
    await reg.aregister(spec)
    svc = EngineService(repo, reg, None, shadow_mode=False)
    await svc._hydrate()
    svc.set_dependencies(
        "orphan-watcher",
        [DependencyRef(label="bus", getter=lambda: None)],
    )
    calls = {"n": 0}

    def executor():
        calls["n"] += 1

    svc.set_executor("orphan-watcher", executor)
    loop = svc._execution_loop

    with caplog.at_level(logging.ERROR, logger="daemon.services.engine.guardrails"):
        # Tick 1: records first_missing_at.
        await loop._tick()
        await asyncio.sleep(5.0)  # age past DEP_MISSING_DEAD_FACTOR * effective interval
        # Tick 2: should escalate.
        await loop._tick()

    assert calls["n"] == 0  # skip path, never invokes the executor
    cache = svc._cache["orphan-watcher"]
    assert cache.get("persisted_dead") is True
    job = repo.get_job("orphan-watcher")
    assert getattr(job, "dead", 0) == 1
    with Session(repo.engine) as s:
        rows = list(
            s.exec(select(EngineRun).where(EngineRun.job_id == "orphan-watcher")).all()
        )
    stamped = [r for r in rows if r.outcome == "SKIPPED_DEP_MISSING"]
    assert len(stamped) >= 1
    err_lines = [
        r for r in caplog.records
        if r.levelno == logging.ERROR and "escalated to dead" in r.getMessage()
    ]
    assert len(err_lines) == 1, f"expected one ERROR escalation, got {len(err_lines)}"
