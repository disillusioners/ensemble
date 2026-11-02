"""The metrics query substrate — the ONE read substrate of the door.

This package is the additive substrate the query door stands on:

* :class:`~daemon.services.metrics_query.protocol.MetricsQueryProvider`
  — the HTTP-agnostic provider protocol;
* :class:`~daemon.services.metrics_query.provider.RegistryMetricsQueryProvider`
  — the registry-backed provider (published state only);
* :class:`~daemon.services.metrics_query.ring.TrendRingStore` — the
  array-backed in-RAM trend ring (16 B/point);
* :class:`~daemon.services.metrics_query.sampler.MetricsSampler` — the
  cadence-driven sampler thread;
* the envelope builders, the availability model and the chart-primary
  roster.

IMPORT-SIDE-EFFECT-FREE (plan task 2.1): importing this package
starts NO thread, opens NO connection and registers NO metric.  The
package is a pure library; the lifespan wires the provider and the
sampler.

The query package NEVER reads the kill-switch: the ``census_gated``
reason is sourced from the PUBLIC ``CensusSampler.gated`` property (a
read-through of the existing gate site), so the switch keeps being
consulted at exactly two sites.
"""

from __future__ import annotations

from daemon.services.metrics_query.availability import (
    AVAILABILITY_REASONS,
    AvailabilityFacts,
    derive_availability,
)
from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_NAMES,
    POINT_BUDGET,
    QUERY_SCHEMA_VERSION,
    RESPONSE_BYTE_CEILING,
    STALE_INTERVAL_MULTIPLIER,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.downsample import bucket_max, slice_window
from daemon.services.metrics_query.envelope import (
    InvalidCursor,
    activation_block,
    build_families_envelope,
    build_series_envelope,
    build_snapshot_envelope,
    cursor_is_expired,
    decode_cursor,
    effective_interval_s,
    encode_cursor,
    envelope_header,
    restart_epoch,
)
from daemon.services.metrics_query.protocol import MetricsQueryProvider
from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider
from daemon.services.metrics_query.ring import (
    BYTES_PER_POINT,
    HistogramTrendRing,
    TrendRing,
    TrendRingStore,
    compute_depth,
    series_key,
)
from daemon.services.metrics_query.roster import (
    CHART_PRIMARY_ROSTER,
    CHART_ROLE_LINE,
    CHART_ROLE_STEP,
    RosterFamily,
    RosterSeries,
    roster_family_names,
    roster_role_for,
)
from daemon.services.metrics_query.sampler import MetricsSampler, PullAdapter
from daemon.services.metrics_query.selectors import (
    InvalidLabelFilter,
    format_series_selector,
    parse_series_selector,
    selector_matches,
)

__all__ = [
    "AVAILABILITY_REASONS",
    "AvailabilityFacts",
    "BYTES_PER_POINT",
    "CHART_PRIMARY_ROSTER",
    "CHART_ROLE_LINE",
    "CHART_ROLE_STEP",
    "DEFAULT_MAX_POINTS",
    "HistogramTrendRing",
    "InvalidCursor",
    "InvalidLabelFilter",
    "MAX_NAMES",
    "MetricsQueryProvider",
    "MetricsSampler",
    "POINT_BUDGET",
    "PullAdapter",
    "QUERY_SCHEMA_VERSION",
    "RESPONSE_BYTE_CEILING",
    "RegistryMetricsQueryProvider",
    "RosterFamily",
    "RosterSeries",
    "STALE_INTERVAL_MULTIPLIER",
    "TrendRing",
    "TrendRingStore",
    "WINDOW_SECONDS",
    "activation_block",
    "bucket_max",
    "build_families_envelope",
    "build_series_envelope",
    "build_snapshot_envelope",
    "compute_depth",
    "cursor_is_expired",
    "decode_cursor",
    "derive_availability",
    "effective_interval_s",
    "encode_cursor",
    "envelope_header",
    "format_series_selector",
    "parse_series_selector",
    "restart_epoch",
    "roster_family_names",
    "roster_role_for",
    "selector_matches",
    "series_key",
    "slice_window",
]
