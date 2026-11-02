"""The registry-backed query provider (plan task 2.3).

THE READ CONTRACT (load-bearing):

* the provider reads **PUBLISHED registry state only** — it never
  renders, never acquires a METRIC lock, never mutates, never touches
  a database and never reads the kill-switch;
* the only registry lock it takes is the REGISTRY-level lock inside
  ``registered_metrics()`` (the allowlisted call) — a metric's own
  lock is never taken, so a wedged writer can never block the door;
* the staleness of a value is EXPLICIT metadata
  (``last_publish_age_s``), never a silently stale number.

The family metadata is DERIVED FROM THE REGISTRY (the registered
objects + their ``allowed_values`` + their buckets + the
registration-time family metadata), never from a second table: a
newly wired family is discoverable without editing the door.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping, Sequence

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.availability import (
    AVAILABILITY_REASONS,
    AvailabilityFacts,
    derive_availability,
)
from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_NAMES,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.downsample import bucket_max, slice_window
from daemon.services.metrics_query.ring import TrendRingStore, series_key
from daemon.services.metrics_query.selectors import (
    InvalidLabelFilter,
    parse_series_selector,
    selector_matches,
)

logger = logging.getLogger(__name__)

#: The registry-native label-combination count the depth divisor uses
#: (AM-10).  It is the count of the LIVE series, not the catalog count.


def _default_metrics_provider() -> list[Any]:
    """The registered metric objects (the registry-level lock).

    This is the ONLY registry call the provider makes, and it is the
    allowlisted one: it takes the REGISTRY-level lock, never a
    metric's own lock.
    """
    return cm.registered_metrics()


class RegistryMetricsQueryProvider:
    """The provider the query door serves from."""

    def __init__(
        self,
        *,
        census: Any | None = None,
        ring: TrendRingStore | None = None,
        listener_enabled: bool = False,
        capture_active: bool = False,
        metrics_provider: Callable[[], list[Any]] | None = None,
        sampler_interval_s: float = 10.0,
        publisher_cadence: Callable[[], float] | None = None,
    ) -> None:
        self._census = census
        self._ring = ring
        self._listener_enabled = bool(listener_enabled)
        self._capture_active = bool(capture_active)
        self._metrics_provider = metrics_provider or _default_metrics_provider
        self._sampler_interval_s = float(sampler_interval_s)
        self._publisher_cadence = publisher_cadence or cm.publisher_cadence

    # ── the facts the envelope needs ────────────────────────────────────

    @property
    def listener_enabled(self) -> bool:
        """Whether the exposition socket is bound (a PROCESS fact).

        Deliberately NOT the switch state: the socket binds and serves
        a valid empty 200 while the switch is OFF, so this fact is
        switch-INVARIANT (it is not one of the permitted body
        differences).
        """
        return self._listener_enabled

    @property
    def census_enabled(self) -> bool:
        """Whether the census sampler's DB work is ungated.

        Sourced from the PUBLIC ``CensusSampler.gated`` property — a
        read-through of the EXISTING gate site.  The query package
        NEVER reads the switch itself.
        """
        if self._census is None:
            return False
        try:
            return not bool(self._census.gated)
        except Exception:  # pragma: no cover — a broken census never breaks the door
            return False

    @property
    def effective_interval_s(self) -> float:
        """The OBSERVED effective spacing ``max(sampler, publisher)``."""
        try:
            publisher = float(self._publisher_cadence())
        except Exception:  # pragma: no cover
            publisher = cm.DEFAULT_PUBLISHER_CADENCE_SECONDS
        return max(self._sampler_interval_s, publisher)

    @property
    def census_running(self) -> bool:
        if self._census is None:
            return False
        try:
            return bool(self._census.running)
        except Exception:  # pragma: no cover
            return False

    @property
    def availability_facts(self) -> AvailabilityFacts:
        return AvailabilityFacts(
            census_gated=not self.census_enabled,
            census_running=self.census_running,
            capture_active=self._capture_active,
        )

    # ── the read helpers ────────────────────────────────────────────────

    def _metrics(self) -> list[Any]:
        try:
            return list(self._metrics_provider())
        except Exception:  # pragma: no cover — a broken registry never 500s the door
            logger.warning("metrics query provider: registry read failed", exc_info=True)
            return []

    def _published(self, metric: Any) -> Mapping[Any, Mapping[str, Any]] | None:
        """The metric's published snapshot (lock-free, never mutated)."""
        try:
            snapshot = metric.published_snapshot()
        except Exception:  # pragma: no cover
            return None
        if not snapshot:
            return None
        return snapshot

    def _publish_age_s(self, metric: Any) -> float:
        """The seconds since the metric's last publish (AM-13)."""
        try:
            last = float(metric.last_publish_monotonic())
        except Exception:  # pragma: no cover
            return 0.0
        if last <= 0:
            return 0.0
        return max(0.0, time.monotonic() - last)

    @staticmethod
    def _cardinality_class(metric: Any) -> str:
        """``closed`` / ``open`` / ``none`` (AM-2)."""
        label_keys = tuple(getattr(metric, "label_keys", ()) or ())
        if not label_keys:
            return "none"
        allowed = getattr(metric, "allowed_values", {}) or {}
        if all(key in allowed for key in label_keys):
            return "closed"
        return "open"

    @staticmethod
    def _declared_labels(metric: Any) -> dict[str, list[str]] | None:
        """The declared label value sets (``None`` unless the class is closed)."""
        label_keys = tuple(getattr(metric, "label_keys", ()) or ())
        if not label_keys:
            return None
        allowed = getattr(metric, "allowed_values", {}) or {}
        if not all(key in allowed for key in label_keys):
            return None
        return {key: sorted(str(v) for v in allowed[key]) for key in label_keys}

    @staticmethod
    def _monotonic(metric: Any) -> bool:
        """A counter is non-decreasing WITHIN ONE ``restart_epoch``."""
        return getattr(metric, "kind", "") == "counter"

    # ── the three views ─────────────────────────────────────────────────

    def families(self) -> list[dict[str, Any]]:
        """The family catalog — the canonical frozen shape."""
        grouped: dict[str, list[Any]] = {}
        for metric in self._metrics():
            family = str(getattr(metric, "family", "") or metric.name)
            grouped.setdefault(family, []).append(metric)

        facts = self.availability_facts
        out: list[dict[str, Any]] = []
        for family, members in sorted(grouped.items()):
            names: list[dict[str, Any]] = []
            live_series_count = 0
            contribution = 0
            for metric in members:
                snapshot = self._published(metric)
                member_live = len(snapshot) if snapshot else 0
                live_series_count += member_live
                contribution += member_live * int(
                    getattr(metric, "cardinality_weight", 1) or 0
                )
                buckets = getattr(metric, "buckets", None)
                names.append(
                    {
                        "name": metric.name,
                        "kind": metric.kind,
                        "labels": self._declared_labels(metric),
                        "help": metric.help,
                        "buckets": list(buckets) if buckets else None,
                        "cardinality_class": self._cardinality_class(metric),
                        "monotonic": self._monotonic(metric),
                        "chart_role": metric.effective_chart_role(),
                    }
                )
            source_idiom = str(getattr(members[0], "source_idiom", "registry"))
            durability = str(getattr(members[0], "durability", "process"))
            role = (
                cm.FAMILY_ROLE_CHART
                if any(member.is_chart_primary() for member in members)
                else cm.FAMILY_ROLE_STAT
            )
            availability = derive_availability(
                source_idiom=source_idiom,
                live_series_count=live_series_count,
                facts=facts,
            )
            # A-9 — the BACKEND-side reason-set assertion.  The frontend
            # widens its availability union with an ``unknown(<value>)``
            # fallback, so a NEW reason would be invisible there: the
            # frontend would render a reason nobody can match.  The
            # backend therefore refuses to emit a reason outside the
            # CLOSED v1 set — the surface stays observable.
            reason = availability.get("reason")
            assert reason is None or reason in AVAILABILITY_REASONS, (
                f"availability reason {reason!r} for family {family!r} is "
                f"outside the v1 set {AVAILABILITY_REASONS!r}"
            )
            out.append(
                {
                    "id": family,
                    "names": names,
                    "live_series_count": live_series_count,
                    "availability": availability,
                    "role": role,
                    "source_idiom": source_idiom,
                    "durability": durability,
                    "cardinality_contribution": contribution,
                }
            )
        return out

    def snapshot(
        self,
        *,
        family: str | None = None,
        name_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """The current published values of the matching series."""
        out: list[dict[str, Any]] = []
        for metric in self._metrics():
            if family and str(getattr(metric, "family", "") or metric.name) != family:
                continue
            if name_prefix and not metric.name.startswith(name_prefix):
                continue
            snapshot = self._published(metric)
            if not snapshot:
                continue
            age = self._publish_age_s(metric)
            for label_key, series in snapshot.items():
                entry: dict[str, Any] = {
                    "name": metric.name,
                    "labels": {key: value for key, value in label_key},
                    "kind": metric.kind,
                    "monotonic": self._monotonic(metric),
                    "last_publish_age_s": age,
                }
                if metric.kind == "histogram":
                    entry["value"] = None
                    entry["histogram"] = {
                        "count": series.get("count", 0),
                        "sum": series.get("sum", 0.0),
                        "buckets": list(series.get("buckets") or ()),
                    }
                else:
                    entry["value"] = series.get("value")
                out.append(entry)
        return out

    def history(
        self,
        *,
        names: Sequence[str] | None = None,
        series: Sequence[str] | None = None,
        window_s: int | None = None,
        max_points: int | None = None,
        now: int | None = None,
        max_series: int | None = None,
    ) -> list[dict[str, Any]]:
        """The windowed trend points of the matching series.

        ``series`` holds LABEL-AWARE selectors (``<name>{k=v,…}``);
        ``names`` holds NAME-level selectors (every label variant).
        A malformed selector raises :class:`InvalidLabelFilter` (the
        route maps it to ``400 invalid_label_filter``).

        ``max_series`` caps the WORK: the provider stops collecting once
        it holds that many entries (W-2).  The cap is a work bound, not
        a silent truncation of the ANSWER — the caller knows the cap it
        asked for and mints the continuation cursor itself.
        """
        if self._ring is None:
            return []
        parsed: list[tuple[str, dict[str, str]]] = []
        for raw in series or ():
            parsed.append(parse_series_selector(raw))
        name_set = set(names or ())
        if len(name_set) > MAX_NAMES:
            raise InvalidLabelFilter(
                f"at most {MAX_NAMES} names may be requested; got {len(name_set)}"
            )
        window = int(window_s) if window_s else WINDOW_SECONDS
        window = max(1, min(window, WINDOW_SECONDS))
        instant = int(now) if now is not None else int(time.time())
        limit = max_points if max_points is not None else DEFAULT_MAX_POINTS
        cap = max(1, int(max_series)) if max_series is not None else None

        out: list[dict[str, Any]] = []
        for metric in self._metrics():
            if name_set and metric.name not in name_set:
                continue
            snapshot = self._published(metric)
            if not snapshot:
                continue
            for label_key, series_data in snapshot.items():
                labels = {key: value for key, value in label_key}
                if parsed and not any(
                    selector_matches(metric.name, labels, selector)
                    for selector in parsed
                ):
                    continue
                key = series_key(metric.name, label_key)
                if metric.kind == "histogram":
                    points = [
                        (point["timestamp"], float(point["count"]))
                        for point in self._ring.histogram_points(key)
                    ]
                else:
                    points = self._ring.points(key)
                windowed = slice_window(points, now=instant, window_s=window)
                out.append(
                    {
                        "name": metric.name,
                        "labels": labels,
                        "kind": metric.kind,
                        "monotonic": self._monotonic(metric),
                        "points": [
                            [timestamp, value]
                            for timestamp, value in bucket_max(windowed, limit)
                        ],
                    }
                )
                if cap is not None and len(out) >= cap:
                    return out
        return out

    # ── the trend's freshness ───────────────────────────────────────────

    def last_sample_age_s(self) -> float:
        """The age of the newest sample in the ring (0.0 when empty)."""
        if self._ring is None:
            return 0.0
        newest = self._ring.newest_timestamp()
        if newest is None:
            return 0.0
        return max(0.0, float(time.time()) - float(newest))

    def last_sample_timestamp(self) -> int | None:
        """The newest ring sample's timestamp (``None`` when empty)."""
        if self._ring is None:
            return None
        return self._ring.newest_timestamp()

    def oldest_sample_timestamp(self) -> int | None:
        """The OLDEST ring sample's timestamp (``None`` when empty).

        The cursor's EXPIRY probe: a cursor whose pinned evaluation
        instant predates the buffer's oldest sample can no longer be
        resumed faithfully, so it is refused (``400 invalid_cursor``)
        rather than silently answered with a gap.
        """
        if self._ring is None:
            return None
        return self._ring.oldest_timestamp()


__all__ = ["InvalidLabelFilter", "RegistryMetricsQueryProvider"]
