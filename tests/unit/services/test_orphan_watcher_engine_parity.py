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
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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
    rearm_legacy_loop,
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

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()


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


class _SyncSession:
    """A sync Session-like object (the real DB op's handle)."""

    def __init__(self, rows: dict[str, "_Watcher"]) -> None:
        self._rows = rows

    def execute_cancelled(self, cutoff_age_seconds: int) -> int:
        cancelled = 0
        for w in self._rows.values():
            if w.state != "PENDING":
                continue
            if w.source_task_id == "live-task":
                continue
            if w.age_seconds < cutoff_age_seconds:
                continue
            w.state = "CANCELLED"
            cancelled += 1
        return cancelled


class _RealShapedBus:
    """The REAL ``DependencyBus._sweep_orphan_watchers`` SHAPE (C2).

    An ASYNC method whose DB work is a SYNC callable executed via
    ``await asyncio.to_thread(...)`` — the shape the sync bridge must
    survive. The row predicate mirrors the real one (PENDING + dead parent
    + age >= grace).
    """

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.rows = {
            "old-orphan": _Watcher("old-orphan", "PENDING", "dead-task", now - timedelta(seconds=120)),
            "young-orphan": _Watcher("young-orphan", "PENDING", "dead-task", now - timedelta(seconds=5)),
        }
        self.db_thread_ids: list[int] = []
        self.loop_ids: list[int] = []

    async def _sweep_orphan_watchers(self, min_watcher_age_seconds: int = 30) -> int:
        def _sweep_atomic() -> int:
            self.db_thread_ids.append(threading.get_ident())
            session = _SyncSession(self.rows)
            return session.execute_cancelled(int(min_watcher_age_seconds))

        rowcount = await asyncio.to_thread(_sweep_atomic)
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return rowcount


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

def _engine_cfg_stub(enabled: bool = True, shadow_mode: bool = True) -> Any:
    """A stand-in for ``config.engine`` (EngineConfig: enabled + shadow_mode)."""
    return SimpleNamespace(enabled=enabled, shadow_mode=shadow_mode)


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
    assert engine_owns_execution({}, _engine_cfg_stub()) is False
    assert (
        engine_owns_execution(
            {ORPHAN_WATCHER_ENGINE_FLAG: "1"}, _engine_cfg_stub(shadow_mode=False)
        )
        is True
    )
    assert (
        engine_owns_execution(
            {ORPHAN_WATCHER_ENGINE_FLAG: "0"}, _engine_cfg_stub(shadow_mode=False)
        )
        is False
    )


def test_ownership_conjunct_is_fail_to_legacy():
    """C1: engine owns ⟺ flag ON ∧ engine enabled ∧ engine shadow OFF.

    The shipped default (shadow ON) must NOT claim ownership — otherwise the
    construct site would suppress the legacy loop while the engine refuses to
    execute it (the fail-stuck defect the reviewer flagged).
    """
    on = {ORPHAN_WATCHER_ENGINE_FLAG: "1"}
    # flag OFF / unset ⇒ legacy owns, whatever the posture.
    assert engine_owns_execution({}, _engine_cfg_stub(shadow_mode=False)) is False
    assert engine_owns_execution({ORPHAN_WATCHER_ENGINE_FLAG: "0"}, _engine_cfg_stub(shadow_mode=False)) is False
    # flag ON + SHIPPED shadow default ON ⇒ legacy owns (engine observes).
    assert engine_owns_execution(on, _engine_cfg_stub()) is False
    # flag ON + engine disabled ⇒ legacy owns.
    assert engine_owns_execution(on, _engine_cfg_stub(enabled=False, shadow_mode=False)) is False
    # flag ON + shadow OFF + enabled ⇒ engine owns (the happy path).
    assert engine_owns_execution(on, _engine_cfg_stub(shadow_mode=False)) is True
    # No posture at all ⇒ fail-to-legacy.
    assert engine_owns_execution(on, None) is False


def test_construct_site_and_boot_block_share_the_conjunct():
    """Both wiring sites consult the SAME conjunct (no divergent resolution)."""
    assert "elif engine_owns_execution(engine_config=" in API_SRC
    assert "if engine_owns_execution(engine_config=_engine_cfg):" in API_SRC
    # The boot block's _engine_cfg IS the construct site's config.engine.
    assert "_engine_cfg = config.engine" in API_SRC
    assert 'getattr(config, "engine", None)' in API_SRC
    # The executor registers BEFORE the engine starts (no dual-run window).
    reg_at = API_SRC.index("_engine_svc.set_executor(")
    start_at = API_SRC.index("await _engine_svc.start()")
    assert reg_at < start_at


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
    assert engine_owns_execution({}, _engine_cfg_stub()) is False
    assert engine_owns_execution({}, _engine_cfg_stub(shadow_mode=False)) is False

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
    assert engine_owns_execution({}, _engine_cfg_stub()) is False
    assert engine_owns_execution({}, _engine_cfg_stub(shadow_mode=False)) is False

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
    """Flag ON + conjunct true: exactly one executor (engine); legacy suppressed."""
    env = {ORPHAN_WATCHER_ENGINE_FLAG: "1"}
    assert engine_owns_execution(env, _engine_cfg_stub(shadow_mode=False)) is True
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
    legacy_branch = API_SRC[API_SRC.index("elif engine_owns_execution(engine_config="):]
    legacy_branch = legacy_branch[: legacy_branch.index("    else:")]
    assert ".start()" not in legacy_branch
    assert "SUPPRESSED" in legacy_branch


def test_one_flag_rollback_end_to_end():
    """Rollback = flag OFF (or shadow ON, or engine disabled) ⇒ legacy owns.

    C1 update: the rollback is NOT flag-only any more — the ownership
    conjunct includes the engine's own posture, so flipping the flag back
    OFF (or putting the engine back into shadow) returns ownership to the
    legacy loop with no code change (greenlight criterion 8 shape).
    """
    on = {ORPHAN_WATCHER_ENGINE_FLAG: "1"}
    off = {ORPHAN_WATCHER_ENGINE_FLAG: "0"}
    live = _engine_cfg_stub(shadow_mode=False)
    # Roll-forward: flag ON + shadow OFF ⇒ engine owns.
    assert engine_owns_execution(on, live) is True
    # Rollback 1: flag OFF ⇒ legacy owns.
    assert engine_owns_execution(off, live) is False
    # Rollback 2: shadow back ON (engine observes only) ⇒ legacy owns.
    assert engine_owns_execution(on, _engine_cfg_stub(shadow_mode=True)) is False
    # Rollback 3: engine disabled ⇒ legacy owns.
    assert engine_owns_execution(on, _engine_cfg_stub(enabled=False, shadow_mode=False)) is False
    # Both wiring sites consult the SAME decision helper.
    assert API_SRC.count("engine_owns_execution(") >= 2
    reg_at = API_SRC.index("if engine_owns_execution(engine_config=_engine_cfg):")
    start_at = API_SRC.index("elif engine_owns_execution(engine_config=")
    assert start_at < reg_at  # construct site (legacy) precedes boot block (engine)
    # The flag-OFF `else:` branch still starts the legacy loop (byte-identical
    # OFF behavior); the engine-owned `elif` branch does NOT (single executor).
    else_at = API_SRC.index("    else:", start_at)
    legacy_else = API_SRC[else_at : else_at + 400]
    assert "orphan_watcher_sweep.start()" in legacy_else
    assert "OrphanWatcherSweepService started: interval=" in legacy_else


def test_fail_to_legacy_rearm_on_boot_block_failure():
    """C1: a boot-block failure RE-ARMS the legacy loop (never a dead lane)."""
    started = {"n": 0}

    class _FakeService:
        _task = None

        def start(self):
            started["n"] += 1

    svc = _FakeService()
    assert rearm_legacy_loop(svc, reason="engine boot wiring failed: boom") is True
    assert started["n"] == 1
    # Idempotent: a LIVE loop is never started twice.
    class _LiveTask:
        def done(self) -> bool:
            return False

    svc._task = _LiveTask()
    assert rearm_legacy_loop(svc, reason="second attempt") is False
    assert started["n"] == 1
    # A missing service is a no-op (fail-soft).
    assert rearm_legacy_loop(None, reason="no service") is False


def test_boot_block_except_handler_calls_the_rearm():
    """Source pin: the boot-block except handler RE-ARMS legacy (mutation: RED)."""
    handler_at = API_SRC.index("except Exception as _engine_boot_exc:")
    window = API_SRC[handler_at : handler_at + 1600]
    assert "rearm_legacy_loop(" in window
    assert "FAIL-TO-LEGACY" in window or "orphan_watcher_sweep" in window
    # The partial executor is dropped first (a half-started engine must not
    # double-run the sweep beside the re-armed legacy loop).
    assert "_executors.pop(" in window


@pytest.mark.asyncio
async def test_flag_on_shipped_shadow_default_legacy_owns():
    """C1 Path B: flag ON + SHIPPED shadow default ⇒ LEGACY owns, engine observes.

    This is the dead-sweep path the reviewer flagged: flag ON alone must NOT
    suppress the legacy loop, because in shadow the engine never invokes the
    executor. The engine keeps accumulating SKIPPED_SHADOW rows (the correct
    greenlight posture) and the legacy loop keeps sweeping.
    """
    env = {ORPHAN_WATCHER_ENGINE_FLAG: "1"}
    shipped = _engine_cfg_stub(enabled=True, shadow_mode=True)
    assert engine_owns_execution(env, shipped) is False

    cfg = _make_cfg(interval=1, shadow=True)
    bus = _FakeBus(_fixture_watchers())
    monkey = _FakeBus(_fixture_watchers())
    db = _db_engine()
    repo = EngineRepository(db)
    svc = EngineService(repo, EngineRegistry(repo), cfg.engine, shadow_mode=True)
    await EngineRegistry(repo).aregister(_orphan_spec(cfg))
    # NO executor: the boot block skips registration under this conjunct, so
    # the engine OBSERVES the job while the legacy loop owns it.
    await svc.start()
    try:
        await asyncio.sleep(2.6)
    finally:
        await svc.stop()

    with Session(db) as s:
        runs = list(s.exec(select(EngineRun).where(EngineRun.job_id == ORPHAN_WATCHER_ENGINE_JOB_ID)).all())
    assert len(runs) >= 2
    assert {r.outcome for r in runs} == {"SKIPPED_SHADOW"}
    assert bus.grace_calls == [] and monkey.grace_calls == []


@pytest.mark.asyncio
async def test_real_shaped_bus_through_the_sync_bridge(monkeypatch):
    """C2: the adapter bridges a bus whose sweep has the REAL shape.

    ``DependencyBus._sweep_orphan_watchers`` is an ASYNC method whose DB
    work runs in a SYNC callable executed via ``await asyncio.to_thread``.
    The engine's DB_OFFLOAD class refuses coroutine functions, so the
    adapter bridges with ``asyncio.run`` inside the worker thread. This pin
    drives that bridge against a bus with the real shape (to_thread + a
    sync Session-like object) and proves the off-loop hop survives it.
    """
    bus = _RealShapedBus()
    monkeypatch.setattr(ows, "get_dependency_bus", lambda: bus)
    caller_loop_id = id(asyncio.get_running_loop())
    caller_thread = threading.get_ident()

    result = await asyncio.to_thread(build_engine_executor(30))

    assert result["cancelled"] == 1
    assert result["bus_wired"] == 1
    assert result["grace_seconds"] == 30
    # The real shape's DB op ran OFF the caller thread (to_thread honoured).
    assert bus.db_thread_ids
    assert bus.db_thread_ids[0] != caller_thread
    # And it ran inside a FRESH event loop (the asyncio.run bridge), not the
    # caller's loop (which would raise "cannot be called from a running
    # event loop").
    assert bus.loop_ids
    assert bus.loop_ids[0] != caller_loop_id
    assert bus.rows["old-orphan"].state == "CANCELLED"
    assert bus.rows["young-orphan"].state == "PENDING"


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
    # Pilot line lives INSIDE the conjunct-true registration block.
    reg_at = API_SRC.index("if engine_owns_execution(engine_config=_engine_cfg):")
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
