"""Engine Arc Phase-2 PILOT — OrphanWatcher engine-vs-legacy parity harness.

Emperor GO 2026-11-22. Charter:
``.agents/shared/planning/engine-arc-phase1/phase2-pilot-recommendation.md``.

WHAT THIS PINS
--------------
1. Dual-path IDENTITY: one flag (``SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE``)
   shared by the legacy construct site and the engine spec's kill_switch_key;
   flag unset ⇒ legacy owns (shipped dormant default).
2. PARITY on identical fixture shapes: what is swept, what is skipped (grace
   window, live parent, non-PENDING), and the explicit state-stamp mapping
   (legacy tick/cancel/error counters ↔ engine engine_runs/outcome stamps).
3. DORMANT DEFAULT for EXECUTABLES: executor withheld at flag OFF; even a
   manually-registered executor stays shadow-skipped while the global shadow
   switch is on; flag ON ⇒ engine executes and legacy is suppressed; flag OFF
   again ⇒ legacy resumes (one-flag rollback, greenlight criterion 8 shape).
4. EVIDENCE DERIVABILITY: SKIPPED_SHADOW row cadence for the orphan job, the
   boot posture line's stability across ownership, and first-tick immediacy
   (legacy ``_run_loop`` sweeps before sleeping; engine first offset = 0).

DOC-VS-TREE DISCLOSURE (first_tick_kind)
----------------------------------------
The mission presumed ``BOOT_SWEEP``-deadlined. The tree says otherwise: the
legacy periodic ``_run_loop`` calls ``sweep_once()`` BEFORE its first sleep
(immediate first tick; census A1: "first tick immediate"), while the
``BOOT_SWEEP``-deadlined orphan cleanup is ``DependencyBus.start()``'s
ONE-SHOT boot sweep (census #26) — a DIFFERENT mechanism. Parity therefore
requires IMMEDIATE, not BOOT_SWEEP. The engine spec built by
``build_observer_specs`` has ``first_tick_kind=None`` (≡ immediate: offset 0),
so the tree already matches the legacy shape; no engine-core edit is needed.

ENGINE-CORE DRIFT (reported, not patched — engine/** is a reviewed fence)
-------------------------------------------------------------------------
``dualpath.py:31`` derives ``SERVICES_{slug.upper()}_USE_ENGINE`` WITHOUT
hyphen normalization, while ``boot.py:179`` normalizes ``-``→``_``. Passing
the estate slug raw would read a different variable than the engine's kill
switch. ``engine_owns_execution`` normalizes at our call site; test 1 pins
the consequence so a future engine-core fix cannot silently double-execute.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session, select
from sqlmodel.pool import StaticPool

from daemon.repositories.engine.models import EngineRun
from daemon.repositories.engine.repository import EngineRepository
from daemon.services import orphan_watcher_sweep as ows
from daemon.services.engine.boot import build_observer_specs, compute_first_tick_offset
from daemon.services.engine.engine import EngineService
from daemon.services.engine.registry import EngineRegistry
from daemon.services.orphan_watcher_sweep import (
    ORPHAN_WATCHER_ENGINE_FLAG,
    ORPHAN_WATCHER_ENGINE_JOB_ID,
    ORPHAN_WATCHER_ENGINE_SLUG,
    OrphanWatcherSweepService,
    build_engine_executor,
    engine_owns_execution,
)

API_SRC = (Path(__file__).resolve().parents[3] / "daemon" / "api.py").read_text(
    encoding="utf-8"
)
SERVICE_SRC = (Path(__file__).resolve().parents[3] / "daemon" / "services" / "orphan_watcher_sweep.py").read_text(
    encoding="utf-8"
)


# ── fixture shapes ─────────────────────────────────────────────────────────

@dataclass
class _Watcher:
    id: str
    state: str
    source_task_id: str
    created_at: datetime


@dataclass
class _FakeBus:
    """Mirrors the real predicate: PENDING + dead parent + age >= grace."""

    watchers: list[_Watcher] = field(default_factory=list)
    active_task_ids: set[str] = field(default_factory=lambda: {"live-task"})
    grace_calls: list[int] = field(default_factory=list)

    def states(self) -> dict[str, str]:
        return {w.id: w.state for w in self.watchers}

    async def _sweep_orphan_watchers(
        self, min_watcher_age_seconds: int = 30
    ) -> int:
        self.grace_calls.append(int(min_watcher_age_seconds))
        now = datetime.now(timezone.utc)
        cancelled = 0
        for w in self.watchers:
            if w.state != "PENDING":
                continue
            if w.source_task_id in self.active_task_ids:
                continue
            if (now - w.created_at).total_seconds() < min_watcher_age_seconds:
                continue
            w.state = "CANCELLED"
            cancelled += 1
        return cancelled


def _fixture_watchers() -> list[_Watcher]:
    now = datetime.now(timezone.utc)
    return [
        _Watcher("old-orphan", "PENDING", "dead-task", now - timedelta(seconds=120)),
        _Watcher("young-orphan", "PENDING", "dead-task", now - timedelta(seconds=5)),
        _Watcher("live-parent", "PENDING", "live-task", now - timedelta(seconds=120)),
        _Watcher("already-cancelled", "CANCELLED", "dead-task", now - timedelta(seconds=120)),
        _Watcher("fired", "FIRED", "dead-task", now - timedelta(seconds=120)),
    ]


def _skip_reason(w: _Watcher, original_state: str, grace: int) -> str:
    if w.state == "CANCELLED" and original_state == "PENDING":
        return "SWEPT"
    if original_state != "PENDING":
        return "SKIP_NOT_PENDING"
    if w.source_task_id == "live-task":
        return "SKIP_LIVE_PARENT"
    return "SKIP_GRACE_WINDOW"


def _make_cfg(interval: int = 1, shadow: bool = True) -> Any:
    class _Services:
        orphan_watcher_sweep_interval_seconds = interval

    class _EngineCfg:
        enabled = True
        tick_resolution_seconds = 0.1
        tick_workers = 2
        boot_pass_deadline_seconds = 5.0
        extend_ceiling_seconds = 30.0
        retry_backoff_cap_seconds = 30.0
        shadow_mode = shadow

    class _Cfg:
        services = _Services()
        engine = _EngineCfg()

    return _Cfg()


def _db_engine() -> Any:
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _orphan_spec(cfg: Any) -> Any:
    return next(s for s in build_observer_specs(cfg) if s.id == ORPHAN_WATCHER_ENGINE_JOB_ID)


# ── 1. identity / flag derivation ─────────────────────────────────────────

def test_dualpath_identity_flag_and_spec_key_agree():
    assert ORPHAN_WATCHER_ENGINE_SLUG == "orphan-watcher-sweep"
    assert ORPHAN_WATCHER_ENGINE_FLAG == "SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE"
    spec = _orphan_spec(_make_cfg())
    assert spec.kill_switch_key == ORPHAN_WATCHER_ENGINE_FLAG
    # The engine's own derivation (boot.py) is the authority for the key.
    assert spec.kill_switch_key == (
        f"SERVICES_{spec.meta.service_tag.upper().replace('-', '_')}_USE_ENGINE"
    )
    # dualpath.py:31 does NOT normalize hyphens — pin the drift consequence.
    from daemon.services.engine.dualpath import use_engine_enabled

    raw_key = f"SERVICES_{ORPHAN_WATCHER_ENGINE_SLUG.upper()}_USE_ENGINE"
    assert raw_key != ORPHAN_WATCHER_ENGINE_FLAG
    assert use_engine_enabled(ORPHAN_WATCHER_ENGINE_SLUG, {raw_key: "1"}) is True
    assert use_engine_enabled(ORPHAN_WATCHER_ENGINE_SLUG, {ORPHAN_WATCHER_ENGINE_FLAG: "1"}) is False
    # Our normalized decision reads the CANONICAL key.
    assert engine_owns_execution({}) is False
    assert engine_owns_execution({ORPHAN_WATCHER_ENGINE_FLAG: "1"}) is True
    assert engine_owns_execution({ORPHAN_WATCHER_ENGINE_FLAG: "0"}) is False


def test_spec_shape_first_tick_and_class():
    cfg = _make_cfg(interval=90)
    spec = _orphan_spec(cfg)
    assert spec.trigger.seconds == 90  # cadence from services_attr, never hardcoded
    assert spec.trigger.first_tick_kind in (None, "immediate")
    assert compute_first_tick_offset(spec.id, spec.trigger, "seed") == 0.0
    assert spec.concurrency_class == "DB_OFFLOAD"  # leader ruling L-7
    assert spec.meta.category == "reconciler"


# ── 2. parity on identical fixture shapes ─────────────────────────────────

@pytest.mark.asyncio
async def test_parity_swept_rows_and_skip_reasons(monkeypatch):
    legacy_bus = _FakeBus(_fixture_watchers())
    engine_bus = _FakeBus(_fixture_watchers())
    originals = {w.id: w.state for w in _fixture_watchers()}

    legacy = OrphanWatcherSweepService(
        dependency_bus=legacy_bus,
        interval_seconds=90,
        min_watcher_age_seconds=30,
    )
    legacy_result = await legacy.sweep_once()

    # The engine executor LATE-BINDS through the module getter — patch it.
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: engine_bus)
    # DB_OFFLOAD invokes the sync adapter in a worker thread (no running loop).
    engine_result = await asyncio.to_thread(
        build_engine_executor(min_watcher_age_seconds=30)
    )

    assert legacy_result["cancelled"] == 1
    assert engine_result["cancelled"] == 1
    assert legacy_bus.states() == engine_bus.states()
    assert legacy_bus.grace_calls == engine_bus.grace_calls == [30]
    assert engine_result["bus_wired"] == 1
    assert engine_result["grace_seconds"] == 30

    reasons = {
        w.id: _skip_reason(w, originals[w.id], 30) for w in engine_bus.watchers
    }
    assert reasons == {
        "old-orphan": "SWEPT",
        "young-orphan": "SKIP_GRACE_WINDOW",
        "live-parent": "SKIP_LIVE_PARENT",
        "already-cancelled": "SKIP_NOT_PENDING",
        "fired": "SKIP_NOT_PENDING",
    }
    print("\n[PARITY] fixture-shape × comparison-dimension")
    print("[PARITY] shape            legacy            engine             verdict")
    for w in engine_bus.watchers:
        print(
            f"[PARITY] {w.id:<16} {legacy_bus.states()[w.id]:<16} "
            f"{engine_bus.states()[w.id]:<16} {reasons[w.id]}"
        )
    print(f"[PARITY] cancelled       {legacy_result['cancelled']:<16} {engine_result['cancelled']:<16} MATCH")


@pytest.mark.asyncio
async def test_parity_state_stamp_mapping(monkeypatch):
    """Legacy tick/cancel/error counters ↔ engine engine_runs/outcome stamps."""
    cfg = _make_cfg(interval=1, shadow=False)
    bus = _FakeBus(_fixture_watchers())
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: bus)
    calls = {"n": 0}

    def counting_executor() -> dict[str, Any]:
        calls["n"] += 1
        return build_engine_executor(30)()

    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=False)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    svc.set_executor(ORPHAN_WATCHER_ENGINE_JOB_ID, counting_executor)
    await svc.start()
    try:
        await asyncio.sleep(2.6)  # >= 2 intervals at 1s
    finally:
        await svc.stop()

    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert len(runs) >= 2
    assert {r.outcome for r in runs} == {"SUCCEEDED"}
    assert calls["n"] >= 2
    cache = svc._cache[ORPHAN_WATCHER_ENGINE_JOB_ID]
    assert cache["last_outcome"] == "SUCCEEDED"

    # The SAME fixture through the legacy path maps to the same sweep effect.
    legacy_bus = _FakeBus(_fixture_watchers())
    legacy = OrphanWatcherSweepService(dependency_bus=legacy_bus, min_watcher_age_seconds=30)
    legacy_result = await legacy.sweep_once()
    assert legacy_result["cancelled"] == 1
    assert legacy_result["errors"] == 0
    assert legacy_result["effective_runs"] == 1
    # Explicit mapping (documented, not assumed equal):
    #   legacy ticks  -> engine_runs row count (one row per due tick)
    #   legacy cancelled -> executor result["cancelled"] (both 1 on fixture)
    #   legacy errors -> engine outcome FAILED (0 here ⇒ no FAILED rows)
    assert len(runs) >= legacy_result["ticks"]
    assert not any(r.outcome == "FAILED" for r in runs)


# ── 3. dormant default for executables / one-flag rollback ────────────────

@pytest.mark.asyncio
async def test_flag_unset_no_executor_engine_stays_shadow(monkeypatch):
    """Flag UNSET at boot: no executor registered; engine never executes."""
    cfg = _make_cfg(interval=1, shadow=False)  # shadow OFF — executor still absent
    bus = _FakeBus(_fixture_watchers())
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: bus)
    assert engine_owns_execution({}) is False

    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=False)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    # NO set_executor — exactly what the flag-OFF boot block does.
    await svc.start()
    try:
        await asyncio.sleep(2.6)
    finally:
        await svc.stop()

    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert len(runs) >= 2
    assert {r.outcome for r in runs} == {"SKIPPED_SHADOW"}
    assert bus.grace_calls == []  # the executor was never invoked


@pytest.mark.asyncio
async def test_manual_executor_plus_flag_unset_still_shadow(monkeypatch):
    """Task 3 literal case: executor registered + flag UNSET + shadow ON."""
    cfg = _make_cfg(interval=1, shadow=True)
    bus = _FakeBus(_fixture_watchers())
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: bus)
    assert engine_owns_execution({}) is False

    calls = {"n": 0}

    def counting_executor():
        calls["n"] += 1
        return build_engine_executor(30)()

    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=True)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    svc.set_executor(ORPHAN_WATCHER_ENGINE_JOB_ID, counting_executor)
    await svc.start()
    try:
        await asyncio.sleep(2.6)
    finally:
        await svc.stop()

    assert calls["n"] == 0
    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert len(runs) >= 2
    assert {r.outcome for r in runs} == {"SKIPPED_SHADOW"}


@pytest.mark.asyncio
async def test_flag_on_engine_executes_legacy_suppressed(monkeypatch):
    """Flag ON: exactly one executor (engine); legacy loop is suppressed."""
    env = {ORPHAN_WATCHER_ENGINE_FLAG: "1"}
    assert engine_owns_execution(env) is True
    cfg = _make_cfg(interval=1, shadow=False)
    bus = _FakeBus(_fixture_watchers())
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: bus)
    calls = {"n": 0}

    def counting_executor():
        calls["n"] += 1
        return build_engine_executor(30)()

    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=False)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    svc.set_executor(ORPHAN_WATCHER_ENGINE_JOB_ID, counting_executor)
    await svc.start()
    try:
        await asyncio.sleep(2.6)
    finally:
        await svc.stop()

    assert calls["n"] >= 2
    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert {r.outcome for r in runs} == {"SUCCEEDED"}
    # Legacy suppression is a wiring fact (no .start() on the engine-owned branch).
    legacy_branch = API_SRC[API_SRC.index("elif engine_owns_execution():"):]
    legacy_branch = legacy_branch[: legacy_branch.index("    else:")]
    assert ".start()" not in legacy_branch
    assert "SUPPRESSED" in legacy_branch


def test_one_flag_rollback_end_to_end():
    """OFF → ON → OFF flips ownership with no code change (criterion 8 shape)."""
    assert engine_owns_execution({}) is False
    assert engine_owns_execution({ORPHAN_WATCHER_ENGINE_FLAG: "1"}) is True
    assert engine_owns_execution({ORPHAN_WATCHER_ENGINE_FLAG: "0"}) is False
    # Both wiring sites consult the SAME decision helper.
    assert API_SRC.count("engine_owns_execution()") >= 2
    reg_at = API_SRC.index("if engine_owns_execution():")
    start_at = API_SRC.index("elif engine_owns_execution():")
    assert start_at < reg_at  # construct site (legacy) precedes boot block (engine)


# ── 4. greenlight-evidence derivability ───────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_row_cadence_for_orphan_watcher():
    """Shadow-evaluated >= 2× its (fast) interval with SKIPPED_SHADOW rows."""
    cfg = _make_cfg(interval=1, shadow=True)
    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=True)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    await svc.start()
    try:
        await asyncio.sleep(2.6)
    finally:
        await svc.stop()

    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert len(runs) >= 2, f"expected >=2 shadow rows, got {len(runs)}"
    assert {r.outcome for r in runs} == {"SKIPPED_SHADOW"}
    assert {r.skip_reason for r in runs} == {"SHADOW"}
    cache = svc._cache[ORPHAN_WATCHER_ENGINE_JOB_ID]
    assert cache["last_outcome"] == "SKIPPED_SHADOW"
    print(f"\n[EVIDENCE] orphan-watcher SKIPPED_SHADOW rows over 2× interval: {len(runs)}")


def test_posture_line_stable_across_ownership():
    """The single posture line is unchanged; the pilot line is additive."""
    assert API_SRC.count('"engine boot posture: enabled=%s shadow=%s registrations=%d') == 1
    assert "specs (execute_fn withheld, USE_ENGINE all unset)" in API_SRC
    assert "orphan-watcher pilot: engine executor registered, legacy " in API_SRC
    # Pilot line lives INSIDE the flag-ON registration block.
    reg_at = API_SRC.index("if engine_owns_execution():")
    pilot_at = API_SRC.index("orphan-watcher pilot: engine executor registered")
    wiring_at = API_SRC.index("# T4.6 — boot wiring assertion")
    assert reg_at < pilot_at < wiring_at


def test_first_tick_immediate_in_both_paths():
    """Legacy sweeps before its first sleep; engine first offset is zero."""
    loop_at = SERVICE_SRC.index("async def _run_loop")
    sweep_at = SERVICE_SRC.index("await self.sweep_once()", loop_at)
    sleep_at = SERVICE_SRC.index("asyncio.wait_for(", loop_at)
    assert sweep_at < sleep_at
    spec = _orphan_spec(_make_cfg())
    assert compute_first_tick_offset(spec.id, spec.trigger, "boot-seed") == 0.0
