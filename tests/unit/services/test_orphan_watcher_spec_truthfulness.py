"""The HONESTY fixes for the migrated spec (Phase-2 pilot, 2026-11-23).

The landed pilot registration stamped EVERY spec with a blanket
``"P4 observer spec — execute_fn withheld"`` description and
``metadata={"observer": True, "phase": "P4"}``. That is FALSE for the
migrated spec: the orphan-watcher job IS executed by the engine when the
ownership conjunct holds. An operator reading the ``engine_jobs`` row (or
the metrics labels built from it) would read a lie.

WHAT IS PINNED HERE
-------------------
1. The migrated spec's description states BOTH postures (dual-path), and
   its metadata carries the ownership contract an operator needs.
2. The OTHER 11 specs are UNTOUCHED — the blanket observer description is
   still exactly what it was (the fix is scoped, not a rewrite).
3. The metadata's ``engine_flag`` equals the spec's ``kill_switch_key`` —
   they are derived from the same expression, so they cannot drift.
4. **DEAD-DETECTION SAFETY (the CS-9 carve-out).** The observer dead rule
   keys off ``job_id not in engine._executors`` — NOT off this metadata.
   A truthful description must therefore NOT be able to create a
   false-dead signal on a live lane. Pinned at the source level AND by the
   live behaviour of ``_compute_dead``.
5. The boot posture line carries the executable-specs count, so the
   ceremony's evidence line cannot say "observer specs (execute_fn
   withheld)" while the engine is actually executing the sweep.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from daemon.services.engine.boot import build_observer_specs
from daemon.services.engine.metrics import EngineMetrics
from daemon.services.orphan_watcher_sweep import (
    ORPHAN_WATCHER_ENGINE_FLAG,
    ORPHAN_WATCHER_ENGINE_JOB_ID,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
API_SRC = (REPO_ROOT / "daemon" / "api.py").read_text()
METRICS_SRC = (REPO_ROOT / "daemon" / "services" / "engine" / "metrics.py").read_text()


def _specs() -> tuple[Any, ...]:
    class _Services:
        orphan_watcher_sweep_interval_seconds = 90
        eligible_pending_sweep_interval_seconds = 60
        job_lock_sweep_interval_seconds = 90
        tmp_image_cleanup_interval_seconds = 300
        plane_sync_watchdog_interval_seconds = 60
        stale_pause_sweep_interval_seconds = 90
        waiting_children_watchdog_interval_seconds = 60
        report_delivery_recovery_interval_seconds = 60
        sources_gc_interval_seconds = 600
        drift_reconcile_interval_seconds = 300
        mission_strand_heal_interval_seconds = 300
        watch_reconcile_interval_seconds = 300

    cfg = SimpleNamespace(
        services=_Services(),
        engine=SimpleNamespace(enabled=True, shadow_mode=True),
    )
    return build_observer_specs(cfg)


def _orphan_spec() -> Any:
    return next(s for s in _specs() if s.id == ORPHAN_WATCHER_ENGINE_JOB_ID)


def test_the_migrated_spec_description_is_truthful():
    """The migrated spec must not claim it never executes."""
    spec = _orphan_spec()
    description = spec.meta.description or ""
    assert "execute_fn withheld" not in description, (
        "the migrated spec still carries the blanket observer description — "
        "it IS executed by the engine when the ownership conjunct holds"
    )
    assert "dual-path" in description
    # Both postures must be stated, so the row is readable in either.
    assert "legacy" in description.lower()
    assert "shadow" in description.lower()


def test_the_migrated_spec_metadata_carries_the_ownership_contract():
    spec = _orphan_spec()
    metadata = dict(spec.meta.metadata or {})
    assert metadata.get("dual_path") is True
    assert metadata.get("pilot") == "orphan-watcher-engine"
    assert metadata.get("engine_flag") == ORPHAN_WATCHER_ENGINE_FLAG
    assert metadata.get("ownership_conjunct")
    assert metadata.get("legacy_owner")
    # The self-describing registration fields ride the spec (Emperor ruling).
    assert spec.meta.service_tag == "orphan-watcher-sweep"
    assert spec.meta.category == "reconciler"
    assert spec.meta.module_info == "daemon.services.orphan_watcher_sweep"


def test_the_engine_flag_in_the_metadata_cannot_drift_from_the_kill_switch_key():
    """Both are derived from the estate row — the pin proves they agree."""
    spec = _orphan_spec()
    assert spec.meta.metadata["engine_flag"] == spec.kill_switch_key
    assert spec.kill_switch_key == ORPHAN_WATCHER_ENGINE_FLAG


def test_the_other_eleven_specs_are_untouched():
    """The fix is SCOPED: the other 11 keep the blanket observer shape."""
    others = [s for s in _specs() if s.id != ORPHAN_WATCHER_ENGINE_JOB_ID]
    assert len(others) == 11
    for spec in others:
        assert spec.meta.description == (
            "P4 observer spec — execute_fn withheld; "
            "registration-for-observation, not migration"
        )
        assert dict(spec.meta.metadata or {}) == {"observer": True, "phase": "P4"}


def test_dead_detection_does_not_read_the_metadata():
    """CS-9 SAFETY: the observer dead rule keys off the EXECUTORS dict.

    The carve-out is ``job_id not in engine._executors`` — a runtime fact.
    If it had keyed off ``metadata["observer"]``, the truthful metadata
    would have flipped a live lane to ``dead=true`` (a false dead).
    """
    assert 'is_observer = job_id not in self._executors' in METRICS_SRC
    assert "metadata" not in METRICS_SRC.split("def _compute_dead")[1].split(
        "def "
    )[0]


def test_dead_detection_behaviour_is_unchanged_by_the_metadata():
    """BEHAVIOUR (not just source): an attached executor ⇒ the non-observer rule."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    spec = _orphan_spec()
    fresh_ts = (now - timedelta(seconds=10)).isoformat()
    # The metadata is IDENTICAL in both cases — only the EXECUTORS dict differs.
    assert dict(spec.meta.metadata or {}).get("observer") is True

    without_executor = EngineMetrics(SimpleNamespace(_executors={}))
    with_executor = EngineMetrics(
        SimpleNamespace(_executors={ORPHAN_WATCHER_ENGINE_JOB_ID: lambda: None})
    )
    # No executor ⇒ the CS-9 observer carve-out: fresh shadow evidence ⇒ alive.
    alive_cache = {"shadow_evidence_ts": [fresh_ts]}
    assert (
        without_executor._compute_dead(
            ORPHAN_WATCHER_ENGINE_JOB_ID, alive_cache, spec, now, 90
        )
        is False
    )
    # Executor attached ⇒ the RUN-based rule: a recent last_run_at ⇒ alive.
    run_cache = {"shadow_evidence_ts": [fresh_ts], "last_run_at": fresh_ts}
    assert (
        with_executor._compute_dead(
            ORPHAN_WATCHER_ENGINE_JOB_ID, run_cache, spec, now, 90
        )
        is False
    )


def test_the_boot_posture_line_reports_the_executable_specs_count():
    """The ceremony's evidence line must not understate the posture."""
    assert 'executable_specs=%s;' in API_SRC
    # The pinned substrings other packs assert on must survive the addition.
    assert '"engine boot posture: enabled=%s shadow=%s registrations=%d' in API_SRC
    assert "specs (execute_fn withheld); use_engine_flags=%s;" in API_SRC
