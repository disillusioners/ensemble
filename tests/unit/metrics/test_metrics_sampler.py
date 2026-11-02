"""The trend sampler pack (registered pack ``metrics_sampler``).

The sampler is the component that can wedge a daemon, so its pins are
the failure-mode pins:

* a RAISING adapter is logged and the tick CONTINUES — a broken
  adapter never kills the thread (P2-M5);
* the identity-dedupe means an unchanged published snapshot appends
  NOTHING (P2-M6);
* the cadence is read ONCE at construction (the managed key is
  boot-applied);
* the thread is a DAEMON thread and it stops cleanly;
* a metric that stops publishing is garbage-collected after three
  consecutive idle samplings — while an idle-but-LIVE family (a metric
  that still HAS a published entry) is never dropped.
"""

from __future__ import annotations

import threading
import time

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.ring import TrendRingStore
from daemon.services.metrics_query.sampler import MetricsSampler


def _metric(name: str, kind: str = "gauge", labels=None) -> cm.Metric:
    metric = cm.Metric(name, kind, (), None, "", None, False)
    metric.set(1.0, labels)
    metric.publish(force=True)
    return metric


class _Clock:
    """A deterministic clock (the sampler's monotonic + wall clocks)."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return self.now


class TestAdapterIsolation:
    def test_a_raising_adapter_does_not_stop_the_tick(self) -> None:
        """P2-M5 — the tick continues after an adapter raises."""
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_adapter_gauge")
        ran: list[str] = []

        def _boom(_sampler) -> None:
            raise RuntimeError("adapter exploded")

        def _ok(_sampler) -> None:
            ran.append("ok")

        sampler = MetricsSampler(
            ring=ring,
            adapters=[_boom, _ok],
            metrics_provider=lambda: [metric],
        )
        appended = sampler.tick()
        assert ran == ["ok"], "the second adapter must still run"
        assert appended == 1, "the tick must still collect the published snapshot"

    def test_a_raising_adapter_never_escapes_the_tick(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)

        def _boom(_sampler) -> None:
            raise RuntimeError("adapter exploded")

        sampler = MetricsSampler(
            ring=ring, adapters=[_boom], metrics_provider=lambda: []
        )
        sampler.tick()  # must not raise

    def test_the_thread_survives_a_raising_adapter(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)

        def _boom(_sampler) -> None:
            raise RuntimeError("adapter exploded")

        sampler = MetricsSampler(
            ring=ring, adapters=[_boom], metrics_provider=lambda: [], interval_s=0.01
        )
        assert sampler.start() is True
        time.sleep(0.05)
        assert sampler.running is True
        sampler.stop()


class TestIdentityDedupe:
    def test_an_unchanged_snapshot_appends_nothing(self) -> None:
        """P2-M6 — the dedupe is per metric and per published snapshot."""
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_dedupe_gauge")
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        assert sampler.tick() == 1
        assert sampler.tick() == 0
        assert sampler.tick() == 0
        assert ring.total_points() == 1

    def test_a_new_publish_appends_again(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_republish_gauge")
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        assert sampler.tick() == 1
        metric.set(2.0)
        metric.publish(force=True)
        assert sampler.tick() == 1
        assert ring.total_points() == 2

    def test_a_changed_value_without_a_publish_appends_nothing(self) -> None:
        """The ring reads PUBLISHED state — an unpublished change is invisible."""
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_unpublished_gauge")
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        assert sampler.tick() == 1
        metric.set(99.0)  # not published
        assert sampler.tick() == 0


class TestLifecycle:
    def test_the_cadence_is_read_once_at_construction(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        sampler = MetricsSampler(
            ring=ring, interval_s=17.0, metrics_provider=lambda: []
        )
        assert sampler.interval_s == 17.0

    def test_the_thread_is_a_daemon(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        sampler = MetricsSampler(
            ring=ring, interval_s=0.01, metrics_provider=lambda: []
        )
        assert sampler.start() is True
        assert sampler.running is True
        thread = sampler.stop()
        assert thread is not None
        assert thread.daemon is True
        thread.join(2.0)

    def test_starting_twice_is_a_no_op(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        sampler = MetricsSampler(
            ring=ring, interval_s=0.01, metrics_provider=lambda: []
        )
        assert sampler.start() is True
        assert sampler.start() is False
        thread = sampler.stop()
        if thread is not None:
            thread.join(2.0)

    def test_stop_waits_for_the_in_flight_tick(self) -> None:
        """A-2 — ``stop()`` JOINS the thread.

        A detached stop leaves the tick in flight running — and that
        tick may still hold a one-shot DB connection after the daemon
        has asked the sampler to stop.  The pin is BEHAVIOURAL: while a
        tick is in flight, ``stop()`` must NOT return.
        """
        ring = TrendRingStore(effective_interval_s=15.0)
        entered = threading.Event()
        release = threading.Event()

        def _blocking_provider():
            entered.set()
            release.wait(5.0)
            return []

        sampler = MetricsSampler(
            ring=ring, interval_s=0.01, metrics_provider=_blocking_provider
        )
        assert sampler.start() is True
        assert entered.wait(2.0), "the tick must have started"
        stopped = threading.Event()

        def _stop() -> None:
            sampler.stop()
            stopped.set()

        stopper = threading.Thread(target=_stop, daemon=True)
        stopper.start()
        try:
            assert not stopped.wait(0.3), (
                "stop() returned while a tick was still in flight — the "
                "thread must be JOINED"
            )
        finally:
            release.set()
        assert stopped.wait(5.0), "stop() must return once the tick is finished"

    def test_the_tick_count_advances(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [])
        assert sampler.tick_count == 0
        sampler.tick()
        assert sampler.tick_count == 1


class TestGarbageCollection:
    def test_a_metric_that_stops_publishing_is_dropped_after_three_ticks(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_gc_gauge")
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        sampler.tick()
        assert ring.total_points() == 1
        # The metric stops publishing: its published snapshot is cleared.
        metric.reset()
        sampler.tick()
        sampler.tick()
        assert ring.total_points() == 1, "not yet — the GC threshold is three"
        sampler.tick()
        assert ring.total_points() == 0

    def test_an_idle_but_live_family_is_never_dropped(self) -> None:
        """AM-13 — a metric that still HAS a published entry stays."""
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = _metric("ensemble_sampler_idle_live_gauge")
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        sampler.tick()
        for _ in range(5):
            sampler.tick()
        assert ring.total_points() == 1


class TestHistogramCollection:
    def test_a_histogram_series_is_collected(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        metric = cm.Metric(
            "ensemble_sampler_histogram", "histogram", (), None, "", (1.0, 2.0), False
        )
        metric.observe(1.5)
        metric.publish(force=True)
        sampler = MetricsSampler(ring=ring, metrics_provider=lambda: [metric])
        assert sampler.tick() == 1
        points = ring.histogram_points("ensemble_sampler_histogram|")
        assert points and points[0]["count"] == 1


class TestAgeEviction:
    def test_the_window_tracks_the_samplers_wall_clock(self) -> None:
        ring = TrendRingStore(window_s=100, effective_interval_s=15.0)
        clock = _Clock()
        metric = _metric("ensemble_sampler_evict_gauge")
        sampler = MetricsSampler(
            ring=ring,
            metrics_provider=lambda: [metric],
            clock=clock.monotonic,
            wall_clock=clock.wall,
        )
        sampler.tick()
        assert ring.total_points() == 1
        clock.now = 1_500.0
        metric.set(2.0)
        metric.publish(force=True)
        sampler.tick()
        assert ring.total_points() == 1, "the old sample is age-evicted"


class TestTheRegistryNativeDivisor:
    """AM-10 — the depth's divisor is the REGISTRY-NATIVE count."""

    def test_the_sampler_feeds_the_live_label_combination_count(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        first = _metric("ensemble_sampler_divisor_a")
        second = cm.Metric(
            "ensemble_sampler_divisor_b", "gauge", ("k",), None, "", None, False
        )
        second.set(1, {"k": "x"})
        second.set(2, {"k": "y"})
        second.publish(force=True)
        sampler = MetricsSampler(
            ring=ring, metrics_provider=lambda: [first, second]
        )
        sampler.tick()
        # 1 (unlabeled) + 2 (labeled) live label combinations.
        assert ring.declared_series_count() == 3

    def test_the_declared_count_drives_the_depth(self) -> None:
        ring = TrendRingStore(effective_interval_s=15.0)
        assert ring.declared_series_count() == 0
        ring.set_series_count(10_000)
        assert ring.declared_series_count() == 10_000
        assert ring.depth == 75


class TestTheReusedManagedKey:
    """Plan task 2.8 — the description-only amendment of the sampler key.

    The key was previously UNWIRED; this lane consumes it as the
    sampler's TICK.  The amendment is DESCRIPTION-ONLY: the default,
    the bounds, the type and the restart column are untouched.
    """

    def test_the_description_names_both_consumers(self) -> None:
        from daemon.config import LimitsConfig

        description = LimitsConfig.model_fields["metrics_sampler_interval_s"].description
        assert "host-side" in description
        assert "trend" in description

    def test_the_default_bounds_and_type_are_untouched(self) -> None:
        from daemon.config import LimitsConfig

        field = LimitsConfig.model_fields["metrics_sampler_interval_s"]
        assert field.default == 10
        metadata = field.metadata
        assert next(m.ge for m in metadata if hasattr(m, "ge")) == 1
        assert next(m.le for m in metadata if hasattr(m, "le")) == 60
        assert field.annotation is int

    def test_the_registry_entry_is_untouched(self) -> None:
        from daemon.services.settings_service import MANAGED_KEYS

        spec = MANAGED_KEYS["limits.metrics_sampler_interval_s"]
        assert spec.default == 10
        assert spec.ge == 1
        assert spec.le == 60
        assert spec.value_type == "int"
        assert spec.requires_restart is True

    def test_the_observed_interval_is_the_max_of_the_two_cadences(self) -> None:
        """AM-15 — the OBSERVED spacing, never the configured cadence."""
        from daemon.services.metrics_query.provider import (
            RegistryMetricsQueryProvider,
        )

        provider = RegistryMetricsQueryProvider(
            sampler_interval_s=10.0, publisher_cadence=lambda: 15.0
        )
        assert provider.effective_interval_s == 15.0
        slower = RegistryMetricsQueryProvider(
            sampler_interval_s=30.0, publisher_cadence=lambda: 15.0
        )
        assert slower.effective_interval_s == 30.0
