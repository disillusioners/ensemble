"""The W4 CLUSTER — F23, the nest-anomaly family.

ONE family, FIVE metrics, ONE adapter.  The family reads the
``nest-anomaly-report`` lane's two durable tables (``anomaly_reports``
+ ``anomaly_clusters``) plus TWO write-time process counters the
SERVICE increments at their refusal/suppression points.

THE FIVE METRICS
----------------
=========================================================  =========  ========  ==========
metric                                                     kind       labels    durability
=========================================================  =========  ========  ==========
``ensemble_anomaly_reports_total``                          counter    category  durable
``ensemble_anomaly_clusters_open``                          gauge      category  durable
                                                                       severity
``ensemble_anomaly_reports_rate_limited_total``             counter    category  durable
``ensemble_anomaly_board_items_suppressed_total``           counter    —         process
``ensemble_anomaly_capacity_exhausted_total``               counter    —         process
=========================================================  =========  ========  ==========

THE TWO WRITE-TIME COUNTERS ARE NOT ADAPTER-FED (AM-13f + C3)
--------------------------------------------------------------
``ensemble_anomaly_board_items_suppressed_total`` counts the
AM-13 board-ceiling suppressions (a NOTE appended instead of a new
board item) and ``ensemble_anomaly_capacity_exhausted_total``
counts the C3 GLOBAL capacity-wall refusals.  Neither is a table
aggregate, so the pull adapter NEVER sets them: the anomaly SERVICE
increments each handle at its write point through a LAZY import
(the service must not import the wiring package at module top
level — that would drag w1/w2/w3 into the service's import graph
for one counter).

AM-2d — THE REGISTRY FOLDS, THE ADAPTER DOES NOT
-------------------------------------------------
The adapter passes the RAW stored token to ``.set()`` and lets the
REGISTRY fold a disallowed value to the reserved one
(``daemon/checkpoint_metrics.py`` ``_fold_labels`` +
``_count_invalid_label`` — the folded key is counted in
``ensemble_metrics_invalid_label_total{metric,label}``).  The older
W1/F3 adapter PRE-FOLDS (``w1.py`` clamps to ``vocab.RESERVED``
BEFORE ``.set()``); AM-2d SUPERSEDES that shape for F23: a
pre-folded ``other`` is indistinguishable from a genuine ``other``,
so the registry's own invalid-label counter can never fire and
vocabulary drift is silently absorbed.  Passing the raw token gives
identical published output and turns that counter into a real drift
signal at zero cost.  (The stored ``category`` is folded at WRITE
time per R1, so in steady state every token is chart-legal and the
counter stays at zero — it fires only on drift.)

AM-7 — PEAK SEVERITY LABELLING
------------------------------
``ensemble_anomaly_clusters_open`` counts open clusters by
canonical category, labelled by their PEAK severity
(``max_severity`` is a max-rank rollup over the cluster's window —
"critical (peak)" = the highest severity seen in that window, never
a "current" severity).

AM-2(e) / NR-11 MONOTONIC DISCLOSURE
------------------------------------
The two stored-row counters carry ``kind=counter`` (so the wire
derives ``monotonic: true``) although the VALUE can DECREASE when
retention prunes rows — the house precedent is F3's
``ensemble_wedge_capture_total`` (a COUNT over stored rows).  The
help text says "currently stored" so a step chart of the value is
honest; the FE does not consume the monotonic flag.  The general
fix is routed to the central-metrics lane, not this one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from daemon.services.metrics_query.wiring import db, vocab
from daemon.services.metrics_query.wiring.registry import (
    PullAdapter,
    register_family_metric,
    register_pull_adapter,
)
from daemon.services.metrics_query.wiring.sources import current_sources

logger = logging.getLogger(__name__)

CLUSTER = "W4"

# ── the metric registrations (the import IS the registration) ─────────────

anomaly_reports_total = register_family_metric(
    family="F23",
    cluster=CLUSTER,
    name="ensemble_anomaly_reports_total",
    kind="counter",
    verdict="IN",
    seam="anomaly_reports (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23",
    label_keys=("category",),
    allowed_values={"category": vocab.ANOMALY_CATEGORIES},
    help=(
        "The number of ACCEPTED anomaly reports currently stored, by "
        "canonical category (a stored-row count, not a monotonic "
        "increment — retention can lower it). Only "
        "outcome='accepted' rows are counted; the rate-limited "
        "refusals are counted by "
        "ensemble_anomaly_reports_rate_limited_total."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

anomaly_clusters_open = register_family_metric(
    family="F23",
    cluster=CLUSTER,
    name="ensemble_anomaly_clusters_open",
    kind="gauge",
    verdict="IN",
    seam="anomaly_clusters (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23",
    label_keys=("category", "severity"),
    allowed_values={
        "category": vocab.ANOMALY_CATEGORIES,
        "severity": vocab.ANOMALY_SEVERITIES,
    },
    help=(
        "The number of OPEN anomaly clusters by canonical category, "
        "labelled by their PEAK severity (critical (peak) = the highest "
        "severity seen in that cluster's window — max_severity never "
        "de-escalates). Open is DERIVED, never stored: "
        "last_report_at >= now - 1 hour."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

anomaly_reports_rate_limited_total = register_family_metric(
    family="F23",
    cluster=CLUSTER,
    name="ensemble_anomaly_reports_rate_limited_total",
    kind="counter",
    verdict="IN",
    seam="anomaly_reports.outcome (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23",
    label_keys=("category",),
    allowed_values={"category": vocab.ANOMALY_CATEGORIES},
    help=(
        "The number of rate-limited anomaly REPORT REFUSALS by canonical "
        "category (a stored-row count of outcome='rate_limited' ledger "
        "rows, not a monotonic increment — retention can lower it). The "
        "refusal IS the spam signal (R5b): refusals never consume the "
        "accepted budget and are never folded into the accepted total."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

# AM-13f — the board-suppression counter.  Write-time (the anomaly
# service's board-suppression point increments it through a LAZY import
# of this module-level handle); NOT adapter-fed; process-durable (an
# in-RAM count that resets on restart — honest for a suppression tally).
board_items_suppressed = register_family_metric(
    family="F23",
    cluster=CLUSTER,
    name="ensemble_anomaly_board_items_suppressed_total",
    kind="counter",
    verdict="IN",
    seam="the anomaly service's board-suppression point + daemon/services/metrics_query/wiring/w4.py (write-time increment)",
    label_keys=(),
    allowed_values=None,
    help=(
        "The number of board-item CREATIONS suppressed by the AM-13 board "
        "ceilings (a NOTE was appended to the tree's most recent anomaly "
        " board item instead). A process-durable in-RAM count that resets "
        "on restart."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

# C3(i) — the capacity-exhausted counter.  Write-time (the anomaly
# service's capacity-refusal point increments it through a LAZY import
# of this module-level handle); NOT adapter-fed; process-durable.
# THE SIGNAL DOMAIN IS THE GLOBAL WALL ONLY — see the help text.
capacity_exhausted = register_family_metric(
    family="F23",
    cluster=CLUSTER,
    name="ensemble_anomaly_capacity_exhausted_total",
    kind="counter",
    verdict="IN",
    seam="the anomaly service's capacity-refusal point + daemon/services/metrics_query/wiring/w4.py (write-time increment)",
    label_keys=(),
    allowed_values=None,
    help=(
        "Refusals at the GLOBAL capacity wall (max_rows_global / "
        "max_accepted_per_hour_global) — the nest-wide bound. The "
        "per-tree cap does not fire here: it TRIMS the tree's oldest "
        "rows instead of refusing. A non-zero rate means the nest is at "
        "its storage ceiling and new reports (including criticals) are "
        "being refused."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)


# ── the F23 pull adapter ──────────────────────────────────────────────────

#: The three aggregate statements, against phase 1's verbatim column
#: names.  AM-4: the ``::timestamptz`` cast is BELT-AND-BRACES (the
#: fixed-width TEXT producer is lexicographically order-safe; the cast
#: removes the PG-rendered-right-hand-side hazard of a TEXT compare).
#: PG-only syntax is fine — ``resolve_dsn`` returns ``None`` off-PG
#: and the adapter's ``dsn is None`` early return covers SQLite.
_ANOMALY_REPORTS_ACCEPTED_SQL = (
    "SELECT category, COUNT(*) FROM anomaly_reports "
    "WHERE outcome = 'accepted' GROUP BY category"
)

_ANOMALY_REPORTS_RATE_LIMITED_SQL = (
    "SELECT category, COUNT(*) FROM anomaly_reports "
    "WHERE outcome = 'rate_limited' GROUP BY category"
)

#: ADVISORY-⑦ (code council fix cycle, 2026-11-05): the open-clusters
#: cutoff is COMPUTED IN PYTHON from ``NestReportConfig().
#: resolve_window_seconds`` and BOUND as a query parameter — the SQL
#: carries no ``interval`` literal, so the knob cannot silently desync
#: from the query (the pre-fix shape hardcoded ``interval '1 hour'``,
#: decoupled from the knob).
_ANOMALY_CLUSTERS_OPEN_SQL = (
    "SELECT category, max_severity, COUNT(*) FROM anomaly_clusters "
    "WHERE last_report_at::timestamptz >= %s "
    "GROUP BY category, max_severity"
)


def _open_clusters_cutoff(
    now: datetime | None = None,
    window_seconds: int | None = None,
) -> datetime:
    """The open-clusters cutoff — derived from the resolve-window knob.

    AM-4: the cutoff is computed in Python (single source:
    ``NestReportConfig().resolve_window_seconds`` — the same knob the
    anomaly service's resolution sweep reads) and bound as a parameter
    by :func:`adapt_f23`.  The import is LAZY so ``daemon.config``
    stays off the wiring package's import graph (the same discipline
    the write-time counters use in this module).
    """
    from daemon.config import NestReportConfig  # LAZY (see docstring)

    window = (
        window_seconds
        if window_seconds is not None
        else NestReportConfig().resolve_window_seconds
    )
    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(seconds=window)


def adapt_f23(sampler: Any) -> None:
    """F23 — the anomaly family (the two durable tables).

    The house pull-adapter shape: resolve the sources, early-return on
    no DSN, run the three aggregates on ONE one-shot psycopg connection
    (closed inside the tick, never a pool), guard every count with
    ``int()``, and hand the RAW stored token to ``.set()`` — the
    REGISTRY folds a disallowed value (AM-2d), which keeps
    ``ensemble_metrics_invalid_label_total`` a live drift signal.

    The TWO write-time counters (``ensemble_anomaly_board_items_
    suppressed_total`` / ``ensemble_anomaly_capacity_exhausted_total``)
    are NOT set here — the anomaly service increments them at their
    write points.
    """
    sources = current_sources()
    if not sources.dsn:
        return
    with db.one_shot(
        sources.dsn,
        application_name="ensemble-metrics-w4-anomaly",
        connect=sources.db_connect,
    ) as conn:
        report_rows = db.fetch_rows(conn, _ANOMALY_REPORTS_ACCEPTED_SQL)
        limited_rows = db.fetch_rows(conn, _ANOMALY_REPORTS_RATE_LIMITED_SQL)
        open_rows = db.fetch_rows(
            conn,
            _ANOMALY_CLUSTERS_OPEN_SQL,
            params=(_open_clusters_cutoff(),),
        )

    # AM-2d — the RAW stored token reaches ``.set()``; the registry
    # folds.  There is NO ``token not in vocab.ANOMALY_CATEGORIES``
    # clamp here on purpose (the F3 pre-fold shape is superseded).
    for category, count in report_rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        anomaly_reports_total.set(value, category=str(category))
    for category, count in limited_rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        anomaly_reports_rate_limited_total.set(value, category=str(category))
    for category, severity, count in open_rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        anomaly_clusters_open.set(
            value, category=str(category), severity=str(severity)
        )


def register_w4_adapters() -> tuple[PullAdapter, ...]:
    """Register the W4 pull adapter (the sampler's stage A)."""
    adapters: tuple[tuple[str, PullAdapter], ...] = (
        ("F23", adapt_f23),
    )
    for family, adapter in adapters:
        register_pull_adapter(family, adapter)
    return tuple(adapter for _family, adapter in adapters)
