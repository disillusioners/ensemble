"""Resource monitor facade — the lifespan-managed entry point.

Composes the Sampler (T5) + Flusher (T6) + Ring + Counters into
a single subsystem whose lifecycle is owned by the daemon
lifespan (T7). The /api/resources/live + /history + /self-cost
endpoints (T8) consume this facade through ``request.app.state
.resource_monitor``.

D1 / D10 posture: BOTH threads are ``threading.Thread(daemon=
True)`` — NEVER asyncio tasks. Boot failure of either thread
DEGRADES the subsystem (the facade returns ``enabled=False``)
without crashing the lifespan.

The facade never raises on construction. Any structural error
during ``start()`` is caught and degraded.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.engine import Engine

from .flusher import Flusher, FlusherCounters
from .ring import TierRing, default_ring_sizes
from .rollup import RetentionPolicy
from .sampler import Sampler, SamplerCounters

logger = logging.getLogger(__name__)


class ResourceMonitor:
    """Facade — Sampler + Flusher + Ring + Counters.

    Constructed via :func:`build_resource_monitor` in the lifespan
    (T7). The three P1 endpoints (T8) read the facade's attributes
    for live data; the DB-backed reads (durably flushed rows) hit
    the engine via the router-side repos.

    P4: ``anomaly`` carries the anomaly engine (:mod:`daemon.services
    .resource_anomaly`) or ``None`` when the anomaly layer is
    disabled (kill-switch) — the router's ``/anomalies`` family and
    the traceback ``findings[]`` fill read it; ``None`` ⇒ empty
    lists (D11 degrade shape).
    """

    def __init__(
        self,
        *,
        sampler: Sampler,
        flusher: Flusher,
        ring: TierRing,
        retention: RetentionPolicy,
        enabled: bool = True,
        boot_error: Optional[str] = None,
        anomaly: Optional[Any] = None,
    ) -> None:
        self.sampler = sampler
        self.flusher = flusher
        self.ring = ring
        self.retention = retention
        self.enabled = enabled
        self.boot_error = boot_error
        self.anomaly = anomaly
        self.stop_event = threading.Event()

    # Convenience accessors (the router reads these).
    @property
    def sampler_counters(self) -> SamplerCounters:
        return self.sampler.counters

    @property
    def flusher_counters(self) -> FlusherCounters:
        return self.flusher.counters

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the sampler + flusher threads. If either start
        fails, degrade — ``enabled=False`` + a one-shot boot log
        (D10). NEVER raises.

        P2 T10: enabling the monitor ALSO arms the process-wide
        charge registry (``charge.set_enabled(True)``) — the seams
        are identity no-ops until this flip (lane-A integration
        TODO closed here, per the dispatch brief). ``stop()``
        disarms symmetrically.
        """
        if not self.enabled:
            return
        try:
            from . import charge as _charge

            _charge.set_enabled(True)
        except Exception:  # pragma: no cover — import is intra-package
            logger.warning(
                "resource-monitor: charge registry enable failed",
                exc_info=True,
            )
        try:
            self.sampler.start()
        except Exception as exc:
            self.enabled = False
            self.boot_error = f"sampler start failed: {exc}"
            logger.error(
                "resource-monitor DISABLED: %s", self.boot_error, exc_info=True
            )
            # Symmetric disarm — the seams must not charge while the
            # monitor is down.
            try:
                from . import charge as _charge

                _charge.set_enabled(False)
            except Exception:
                pass
            return
        try:
            self.flusher.start()
        except Exception as exc:
            self.enabled = False
            self.boot_error = f"flusher start failed: {exc}"
            logger.error(
                "resource-monitor DISABLED: %s", self.boot_error, exc_info=True
            )
            # Best-effort: stop the sampler too.
            self.sampler.stop()
            try:
                from . import charge as _charge

                _charge.set_enabled(False)
            except Exception:
                pass
            return
        logger.info(
            "resource-monitor started: sample_s=%d flush_s=%d retention=%s",
            self.sampler.sample_seconds,
            self.flusher.flush_batch_seconds,
            (
                self.retention.raw_minutes,
                self.retention.minute_hours,
                self.retention.hour_days,
            ),
        )

    def stop(self) -> None:
        """Signal stop on both. Non-blocking — join happens at the
        lifespan level on a detached thread (D10).

        P2 T10: disarms the charge registry (``set_enabled(False)``)
        — the seams return to identity no-ops the moment the monitor
        stops (T1/AC1 contract).
        """
        self.stop_event.set()
        if self.enabled:
            self.sampler.stop()
            self.flusher.stop()
        try:
            from . import charge as _charge

            _charge.set_enabled(False)
        except Exception:
            pass

    def join(self, timeout: float = 5.0) -> None:
        """Join both threads with a bounded timeout.

        NEVER called on the event loop — the caller (lifespan
        shutdown) runs this on a detached thread (D10).
        """
        for th in (self.sampler._thread, self.flusher._thread):
            if th is not None and th.is_alive():
                th.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self.sampler.is_alive() or self.flusher.is_alive()


def default_workers_provider() -> Optional[int]:
    """The OBSERVED live worker count — started ``Worker`` threads.

    Counts alive ``worker_pool.Worker`` instances across BOTH pools
    (default + chat lane) via a thread walk — no manager reference is
    needed, so the monitor stays decoupled from the lifespan scope.
    Best-effort: any failure ⇒ ``None`` (the anomaly stratum degrades
    to its configured-limit dimension alone; the pass never raises).
    """
    try:
        from daemon.services.worker_pool import Worker

        return sum(
            1
            for t in threading.enumerate()
            if isinstance(t, Worker) and t.is_alive()
        )
    except Exception:
        return None


def build_resource_monitor(
    *,
    config_resource_monitor,  # daemon.config.ResourceMonitorConfig
    engine: Engine,
    proc_root: Path | str = "/proc",
    cgroup_root: Path | str = "/sys/fs/cgroup",
    child_pids_provider: Optional[Callable[[], list[int]]] = None,
    graph_cache_provider: Optional[Callable[[], object]] = None,
    graph_tasks_provider: Optional[Callable[[], object]] = None,
    state_message_counts_provider: Optional[Callable[[], object]] = None,
    workers_provider: Optional[Callable[[], Optional[int]]] = None,
) -> ResourceMonitor:
    """Construct the facade from the typed config + engine.

    Called from ``daemon/api.py`` lifespan beside the stream-watchdog
    block. NEVER raises — a build failure degrades the subsystem
    (``enabled=False`` + boot_error) without crashing the lifespan.

    P2 T10 provider params (all optional; wired from the api.py
    lifespan with cheap lambdas over the manager):

      * ``graph_cache_provider``        — ``manager.instances`` (the
        deep-scan mapping + ``graph_cache_present`` membership).
      * ``graph_tasks_provider``        — ``manager._graph_tasks``
        (COUNT ONLY: len() + membership; no Task sizing, D5).
      * ``state_message_counts_provider`` — the messaging service's
        ``_last_seen_message_count`` map (cheap dict .get — the T5
        ``all_state_messages_len`` source, no deep walk).
    """
    cfg = config_resource_monitor

    try:
        retention = RetentionPolicy.from_config(
            cfg.retention_raw_minutes,
            cfg.retention_minute_hours,
            cfg.retention_hour_days,
        )
    except Exception as exc:
        # RetentionPolicy.from_config clamps + should not raise; if
        # it does, degrade with a tiny stub so the endpoints still
        # respond (D11).
        logger.error(
            "resource-monitor retention build failed: %s", exc, exc_info=True
        )
        retention = RetentionPolicy(
            raw_minutes=2880, minute_hours=336, hour_days=180
        )

    if not cfg.enabled:
        logger.info(
            "resource-monitor disabled by kill-switch "
            "(RESOURCE_MONITOR_ENABLED=false)"
        )
        # Build a live-but-not-started facade so the router can still
        # serve empty shapes (D11).
        try:
            ring = TierRing(tier="raw", maxlen=16)
            sampler = Sampler(
                sample_seconds=cfg.sample_seconds,
                self_cpu_budget_pct=cfg.self_cpu_budget_pct,
                self_rss_budget_mb=cfg.self_rss_budget_mb,
                detail_level=cfg.detail_level,
                proc_root=proc_root,
                cgroup_root=cgroup_root,
                ring=ring,
            )
            flusher = Flusher(
                engine=engine,
                raw_ring=ring,
                flush_batch_seconds=cfg.flush_batch_seconds,
                retention=retention,
            )
        except Exception as exc:
            logger.error(
                "resource-monitor disabled-facade build failed: %s",
                exc,
                exc_info=True,
            )
            return _disabled_stub(engine, retention, f"disabled build failed: {exc}")
        return ResourceMonitor(
            sampler=sampler,
            flusher=flusher,
            ring=ring,
            retention=retention,
            enabled=False,
            boot_error="disabled by config",
        )

    try:
        raw_maxlen, _m, _h = default_ring_sizes(
            sample_seconds=cfg.sample_seconds,
            retention_raw_minutes=cfg.retention_raw_minutes,
            retention_minute_hours=cfg.retention_minute_hours,
            retention_hour_days=cfg.retention_hour_days,
        )
        ring = TierRing(tier="raw", maxlen=raw_maxlen)
        # P2 T10 — bounded instance-row ring (raw tier, no roll-down;
        # sized for several flush windows of handoff headroom; the DB
        # is the durable store).
        instance_ring = TierRing(
            tier="instance",
            maxlen=max(2, 8 * (min(int(getattr(cfg, "instance_cap", 64)), 64) + 1)),
        )
        sampler = Sampler(
            sample_seconds=cfg.sample_seconds,
            self_cpu_budget_pct=cfg.self_cpu_budget_pct,
            self_rss_budget_mb=cfg.self_rss_budget_mb,
            detail_level=cfg.detail_level,
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            ring=ring,
            child_pids_provider=child_pids_provider,
            instance_ring=instance_ring,
            instance_cap=int(getattr(cfg, "instance_cap", 64)),
            graph_cache_provider=graph_cache_provider,
            graph_tasks_provider=graph_tasks_provider,
            state_message_counts_provider=state_message_counts_provider,
        )
        # P4 — the anomaly engine + scheduler gate. Built INSIDE the
        # same try (a build failure degrades to anomaly=None with a
        # log — sampling/flushing are untouched). The configured-limit
        # provider reads the managed setting ``limits.llm_concurrency``
        # read-only via the resolver ladder (phase4-plan decision 5).
        anomaly_engine = None
        anomaly_gate = None
        if bool(getattr(cfg, "anomaly_enabled", True)):
            try:
                from daemon.services.resource_anomaly import (
                    AnomalyEngine,
                    AnomalyScheduler,
                    make_configured_limit_provider,
                )

                anomaly_engine = AnomalyEngine(
                    mad_threshold=float(
                        getattr(cfg, "anomaly_mad_threshold", 6.0)
                    ),
                    min_baseline_points=int(
                        getattr(cfg, "anomaly_min_baseline_points", 12)
                    ),
                    workers_provider=(
                        workers_provider or default_workers_provider
                    ),
                    configured_limit_provider=(
                        make_configured_limit_provider(engine)
                    ),
                    db_engine=engine,
                )
                anomaly_gate = AnomalyScheduler(
                    anomaly_engine,
                    interval_s=int(
                        getattr(cfg, "anomaly_interval_s", 600)
                    ),
                )
                flusher_anomaly_gate: Optional[
                    Callable[[list[dict], list[dict]], None]
                ] = anomaly_gate
            except Exception as exc:
                logger.error(
                    "resource-monitor anomaly layer DISABLED (build "
                    "failed): %s",
                    exc,
                    exc_info=True,
                )
                anomaly_engine = None
                flusher_anomaly_gate = None
        else:
            flusher_anomaly_gate = None

        flusher = Flusher(
            engine=engine,
            raw_ring=ring,
            flush_batch_seconds=cfg.flush_batch_seconds,
            retention=retention,
            instance_ring=instance_ring,
            anomaly_gate=flusher_anomaly_gate,
        )
    except Exception as exc:
        logger.error(
            "resource-monitor DISABLED: build failed: %s", exc, exc_info=True
        )
        return _disabled_stub(engine, retention, f"build failed: {exc}")

    return ResourceMonitor(
        sampler=sampler,
        flusher=flusher,
        ring=ring,
        retention=retention,
        enabled=True,
        anomaly=anomaly_engine,
    )


def _disabled_stub(
    engine: Engine,
    retention: RetentionPolicy,
    reason: str,
) -> ResourceMonitor:
    """Build a minimal disabled facade (endpoints return empty shapes)."""
    ring = TierRing(tier="raw", maxlen=2)
    sampler = Sampler(
        sample_seconds=30,
        self_cpu_budget_pct=1.0,
        self_rss_budget_mb=50,
        detail_level="auto",
        ring=ring,
    )
    flusher = Flusher(
        engine=engine,
        raw_ring=ring,
        flush_batch_seconds=60,
        retention=retention,
    )
    return ResourceMonitor(
        sampler=sampler,
        flusher=flusher,
        ring=ring,
        retention=retention,
        enabled=False,
        boot_error=reason,
    )


__all__ = [
    "ResourceMonitor",
    "build_resource_monitor",
    "default_workers_provider",
]