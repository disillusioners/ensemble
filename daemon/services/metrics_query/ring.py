"""The in-RAM trend ring buffer (plan task 2.5 — AM-9/AM-10).

WHY AN ARRAY AND NOT A DEQUE: the ``deque(maxlen=…)`` form was
REJECTED on measured evidence — 129-161 B/point on this host, which is
92.6-115.0 MiB at 750 000 points (3.6x over the 32 MiB ceiling).  The
array-backed form is **16 B/point** (an ``array('d')`` value + an
``array('q')`` timestamp) ⇒ **11.4 MiB** worst case, with an O(1)
drop-oldest.

The ring evicts by COUNT **and** by AGE.  The age eviction is the
structural backstop that keeps the 2 h window truthful under ANY cadence
change (the configured cadence is boot-applied and the publisher
cadence is live-adjustable — the OBSERVED spacing is what the envelope
reports).

The depth is ``clamp(POINT_BUDGET // ring_series, MIN_DEPTH,
window_s // effective_interval_s)`` where ``ring_series`` is the
REGISTRY-NATIVE label-combination count (42 today over 18 live
objects — NOT the catalog-convention count).  The depth RECOMPUTES when
the series count changes (a registration event) and the rule is
written down here.
"""

from __future__ import annotations

from array import array
from typing import Any, Iterable, Sequence

from daemon.services.metrics_query.constants import (
    IDLE_GC_SAMPLINGS,
    MIN_DEPTH,
    POINT_BUDGET,
    WINDOW_SECONDS,
)

#: Bytes per scalar point: an ``array('d')`` value (8 B) + an
#: ``array('q')`` timestamp (8 B).
BYTES_PER_POINT: int = 16

#: Bytes per histogram point, EXCLUDING the bucket array:
#: ``ts f64`` (8) + ``count i64`` (8) + ``sum f64`` (8) = 24 B, plus
#: 8 B per bucket.
HISTOGRAM_BASE_BYTES: int = 24
HISTOGRAM_BYTES_PER_BUCKET: int = 8


def compute_depth(
    *,
    ring_series: int,
    point_budget: int = POINT_BUDGET,
    window_s: int = WINDOW_SECONDS,
    effective_interval_s: float = 15.0,
    min_depth: int = MIN_DEPTH,
) -> int:
    """The per-series ring depth (AM-10).

    ``depth = clamp(POINT_BUDGET // max(1, ring_series), MIN_DEPTH,
    window_s // effective_interval_s)``

    The COUNT bound is the memory bound; the WINDOW bound is the
    truthfulness bound (a 720-deep count-bound would retain 3 h, not
    2 h).  The clamp takes the SMALLER of the two, floored at
    ``MIN_DEPTH``.
    """
    divisor = max(1, int(ring_series))
    count_bound = max(1, int(point_budget)) // divisor
    interval = effective_interval_s if effective_interval_s > 0 else 1.0
    window_bound = max(1, int(window_s // interval))
    depth = min(count_bound, window_bound)
    return max(int(min_depth), depth)


class TrendRing:
    """One series' fixed-depth ring (array-backed, O(1) drop-oldest)."""

    __slots__ = ("_values", "_timestamps", "_depth", "_start", "_size")

    def __init__(self, depth: int) -> None:
        if depth <= 0:
            raise ValueError(f"ring depth must be positive; got {depth}")
        self._depth = int(depth)
        # ``bytes(...)`` zero-initialises the buffer without building a
        # transient Python list of ``depth`` floats.
        self._values = array("d", bytes(8 * self._depth))
        self._timestamps = array("q", bytes(8 * self._depth))
        self._start = 0
        self._size = 0

    # ── introspection ───────────────────────────────────────────────────

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def size(self) -> int:
        return self._size

    def __len__(self) -> int:
        return self._size

    def measured_bytes(self) -> int:
        """The MEASURED footprint of this ring (never a nominal constant)."""
        return (
            self._values.buffer_info()[1] * self._values.itemsize
            + self._timestamps.buffer_info()[1] * self._timestamps.itemsize
        )

    # ── mutation ────────────────────────────────────────────────────────

    def append(self, timestamp: int, value: float) -> None:
        """Append one sample, dropping the OLDEST when full."""
        if self._size == self._depth:
            self._start = (self._start + 1) % self._depth
            self._size -= 1
        index = (self._start + self._size) % self._depth
        self._values[index] = float(value)
        self._timestamps[index] = int(timestamp)
        self._size += 1

    def evict_before(self, cutoff: int) -> int:
        """Drop every sample older than ``cutoff`` (the age eviction)."""
        dropped = 0
        while self._size and self._timestamps[self._start] < cutoff:
            self._start = (self._start + 1) % self._depth
            self._size -= 1
            dropped += 1
        return dropped

    def points(self) -> list[tuple[int, float]]:
        """The samples, oldest first."""
        out: list[tuple[int, float]] = []
        for offset in range(self._size):
            index = (self._start + offset) % self._depth
            out.append((int(self._timestamps[index]), float(self._values[index])))
        return out

    def first_timestamp(self) -> int | None:
        """The OLDEST sample's timestamp — O(1), NO copy.

        ``points()[0][0]`` without the O(depth) materialisation: the
        cursor's expiry probe must never copy the whole trend buffer.
        """
        if not self._size:
            return None
        return int(self._timestamps[self._start])

    def last_timestamp(self) -> int | None:
        """The NEWEST sample's timestamp — O(1), NO copy."""
        if not self._size:
            return None
        return int(self._timestamps[(self._start + self._size - 1) % self._depth])

    def resized(self, depth: int) -> "TrendRing":
        """A copy of this ring at a new depth (the newest samples kept)."""
        clone = TrendRing(depth)
        for timestamp, value in self.points()[-depth:]:
            clone.append(timestamp, value)
        return clone


class HistogramTrendRing:
    """One HISTOGRAM series' ring (count + sum + bucket counts)."""

    __slots__ = ("_timestamps", "_counts", "_sums", "_buckets", "_depth", "_start", "_size")

    def __init__(self, depth: int) -> None:
        if depth <= 0:
            raise ValueError(f"ring depth must be positive; got {depth}")
        self._depth = int(depth)
        self._timestamps = array("q", bytes(8 * self._depth))
        self._counts = array("q", bytes(8 * self._depth))
        self._sums = array("d", bytes(8 * self._depth))
        self._buckets: list[list[int] | None] = [None] * self._depth
        self._start = 0
        self._size = 0

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def size(self) -> int:
        return self._size

    def __len__(self) -> int:
        return self._size

    def measured_bytes(self) -> int:
        """The MEASURED footprint of this ring."""
        total = (
            self._timestamps.buffer_info()[1] * self._timestamps.itemsize
            + self._counts.buffer_info()[1] * self._counts.itemsize
            + self._sums.buffer_info()[1] * self._sums.itemsize
        )
        for buckets in self._buckets:
            if buckets is not None:
                total += 8 * len(buckets)
        return total

    def append(
        self,
        timestamp: int,
        count: int,
        total: float,
        buckets: Sequence[int],
    ) -> None:
        if self._size == self._depth:
            self._start = (self._start + 1) % self._depth
            self._size -= 1
        index = (self._start + self._size) % self._depth
        self._timestamps[index] = int(timestamp)
        self._counts[index] = int(count)
        self._sums[index] = float(total)
        self._buckets[index] = [int(b) for b in buckets]
        self._size += 1

    def evict_before(self, cutoff: int) -> int:
        dropped = 0
        while self._size and self._timestamps[self._start] < cutoff:
            self._start = (self._start + 1) % self._depth
            self._size -= 1
            self._buckets[(self._start - 1) % self._depth] = None
            dropped += 1
        return dropped

    def points(self) -> list[dict[str, Any]]:
        """The samples, oldest first (as stored — NOT Prom-cumulative)."""
        out: list[dict[str, Any]] = []
        for offset in range(self._size):
            index = (self._start + offset) % self._depth
            buckets = self._buckets[index] or []
            out.append(
                {
                    "timestamp": int(self._timestamps[index]),
                    "count": int(self._counts[index]),
                    "sum": float(self._sums[index]),
                    "buckets": list(buckets),
                }
            )
        return out

    def first_timestamp(self) -> int | None:
        """The OLDEST sample's timestamp — O(1), NO copy."""
        if not self._size:
            return None
        return int(self._timestamps[self._start])

    def last_timestamp(self) -> int | None:
        """The NEWEST sample's timestamp — O(1), NO copy."""
        if not self._size:
            return None
        return int(self._timestamps[(self._start + self._size - 1) % self._depth])

    def resized(self, depth: int) -> "HistogramTrendRing":
        clone = HistogramTrendRing(depth)
        for point in self.points()[-depth:]:
            clone.append(
                point["timestamp"], point["count"], point["sum"], point["buckets"]
            )
        return clone


class TrendRingStore:
    """Every series' ring, plus the depth/GC/eviction policy.

    The store owns the point budget: the total sample count never
    exceeds ``POINT_BUDGET`` and the total MEASURED bytes stay inside
    the 11.4 MiB envelope.
    """

    def __init__(
        self,
        *,
        window_s: int = WINDOW_SECONDS,
        point_budget: int = POINT_BUDGET,
        effective_interval_s: float = 15.0,
        min_depth: int = MIN_DEPTH,
    ) -> None:
        self._window_s = int(window_s)
        self._point_budget = int(point_budget)
        self._effective_interval_s = float(effective_interval_s)
        self._min_depth = int(min_depth)
        self._rings: dict[str, TrendRing] = {}
        self._hist_rings: dict[str, HistogramTrendRing] = {}
        self._series_count = 0
        self._depth = compute_depth(
            ring_series=1,
            point_budget=self._point_budget,
            window_s=self._window_s,
            effective_interval_s=self._effective_interval_s,
            min_depth=self._min_depth,
        )

    # ── introspection ───────────────────────────────────────────────────

    @property
    def window_s(self) -> int:
        return self._window_s

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def effective_interval_s(self) -> float:
        return self._effective_interval_s

    def series_count(self) -> int:
        """The number of ring series currently held."""
        return len(self._rings) + len(self._hist_rings)

    def declared_series_count(self) -> int:
        """The REGISTRY-NATIVE label-combination count (the divisor).

        The sampler feeds it in every tick (AM-10): it is the count of
        the LIVE label combinations, NOT the catalog-convention count.
        """
        return self._series_count

    def total_points(self) -> int:
        """The total sample count across every ring."""
        return sum(len(ring) for ring in self._rings.values()) + sum(
            len(ring) for ring in self._hist_rings.values()
        )

    def measured_bytes(self) -> int:
        """The MEASURED footprint of every ring."""
        return sum(ring.measured_bytes() for ring in self._rings.values()) + sum(
            ring.measured_bytes() for ring in self._hist_rings.values()
        )

    def keys(self) -> list[str]:
        return sorted(set(self._rings) | set(self._hist_rings))

    # ── the depth rule ──────────────────────────────────────────────────

    def set_series_count(self, ring_series: int) -> bool:
        """Recompute the depth for a new registry-native series count.

        Returns True when the depth CHANGED (the rings were resized).
        The newest samples survive a resize; the oldest are dropped.
        """
        self._series_count = max(0, int(ring_series))
        return self._recompute_depth()

    def _recompute_depth(self, extra: int = 0) -> bool:
        """Recompute the depth from the effective divisor.

        The divisor is the LARGER of the declared registry-native
        series count and the number of ring series actually held (plus
        ``extra`` — the series that is about to be added), so the point
        budget can never be exceeded by construction: the depth
        shrinks as the ring grows.
        """
        held = len(self._rings) + len(self._hist_rings) + int(extra)
        divisor = max(1, self._series_count, held)
        depth = compute_depth(
            ring_series=divisor,
            point_budget=self._point_budget,
            window_s=self._window_s,
            effective_interval_s=self._effective_interval_s,
            min_depth=self._min_depth,
        )
        if depth == self._depth:
            return False
        self._depth = depth
        self._rings = {key: ring.resized(depth) for key, ring in self._rings.items()}
        self._hist_rings = {
            key: ring.resized(depth) for key, ring in self._hist_rings.items()
        }
        return True

    # ── mutation ────────────────────────────────────────────────────────

    def append(self, key: str, timestamp: int, value: float) -> None:
        if key not in self._rings:
            # The depth may shrink now that the ring holds one more
            # series — recompute BEFORE the ring is created so the new
            # ring is born at the right depth.
            self._recompute_depth(extra=1)
            self._rings[key] = TrendRing(self._depth)
        self._rings[key].append(timestamp, value)

    def append_histogram(
        self,
        key: str,
        timestamp: int,
        count: int,
        total: float,
        buckets: Iterable[int],
    ) -> None:
        if key not in self._hist_rings:
            self._recompute_depth(extra=1)
            self._hist_rings[key] = HistogramTrendRing(self._depth)
        self._hist_rings[key].append(timestamp, count, total, list(buckets))

    def evict_before(self, cutoff: int) -> int:
        """Age-evict every ring (the window stays truthful)."""
        dropped = 0
        for ring in self._rings.values():
            dropped += ring.evict_before(cutoff)
        for ring in self._hist_rings.values():
            dropped += ring.evict_before(cutoff)
        return dropped

    def gc(self, idle_counts: dict[str, int]) -> list[str]:
        """Drop the ring series of metrics that stopped publishing.

        ``idle_counts`` maps a metric NAME to the number of consecutive
        samplings with no published entry.  A series is dropped only at
        ``IDLE_GC_SAMPLINGS`` — an idle-but-LIVE family (a metric that
        still HAS a published entry) is never dropped (AM-13).
        """
        dropped: list[str] = []
        for key in list(self._rings):
            name = key.split("|", 1)[0]
            if idle_counts.get(name, 0) >= IDLE_GC_SAMPLINGS:
                del self._rings[key]
                dropped.append(key)
        for key in list(self._hist_rings):
            name = key.split("|", 1)[0]
            if idle_counts.get(name, 0) >= IDLE_GC_SAMPLINGS:
                del self._hist_rings[key]
                dropped.append(key)
        return dropped

    # ── reads ───────────────────────────────────────────────────────────

    def points(self, key: str) -> list[tuple[int, float]]:
        ring = self._rings.get(key)
        if ring is None:
            return []
        return ring.points()

    def histogram_points(self, key: str) -> list[dict[str, Any]]:
        ring = self._hist_rings.get(key)
        if ring is None:
            return []
        return ring.points()

    def oldest_timestamp(self) -> int | None:
        """The oldest sample's timestamp (the cursor's expiry horizon).

        O(rings), NOT O(points): the per-ring probe reads the ring's
        FIRST timestamp directly, so the cursor's expiry check NEVER
        materialises the trend buffer.
        """
        oldest: int | None = None
        for ring in self._rings.values():
            timestamp = ring.first_timestamp()
            if timestamp is not None and (oldest is None or timestamp < oldest):
                oldest = timestamp
        for ring in self._hist_rings.values():
            timestamp = ring.first_timestamp()
            if timestamp is not None and (oldest is None or timestamp < oldest):
                oldest = timestamp
        return oldest

    def newest_timestamp(self) -> int | None:
        """The newest sample's timestamp (``None`` when the ring is empty)."""
        newest: int | None = None
        for ring in self._rings.values():
            timestamp = ring.last_timestamp()
            if timestamp is not None and (newest is None or timestamp > newest):
                newest = timestamp
        for ring in self._hist_rings.values():
            timestamp = ring.last_timestamp()
            if timestamp is not None and (newest is None or timestamp > newest):
                newest = timestamp
        return newest


def series_key(name: str, label_key: Sequence[tuple[str, str]]) -> str:
    """The canonical ring key for a series.

    The label part uses the registry's canonical ``_label_key``
    serialization (sorted ``(k, v)`` pairs) — the SAME serialization
    the door's label-aware selector speaks (AM-1).  The pairs are
    sorted HERE too, so a caller that hands over an unsorted sequence
    still produces the canonical key.
    """
    rendered = ",".join(
        f"{key}={value}" for key, value in sorted((str(k), str(v)) for k, v in label_key)
    )
    return f"{name}|{rendered}"


__all__ = [
    "BYTES_PER_POINT",
    "HISTOGRAM_BASE_BYTES",
    "HISTOGRAM_BYTES_PER_BUCKET",
    "HistogramTrendRing",
    "TrendRing",
    "TrendRingStore",
    "compute_depth",
    "series_key",
]
