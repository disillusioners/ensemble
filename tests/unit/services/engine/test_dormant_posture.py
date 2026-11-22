"""Leg D — engine dormant posture release-blocking gate (Engine Arc P7).

Capability coverage: test-strategy §1.1 T8 (dormant default) + phase7-plan
§4.2 Leg D. Task path authority:
``tests/unit/services/engine/test_dormant_posture.py``.

WHAT IT PINS (CS-1, char-faithful): "N observer specs registered, zero
executables (execute_fn withheld), shadow rows present" —

  1. Drive the REAL boot-block registration path (build_observer_specs +
     EngineRegistry.aregister) with the shipped defaults. N is derived
     from the real wiring (N >= 1; the tree registers 12). The test
     follows the TREE's count, never a hand-built fixture set.
  2. ZERO executables: every registered spec carries execute_fn withheld;
     an invocation SPY on the dispatch/execute path (the three concurrency
     runners) asserts execute_fn call count == 0 across >= 3 tick
     intervals. Fast cadence: job intervals overridden to 1s and tick
     resolution to 0.1s (config override, never a timeout relax).
  3. Shadow rows: engine_runs carries ONLY outcome=SKIPPED_SHADOW rows
     (one per due job per interval) across >= 3 intervals.
  4. All SERVICES_*_USE_ENGINE unset; the boot posture line present
     (enabled=True shadow=True registrations=N observer specs).

Deliberately-dormant structure: shipped defaults
(enabled=true + shadow_mode=true per AMD-3c) — we do NOT hand-build a
fixture spec set; we use what the wiring registers.

NON-VACUITY DISCLOSURE (compound mutation — approver iteration-002, N-e):
the mutation is deliberately COMPOUND — each half alone is structurally
inert, not a weaker test:

  * flipping the shadow default alone leaves zero executables (dispatch is
    structurally impossible regardless of polarity — D4.11), so the spy
    stays 0;
  * registering a fixture job alone leaves the shipped shadow_mode=true
    guard in place (D2.13), so the spy stays 0.

Only the PAIR produces an observable violation, and the pair is ONE planted
defect class (guard + registration wiring reverted to a pre-dormant shape),
restored together. This disclosure exists so the compound is read as
required detection power, not as a vacuity gap.

Mutation (disposable extract): flip ENSEMBLE_ENGINE_SHADOW_MODE default to
false AND register one fixture executable job (engine.set_executor on a
synthetic job) => the spy count becomes > 0 => the pin REDs. Proof run
in a disposable extract (git archive ...); see the pack/commit notes.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session, select
from sqlmodel.pool import StaticPool

from daemon.repositories.engine.models import EngineRun
from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine import dispatch as D
from daemon.services.engine.boot import build_observer_specs
from daemon.services.engine.engine import EngineService
from daemon.services.engine.registry import EngineRegistry


def _make_services():
    class _Services:
        watch_reconcile_sweep_interval_seconds = 1
        mission_strand_heal_interval_seconds = 1
        report_delivery_recovery_interval_seconds = 1
        sources_gc_interval_seconds = 1
        eligible_pending_sweep_interval_seconds = 1
        orphan_watcher_sweep_interval_seconds = 1
        job_lock_sweep_interval_seconds = 1
        tmp_image_cleanup_interval_seconds = 1
        plane_sync_watchdog_interval_seconds = 1
        stale_pause_sweep_interval_seconds = 1
        waiting_children_watchdog_interval_seconds = 1
        drift_reconcile_interval_seconds = 1

    return _Services()


def _make_engine_cfg(shadow_mode: bool = True):
    class _EngineCfg:
        enabled = True
        tick_resolution_seconds = 0.1
        tick_workers = 2
        boot_pass_deadline_seconds = 5.0
        extend_ceiling_seconds = 30.0
        retry_backoff_cap_seconds = 30.0

    _EngineCfg.shadow_mode = shadow_mode
    return _EngineCfg()


def _make_cfg(shadow_mode: bool = True):
    class _Cfg:
        services = _make_services()
        engine = _make_engine_cfg(shadow_mode)

    return _Cfg()


def _db_engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.mark.asyncio
async def test_dormant_posture_in_process():
    """In-process real boot wiring: N observer specs, zero execute, shadow rows."""
    cfg = _make_cfg(shadow_mode=True)

    # 4. All SERVICES_*_USE_ENGINE unset (before wiring).
    specs = build_observer_specs(cfg)
    for spec in specs:
        assert os.environ.get(spec.kill_switch_key) is None

    # N derived from the real wiring.
    assert len(specs) >= 1
    N = len(specs)

    # 1. execute_fn withheld on every spec.
    for spec in specs:
        assert not hasattr(spec, "execute_fn")

    # Real boot wiring (in-process).
    db = _db_engine()
    repo = EngineRepository(db)
    registry = EngineRegistry(repo)
    svc = EngineService(repo, registry, cfg.engine, shadow_mode=cfg.engine.shadow_mode)
    for spec in specs:
        await registry.aregister(spec)
    await svc.start()

    # Spy on the dispatch/execute path (the three concurrency runners).
    spy = {"count": 0}
    orig_main = D.run_main_loop
    orig_db = D.run_db_offload
    orig_ded = D.run_dedicated_executor

    async def spy_main(fn, *a, **k):
        spy["count"] += 1
        return await orig_main(fn, *a, **k)

    async def spy_db(fn, *a, **k):
        spy["count"] += 1
        return await orig_db(fn, *a, **k)

    async def spy_ded(executor, fn, *a, **k):
        spy["count"] += 1
        return await orig_ded(executor, fn, *a, **k)

    D.run_main_loop = spy_main
    D.run_db_offload = spy_db
    D.run_dedicated_executor = spy_ded

    try:
        # >= 3 tick intervals (job interval 1s, tick resolution 0.1s).
        await asyncio.sleep(3.5)
    finally:
        await svc.stop()
        D.run_main_loop = orig_main
        D.run_db_offload = orig_db
        D.run_dedicated_executor = orig_ded

    # 2. Spy count == 0.
    assert spy["count"] == 0, f"execute_fn invoked {spy['count']} times"

    # 3. Shadow rows: engine_runs carries ONLY SKIPPED_SHADOW, >= 3 intervals.
    with Session(db) as s:
        runs = list(s.exec(select(EngineRun)).all())
    assert len(runs) >= N * 3, (
        f"expected >= {N * 3} shadow rows, got {len(runs)}"
    )
    assert all(r.outcome == "SKIPPED_SHADOW" for r in runs), (
        "non-shadow outcomes present: "
        + str({r.outcome for r in runs if r.outcome != "SKIPPED_SHADOW"})
    )

    # Job count == N.
    jobs = repo.list_jobs()
    assert len(jobs) == N


def test_posture_line_present_in_boot_wiring():
    """Source pin: exactly ONE posture INFO line with enabled/shadow/registrations."""
    from pathlib import Path

    API = Path(__file__).resolve().parents[4] / "daemon" / "api.py"
    src = API.read_text(encoding="utf-8")
    assert src.count('"engine boot posture: enabled=%s shadow=%s registrations=%d') == 1
    # F1: the posture line reports the ACTUAL USE_ENGINE state. The static
    # "all unset" claim is gone — it used to print while a flag was set.
    assert "specs (execute_fn withheld); use_engine_flags=%s;" in src
    assert "USE_ENGINE all unset" not in src


def test_all_use_engine_flags_unset():
    """CS-1/AMD-1(b): no SERVICES_*_USE_ENGINE flag is set at boot."""
    for spec in build_observer_specs(_make_cfg(shadow_mode=True)):
        assert os.environ.get(spec.kill_switch_key) is None
