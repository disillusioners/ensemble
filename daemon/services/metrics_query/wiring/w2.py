"""The W2 CLUSTER — F7, F8, F9, F10, F11, F12, F13.

Every adapter here is a **reader**.  The one PRODUCER edit this lane
ships is the F7 bytes call site (``daemon/persistence.py``) — an
existing observe that gains a REAL value; no new observe POINT is
created (phase4-plan.md 4.5).

THE FAMILIES
------------
=========  =====================================  ==========================
family     metric(s)                             seam
=========  =====================================  ==========================
F7         the saver-op BYTES histogram           the existing saver-op
                                                 observe at the aget
                                                 call site
F8         the checkpoint store sizes             the maintenance module's
                                                 table-bytes query
F9         readiness (the PUBLISHED composite)    ``/readyz``'s published
                                                 composite — never a
                                                 re-probe
F10        the job-queue depth                    ``job_queue_items``
                                                 ``admission_state``
F11        the worker pool (PARTIAL 16/30)        ``WorkerPool.get_stats``
F12        the sweep counters                     the sweep services'
                                                 ``counters()``
F13        the instance census (PARTIAL 19/29)    the instance repository
                                                 census + the
                                                 waiting-children query
=========  =====================================  ==========================

THE OUT METRICS OF THE PARTIAL FAMILIES
---------------------------------------
F11's claim latency and F13's wake-turn / lifecycle counters are NOT
registered — they would need a NEW increment at a producer site (AD3).
Their absence is pinned by the adjudication pack.
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

CLUSTER = "W2"

#: The F7 bytes histogram's buckets (catalog §8.11 — the byte set, not
#: the latency set).
CHECKPOINT_OP_BYTES_BUCKETS: tuple[float, ...] = (
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
)

# ── the metric registrations ──────────────────────────────────────────────
checkpoint_op_bytes = register_family_metric(
    family="F7",
    cluster=CLUSTER,
    name="ensemble_checkpoint_op_bytes",
    kind="histogram",
    verdict="PARTIAL",
    seam="daemon/persistence.py:632 (the existing saver-op observe)",
    label_keys=("op",),
    allowed_values={"op": ("aget", "other")},
    buckets=CHECKPOINT_OP_BYTES_BUCKETS,
    help=(
        "The saver-op payload size in bytes, labeled by op. The value is "
        "the REAL byte count observed at the existing saver-op call site."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

checkpoint_blobs_table_bytes = register_family_metric(
    family="F8",
    cluster=CLUSTER,
    name="ensemble_checkpoint_blobs_table_bytes",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/maintenance.py:531-536",
    help=(
        "The total on-disk size of the three checkpoint tables, split out "
        "of the maintenance module's table-bytes query."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

checkpoint_table_bytes = register_family_metric(
    family="F8",
    cluster=CLUSTER,
    name="ensemble_checkpoint_table_bytes",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/maintenance.py:531-536",
    label_keys=("table",),
    allowed_values={"table": vocab.CHECKPOINT_TABLES},
    help="The on-disk size of each checkpoint table, in bytes.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

readiness_probe_total = register_family_metric(
    family="F9",
    cluster=CLUSTER,
    name="ensemble_readiness_probe_total",
    kind="counter",
    verdict="IN",
    seam="daemon/api.py:5015 (the published composite)",
    label_keys=("component", "outcome"),
    allowed_values={
        "component": vocab.READINESS_COMPONENTS,
        "outcome": vocab.READINESS_OUTCOMES,
    },
    help=(
        "The number of readiness observations per component and outcome "
        "class, accumulated by the metrics layer over the PUBLISHED "
        "composite. The composite is never re-probed."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

readiness_grace_active = register_family_metric(
    family="F9",
    cluster=CLUSTER,
    name="ensemble_readiness_grace_active",
    kind="gauge",
    verdict="IN",
    seam="daemon/api.py:5015 (the published composite)",
    help="1 while a class-gated readiness grace is held, 0 otherwise.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

readiness_failure_streak = register_family_metric(
    family="F9",
    cluster=CLUSTER,
    name="ensemble_readiness_failure_streak",
    kind="gauge",
    verdict="IN",
    seam="daemon/api.py:5015 (the published composite)",
    label_keys=("component",),
    allowed_values={"component": vocab.READINESS_COMPONENTS},
    help="The consecutive-failure streak per readiness component.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

readiness_component_state = register_family_metric(
    family="F9",
    cluster=CLUSTER,
    name="ensemble_readiness_component_state",
    kind="gauge",
    verdict="IN",
    seam="daemon/api.py:5015 (the published composite)",
    label_keys=("component",),
    allowed_values={"component": vocab.READINESS_COMPONENTS},
    help=(
        "1 when the readiness component is healthy, 0 when it is degraded "
        "(the composite's own failure class)."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

job_queue_depth = register_family_metric(
    family="F10",
    cluster=CLUSTER,
    name="ensemble_job_queue_depth",
    kind="gauge",
    verdict="IN",
    seam="daemon/repositories/job_queue/models.py:40-43",
    label_keys=("admission_state",),
    allowed_values={"admission_state": vocab.admission_states()},
    help="The number of job-queue items per admission state.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

dead_letter_items = register_family_metric(
    family="F10",
    cluster=CLUSTER,
    name="ensemble_dead_letter_items",
    kind="gauge",
    verdict="IN",
    seam="daemon/repositories/job_queue/models.py:40-43",
    help="The number of dead-lettered job-queue items.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

job_queue_items_created_total = register_family_metric(
    family="F10",
    cluster=CLUSTER,
    name="ensemble_job_queue_items_created_total",
    kind="counter",
    verdict="IN",
    seam="daemon/repositories/job_queue/models.py:40-43",
    help="The cumulative number of created job-queue items.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

workers_total = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_workers_total",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/worker_pool.py:1518",
    label_keys=("pool",),
    allowed_values={"pool": vocab.WORKER_POOL_NAMES},
    help="The number of workers in the pool.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

workers_busy = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_workers_busy",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/instance_read.py:2092",
    label_keys=("pool",),
    allowed_values={"pool": vocab.WORKER_POOL_NAMES},
    help="The number of workers currently holding a task.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

worker_pool_wakeup_efficiency = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_worker_pool_wakeup_efficiency",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/worker_pool.py:1537",
    label_keys=("pool",),
    allowed_values={"pool": vocab.WORKER_POOL_NAMES},
    help=(
        "The share of wakeups that produced useful work "
        "(notifications / (notifications + empty claims))."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

worker_pool_events_total = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_worker_pool_events_total",
    kind="counter",
    verdict="PARTIAL",
    seam="daemon/services/worker_pool.py:1381-1386",
    label_keys=("pool", "event"),
    allowed_values={
        "pool": vocab.WORKER_POOL_NAMES,
        "event": vocab.WORKER_POOL_EVENTS,
    },
    help="The worker pool's own event counters, read from get_stats().",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

task_heartbeat_age_seconds = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_task_heartbeat_age_seconds",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/repositories/task/models.py:284",
    help=(
        "The age of the oldest task heartbeat, in seconds. Zero means "
        "there are no tasks at all (a real zero)."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

task_heartbeat_cadence_seconds = register_family_metric(
    family="F11",
    cluster=CLUSTER,
    name="ensemble_task_heartbeat_cadence_seconds",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/services/worker_pool.py:45",
    help=(
        "The CONFIGURED heartbeat cadence, published so the operator can "
        "tell a stale heartbeat from a long cadence."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

# NOTE — F12's ``ensemble_sweep_cycle_seconds`` is NOT registered: no
# sweep service publishes an observed cycle DURATION (the ``counters()``
# readers carry ticks / row counts / errors only), and publishing the
# CONFIGURED interval under an "observed duration" name would be a
# fabrication.  A new timing call at a sweep site is instrumentation
# (AD3) ⇒ the metric is OUT of v1 (a disclosed shortfall against the
# catalog's +69 estimate — the family lands at its honest count).


sweep_cycles_total = register_family_metric(
    family="F12",
    cluster=CLUSTER,
    name="ensemble_sweep_cycles_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/watch_reconcile_sweep.py:356",
    label_keys=("lane",),
    allowed_values={"lane": vocab.SWEEP_LANES},
    help=(
        "The cumulative number of sweep cycles per lane — accumulated by "
        "the metrics layer over the producer's per-tick counters."
    ),
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

sweep_errors_total = register_family_metric(
    family="F12",
    cluster=CLUSTER,
    name="ensemble_sweep_errors_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/watch_reconcile_sweep.py:356",
    label_keys=("lane",),
    allowed_values={"lane": vocab.SWEEP_LANES},
    help="The cumulative number of sweep errors per lane.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

sweep_rows_total = register_family_metric(
    family="F12",
    cluster=CLUSTER,
    name="ensemble_sweep_rows_total",
    kind="counter",
    verdict="IN",
    seam="daemon/services/watch_reconcile_sweep.py:356",
    label_keys=("lane", "outcome"),
    allowed_values={
        "lane": vocab.SWEEP_LANES,
        "outcome": vocab.SWEEP_ROW_OUTCOMES,
    },
    help="The cumulative number of sweep rows per lane and outcome.",
    source_idiom="registry",
    durability="process",
    cardinality_weight=1,
)

instance_status_count = register_family_metric(
    family="F13",
    cluster=CLUSTER,
    name="ensemble_instance_status_count",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/instance_read.py:2019",
    label_keys=("status",),
    allowed_values={"status": vocab.instance_statuses()},
    help="The number of instances per status.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

instance_liveness_count = register_family_metric(
    family="F13",
    cluster=CLUSTER,
    name="ensemble_instance_liveness_count",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/tools/instance_read.py:2019",
    label_keys=("liveness",),
    allowed_values={"liveness": vocab.INSTANCE_LIVENESS},
    help="The number of instances per liveness class.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

waiting_children_count = register_family_metric(
    family="F13",
    cluster=CLUSTER,
    name="ensemble_waiting_children_count",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/repositories/instance (the waiting-children query)",
    help="The number of instances waiting for their children.",
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)

waiting_children_max_age_seconds = register_family_metric(
    family="F13",
    cluster=CLUSTER,
    name="ensemble_waiting_children_max_age_seconds",
    kind="gauge",
    verdict="PARTIAL",
    seam="daemon/repositories/instance (the waiting-children query)",
    help=(
        "The age of the oldest waiting-children instance, in seconds. "
        "Zero means there is none (a real zero)."
    ),
    source_idiom="registry",
    durability="durable",
    cardinality_weight=1,
)


# ── the accumulators (the metrics layer's own accumulation) ───────────────
_sweep_deltas = DeltaAccumulator()
_readiness_observations = DeltaAccumulator()
_queue_created = DeltaAccumulator()


# ── the F8 adapter ────────────────────────────────────────────────────────
_TABLE_BYTES_SQL = (
    "SELECT c.relname, COALESCE(pg_relation_size(c.oid), 0) "
    "FROM pg_class c WHERE c.oid IN ("
    "'checkpoints'::regclass, 'checkpoint_writes'::regclass, "
    "'checkpoint_blobs'::regclass)"
)


def adapt_f8(sampler: Any) -> None:
    """F8 — the checkpoint store sizes (the maintenance module's query)."""
    sources = current_sources()
    if not sources.dsn:
        return
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w2-store", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, _TABLE_BYTES_SQL)
    if not rows:
        return
    total = 0
    for table, size in rows:
        try:
            value = int(size)
        except (TypeError, ValueError):
            continue
        total += value
        checkpoint_table_bytes.set(value, table=str(table))
    checkpoint_blobs_table_bytes.set(total)


# ── the F9 adapter ────────────────────────────────────────────────────────
def adapt_f9(sampler: Any) -> None:
    """F9 — the readiness family over the PUBLISHED composite."""
    sources = current_sources()
    provider = sources.readiness_composite
    if provider is None:
        return
    try:
        composite = provider()
    except Exception:  # noqa: BLE001 — a broken composite degrades the family
        logger.debug("metrics wiring: F9 composite read failed", exc_info=True)
        return
    if composite is None:
        return

    classes = dict(getattr(composite, "component_classes", {}) or {})
    streaks = dict(getattr(composite, "failure_streak", {}) or {})
    grace_active = bool(getattr(composite, "grace_active", False))

    readiness_grace_active.set(1 if grace_active else 0)

    for component in vocab.READINESS_COMPONENTS:
        failure_class = str(classes.get(component, "unknown"))
        healthy = failure_class == "ok"
        # 1 = the component is HEALTHY (the conventional "is up"
        # semantics); 0 = it is degraded.  The failure CLASS itself is
        # carried by the probe_total counter's outcome label.
        readiness_component_state.set(1 if healthy else 0, component=component)
        try:
            readiness_failure_streak.set(int(streaks.get(component, 0)), component=component)
        except (TypeError, ValueError):
            pass
        # The observation counter: the metrics layer counts the published
        # per-tick class (the same accumulation discipline as F12).
        outcome = failure_class if failure_class in vocab.READINESS_OUTCOMES else "unknown"
        readiness_probe_total.inc(1, component=component, outcome=outcome)


# ── the F10 adapter ───────────────────────────────────────────────────────
_QUEUE_DEPTH_SQL = (
    "SELECT admission_state, COUNT(*) FROM job_queue_items GROUP BY admission_state"
)


def adapt_f10(sampler: Any) -> None:
    """F10 — the job-queue depth gauges (the durable admission state)."""
    sources = current_sources()
    if not sources.dsn:
        return
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w2-queue", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, _QUEUE_DEPTH_SQL)
    if not rows:
        return
    total = 0
    dead = 0
    for state, count in rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        total += value
        token = str(state)
        if token == "dead":
            dead = value
        job_queue_depth.set(value, admission_state=token)
    dead_letter_items.set(dead)
    job_queue_items_created_total.set(total)


# ── the F11 adapter ───────────────────────────────────────────────────────
def _pools(sources: WiringSources) -> tuple[tuple[str, Any], ...]:
    """The daemon's worker pools (the default lane + the chat lane)."""
    manager = sources.manager
    if manager is None:
        return ()
    return (
        ("worker", getattr(manager, "_worker_pool", None)),
        ("chat", getattr(manager, "_chat_worker_pool", None)),
    )


def _busy_count(pool: Any) -> int | None:
    """The number of workers holding a task (the live tool's derivation)."""
    if pool is None:
        return None
    workers = getattr(pool, "_workers", None)
    if not workers:
        return None
    busy = 0
    for worker in workers:
        if getattr(worker, "_current_task_id", None) is not None:
            busy += 1
    return busy


def adapt_f11(sampler: Any) -> None:
    """F11 — the worker pool family (PARTIAL: no claim latency)."""
    sources = current_sources()
    for lane, pool in _pools(sources):
        if pool is None:
            continue
        try:
            stats = pool.get_stats()
        except Exception:  # noqa: BLE001 — a broken pool degrades the family
            logger.debug("metrics wiring: F11 pool read failed", exc_info=True)
            continue
        if not isinstance(stats, dict):
            continue
        workers = stats.get("num_workers")
        if workers is not None:
            workers_total.set(int(workers), pool=lane)
        busy = _busy_count(pool)
        if busy is not None:
            workers_busy.set(busy, pool=lane)
        efficiency = stats.get("wakeup_efficiency")
        if efficiency is not None:
            worker_pool_wakeup_efficiency.set(float(efficiency), pool=lane)
        for event in vocab.WORKER_POOL_EVENTS:
            value = stats.get(event)
            if value is None:
                continue
            # The pool's own counters are cumulative; the metrics layer
            # publishes the durable value as-is (the counter is the pool's).
            worker_pool_events_total.set(int(value), pool=lane, event=event)

    _heartbeat_family(sources)


def _heartbeat_family(sources: WiringSources) -> None:
    """The heartbeat age + cadence gauges."""
    cadence = None
    for lane, pool in _pools(sources):
        if pool is None:
            continue
        value = getattr(pool, "_heartbeat_interval_s", None)
        if value is None:
            value = getattr(pool, "heartbeat_interval_s", None)
        if value is not None:
            cadence = float(value)
            break
    if cadence is not None:
        task_heartbeat_cadence_seconds.set(cadence)

    age = _oldest_heartbeat_age(sources)
    if age is not None:
        task_heartbeat_age_seconds.set(age)


def _oldest_heartbeat_age(sources: WiringSources) -> float | None:
    """The age of the oldest task heartbeat (a durable query)."""
    if not sources.dsn:
        return None
    sql = (
        "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(last_heartbeat_at))), 0) "
        "FROM task WHERE last_heartbeat_at IS NOT NULL"
    )
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w2-heartbeat", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, sql)
    if not rows or not rows[0]:
        return None
    try:
        return float(rows[0][0])
    except (TypeError, ValueError):
        return None


# ── the F12 adapter ───────────────────────────────────────────────────────
#: The counter keys the F12 family accumulates, per lane.
_SWEEP_CYCLE_KEYS = ("ticks",)
_SWEEP_ERROR_KEYS = ("errors_total",)
_SWEEP_ROW_KEYS = (
    "fired_total",
    "retired_total",
    "notified_total",
    "skipped_promote_hold_total",
    "cancelled_total",
    "released_total",
    "processed_total",
    "skipped_total",
    "held_total",
    "orphan_released",
)


def _sweep_services(sources: WiringSources) -> tuple[tuple[str, Any], ...]:
    """The sweep services the daemon publishes, by lane."""
    return tuple(sources.sweeps.items())


def adapt_f12(sampler: Any) -> None:
    """F12 — the sweep counters (accumulated in the metrics layer)."""
    sources = current_sources()
    for lane, service in _sweep_services(sources):
        if service is None:
            continue
        counters = getattr(service, "counters", None)
        if not callable(counters):
            continue
        try:
            values = counters()
        except Exception:  # noqa: BLE001 — a broken sweep degrades the lane
            logger.debug("metrics wiring: F12 counters read failed", exc_info=True)
            continue
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            delta = _sweep_deltas.observe((lane, key), numeric)
            if delta <= 0:
                continue
            if key in _SWEEP_CYCLE_KEYS:
                sweep_cycles_total.inc(delta, lane=lane)
            elif key in _SWEEP_ERROR_KEYS:
                sweep_errors_total.inc(delta, lane=lane)
            elif key in _SWEEP_ROW_KEYS:
                outcome = _row_outcome_for(key)
                if outcome is not None:
                    sweep_rows_total.inc(delta, lane=lane, outcome=outcome)


def _row_outcome_for(key: str) -> str | None:
    """The sweep row outcome a counter key stands for."""
    mapping = {
        "fired_total": "processed",
        "retired_total": "retired",
        "notified_total": "processed",
        "cancelled_total": "skipped",
        "processed_total": "processed",
        "skipped_total": "skipped",
        "held_total": "held",
        "released_total": "released",
        "orphan_released": "released",
    }
    return mapping.get(key)


# ── the F13 adapter ───────────────────────────────────────────────────────
_INSTANCE_CENSUS_SQL = "SELECT status, COUNT(*) FROM instances GROUP BY status"


def adapt_f13(sampler: Any) -> None:
    """F13 — the instance census (PARTIAL: no wake/lifecycle counters)."""
    sources = current_sources()
    if not sources.dsn:
        return
    with db.one_shot(sources.dsn, application_name="ensemble-metrics-w2-instances", connect=sources.db_connect) as conn:
        rows = db.fetch_rows(conn, _INSTANCE_CENSUS_SQL)
        waiting_rows = db.fetch_rows(conn, _WAITING_CHILDREN_SQL)
    if not rows:
        return

    liveness = _liveness_map()
    counts_by_liveness: dict[str, int] = {}
    for status, count in rows:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        instance_status_count.set(value, status=str(status))
        token = liveness.get(str(status), "other")
        counts_by_liveness[token] = counts_by_liveness.get(token, 0) + value
    for liveness_class in vocab.INSTANCE_LIVENESS:
        instance_liveness_count.set(counts_by_liveness.get(liveness_class, 0), liveness=liveness_class)

    count, age = _waiting_children(waiting_rows)
    waiting_children_count.set(count)
    waiting_children_max_age_seconds.set(age)


#: The durable waiting-children query (the F13 verification anchor —
#: task 4.6 confirms the query EXISTS before the gauges ship).
_WAITING_CHILDREN_SQL = (
    "SELECT COUNT(*), "
    "COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN(last_activity_at))), 0) "
    "FROM instances WHERE status = 'waiting_children'"
)


def _waiting_children(rows: list[tuple[Any, ...]]) -> tuple[int, float]:
    """The waiting-children count + the oldest age."""
    if not rows or not rows[0]:
        return 0, 0.0
    try:
        count = int(rows[0][0])
    except (TypeError, ValueError, IndexError):
        count = 0
    try:
        age = float(rows[0][1])
    except (TypeError, ValueError, IndexError):
        age = 0.0
    return count, age


def _liveness_map() -> dict[str, str]:
    """The instance status → liveness class map (the canonical one)."""
    try:
        from daemon.services.work_status import _STATUS_CANONICAL_MAP

        return dict(_STATUS_CANONICAL_MAP)
    except Exception:  # noqa: BLE001 — the map degrades to the residual
        return {}


def register_w2_adapters() -> tuple[PullAdapter, ...]:
    """Register the W2 pull adapters (the sampler's stage A)."""
    adapters: tuple[tuple[str, PullAdapter], ...] = (
        ("F8", adapt_f8),
        ("F9", adapt_f9),
        ("F10", adapt_f10),
        ("F11", adapt_f11),
        ("F12", adapt_f12),
        ("F13", adapt_f13),
    )
    for family, adapter in adapters:
        register_pull_adapter(family, adapter)
    return tuple(adapter for _family, adapter in adapters)


__all__ = [
    "CHECKPOINT_OP_BYTES_BUCKETS",
    "adapt_f8",
    "adapt_f9",
    "adapt_f10",
    "adapt_f11",
    "adapt_f12",
    "adapt_f13",
    "checkpoint_op_bytes",
    "register_w2_adapters",
]
