"""The cadence-driven trend sampler (plan task 2.6).

ONE tick = stage A + stage B:

* **stage A** — run the registered PULL ADAPTERS.  An adapter READS an
  existing producer's already-computed value and writes it into the
  registry.  The seam ships EMPTY in this phase — Phase 4 plugs the
  families in.  An adapter that raises is LOGGED and the tick
  CONTINUES: a broken adapter must never kill the thread.
* **stage B** — read the published snapshot, IDENTITY-DEDUPE it against
  the previous tick (per metric) and append the changed series to the
  rings.

The cadence is the reused managed key
``limits.metrics_sampler_interval_s`` (default 10 s, boot-applied).  It
is read ONCE at construction — a boot-applied key that is read live
would produce two cadences in one process.

The reported ``window.interval_s`` is the OBSERVED effective spacing
``max(sampler, publisher)`` (AM-15) — never the configured cadence.

FAIL-SAFE POSTURE: an adapter error, a registry error or a ring error
is logged and swallowed.  The thread NEVER propagates into the daemon
loop and NEVER dies.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable, Sequence

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.ring import TrendRingStore, series_key

logger = logging.getLogger(__name__)

#: A pull adapter is a callable that reads a producer's already
#: computed value and writes it into the registry.  It receives the
#: sampler (so it can reach the registry) and returns nothing.
PullAdapter = Callable[["MetricsSampler"], None]


def _default_adapters() -> tuple[PullAdapter, ...]:
    """The P4 pull adapters (the wiring module's registry).

    The import is LAZY and inside a guard: the wiring package imports
    the metrics substrate, so a module-level import here would be a
    cycle.  A wiring import failure degrades to "no adapters" — the
    sampler still runs, the ring stays empty, and the daemon continues.
    """
    try:
        from daemon.services.metrics_query.wiring import default_adapters

        return tuple(default_adapters())
    except Exception:  # noqa: BLE001 — the ring degrades, the tick survives
        logger.warning(
            "metrics trend sampler: the pull-adapter wiring is unavailable",
            exc_info=True,
        )
        return ()


class MetricsSampler:
    """The sampler thread — it fills the trend rings."""

    def __init__(
        self,
        *,
        ring: TrendRingStore,
        interval_s: float = 10.0,
        adapters: Iterable[PullAdapter] | None = None,
        metrics_provider: Callable[[], list[Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        window_s: int | None = None,
    ) -> None:
        self._ring = ring
        self._interval_s = float(interval_s)
        self._adapters: list[PullAdapter] = list(
            _default_adapters() if adapters is None else adapters
        )
        self._metrics_provider = metrics_provider or cm.registered_metrics
        self._clock = clock
        self._wall_clock = wall_clock
        self._window_s = int(window_s) if window_s else ring.window_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_snapshots: dict[str, Any] = {}
        self._idle_counts: dict[str, int] = {}
        self._tick_count = 0

    # ── introspection ───────────────────────────────────────────────────

    @property
    def interval_s(self) -> float:
        """The BOOT-APPLIED tick cadence (read once at construction)."""
        return self._interval_s

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def idle_counts(self) -> dict[str, int]:
        """The per-metric consecutive-no-publish counters (the GC key)."""
        return dict(self._idle_counts)

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the sampler thread (a DAEMON thread)."""
        if self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="metrics-trend-sampler", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> threading.Thread | None:
        """Stop the sampler thread and JOIN it (bounded).

        THE JOIN IS THE POINT (A-2): a detached stop leaves the tick in
        flight running — and that tick may still hold a one-shot DB
        connection (the DB adapters open and close one PER TICK, so the
        window is short but real) after the daemon has asked the sampler
        to stop.  The join closes that window.

        The join is BOUNDED (the tick cadence, at least 5 s) so a stuck
        adapter can never wedge the daemon's shutdown; a thread that
        outlives the bound is LOGGED, not waited on forever.
        """
        thread = self._thread
        self._stop.set()
        if thread is not None and thread.is_alive():
            timeout = max(5.0, float(self._interval_s))
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(
                    "metrics trend sampler: the thread did not stop within "
                    "%.1fs — the shutdown continues (the tick may still be "
                    "finishing)",
                    timeout,
                )
        self._thread = None
        return thread

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover — the tick never kills the thread
                logger.warning("metrics trend sampler: tick failed", exc_info=True)
            self._stop.wait(self._interval_s)

    # ── the tick ────────────────────────────────────────────────────────

    def tick(self) -> int:
        """Run ONE tick and return the number of samples appended."""
        appended = self._run_adapters()
        appended += self._collect()
        self._tick_count += 1
        return appended

    def _metrics(self) -> list[Any]:
        """The registered metric objects (the allowlisted registry read)."""
        try:
            return list(self._metrics_provider())
        except Exception:  # pragma: no cover — a broken registry never kills the tick
            logger.warning("metrics trend sampler: registry read failed", exc_info=True)
            return []

    def _registry_native_series_count(self) -> int:
        """The REGISTRY-NATIVE label-combination count (AM-10).

        This is the divisor of the ring depth — the count of the LIVE
        label combinations, NOT the catalog-convention count.  A
        catalog-count divisor mis-scales the ring (a 720-deep count
        bound would retain 3 h, not 2 h).
        """
        total = 0
        for metric in self._metrics():
            try:
                snapshot = metric.published_snapshot()
            except Exception:  # pragma: no cover
                continue
            if snapshot:
                total += len(snapshot)
        return total

    def _run_adapters(self) -> int:
        """Stage A — the pull adapters (each isolated from the others)."""
        for adapter in self._adapters:
            try:
                adapter(self)
            except Exception:  # noqa: BLE001 — a broken adapter never kills the tick
                logger.warning(
                    "metrics trend sampler: pull adapter failed (%s)",
                    getattr(adapter, "__name__", adapter),
                    exc_info=True,
                )
        return 0

    def _collect(self) -> int:
        """Stage B — read the published snapshots and fill the rings."""
        appended = 0
        now = int(self._wall_clock())
        seen: set[str] = set()
        # The depth follows the REGISTRY-NATIVE series count (AM-10) —
        # it recomputes when a registration event changes the count.
        self._ring.set_series_count(self._registry_native_series_count())
        for metric in self._metrics():
            name = metric.name
            seen.add(name)
            snapshot = metric.published_snapshot()
            if not snapshot:
                # No published entry — count the idle streak.  A metric
                # that HAS a published entry never reaches the GC
                # threshold (AM-13): an idle-but-live family is never
                # dropped.
                self._idle_counts[name] = self._idle_counts.get(name, 0) + 1
                continue
            self._idle_counts.pop(name, None)
            if self._last_snapshots.get(name) is snapshot:
                # IDENTITY DEDUPE — the same published snapshot means
                # nothing changed since the previous tick.
                continue
            self._last_snapshots[name] = snapshot
            for label_key, series in snapshot.items():
                key = series_key(name, label_key)
                if getattr(metric, "kind", "") == "histogram":
                    self._ring.append_histogram(
                        key,
                        now,
                        int(series.get("count", 0)),
                        float(series.get("sum", 0.0)),
                        series.get("buckets") or (),
                    )
                else:
                    self._ring.append(key, now, float(series.get("value", 0.0)))
                appended += 1
        # A metric that vanished from the registry ages out like any
        # other idle metric.
        for name in list(self._last_snapshots):
            if name not in seen:
                self._idle_counts[name] = self._idle_counts.get(name, 0) + 1
        self._ring.evict_before(now - self._window_s)
        self._ring.gc(self._idle_counts)
        return appended


__all__ = ["MetricsSampler", "PullAdapter"]
