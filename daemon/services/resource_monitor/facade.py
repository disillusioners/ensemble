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
    ) -> None:
        self.sampler = sampler
        self.flusher = flusher
        self.ring = ring
        self.retention = retention
        self.enabled = enabled
        self.boot_error = boot_error
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
        """
        if not self.enabled:
            return
        try:
            self.sampler.start()
        except Exception as exc:
            self.enabled = False
            self.boot_error = f"sampler start failed: {exc}"
            logger.error(
                "resource-monitor DISABLED: %s", self.boot_error, exc_info=True
            )
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
        """
        self.stop_event.set()
        if self.enabled:
            self.sampler.stop()
            self.flusher.stop()

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


def build_resource_monitor(
    *,
    config_resource_monitor,  # daemon.config.ResourceMonitorConfig
    engine: Engine,
    proc_root: Path | str = "/proc",
    cgroup_root: Path | str = "/sys/fs/cgroup",
    child_pids_provider: Optional[Callable[[], list[int]]] = None,
) -> ResourceMonitor:
    """Construct the facade from the typed config + engine.

    Called from ``daemon/api.py`` lifespan beside the stream-watchdog
    block. NEVER raises — a build failure degrades the subsystem
    (``enabled=False`` + boot_error) without crashing the lifespan.
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
        sampler = Sampler(
            sample_seconds=cfg.sample_seconds,
            self_cpu_budget_pct=cfg.self_cpu_budget_pct,
            self_rss_budget_mb=cfg.self_rss_budget_mb,
            detail_level=cfg.detail_level,
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            ring=ring,
            child_pids_provider=child_pids_provider,
        )
        flusher = Flusher(
            engine=engine,
            raw_ring=ring,
            flush_batch_seconds=cfg.flush_batch_seconds,
            retention=retention,
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
]