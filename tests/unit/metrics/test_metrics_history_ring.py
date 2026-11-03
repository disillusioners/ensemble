"""The trend ring pack (registered pack ``metrics_history_ring``).

The ring is the memory-bound artefact of the lane, so its pins are
MEASUREMENT pins:

* the ring is ARRAY-backed at 16 B/point (AM-9) — the ``deque(maxlen)``
  form measured 129-161 B/point and was rejected;
* the depth follows the pinned rule (AM-10): the count bound AND the
  window bound, floored at ``MIN_DEPTH``, with the REGISTRY-NATIVE
  label-combination count as the divisor;
* eviction drops the OLDEST (by count) and the OUTDATED (by age);
* the total sample count never exceeds ``POINT_BUDGET`` and the
  measured bytes stay inside the 11.4 MiB envelope.

Mutation table: ``P2-M3`` (the depth clamp floor removed) · ``P2-M4``
(eviction drops the newest) · ``P2-M9`` (the ring switched back to
``deque(maxlen)``) · ``P2-M10`` (the age eviction removed) ·
``P2-M11`` (the depth divisor switched to the catalog count).
"""

from __future__ import annotations

import threading
import time

import pytest

from daemon.services.metrics_query.constants import (
    MIN_DEPTH,
    POINT_BUDGET,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.ring import (
    BYTES_PER_POINT,
    HistogramTrendRing,
    TrendRing,
    TrendRingStore,
    compute_depth,
    series_key,
)


class TestDepthRule:
    """AM-10 — the pinned depth formula."""

    def test_the_depth_takes_the_smaller_of_the_two_bounds(self) -> None:
        # 750 000 // 42 = 17 857 (count bound); 7200 // 15 = 480
        # (window bound) ⇒ the window bound wins.
        assert compute_depth(ring_series=42, effective_interval_s=15.0) == 480

    def test_the_count_bound_wins_when_the_series_count_is_large(self) -> None:
        # 750 000 // 10 000 = 75 (count bound) < 480 (window bound).
        assert compute_depth(ring_series=10_000, effective_interval_s=15.0) == 75

    def test_the_divisor_is_the_series_count(self) -> None:
        """P2-M11 — the divisor is the registry-native series count."""
        wide = compute_depth(ring_series=10_000, effective_interval_s=15.0)
        narrow = compute_depth(ring_series=12_000, effective_interval_s=15.0)
        assert wide == 75  # 750 000 // 10 000
        assert narrow == 62  # 750 000 // 12 000
        assert wide != narrow

    def test_the_clamp_floor_holds(self) -> None:
        """P2-M3 — the depth never falls below MIN_DEPTH."""
        assert (
            compute_depth(ring_series=10_000_000, effective_interval_s=15.0)
            == MIN_DEPTH
        )
        assert compute_depth(ring_series=1, effective_interval_s=15.0) >= MIN_DEPTH

    def test_a_zero_series_count_does_not_divide_by_zero(self) -> None:
        assert compute_depth(ring_series=0, effective_interval_s=15.0) >= MIN_DEPTH

    def test_the_store_recomputes_the_depth_when_the_series_count_changes(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        assert store.depth == 480
        assert store.set_series_count(10_000) is True
        assert store.depth == 75
        assert store.set_series_count(10_000) is False

    def test_a_resize_keeps_the_newest_samples(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        for index in range(10):
            store.append("k", 1_000 + index, float(index))
        store.set_series_count(10_000)  # depth 480 — no shrink
        assert store.points("k")[-1] == (1_009, 9.0)


class TestRepresentation:
    """AM-9 — the array-backed representation."""

    def test_the_ring_is_array_backed(self) -> None:
        ring = TrendRing(16)
        assert type(ring._values).__name__ == "array"
        assert type(ring._timestamps).__name__ == "array"

    def test_the_measured_bytes_per_point_stay_at_the_budget(self) -> None:
        """P2-M9 — a deque form measured 129-161 B/point."""
        ring = TrendRing(4_096)
        for index in range(4_096):
            ring.append(1_000 + index, float(index))
        per_point = ring.measured_bytes() / len(ring)
        assert per_point <= BYTES_PER_POINT + 8, (
            f"the ring must stay array-backed (16 B/point); measured {per_point} B/point"
        )

    def test_the_histogram_ring_measures_its_buckets(self) -> None:
        ring = HistogramTrendRing(64)
        ring.append(1_000, 3, 1.5, [1, 2, 3])
        assert ring.measured_bytes() > 0
        assert len(ring) == 1


class TestEviction:
    """P2-M4 (count eviction) and P2-M10 (age eviction)."""

    def test_the_oldest_is_dropped_when_the_ring_is_full(self) -> None:
        ring = TrendRing(3)
        for index in range(5):
            ring.append(1_000 + index, float(index))
        assert [timestamp for timestamp, _ in ring.points()] == [1_002, 1_003, 1_004]

    def test_the_newest_samples_survive_an_overflow(self) -> None:
        ring = TrendRing(3)
        for index in range(5):
            ring.append(1_000 + index, float(index))
        values = [value for _, value in ring.points()]
        assert values == [2.0, 3.0, 4.0]

    def test_the_age_eviction_drops_the_outdated_samples(self) -> None:
        """P2-M10 — the window tracks ``window_s``."""
        ring = TrendRing(60)
        for index in range(10):
            ring.append(1_000 + index, float(index))
        dropped = ring.evict_before(1_005)
        assert dropped == 5
        assert [timestamp for timestamp, _ in ring.points()] == [
            1_005,
            1_006,
            1_007,
            1_008,
            1_009,
        ]

    def test_the_age_eviction_keeps_the_recent_samples(self) -> None:
        ring = TrendRing(60)
        ring.append(2_000, 1.0)
        assert ring.evict_before(1_000) == 0
        assert len(ring) == 1

    def test_the_store_evicts_by_age_across_every_ring(self) -> None:
        store = TrendRingStore(window_s=100, effective_interval_s=15.0)
        store.append("a|", 1_000, 1.0)
        store.append("b|", 1_000, 2.0)
        dropped = store.evict_before(1_050)
        assert dropped == 2
        assert store.total_points() == 0


class TestBudget:
    def test_the_total_sample_count_never_exceeds_the_point_budget(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        for index in range(20_000):
            store.append(f"k{index % 2_000}", 1_000 + index, float(index))
        assert store.total_points() <= POINT_BUDGET

    def test_the_depth_shrinks_as_the_ring_grows(self) -> None:
        """The budget is held BY CONSTRUCTION, not by a later check."""
        store = TrendRingStore(effective_interval_s=15.0)
        assert store.depth == 480
        for index in range(2_000):
            store.append(f"k{index}", 1_000 + index, float(index))
        assert store.depth < 480
        assert store.total_points() <= POINT_BUDGET

    def test_the_measured_bytes_stay_inside_the_envelope(self) -> None:
        """11.4 MiB worst case at 16 B/point (the 32 MiB ceiling is 2.8x over)."""
        store = TrendRingStore(effective_interval_s=15.0)
        for index in range(10_000):
            store.append(f"k{index % 100}", 1_000 + index, float(index))
        assert store.measured_bytes() <= 11.4 * 1024 * 1024

    def test_the_point_budget_constant_is_the_pinned_one(self) -> None:
        assert POINT_BUDGET == 750_000
        assert WINDOW_SECONDS == 7200


class TestGarbageCollection:
    def test_a_series_is_dropped_after_three_idle_samplings(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        store.append("ensemble_idle|", 1_000, 1.0)
        assert store.gc({"ensemble_idle": 2}) == []
        assert store.gc({"ensemble_idle": 3}) == ["ensemble_idle|"]
        assert store.points("ensemble_idle|") == []

    def test_an_idle_but_live_family_is_never_dropped(self) -> None:
        """AM-13 — a metric that still HAS a published entry stays."""
        store = TrendRingStore(effective_interval_s=15.0)
        store.append("ensemble_live|", 1_000, 1.0)
        assert store.gc({"ensemble_live": 0}) == []
        assert store.points("ensemble_live|") == [(1_000, 1.0)]


class TestSeriesKeys:
    def test_the_key_uses_the_canonical_label_serialization(self) -> None:
        key = series_key("ensemble_x", (("b", "2"), ("a", "1")))
        assert key == "ensemble_x|a=1,b=2"

    def test_the_label_order_does_not_change_the_key(self) -> None:
        assert series_key("ensemble_x", (("a", "1"), ("b", "2"))) == series_key(
            "ensemble_x", (("b", "2"), ("a", "1"))
        )

    def test_the_key_agrees_with_the_registry_serialization(self) -> None:
        from daemon.checkpoint_metrics import label_key

        key = series_key("ensemble_x", label_key({"b": "2", "a": "1"}))
        assert key == "ensemble_x|a=1,b=2"


class TestStoreReads:
    def test_the_store_reports_the_newest_and_oldest_timestamps(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        store.append("k|", 1_000, 1.0)
        store.append("k|", 1_060, 2.0)
        assert store.oldest_timestamp() == 1_000
        assert store.newest_timestamp() == 1_060

    def test_an_empty_store_reports_no_timestamps(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        assert store.oldest_timestamp() is None
        assert store.newest_timestamp() is None

    def test_a_histogram_point_round_trips(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        store.append_histogram("h|", 1_000, 4, 9.5, [1, 2, 1])
        point = store.histogram_points("h|")[0]
        assert point["count"] == 4
        assert point["sum"] == 9.5
        assert point["buckets"] == [1, 2, 1]

    def test_a_zero_depth_ring_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            TrendRing(0)


class TestTheRingIsLocked:
    """W-5 — the ring is SHARED between the sampler thread and the loop.

    The ring is written by the sampler thread and read by the event
    loop.  The value and the timestamp live in two parallel arrays, and
    ``points()`` reads the size once but the start index per iteration —
    so an unlocked ring can pair a value with a FOREIGN timestamp (a
    torn point) when a writer drops the oldest sample mid-read.

    THE PINS ARE BEHAVIOURAL, NOT STRUCTURAL: a concurrent reader must
    not proceed while the ring is locked, and a concurrent append/read
    must never produce a torn pair.
    """

    def test_a_concurrent_read_does_not_proceed_while_the_lock_is_held(self) -> None:
        ring = TrendRing(4)
        ring.append(1, 1.0)
        done = threading.Event()

        def _read() -> None:
            ring.points()
            done.set()

        with ring._lock:
            thread = threading.Thread(target=_read, daemon=True)
            thread.start()
            proceeded = done.wait(0.2)
        assert not proceeded, (
            "the read path must take the ring's lock — a read proceeded "
            "while the lock was held"
        )
        assert done.wait(2.0), "the read must complete once the lock is free"

    def test_a_concurrent_append_does_not_proceed_while_the_lock_is_held(self) -> None:
        ring = TrendRing(4)
        done = threading.Event()

        def _append() -> None:
            ring.append(2, 2.0)
            done.set()

        with ring._lock:
            thread = threading.Thread(target=_append, daemon=True)
            thread.start()
            proceeded = done.wait(0.2)
        assert not proceeded, (
            "the mutation path must take the ring's lock — an append "
            "proceeded while the lock was held"
        )
        assert done.wait(2.0), "the append must complete once the lock is free"

    def test_a_concurrent_append_and_read_never_torn_a_point(self) -> None:
        """The torn-point regression — every pair stays self-consistent.

        The value written is the timestamp, so a torn pair (a value with
        a foreign timestamp) is visible as ``value != timestamp``.
        """
        ring = TrendRing(4)
        for index in range(4):
            ring.append(index, float(index))
        stop = threading.Event()
        torn: list[tuple[int, float]] = []

        def _writer() -> None:
            tick = 100
            while not stop.is_set():
                ring.append(tick, float(tick))
                tick += 1

        def _reader() -> None:
            while not stop.is_set():
                for timestamp, value in ring.points():
                    if value != float(timestamp):
                        torn.append((timestamp, value))

        writer = threading.Thread(target=_writer, daemon=True)
        reader = threading.Thread(target=_reader, daemon=True)
        writer.start()
        reader.start()
        time.sleep(0.4)
        stop.set()
        writer.join(2.0)
        reader.join(2.0)
        assert torn == [], f"torn points observed: {torn[:5]}"

    def test_the_store_serialises_its_own_maps(self) -> None:
        store = TrendRingStore(effective_interval_s=15.0)
        store.append("a|", 1, 1.0)
        done = threading.Event()

        def _append() -> None:
            store.append("b|", 2, 2.0)
            done.set()

        with store._lock:
            thread = threading.Thread(target=_append, daemon=True)
            thread.start()
            proceeded = done.wait(0.2)
        assert not proceeded, (
            "the store's map mutations must take the store's lock"
        )
        assert done.wait(2.0)
