"""The ``SERVICES_*`` continuity table (Engine Arc P1, T1.11) — DATA ONLY.

Phase 1 does **not** migrate a single module. What it ships is the mapping
that makes the migration program (Phase 2+, one gated commission per
module) a mechanical read rather than a re-census: the 12 in-scope
``ServicesConfig`` interval knobs, each paired with the continuity job id,
its VERBATIM current default, its category, its stable service tag, and
the origin module path.

Frozen design anchor: ``central-polling-engine-study/decision-report.md``
section B.2 "Config continuity" (lines 110-131) — "Existing ``SERVICES_*``
knobs map 1:1 into ``engine.jobs.<id>.interval_seconds`` with defaults
preserved — migration is NOT a config break".

**No registration. No execution.** This module imports no engine runtime
and calls nothing: importing it only builds an immutable tuple of data
rows. P2+ reads it when a module's migration commission asks "what was
this knob's cadence and default?".

DRIFT TEST (AC9): ``services_attr`` values are resolved on
``ServicesConfig.model_fields`` and each row's recorded
``default_seconds`` must EQUAL the live ``Field.default``. A knob default
that drifts under this table REDs the substrate pack — that is the whole
point of recording the number here rather than only in prose.

Anchor provenance: every field default below was re-grepped at the
pre-P1 drift-merge base ``1a74b0493`` (``daemon/config.py`` line numbers
in the row comments). Re-grep at use time — the estate drifts.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CONTINUITY_JOBS", "ContinuitySpec"]


@dataclass(frozen=True)
class ContinuitySpec:
    """One row of the ``SERVICES_*`` continuity table.

    Attributes:
        job_id: The engine job id this module's cadence becomes (the
            stable ops slug — D1.4).
        services_attr: The ``ServicesConfig`` field name whose cadence is
            being preserved. Must exist on ``ServicesConfig.model_fields``.
        default_seconds: The knob's CURRENT default, recorded verbatim so a
            drift under the table is detectable (AC9).
        category: The ``ServiceMeta.category`` the job registers under
            (starter vocabulary — D1.10).
        service_tag: The stable ops identity / metrics label (AMD-7: unique
            across the estate).
        module_info: The origin module path of the implementation the job
            will eventually own.
    """

    job_id: str
    services_attr: str
    default_seconds: int
    category: str
    service_tag: str
    module_info: str


#: The 12 in-scope ``SERVICES_*`` continuity rows (frozen design section
#: B.2). Defaults re-grepped at base ``1a74b0493``.
CONTINUITY_JOBS: tuple[ContinuitySpec, ...] = (
    ContinuitySpec(
        job_id="watch-reconcile",
        services_attr="watch_reconcile_sweep_interval_seconds",
        default_seconds=300,  # config.py:2915 (Field(default=300))
        category="reconciler",
        service_tag="watch-reconcile-sweep",
        module_info="daemon.services.watch_reconcile_sweep",
    ),
    ContinuitySpec(
        job_id="mission-strand-heal",
        services_attr="mission_strand_heal_interval_seconds",
        default_seconds=300,  # config.py:2947 (Field(default=300))
        category="reconciler",
        service_tag="mission-strand-heal",
        module_info="daemon.services.mission_strand_healer",
    ),
    ContinuitySpec(
        job_id="report-delivery-recovery",
        services_attr="report_delivery_recovery_interval_seconds",
        default_seconds=300,  # config.py:2276 (Field(default=300))
        category="recovery_sweep",
        service_tag="report-delivery-recovery",
        # Read site: daemon/services/pool_orchestrator.py:370 (inside the
        # InstanceManager.setup_worker_pool wiring block — plan section 13(a)).
        module_info="daemon.services.report_delivery_recovery",
    ),
    ContinuitySpec(
        job_id="sources-gc",
        services_attr="sources_gc_interval_seconds",
        default_seconds=3600,  # config.py:3063 (Field(default=3600))
        category="housekeeping",
        service_tag="sources-gc",
        module_info="daemon.services.sources_gc_service",
    ),
    ContinuitySpec(
        job_id="eligible-pending",
        services_attr="eligible_pending_sweep_interval_seconds",
        default_seconds=90,  # config.py:2673 (Field(default=90))
        category="recovery_sweep",
        service_tag="eligible-pending-sweep",
        module_info="daemon.services.eligible_pending_sweep",
    ),
    ContinuitySpec(
        job_id="orphan-watcher",
        services_attr="orphan_watcher_sweep_interval_seconds",
        default_seconds=90,  # config.py:2704 (Field(default=90))
        category="reconciler",
        service_tag="orphan-watcher-sweep",
        module_info="daemon.services.orphan_watcher_sweep",
    ),
    ContinuitySpec(
        job_id="job-lock",
        services_attr="job_lock_sweep_interval_seconds",
        default_seconds=90,  # config.py:2780 (Field(default=90))
        category="reconciler",
        service_tag="job-lock-sweep",
        module_info="daemon.services.job_lock_sweep",
    ),
    ContinuitySpec(
        job_id="tmp-image",
        services_attr="tmp_image_cleanup_interval_seconds",
        default_seconds=3600,  # config.py:3150 (Field(default=3600))
        category="housekeeping",
        service_tag="tmp-image-cleanup",
        module_info="daemon.services.tmp_image_cleanup_service",
    ),
    ContinuitySpec(
        job_id="plane-sync-watchdog",
        services_attr="plane_sync_watchdog_interval_seconds",
        default_seconds=300,  # config.py:2841 (Field(default=300))
        category="realtime_guard",
        service_tag="plane-sync-watchdog",
        module_info="daemon.services.plane_sync_watchdog_service",
    ),
    ContinuitySpec(
        job_id="stale-pause",
        services_attr="stale_pause_sweep_interval_seconds",
        default_seconds=300,  # config.py:2443 (Field(default=300))
        category="recovery_sweep",
        service_tag="stale-pause-sweep",
        module_info="daemon.services.stale_pause_sweep",
    ),
    ContinuitySpec(
        job_id="waiting-children-watchdog",
        services_attr="waiting_children_watchdog_interval_seconds",
        default_seconds=3600,  # config.py:2628 (Field(default=3600))
        category="realtime_guard",
        service_tag="waiting-children-watchdog",
        module_info="daemon.services.waiting_children_watchdog",
    ),
    ContinuitySpec(
        job_id="drift-reconcile",
        services_attr="drift_reconcile_interval_seconds",
        default_seconds=300,  # config.py:2255 (Field(default=300))
        category="reconciler",
        service_tag="drift-reconcile",
        # The loop lives on the daemon module today (def
        # _periodic_drift_reconcile_loop, invoked from the lifespan) —
        # recorded as-is so the migration commission knows what it inherits.
        module_info="daemon.api._periodic_drift_reconcile_loop",
    ),
)
