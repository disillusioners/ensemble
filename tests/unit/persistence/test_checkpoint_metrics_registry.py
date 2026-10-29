"""Unit tests for the canonical metrics registry (metrics-collection P0, T4/T6).

Covers the registry contract from ``metric-catalog.md`` §0.2/§0.5 and
``architecture-recommendation.md`` §1.3/§2.1-§2.4:

* render emits every registered family;
* the extended checkpoint bucket set (73-99 s reads land in a finite
  bucket, not ``+Inf``);
* non-cumulative bucket storage produces the same cumulative output;
* a wedged writer never blocks the render (bounded try-acquire, the
  last published snapshot is served, ``# <family> unavailable:
  lock_contended``);
* the three-tier never-exported enforcement (registration-time enum
  pin, observe-time ``other`` fold, render-time allowlist);
* the hard cardinality cap is atomic (no overshoot, every rejection
  counted);
* the deprecated aliases expose the SAME series (never a second
  measurement) and log the deprecation exactly once per process;
* the frozen vocabularies still match their runtime sources (A1).
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from daemon import checkpoint_metrics as cm
from daemon.checkpoint_metrics import (
    CHECKPOINT_OP_BUCKETS,
    DEFAULT_LATENCY_BUCKETS,
    VOCABULARY_VERSION,
    checkpoint_list_total,
    register_counter,
    register_gauge,
    register_histogram,
    render_metrics,
    reset_for_tests,
    saver_op_latency_seconds,
    set_cardinality_cap_provider,
    set_publisher_cadence_provider,
)


@pytest.fixture(autouse=True)
def _restore_providers():
    """Restore the DEFAULT cadence/cap providers around every test.

    The providers are process-global (the api lifespan wires the managed
    keys into them), so each test starts from — and leaves behind — the
    module defaults.
    """
    cm._publisher_cadence_provider = cm._default_publisher_cadence_provider
    cm._cardinality_cap_provider = cm._default_cardinality_cap_provider
    yield
    cm._publisher_cadence_provider = cm._default_publisher_cadence_provider
    cm._cardinality_cap_provider = cm._default_cardinality_cap_provider


@pytest.fixture()
def clean_registry():
    """Reset every registered metric before/after a test."""
    reset_for_tests()
    yield
    reset_for_tests()


# ─── (a) render emits every registered family ───────────────────────────────


class TestRenderCoversRegisteredFamilies:
    def test_render_emits_every_registered_metric(self, clean_registry):
        """Each registered metric name appears in the rendered output."""
        rendered = render_metrics()
        for metric in cm.registered_metrics():
            assert metric.name in rendered, f"{metric.name} missing from render"

    def test_render_includes_the_f0_2_and_f0_4_families(self, clean_registry):
        """F0.2 + F0.4 series are rendered (they are always present)."""
        rendered = render_metrics()
        assert "ensemble_metrics_registry_measurements" in rendered
        assert "ensemble_metrics_cap_rejected_total" in rendered
        assert "ensemble_metrics_invalid_label_total" in rendered
        assert "ensemble_metrics_vocabulary_version" in rendered

    def test_vocabulary_version_gauge_renders_the_frozen_version(self, clean_registry):
        """F0.4 — the freeze marker is a rendered series, not just a doc field."""
        rendered = render_metrics()
        assert f"ensemble_metrics_vocabulary_version {VOCABULARY_VERSION}" in rendered

    def test_registry_measurements_counts_measurement_objects(self, clean_registry):
        """F0.2 — the gauge counts registered measurement OBJECTS, not series."""
        rendered = render_metrics()
        expected = len(cm.registered_metrics())
        assert f"ensemble_metrics_registry_measurements {expected}" in rendered

    def test_render_is_prometheus_text_exposition(self, clean_registry):
        """The output is the Prometheus classic text format."""
        register_gauge("ensemble_test_shape_gauge", help="shape probe")
        rendered = render_metrics()
        assert "# HELP ensemble_test_shape_gauge shape probe" in rendered
        assert "# TYPE ensemble_test_shape_gauge gauge" in rendered
        assert "ensemble_test_shape_gauge 0" in rendered

    def test_counter_with_labels_renders_labeled_series(self, clean_registry):
        """A labeled counter renders one line per label combination."""
        counter = register_counter(
            "ensemble_test_labeled_counter",
            label_keys=("outcome",),
            allowed_values={"outcome": frozenset({"ok", "error"})},
        )
        counter.inc(labels={"outcome": "ok"})
        counter.inc(2, labels={"outcome": "error"})
        rendered = render_metrics()
        assert 'ensemble_test_labeled_counter{outcome="ok"} 1' in rendered
        assert 'ensemble_test_labeled_counter{outcome="error"} 2' in rendered


# ─── (b) bucket-extension pin ────────────────────────────────────────────────


class TestBucketExtension:
    def test_checkpoint_buckets_extend_the_legacy_set(self):
        """CR-3 — the legacy 0.001..10.0 set is EXTENDED, not replaced."""
        assert CHECKPOINT_OP_BUCKETS[: len(DEFAULT_LATENCY_BUCKETS)] == DEFAULT_LATENCY_BUCKETS
        assert CHECKPOINT_OP_BUCKETS[-4:] == (30.0, 60.0, 120.0, 300.0)
        assert len(CHECKPOINT_OP_BUCKETS) == 17

    def test_73_to_99_second_read_lands_in_a_finite_bucket(self, clean_registry):
        """A 73-99 s read must not collapse into ``+Inf`` (CR-3)."""
        saver_op_latency_seconds.observe(73.0, op="aget")
        saver_op_latency_seconds.observe(99.0, op="aget")
        rendered = render_metrics()
        assert 'ensemble_checkpoint_op_seconds_bucket{le="120",op="aget"} 2' in rendered
        assert 'ensemble_checkpoint_op_seconds_bucket{le="300",op="aget"} 2' in rendered
        # The +Inf bucket equals the total count (every observation is
        # inside a finite bucket above 10 s).
        assert 'ensemble_checkpoint_op_seconds_bucket{le="+Inf",op="aget"} 2' in rendered

    def test_observation_above_the_top_bucket_lands_in_inf(self, clean_registry):
        """A value above 300 s has no finite bucket (it is in ``+Inf``)."""
        saver_op_latency_seconds.observe(400.0, op="aget")
        rendered = render_metrics()
        assert 'ensemble_checkpoint_op_seconds_bucket{le="300",op="aget"} 0' in rendered
        assert 'ensemble_checkpoint_op_seconds_bucket{le="+Inf",op="aget"} 1' in rendered
        assert 'ensemble_checkpoint_op_seconds_count{op="aget"} 1' in rendered


# ─── (c) non-cumulative observe correctness ──────────────────────────────────


class TestNonCumulativeObserve:
    def test_observation_count_and_sum_are_exact(self, clean_registry):
        saver_op_latency_seconds.observe(0.003, op="aget")
        saver_op_latency_seconds.observe(0.020, op="aget")
        assert saver_op_latency_seconds.get_count(op="aget") == 2
        assert abs(saver_op_latency_seconds.get_sum(op="aget") - 0.023) < 1e-9

    def test_rendered_cumulative_buckets_match_the_legacy_semantics(self, clean_registry):
        """The rendered cumulative output equals the pre-change output."""
        values = [0.0005, 0.003, 0.020, 0.3, 2.0, 7.5, 45.0]
        for value in values:
            saver_op_latency_seconds.observe(value, op="aget")

        rendered = render_metrics()
        for bound in CHECKPOINT_OP_BUCKETS:
            expected = sum(1 for v in values if v <= bound)
            assert f'ensemble_checkpoint_op_seconds_bucket{{le="{bound:g}",op="aget"}} {expected}' in rendered

    def test_stored_buckets_are_non_cumulative(self, clean_registry):
        """The raw storage holds per-bucket counts (accumulated at render)."""
        saver_op_latency_seconds.observe(0.003, op="aget")
        saver_op_latency_seconds.observe(0.020, op="aget")
        raw = saver_op_latency_seconds.get_raw_bucket_counts(op="aget")
        assert raw[2] == 1  # 0.005 bucket
        assert raw[4] == 1  # 0.025 bucket
        assert sum(raw) == 2

    def test_cumulative_accessor_is_monotonic(self, clean_registry):
        saver_op_latency_seconds.observe(0.003, op="aget")
        saver_op_latency_seconds.observe(0.020, op="aget")
        counts = saver_op_latency_seconds.get_bucket_counts(op="aget")
        for previous, current in zip(counts, counts[1:]):
            assert previous <= current

    def test_observe_does_not_scan_every_bucket(self, clean_registry):
        """observe touches ONE bucket (O(1) per observation, not O(B))."""
        histogram = register_histogram(
            "ensemble_test_observe_cost_histogram",
            label_keys=(),
            buckets=CHECKPOINT_OP_BUCKETS,
        )
        histogram.observe(0.003)
        raw = histogram.get_raw_bucket_counts()
        assert sum(raw) == 1
        assert raw.count(1) == 1


# ─── (d) wedged-writer simulation ────────────────────────────────────────────


class TestWedgedWriter:
    def test_render_returns_promptly_when_a_metric_lock_is_held(self, clean_registry):
        """A wedged writer must not hang the scrape (RK-5)."""
        counter = register_counter("ensemble_test_wedged_counter")
        counter.inc()

        started = time.monotonic()
        with counter.lock:
            rendered = render_metrics()
        elapsed = time.monotonic() - started

        assert elapsed < 0.1, f"render blocked for {elapsed:.3f}s (must stay under 100 ms)"
        assert "# ensemble_test_wedged_counter unavailable: lock_contended" in rendered

    def test_last_published_snapshot_is_served_on_contention(self, clean_registry):
        """On contention the last published snapshot is served."""
        counter = register_counter("ensemble_test_wedged_snapshot_counter")
        counter.inc(7)
        # A publish happened (the writer publishes on the first write).
        published = counter.published_snapshot()
        assert published is not None

        with counter.lock:
            rendered = render_metrics()
        assert "ensemble_test_wedged_snapshot_counter 7" in rendered

    def test_many_wedged_families_still_render_within_the_budget(self, clean_registry):
        """The render-wide budget bounds the WHOLE scrape (RK-5)."""
        wedged = [register_counter(f"ensemble_test_budget_{i}") for i in range(10)]
        for counter in wedged:
            counter.inc()
        started = time.monotonic()
        for counter in wedged:
            counter.lock.acquire()
        try:
            rendered = render_metrics()
        finally:
            for counter in wedged:
                counter.lock.release()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"render blocked for {elapsed:.3f}s on many wedged families"
        assert "unavailable:" in rendered

    def test_other_families_still_render_while_one_is_wedged(self, clean_registry):
        """One wedged family does not stall the others."""
        wedged = register_counter("ensemble_test_wedged_only")
        healthy = register_counter("ensemble_test_healthy_counter")
        healthy.inc(3)
        with wedged.lock:
            rendered = render_metrics()
        assert "ensemble_test_healthy_counter 3" in rendered
        assert "# ensemble_test_wedged_only unavailable: lock_contended" in rendered


# ─── (e) poisoned-registry defence in depth ─────────────────────────────────


class TestNeverExportedEnforcement:
    def test_registration_rejects_a_non_canonical_name(self):
        """Tier 1 — only ``^ensemble_[a-z0-9_]+$`` names are canonical."""
        with pytest.raises(ValueError, match="canonical"):
            register_counter("Message_Api_Counter")
        with pytest.raises(ValueError, match="canonical"):
            register_gauge("ensemble Bad Name")

    def test_registration_rejects_an_unknown_label_key_in_allowed_values(self):
        """Tier 1 — the enum pin rejects a wrong label KEY at registration."""
        with pytest.raises(ValueError, match="not a declared label key"):
            register_counter(
                "ensemble_test_bad_key",
                label_keys=("outcome",),
                allowed_values={"result": frozenset({"ok"})},
            )

    def test_unknown_label_key_raises_at_observe_time(self, clean_registry):
        """A wrong label key is a programmer error — it raises."""
        counter = register_counter("ensemble_test_unknown_key", label_keys=("outcome",))
        with pytest.raises(KeyError, match="unknown label keys"):
            counter.inc(labels={"result": "ok"})

    def test_wrong_label_value_folds_to_other_and_is_counted(self, clean_registry):
        """Tier 2 — a wrong label VALUE folds to ``other`` and is counted."""
        counter = register_counter(
            "ensemble_test_invalid_value",
            label_keys=("outcome",),
            allowed_values={"outcome": frozenset({"ok", "error"})},
        )
        counter.inc(labels={"outcome": "teapot"})
        rendered = render_metrics()
        assert 'ensemble_test_invalid_value{outcome="other"} 1' in rendered
        assert "ensemble_metrics_invalid_label_total" in rendered
        assert 'ensemble_metrics_invalid_label_total{label="outcome",metric="ensemble_test_invalid_value"} 1' in rendered

    def test_illegal_label_characters_fold_to_other(self, clean_registry):
        """The charset gate folds values carrying ``=``, ``,``, ``"`` or newline."""
        counter = register_counter("ensemble_test_charset", label_keys=("name",))
        counter.inc(labels={"name": 'a,b"c\nd=e'})
        rendered = render_metrics()
        assert 'ensemble_test_charset{name="other"} 1' in rendered

    def test_render_time_allowlist_drops_a_poisoned_series(self, clean_registry):
        """Tier 3 — defence in depth: a poisoned series never renders."""
        counter = register_counter(
            "ensemble_test_tier3",
            label_keys=("outcome",),
            allowed_values={"outcome": frozenset({"ok"})},
        )
        # Inject a poisoned series directly into the metric's state
        # (simulating a bug that bypassed the observe-time fold).
        counter._series[(("outcome", "poison"),)] = {"kind": "counter", "value": 99}
        counter._dirty = True
        rendered = render_metrics()
        assert "poison" not in rendered
        assert "ensemble_test_tier3" in rendered


# ─── (f) hard cardinality cap ────────────────────────────────────────────────


class TestHardCardinalityCap:
    def test_observation_beyond_the_cap_is_rejected(self, clean_registry):
        """R-m3 — the cap is HARD: the observation is rejected, not warned."""
        set_cardinality_cap_provider(lambda: 2)
        counter = register_counter("ensemble_test_capped", label_keys=("id",))
        counter.inc(labels={"id": "a"})
        counter.inc(labels={"id": "b"})
        counter.inc(labels={"id": "c"})  # rejected — the cap is 2
        assert counter.get(labels={"id": "a"}) == 1
        assert counter.get(labels={"id": "c"}) == 0
        rendered = render_metrics()
        assert 'ensemble_metrics_cap_rejected_total{metric="ensemble_test_capped"} 1' in rendered

    def test_default_cap_is_2000(self):
        assert cm.DEFAULT_CARDINALITY_CAP == 2000
        assert cm._cardinality_cap() == 2000

    def test_concurrent_observers_never_overshoot_the_cap(self, clean_registry):
        """The check-then-increment is atomic (no TOCTOU, no overshoot)."""
        set_cardinality_cap_provider(lambda: 10)
        counter = register_counter("ensemble_test_capped_concurrent", label_keys=("id",))
        observers = 8
        per_observer = 25
        barrier = threading.Barrier(observers)

        def worker(index: int) -> None:
            barrier.wait()
            for i in range(per_observer):
                counter.inc(labels={"id": f"{index}-{i}"})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(observers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        series_count = len(counter.series_keys())
        assert series_count == 10, f"cap overshoot: {series_count} series (cap is 10)"
        total_attempts = observers * per_observer
        rejections = total_attempts - series_count
        assert rejections == 190, f"unexpected rejection count: {rejections}"
        rendered = render_metrics()
        assert (
            f'ensemble_metrics_cap_rejected_total{{metric="ensemble_test_capped_concurrent"}} {rejections}'
            in rendered
        )


# ─── (g) alias dual exposition ───────────────────────────────────────────────


class TestDeprecatedAliases:
    def test_alias_exposes_the_same_series_not_a_second_measurement(self, clean_registry):
        """A legacy name is a second exposition of the SAME series."""
        checkpoint_list_total.inc()
        rendered = render_metrics()
        assert "ensemble_checkpoint_list_total 1" in rendered
        assert "message_api_checkpoint_list_total 1" in rendered

    def test_alias_histogram_shares_the_observations(self, clean_registry):
        saver_op_latency_seconds.observe(0.003, op="aget")
        rendered = render_metrics()
        assert 'ensemble_checkpoint_op_seconds_count{op="aget"} 1' in rendered
        assert 'message_api_saver_op_latency_seconds_count{op="aget"} 1' in rendered

    def test_deprecation_is_logged_once_per_process(self, clean_registry, caplog):
        """One deprecation line per alias per process (not per scrape)."""
        cm._DEPRECATION_EMITTED.clear()
        with caplog.at_level(logging.WARNING, logger="daemon.checkpoint_metrics"):
            render_metrics()
            render_metrics()
            render_metrics()
        deprecations = [
            record
            for record in caplog.records
            if "Deprecated metric name" in record.getMessage()
            and "message_api_checkpoint_list_total" in record.getMessage()
        ]
        assert len(deprecations) == 1

    def test_aliases_are_registered_at_construction_time(self):
        """A27 — the aliases exist at registry-construction time."""
        assert "message_api_checkpoint_list_total" in checkpoint_list_total.alias_names
        assert "message_api_saver_op_latency_seconds" in saver_op_latency_seconds.alias_names

    def test_legacy_names_are_alias_only_never_canonical(self):
        """The legacy names violate the canonical rule on purpose."""
        assert "message_api_checkpoint_list_total" in cm.LEGACY_ALIAS_NAMES
        assert "message_api_saver_op_latency_seconds" in cm.LEGACY_ALIAS_NAMES
        for metric in cm.registered_metrics():
            assert not metric.name.startswith("message_api_")


# ─── publisher cadence (R-m5) ────────────────────────────────────────────────


class TestPublisherCadence:
    def test_default_cadence_is_15_seconds(self):
        assert cm.DEFAULT_PUBLISHER_CADENCE_SECONDS == 15.0
        assert cm._publisher_cadence() == 15.0
        assert cm._default_publisher_cadence_provider() == 15.0

    def test_cadence_provider_is_installable(self):
        set_publisher_cadence_provider(lambda: 0.0)
        assert cm._publisher_cadence() == 0.0

    def test_publish_is_rate_limited_by_the_cadence(self, clean_registry):
        """A writer publishes at most once per cadence window."""
        set_publisher_cadence_provider(lambda: 3600.0)
        counter = register_counter("ensemble_test_cadence_counter")
        counter.inc()
        first = counter.published_snapshot()
        counter.inc()
        second = counter.published_snapshot()
        assert first is not None
        # The snapshot is not refreshed inside the cadence window.
        assert second is first or second == first

    def test_force_publish_refreshes_the_snapshot(self, clean_registry):
        set_publisher_cadence_provider(lambda: 3600.0)
        counter = register_counter("ensemble_test_force_publish")
        counter.inc()
        counter.inc()
        assert counter.publish(force=True) is True
        snapshot = counter.published_snapshot()
        assert snapshot is not None
