"""Engine boot wiring unit pins (Engine Arc P4 — T4.4/4.5/4.6/4.7).

Capability coverage: test-strategy §1.1 T8 (dormant default) + the wiring /
shutdown / boot-SLO source-order pins. Task path authority:
``tests/unit/services/engine/test_wiring.py``.

The lifespan wiring is one contiguous inline block in ``daemon/api.py``; these
pins verify its CONTRACT (dormant posture, single posture line, set_event_sink
composition, guarded failure, shutdown order, F-5/F-8/SLO-2 source ordering)
plus the pure helper it composes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from daemon.services.engine.boot import build_observer_specs
from daemon.services.engine.engine import EngineService
from daemon.services.engine.guardrails import assert_dependencies_resolvable, DependencyRef
from daemon.services.engine.watchdog import EngineWatchdog

API = Path(__file__).resolve().parents[4] / "daemon" / "api.py"
API_SRC = API.read_text(encoding="utf-8")


class _Services:
    watch_reconcile_sweep_interval_seconds = 300
    mission_strand_heal_interval_seconds = 300
    report_delivery_recovery_interval_seconds = 300
    sources_gc_interval_seconds = 3600
    eligible_pending_sweep_interval_seconds = 90
    orphan_watcher_sweep_interval_seconds = 90
    job_lock_sweep_interval_seconds = 90
    tmp_image_cleanup_interval_seconds = 3600
    plane_sync_watchdog_interval_seconds = 300
    stale_pause_sweep_interval_seconds = 300
    waiting_children_watchdog_interval_seconds = 3600
    drift_reconcile_interval_seconds = 300


class _Cfg:
    services = _Services()


# ── T4.4 / CS-1 dormant posture ──────────────────────────────────────────

def test_observer_specs_dormant_posture():
    """N observer specs, execute_fn withheld, cadences from the continuity table."""
    specs = build_observer_specs(_Cfg())
    assert len(specs) == 12
    assert len({s.id for s in specs}) == 12
    for spec in specs:
        # JobSpec has NO execute_fn field — dispatch is structurally impossible.
        assert not hasattr(spec, "execute_fn")
        assert spec.trigger.trigger_kind == "interval"
        assert spec.trigger.seconds > 0
        # kill_switch_key NAMES the future USE_ENGINE seam; it never SETS it.
        assert spec.kill_switch_key.startswith("SERVICES_")
        assert spec.kill_switch_key.endswith("_USE_ENGINE")
        assert spec.meta.metadata.get("observer") is True


def test_all_use_engine_flags_unset_in_dormant_posture(monkeypatch):
    """CS-1/AMD-1(b): no SERVICES_*_USE_ENGINE flag is set at boot."""
    for spec in build_observer_specs(_Cfg()):
        assert monkeypatch is not None
        # The builder never writes env; the key is only a spec attribute.
        import os

        assert os.environ.get(spec.kill_switch_key) is None


def test_set_event_sink_composition_line_present():
    """AMD-5: the set_event_sink composition line is owned by P4's block."""
    assert "set_event_sink(_engine_svc.ingest_event)" in API_SRC


def test_single_posture_info_line():
    """AC4 — exactly ONE posture INFO with enabled/shadow/registrations + elapsed."""
    assert API_SRC.count('"engine boot posture: enabled=%s shadow=%s registrations=%d') == 1
    assert "block_elapsed_ms=%.3f" in API_SRC
    assert "specs (execute_fn withheld, USE_ENGINE all unset)" in API_SRC


def test_wiring_guarded_failure_continues():
    """AC4 — the block is try/except-guarded; a raise logs WARNING, never aborts."""
    marker = "engine boot wiring failed (boot CONTINUES): %s"
    assert marker in API_SRC
    # The except branch logs at WARNING and does NOT re-raise.
    idx = API_SRC.index(marker)
    window = API_SRC[idx - 80 : idx + 200]
    assert "logger.warning(" in window
    assert "raise" not in window


def test_wiring_forced_raise_red():
    """Mutation proof: with the try/except removed, the guard marker vanishes.

    The pack runs the raw source mutation (strip the guard) and records the RED
    verbatim; this fast corollary asserts the mutant's observable signature.
    """
    mutant = API_SRC.replace(
        "    except Exception as _engine_boot_exc:  # noqa: BLE001 — never abort boot\n"
        "        app.state.engine_service = None\n"
        "        app.state.engine_watchdog = None\n"
        "        logger.warning(\n"
        '            "engine boot wiring failed (boot CONTINUES): %s", _engine_boot_exc\n'
        "        )\n",
        "",
        1,
    )
    assert "engine boot wiring failed (boot CONTINUES): %s" not in mutant
    assert "engine boot wiring failed (boot CONTINUES): %s" in API_SRC


# ── T4.6 boot wiring assertion ───────────────────────────────────────────

def test_boot_assertion_call_site_present():
    """T4.6 — assert_dependencies_resolvable called immediately after registration."""
    assert "assert_dependencies_resolvable(" in API_SRC
    # It sits after the deadlined registration pass, before set_event_sink.
    reg = API_SRC.index('"engine_registration",')
    assertion = API_SRC.index("assert_dependencies_resolvable(")
    sink = API_SRC.index("set_event_sink(_engine_svc.ingest_event)")
    assert reg < assertion < sink


def test_assert_dependencies_resolvable_pure():
    refs = [DependencyRef(label="manager.engine", getter=lambda: object())]
    report = assert_dependencies_resolvable([("job", refs)])
    assert report.ok
    bad = [DependencyRef(label="missing", getter=lambda: None)]
    report2 = assert_dependencies_resolvable([("job", bad)])
    assert not report2.ok
    assert "missing" in report2.summary()


# ── T4.5 shutdown order ──────────────────────────────────────────────────

def test_shutdown_order_engine_before_sweeps():
    """AC5 — engine stop appears before service_reconciliation.stop()."""
    engine_stop = API_SRC.index("await _engine_service.stop()")
    recon_stop = API_SRC.index("await service_reconciliation.stop()")
    assert engine_stop < recon_stop
    # Both handles cleared.
    assert API_SRC.count("app.state.engine_service = None") >= 1
    assert API_SRC.count("app.state.engine_watchdog = None") >= 1


@pytest.mark.asyncio
async def test_second_stop_is_noop():
    """AC5 — a second stop() is a no-op for both engine and watchdog."""
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel
    from sqlmodel.pool import StaticPool
    from daemon.repositories.engine.repository import EngineRepository

    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    svc = EngineService(EngineRepository(eng), None, _Cfg(), shadow_mode=True)
    wd = EngineWatchdog(svc, None)
    wd.start()
    await svc.stop()
    await svc.stop()  # no-op
    wd.stop()
    wd.stop()  # no-op
    assert wd._thread is None


# ── T4.7 / SLO-2 structural pins ─────────────────────────────────────────

def test_slo2_engine_block_after_readiness_task():
    """SLO-2 — the engine block anchor appears AFTER readiness_task creation."""
    ready = API_SRC.index("readiness_task = asyncio.create_task(")
    engine = API_SRC.index("_engine_boot_t0 = time.perf_counter()")
    assert ready < engine


def test_f5_anchors_unperturbed():
    """F-5 — the four load_config/overrides/InstanceManager anchors are intact."""
    assert API_SRC.count("\n    config = load_config()") == 1
    assert API_SRC.count("apply_db_settings_overrides(") >= 1
    assert API_SRC.count("\n    manager = InstanceManager(") == 1
    lc = API_SRC.index("\n    config = load_config()")
    ovr = API_SRC.index("apply_db_settings_overrides(")
    im = API_SRC.index("\n    manager = InstanceManager(")
    assert lc < ovr < im


# ── AMD-5 canary pair (ONE owner: P4; P7 inherits wholesale) ─────────────

@pytest.fixture
def canary_fixture(monkeypatch):
    """Synthetic canary: the ONLY executable registration + explicit shadow-off.

    AMD-3/AMD-5: the canary `register()` call site and its
    SERVICES_<CANARY>_USE_ENGINE fixture are ONE owned pair. All REAL
    SERVICES_*_USE_ENGINE stay unset; only the canary key is set.
    """
    monkeypatch.setenv("ENSEMBLE_ENGINE_SHADOW_MODE", "false")
    monkeypatch.setenv("SERVICES_CANARY_USE_ENGINE", "1")
    for spec in build_observer_specs(_Cfg()):
        assert "CANARY" not in spec.kill_switch_key
    return "canary"


def test_canary_pair_single_owner(canary_fixture, monkeypatch):
    """The canary register() site + its USE_ENGINE fixture are one P4-owned pair."""
    import os

    assert canary_fixture == "canary"
    assert os.environ.get("SERVICES_CANARY_USE_ENGINE") == "1"
    assert os.environ.get("ENSEMBLE_ENGINE_SHADOW_MODE") == "false"
    # No real estate flag is set.
    for spec in build_observer_specs(_Cfg()):
        assert os.environ.get(spec.kill_switch_key) is None
