"""The W1 CLUSTER — F0.3, F1, F3, F4, F5, F6.

Every adapter here is a **reader**: it reads an existing producer's
already-computed value and writes it into the registry.  No producer
module is edited, no new observe/inc call is added at any module site
(AD3, strict).

THE FAMILIES
------------
===========  ==================================  ==========================
family       metric(s)                           seam
===========  ==================================  ==========================
F0.3         spine tables accepting rows + rows  the resource monitor's
             written                             canonical counters + a
                                                one-shot scan of the four
                                                spine tables
F1           loop-lag quantiles + probe state    the wedge capture's lag
                                                probe histogram
F3           wedge capture counters              the durable
                                                ``wedge_captures`` table
F4           pool native quantities              the pool samplers
F5           pool derived vs native              the pool samplers +
                                                the collector's A2
                                                derivation
F6           the PostgreSQL census               the frozen
                                                ``CensusSnapshot``
===========  ==================================  ==========================

F2 (thread CPU) is NOT here — it was dropped from v1 by the VALUE
ruling (AM-25 / LR-2), not by the mechanical rule.  Its absence is
pinned by the adjudication pack (P4-M12).
"""

from __future__ import annotations

import logging
from typing import Any

from daemon.services.metrics_query.wiring import db, vocab
from daemon.services.metrics_query.wiring.accumulate import DeltaAccumulator
from daemon.services.metrics_query.wiring.registry import (
    PullAdapter,
    register_family_metric,
    register_pull_adapter,
)
from daemon.services.metrics_query.wiring.sources import WiringSources, current_sources

logger = logging.getLogger(__name__)

CLUSTER = "W1"

# ── the metric registrations ──────────────────────────────────────────────
# (the registrations happen at import time so the door discovers the
# families from the registry — there is no second family table)

spine_tables_accepting_rows = register_family_metric(
    family="F0.3",
    cluster=CLUSTER,
    name="ensemble_resource_spine_tables_accepting_rows",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/resource_monitor/flusher.py:161 + sampler.py",
    label_keys=("table",),
    allowed_values={"table": vocab.SPINE_TABLES},
    help=(
        "1 when the resource spine accepts rows for the table, 0 when it "
        "does not. resource_limit_policy accepts ZERO rows by design (it "
        "is a policy table), so it renders 1 while its row counter stays 0."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

spine_rows_written_total = register_family_metric(
    family="F0.3",
    cluster=CLUSTER,
    name="ensemble_resource_spine_rows_written_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/resource_monitor/flusher.py:161 + sampler.py",
    label_keys=("table",),
    allowed_values={"table": vocab.SPINE_TABLES},
    help=(
        "Rows written to the resource spine per table. The value is the "
        "accumulated POSITIVE delta of the table's row count, read from "
        "the durable table — a delete does not decrement the counter."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

loop_lag_seconds = register_family_metric(
    family="F1",
    cluster=CLUSTER,
    name="ensemble_loop_lag_seconds",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/wedge_capture/lag_probe.py:107-116, :185",
    label_keys=("stat",),
    allowed_values={"stat": vocab.LAG_STATS},
    help=(
        "Event-loop lag quantiles in seconds, computed by the probe's "
        "300-sample rolling window (nearest-rank). NOT a histogram: the "
        "quantiles are computed, not bucketed."
    ),
    source_idiom="wedge_capture",
    durability="process",
    cardinality_weight=1,
)

loop_lag_samples = register_family_metric(
    family="F1",
    cluster=CLUSTER,
    name="ensemble_loop_lag_samples",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/wedge_capture/lag_probe.py:185",
    help="The number of lag samples the probe's rolling window currently holds.",
    source_idiom="wedge_capture",
    durability="process",
    cardinality_weight=1,
)

loop_lag_overflow_total = register_family_metric(
    family="F1",
    cluster=CLUSTER,
    name="ensemble_loop_lag_overflow_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/wedge_capture/lag_probe.py:115-119",
    help="The number of lag samples the probe clamped into its top bucket.",
    source_idiom="wedge_capture",
    durability="process",
    cardinality_weight=1,
)

loop_lag_probe_state = register_family_metric(
    family="F1",
    cluster=CLUSTER,
    name="ensemble_loop_lag_probe_state",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/wedge_capture/lag_probe.py:145-147",
    label_keys=("state",),
    allowed_values={"state": vocab.LAG_PROBE_STATES},
    help=(
        "The lag probe's state vector (1 for the current state, 0 for the "
        "others). The probe is dark when the wedge capture is off."
    ),
    source_idiom="wedge_capture",
    durability="process",
    cardinality_weight=1,
)

wedge_capture_total = register_family_metric(
    family="F3",
    cluster=CLUSTER,
    name="ensemble_wedge_capture_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/wedge_capture/triggers.py:202/:237/:281",
    label_keys=("reason",),
    allowed_values={"reason": vocab.WEDGE_REASONS},
    help=(
        "The number of recorded wedge captures per trigger reason, read "
        "from the durable wedge_captures table."
    ),
    source_idiom="wedge_capture",
    durability="durable",
    cardinality_weight=1,
)

wedge_capture_write_total = register_family_metric(
    family="F3",
    cluster=CLUSTER,
    name="ensemble_wedge_capture_write_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/wedge_capture/watchdog.py:417/:418",
    label_keys=("outcome",),
    allowed_values={"outcome": vocab.WEDGE_WRITE_OUTCOMES},
    help=(
        "The number of wedge-capture metadata writes per outcome. Only a "
        "SUCCESSFUL write leaves a durable record, so the failed outcome "
        "renders no series while no failure counter exists."
    ),
    source_idiom="wedge_capture",
    durability="durable",
    cardinality_weight=1,
)

db_pool_native = register_family_metric(
    family="F4",
    cluster=CLUSTER,
    name="ensemble_db_pool_native",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/readiness.py:658/:685",
    label_keys=("pool", "quantity"),
    allowed_values={
        "pool": vocab.POOL_NAMES,
        "quantity": vocab.POOL_NATIVE_QUANTITIES,
    },
    help=(
        "The NATIVE pool quantities, read from the pool's own counters. A "
        "quantity the pool does not expose renders no series (absent is "
        "not zero)."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

db_pool_requests_errors_total = register_family_metric(
    family="F4",
    cluster=CLUSTER,
    name="ensemble_db_pool_requests_errors_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/wedge_capture/collector.py:24-26",
    label_keys=("pool", "kind"),
    allowed_values={
        "pool": vocab.POOL_NAMES,
        "kind": vocab.POOL_REQUEST_ERROR_KINDS,
    },
    help=(
        "The pool's request-error counter — a SUPERSET of the waiting "
        "exceptions plus the too-many-requests rejections, NOT a checkout "
        "timeout count (psycopg_pool 3.3.1 exposes no such counter)."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

db_pool_checked_out = register_family_metric(
    family="F5",
    cluster=CLUSTER,
    name="ensemble_db_pool_checked_out",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/wedge_capture/collector.py:19-20",
    label_keys=("pool",),
    allowed_values={"pool": vocab.POOL_NAMES},
    help=(
        "The DERIVED checked-out quantity (pool_size - pool_available, "
        "clamped at 0) — a separate metric so the derivation is visible "
        "in the name, not merged into a state label."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

db_pool_waiting_clients = register_family_metric(
    family="F5",
    cluster=CLUSTER,
    name="ensemble_db_pool_waiting_clients",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/wedge_capture/collector.py:23",
    label_keys=("pool",),
    allowed_values={"pool": vocab.POOL_NAMES},
    help=(
        "The NATIVE waiting-clients counter (requests_waiting). It exists "
        "on the async pool only — the manager pool renders no series."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

pg_backends = register_family_metric(
    family="F6",
    cluster=CLUSTER,
    name="ensemble_pg_backends",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/metrics_census.py (CensusSnapshot)",
    label_keys=("state",),
    allowed_values={"state": vocab.PG_BACKEND_STATES},
    help=(
        "The PostgreSQL backend count per pg_stat_activity state. The "
        "census is COMPLETE, so a state with no backend renders 0 (a real "
        "zero, not an absent series)."
    ),
    source_idiom="postgres_census",
    durability="process",
    cardinality_weight=1,
)

pg_idle_in_txn_oldest_seconds = register_family_metric(
    family="F6",
    cluster=CLUSTER,
    name="ensemble_pg_idle_in_txn_oldest_seconds",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/metrics_census.py (CensusSnapshot)",
    help=(
        "The age (seconds) of the OLDEST idle-in-transaction backend. Zero "
        "means there is no idle-in-transaction backend."
    ),
    source_idiom="postgres_census",
    durability="process",
    cardinality_weight=1,
)

pg_leaked_txn = register_family_metric(
    family="F6",
    cluster=CLUSTER,
    name="ensemble_pg_leaked_txn",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/metrics_census.py (CensusSnapshot)",
    label_keys=("site",),
    allowed_values={"site": vocab.PG_LEAK_SITES},
    help="The idle-in-transaction backend count per leak site.",
    source_idiom="postgres_census",
    durability="process",
    cardinality_weight=1,
)

pg_census_last_success_timestamp_seconds = register_family_metric(
    family="F6",
    cluster=CLUSTER,
    name="ensemble_pg_census_last_success_timestamp_seconds",
    kind="gauge",
    verdict="IN",
    seam="daemon/services/metrics_census.py (CensusSnapshot)",
    help=(
        "The wall-clock stamp of the last SUCCESSFUL census. A stale "
        "stamp is the operator's cue that the census is dark."
    ),
    source_idiom="postgres_census",
    durability="process",
    cardinality_weight=1,
)


# ── the accumulators (the metrics layer's own accumulation) ───────────────
_spine_rows = DeltaAccumulator()


# ── the F0.3 adapter ──────────────────────────────────────────────────────
_SPINE_COUNT_SQL = (
    "SELECT (SELECT COUNT(*) FROM resource_daemon_bucket) AS t0, "
    "(SELECT COUNT(*) FROM resource_instance_sample) AS t1, "
    "(SELECT COUNT(*) FROM resource_pid_map) AS t2, "
    "(SELECT COUNT(*) FROM resource_limit_policy) AS t3"
)


def _spine_healthy(sources: WiringSources) -> bool:
    """Whether the spine's canonical write-side counters say it is alive.

    The resource monitor's counters are the CANONICAL source for the
    spine's health (the catalog's WS-A canonical-counters rule): the
    flusher's error count plus the sampler's produced-sample count.
    """
    monitor = sources.resource_monitor
    if monitor is None:
        return False
    try:
        flusher = monitor.flusher_counters()
        sampler = monitor.sampler_counters()
    except Exception:  # noqa: BLE001 — an unreadable monitor ⇒ not healthy
        return False
    try:
        return int(flusher.errors) == 0 and int(sampler.ticks) > 0
    except Exception:  # noqa: BLE001
        return False


def adapt_f0_3(sampler: Any) -> None:
    """F0.3 — the resource spine's health + row counters."""
    sources = current_sources()
    monitor = sources.resource_monitor
    if monitor is None:
        # No spine ⇒ NO series (absent is not zero).
        return
    healthy = _spine_healthy(sources)

    counts: dict[str, int] = {}
    if sources.dsn:
        with db.one_shot(sources.dsn, application_name="ensemble-metrics-w1-spine", connect=sources.db_connect) as conn:
            rows = db.fetch_rows(conn, _SPINE_COUNT_SQL)
        if rows and rows[0]:
            values = rows[0]
            for table, value in zip(vocab.SPINE_TABLES, values):
                try:
                    counts[table] = int(value)
                except (TypeError, ValueError):
                    continue

    for table in vocab.SPINE_TABLES:
        if table == "resource_limit_policy":
            # The policy table accepts ZERO rows BY DESIGN — it renders 1
            # whenever the spine is reachable (the catalog's F0.3
            # asymmetry: the gauge answers "does the table accept rows",
            # the counter answers "how many rows are there").
            accepting = 1
        else:
            accepting = 1 if healthy else 0
        spine_tables_accepting_rows.set(accepting, table=table)

    for table, count in counts.items():
        delta = _spine_rows.observe(("F0.3", table), count)
        if delta > 0:
            spine_rows_written_total.inc(delta, table=table)


# ── the F1 adapter ────────────────────────────────────────────────────────
def _lag_probe(sources: WiringSources) -> Any:
    """The wedge capture's lag probe (a READ of existing state).

    The watchdog keeps the probe in a private attribute; the probe is
    the ONLY holder of the computed quantiles, and the watchdog's own
    status surface publishes only the probe STATE.  This is a read, not
    an edit — the watchdog module is untouched by this lane.
    """
    watchdog = sources.wedge_watchdog
    if watchdog is None:
        return None
    return getattr(watchdog, "_lag_probe", None)


def adapt_f1(sampler: Any) -> None:
    """F1 — the loop-lag quantiles + the probe state."""
    sources = current_sources()
    probe = _lag_probe(sources)
    if probe is None:
        return
    try:
        histogram = probe.snapshot_histogram()
        state = str(probe.probe_state)
    except Exception:  # noqa: BLE001 — a broken probe degrades the family
        logger.debug("metrics wiring: F1 probe read failed", exc_info=True)
        return
    if not isinstance(histogram, dict):
        return

    for stat in vocab.LAG_STATS:
        value = histogram.get(stat)
        if value is None:
            continue
        try:
            loop_lag_seconds.set(float(value), stat=stat)
        except (TypeError, ValueError):
            continue

    samples = histogram.get("samples")
    if samples is not None:
        try:
            loop_lag_samples.set(int(samples))
        except (TypeError, ValueError):
            pass

    overflow = histogram.get("overflow_count")
    if overflow is not None:
        try:
            loop_lag_overflow_total.set(int(overflow))
        except (TypeError, ValueError):
            pass

    for candidate in vocab.LAG_PROBE_STATES:
        loop_lag_probe_state.set(1 if candidate == state else 0, state=candidate)


# ── the F3 adapter ────────────────────────────────────────────────────────
_WEDGE_CAPTURE_COUNT_SQL = (
    "SELECT trigger_reason, COUNT(*) FROM wedge_captures GROUP BY trigger_reason"
)


def adapt_f3(sampler: Any) -> None:
    """F3 — the wedge capture counters (the durable capture table)."""
    sources = current_sources()
    if not sources.dsn:
        return
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w1-wedge", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, _WEDGE_CAPTURE_COUNT_SQL)
    if not rows:
        return
    total = 0
    for reason, count in rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        total += value
        token = str(reason)
        if token not in vocab.WEDGE_REASONS:
            # A reason outside the canonical set folds to the reserved
            # residual — the registry counts it as an invalid label.
            token = vocab.RESERVED
        wedge_capture_total.set(value, reason=token)
    # Only a SUCCESSFUL metadata write leaves a durable row, so the
    # successful outcome is the total; the failed outcome has no durable
    # record today and therefore renders no series.
    wedge_capture_write_total.set(total, outcome="ok")


# ── the F4 / F5 adapters ──────────────────────────────────────────────────
def _pool_states(sources: WiringSources) -> tuple[dict[str, Any], dict[str, Any]]:
    """The manager pool's native sample + the saver pool's A2 block."""
    manager_state: dict[str, Any] = {}
    saver_state: dict[str, Any] = {}
    manager = sources.manager
    if manager is None:
        return manager_state, saver_state
    try:
        from daemon.services.readiness import (
            resolve_psycopg_pool,
            sample_psycopg_pool_state,
            sample_sqlalchemy_pool_state,
        )

        engine = getattr(manager, "engine", None)
        pool = getattr(engine, "pool", None) if engine is not None else None
        sampled = sample_sqlalchemy_pool_state(pool)
        if sampled:
            manager_state = sampled

        saver_pool = resolve_psycopg_pool(getattr(manager, "checkpointer", None))
        if saver_pool is not None:
            # The READINESS sampler's shape (pool_size / pool_max /
            # pool_available / requests_waiting) — the F4 catalog seam.
            sampled_psycopg = sample_psycopg_pool_state(saver_pool)
            if sampled_psycopg:
                saver_state.update(sampled_psycopg)
            # The COLLECTOR's A2 block — the request-errors counter and
            # the DERIVED checked-out quantity.
            from daemon.services.wedge_capture import collector as _collector

            collected = _collector.collect_pool(saver_pool)
            if isinstance(collected, dict):
                saver_state.update(collected)
    except Exception:  # noqa: BLE001 — a missing pool degrades the family
        logger.debug("metrics wiring: pool read failed", exc_info=True)
    return manager_state, saver_state


def adapt_f4_f5(sampler: Any) -> None:
    """F4 (native) + F5 (derived) — the two pool families, kept APART."""
    sources = current_sources()
    manager_state, saver_state = _pool_states(sources)

    # ── F4 — the NATIVE quantities ──────────────────────────────────
    if manager_state:
        size = manager_state.get("size")
        overflow = manager_state.get("overflow")
        if size is not None:
            db_pool_native.set(int(size), pool="manager", quantity="size")
        if overflow is not None:
            db_pool_native.set(int(overflow), pool="manager", quantity="overflow")
        # The manager pool exposes no native `max` / `available` — they
        # render no series (never a fabricated value).

    if saver_state:
        size = saver_state.get("pool_size_current")
        max_size = saver_state.get("pool_max")
        available = saver_state.get("pool_available")
        if size is not None:
            db_pool_native.set(int(size), pool="checkpoint", quantity="size")
        if max_size is not None:
            db_pool_native.set(int(max_size), pool="checkpoint", quantity="max")
        if available is not None:
            db_pool_native.set(int(available), pool="checkpoint", quantity="available")
        errors = saver_state.get("pool_requests_errors")
        if errors is not None:
            db_pool_requests_errors_total.set(
                int(errors), pool="checkpoint", kind="unclassified"
            )

    # ── F5 — the DERIVED / native-but-separate quantities ───────────
    checked_out = saver_state.get("pool_checked_out")
    if checked_out is not None:
        db_pool_checked_out.set(int(checked_out), pool="checkpoint")
    waiting = saver_state.get("pool_waiting_clients")
    if waiting is not None:
        db_pool_waiting_clients.set(int(waiting), pool="checkpoint")
    if manager_state:
        # The manager pool's checked-out value is its OWN native counter
        # (the SQLAlchemy pool has no size/available pair to subtract).
        checked = manager_state.get("checkedout")
        if checked is not None:
            db_pool_checked_out.set(int(checked), pool="manager")


# ── the F6 adapter ────────────────────────────────────────────────────────
def adapt_f6(sampler: Any) -> None:
    """F6 — the PostgreSQL census gauges over the frozen snapshot."""
    sources = current_sources()
    census = sources.census
    if census is None:
        # A non-postgres runtime (or a census that never started) has no
        # snapshot — the family renders NO series (P4-M5).
        return
    try:
        snapshot = census.snapshot
    except Exception:  # noqa: BLE001 — a broken census degrades the family
        logger.debug("metrics wiring: F6 census read failed", exc_info=True)
        return
    if snapshot is None:
        return

    state_counts = dict(getattr(snapshot, "state_counts", {}) or {})
    for state in vocab.PG_BACKEND_STATES:
        pg_backends.set(int(state_counts.get(state, 0)), state=state)

    by_site = dict(getattr(snapshot, "idle_in_txn_by_site", {}) or {})
    for site in vocab.PG_LEAK_SITES:
        pg_leaked_txn.set(int(by_site.get(site, 0)), site=site)

    max_age_by_site = dict(getattr(snapshot, "idle_in_txn_max_age_by_site", {}) or {})
    ages = [float(v) for v in max_age_by_site.values() if v is not None]
    pg_idle_in_txn_oldest_seconds.set(max(ages) if ages else 0.0)

    last_success = getattr(snapshot, "last_success_epoch", None)
    if last_success is not None:
        pg_census_last_success_timestamp_seconds.set(float(last_success))


def register_w1_adapters() -> tuple[PullAdapter, ...]:
    """Register the W1 pull adapters (the sampler's stage A)."""
    adapters: tuple[PullAdapter, ...] = (
        adapt_f0_3,
        adapt_f1,
        adapt_f3,
        adapt_f4_f5,
        adapt_f6,
    )
    for adapter in adapters:
        register_pull_adapter(adapter.__doc__ or "", adapter)
    return adapters


__all__ = [
    "adapt_f0_3",
    "adapt_f1",
    "adapt_f3",
    "adapt_f4_f5",
    "adapt_f6",
    "register_w1_adapters",
]
