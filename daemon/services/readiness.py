"""Readiness composite for the /readyz probe (Auto-Restart Phase 1, ADR-003).

Canonical home for readiness *computation*: a pure dataclass plus pure
functions that take injected callables, and thin engine-bound probe
factories. ``daemon/api.py`` only wires these into the background
refresher task and the HTTP handler — no probe logic lives there. The
injected-callable seam is what lets tests exercise the composite
without a full app or a live database.

Contract (ADR-003): the HTTP handler is an O(1) memory read of the
last composite; the ONLY database toucher for readiness is the
background refresher running at ``readiness_refresh_interval_seconds``
(default 10s). Liveness (``/livez``) never consults this module.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Hard timeout for the SELECT 1 liveness query against the manager
# engine. The existing engine is sync SQLAlchemy, so the query runs in
# a worker thread via asyncio.to_thread and asyncio.wait_for enforces
# the budget. A timed-out to_thread leaves the underlying thread
# running until the driver's own connect/execute timeout releases it —
# bounded leakage, acceptable at the 10s refresh cadence.
DB_PROBE_TIMEOUT_S: float = 0.5

# Timeout for the queue-freshness aggregate. A single indexed MAX()
# over RUNNING tasks; the budget only guards against a hung database,
# not query cost.
QUEUE_PROBE_TIMEOUT_S: float = 2.0

# Task-status literal for RUNNING (TaskStatus.RUNNING.value in
# daemon/repositories/task/models.py). Kept as a literal so this
# module does not import the task models — keeps the pure seam
# dependency-free.
TASK_STATUS_RUNNING = "running"

# Queue-freshness aggregate, computed SQL-side per dialect.
#
# Why SQL-side (plan deviation, recorded in the Phase-1 report): the
# plan text says to read MAX(last_heartbeat_at) and compute
# max_age = now - max(ts) in Python. Reality: ``last_heartbeat_at``
# is a timezone-naive TIMESTAMP, and psycopg renders timezone-AWARE
# binds (the writers stamp datetime.now(timezone.utc)) into
# SESSION-LOCAL wall time before storing. Reading the naive value
# back and attaching UTC — as Python-side subtraction must — is
# therefore wrong by the session offset whenever the PG session TZ
# is not UTC (verified experimentally: 7h skew on a +07 session).
# Computing the age inside SQL with the database's own ``now()``
# keeps both operands in the same session frame — exactly how the
# existing StaleTaskRecovery predicates stay correct — and also
# removes app-host vs DB-host clock skew. SQLite's ``julianday``
# handles the offset stored in the timestamp string the same way.
_QUEUE_MAX_AGE_SQL_POSTGRES = sa_text(
    "SELECT EXTRACT(EPOCH FROM (now() - MAX(last_heartbeat_at))) "
    "FROM task WHERE status = :status_running"
)
_QUEUE_MAX_AGE_SQL_SQLITE = sa_text(
    "SELECT (julianday('now') - julianday(MAX(last_heartbeat_at))) * 86400.0 "
    "FROM task WHERE status = :status_running"
)


@dataclass
class ReadinessComposite:
    """Snapshot of the readiness composite for one refresh cycle.

    Attributes:
        database: SELECT 1 against the manager engine succeeded within
            the probe budget.
        queue_freshness: newest RUNNING-task heartbeat age is within
            the configured threshold (empty RUNNING set = fresh).
        services: critical services (job_processor, live_hub) are
            bound on ``app.state``.
        reasons: human-readable degraded reasons, one per failing
            component (empty when ready).
        queue_max_age_seconds: age of the newest RUNNING heartbeat in
            seconds, computed SQL-side; None when no RUNNING tasks
            exist.
        checked_at: UTC timestamp captured at refresh start — the
            composite may be served for up to one refresh interval
            after this moment.
    """

    database: bool
    queue_freshness: bool
    services: bool
    reasons: list[str] = field(default_factory=list)
    queue_max_age_seconds: Optional[float] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ready(self) -> bool:
        """All components pass."""
        return self.database and self.queue_freshness and self.services

    def to_payload(self, *, draining: bool = False) -> dict:
        """Serialize to the ``ReadyzResponse`` body shape.

        ``draining`` is a reserved Phase-4 drain-controller field; the
        caller passes false in Phase 1.
        """
        return {
            "status": "ready" if self.ready else "degraded",
            "components": {
                "database": self.database,
                "queue_freshness": self.queue_freshness,
                "services": self.services,
            },
            "detail": {
                "reasons": list(self.reasons),
                "queue_max_age_seconds": self.queue_max_age_seconds,
                "checked_at": self.checked_at.isoformat(),
            },
            "draining": draining,
        }


def evaluate_queue_freshness(
    queue_max_age_seconds: Optional[float],
    *,
    threshold_seconds: float,
) -> tuple[bool, Optional[float]]:
    """Pure freshness evaluation over a precomputed age.

    Returns ``(fresh, max_age_seconds)``. An empty RUNNING set is
    expressed as ``queue_max_age_seconds is None`` and is fresh with
    age None. The boundary (age == threshold) counts as fresh.
    Negative ages (clock skew between DB and app host) clamp to 0.0.
    """
    if queue_max_age_seconds is None:
        return True, None
    age = max(float(queue_max_age_seconds), 0.0)
    return age <= threshold_seconds, age


def compute_readiness_composite(
    *,
    database_ok: bool,
    queue_fresh_ok: bool,
    services_ok: bool,
    queue_max_age_seconds: Optional[float],
    checked_at: Optional[datetime] = None,
    extra_reasons: Optional[list[str]] = None,
) -> ReadinessComposite:
    """Assemble a composite from component outcomes.

    Reasons are derived mechanically from failing components so the
    degraded body always explains itself.
    """
    reasons: list[str] = list(extra_reasons or [])
    if not database_ok:
        reasons.append("database: SELECT 1 probe failed or timed out")
    if not queue_fresh_ok:
        reasons.append(
            "queue_freshness: newest RUNNING-task heartbeat age "
            f"{queue_max_age_seconds} exceeds "
            "readiness_queue_freshness_threshold_seconds"
        )
    if not services_ok:
        reasons.append(
            "services: critical services (job_processor/live_hub) not bound"
        )
    return ReadinessComposite(
        database=database_ok,
        queue_freshness=queue_fresh_ok,
        services=services_ok,
        reasons=reasons,
        queue_max_age_seconds=queue_max_age_seconds,
        checked_at=checked_at or datetime.now(timezone.utc),
    )


async def refresh_readiness_composite(
    *,
    db_probe: Optional[Callable[[], bool]],
    queue_probe: Optional[Callable[[], Optional[float]]],
    services_ok: bool,
    queue_freshness_threshold_seconds: float,
    now: Optional[Callable[[], datetime]] = None,
) -> ReadinessComposite:
    """Run one refresh cycle and return the composite.

    Probes are injected sync callables (see :func:`make_db_probe` /
    :func:`make_queue_probe`); they run in worker threads so a hung
    database never blocks the event loop. A probe that is ``None`` or
    raises is a failed component — readiness fails closed. A timed-out
    queue probe returns the empty-set default (None age = fresh) while
    the ``database`` component carries the degradation: a DB hung
    enough to miss the aggregate budget cannot serve SELECT 1 either,
    and the freshness component must not double-report.
    """
    checked_at = (now or (lambda: datetime.now(timezone.utc)))()

    async def _guarded(probe, timeout_s, default):
        if probe is None:
            return default
        try:
            return await asyncio.wait_for(asyncio.to_thread(probe), timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Readiness probe failed: %s", exc)
            return default

    database_ok = await _guarded(db_probe, DB_PROBE_TIMEOUT_S, False)
    queue_max_age_seconds = await _guarded(queue_probe, QUEUE_PROBE_TIMEOUT_S, None)
    fresh, age = evaluate_queue_freshness(
        queue_max_age_seconds,
        threshold_seconds=queue_freshness_threshold_seconds,
    )
    return compute_readiness_composite(
        database_ok=database_ok,
        queue_fresh_ok=fresh,
        services_ok=services_ok,
        queue_max_age_seconds=age,
        checked_at=checked_at,
    )


def make_db_probe(engine: Engine) -> Callable[[], bool]:
    """Build the database component probe bound to a sync engine.

    Raises on any connectivity/execute failure — the orchestrator's
    timeout/exception guard turns that into ``database=False``.
    """

    def _probe() -> bool:
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        return True

    return _probe


def make_queue_probe(engine: Engine) -> Callable[[], Optional[float]]:
    """Build the queue-freshness component probe bound to a sync engine.

    Returns the age in seconds of the newest ``last_heartbeat_at``
    among RUNNING tasks, computed SQL-side (see the module-level
    dialect notes), or None when there are none (or MAX() is NULL).
    Dialect selection is per-call so a test engine swapped under the
    refresher picks the right SQL without reconstruction. Numeric
    coercion is lenient because the drivers disagree on the return
    type: psycopg's EXTRACT comes back as ``decimal.Decimal``, SQLite
    arithmetic as ``float``.
    """

    def _probe() -> Optional[float]:
        statement = (
            _QUEUE_MAX_AGE_SQL_POSTGRES
            if engine.dialect.name == "postgresql"
            else _QUEUE_MAX_AGE_SQL_SQLITE
        )
        with engine.connect() as conn:
            value = conn.execute(
                statement, {"status_running": TASK_STATUS_RUNNING}
            ).scalar()
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning("Queue probe returned non-numeric age: %r", value)
            return None

    return _probe
