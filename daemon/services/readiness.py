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
from typing import Callable, NamedTuple, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from daemon._redact import redact_exc_str
from daemon.constants import (
    CHECKPOINT_SENTINEL_CHANNEL,
    CHECKPOINT_SENTINEL_CHECKPOINT_ID,
    CHECKPOINT_SENTINEL_TASK_ID,
    CHECKPOINT_SENTINEL_THREAD_ID,
    CHECKPOINT_SENTINEL_VALUE,
)
from daemon.services.boot_epoch import BOOT_EPOCH_FLOOR

logger = logging.getLogger(__name__)

# Hard timeout for the SELECT 1 liveness query against the manager
# engine. The existing engine is sync SQLAlchemy, so the query runs in
# a worker thread via asyncio.to_thread and asyncio.wait_for enforces
# the budget. A timed-out to_thread leaves the underlying thread
# running until the driver's own connect/execute timeout releases it —
# bounded leakage, acceptable at the 10s refresh cadence.
DB_PROBE_TIMEOUT_S: float = 0.5

# Hard timeout for the checkpoint-saver probe (incident 2026-10-10 fix).
# The probe schedules the sentinel checkpoint cycle (write-once init +
# ``aget_tuple`` read) on the running loop from the sync callable
# running in a worker thread; the timeout enforced here is the
# wall-clock budget for the round-trip (loop schedule + sentinel read +
# return). The probe is FAIL-CLOSED — any timeout or exception degrades
# ``checkpoint_saver`` with a sentinel-read reason string.
CHECKPOINT_SAVER_PROBE_TIMEOUT_S: float = 1.0

# Timeout for the queue-freshness aggregate. A single indexed MAX()
# over RUNNING tasks; the budget only guards against a hung database,
# not query cost.
QUEUE_PROBE_TIMEOUT_S: float = 2.0

# Task-status literal for RUNNING (TaskStatus.RUNNING.value in
# daemon/repositories/task/models.py). Kept as a literal so this
# module does not import the task models — keeps the pure seam
# dependency-free.
TASK_STATUS_RUNNING = "running"

# Readiness degradation drill knob (Auto-Restart Phase 1, deferred
# tester probe P7). The Phase-1 tester skipped the /readyz
# green→red→green probe with reason "no knob": the daemon exposed no
# documented way to force readiness degradation without touching
# shared infrastructure (stopping the shared PG was explicitly out).
# This env var IS that knob: set ENSEMBLE_READINESS_FORCE_DEGRADED=1
# (or true/yes/on/degraded) to force the /readyz composite to
# degraded — an honest drill surface for operators (demo-env
# validation, /readyz 503 semantics) and for tests.
#
# Fail-safe by construction:
#   * it can ONLY degrade — never report ready when a component
#     failed (no fail-open path, mirroring the m4 sentinel rule);
#   * unknown values (including "0"/"false"/garbage/empty) are OFF —
#     a typo never degrades prod;
#   * it is read per refresh tick, so flipping the env between ticks
#     of an in-process refresher exercises the full green→red→green
#     transition (a live daemon needs a restart with the env set —
#     readiness never restarts the process, and neither does this).
READINESS_FORCE_DEGRADED_ENV = "ENSEMBLE_READINESS_FORCE_DEGRADED"

_FORCED_DEGRADED_VALUES = frozenset(
    {"1", "true", "yes", "on", "degraded"}
)

# Probe-timeout marker for ``_guarded``: the helper returns a
# ``(timed_out, value)`` tuple so a timeout is distinguishable from
# the component's own default at the call site. This matters because
# the two components treat a timeout DIFFERENTLY: a timed-out DB
# probe degrades ``database`` (fail-closed), while a timed-out queue
# probe degrades ``queue_freshness`` (a timeout must NOT read as the
# empty-set default — "no answer" is not "no RUNNING tasks"). A
# sentinel VALUE returned from the shared helper cannot express that
# split (a truthy sentinel leaking into ``database_ok`` would
# fail-OPEN), so the flag travels out-of-band.

# Queue-freshness aggregate, computed SQL-side per dialect.
#
# Why SQL-side (plan deviation, recorded in the Phase-1 report): the
# plan text says to read MAX(last_heartbeat_at) and compute
# max_age = now - max(ts) in Python. Pre-tz-fix reality: the writers
# stamped AWARE ``datetime.now(timezone.utc)`` binds, which psycopg
# rendered into SESSION-LOCAL wall time before storing into the
# timezone-naive TIMESTAMP column — so Python-side subtraction
# (naive digits read back + assumed UTC) was wrong by the session
# offset whenever the PG session TZ was not UTC (verified
# experimentally: 7h skew on a +07 session). The tz fix (Phase 3)
# closed the class at the source — writers now stamp naive-UTC
# digits (``now_utc_naive``) and the PG engine pins its session
# frame to UTC — and this predicate STAYS SQL-side as defense in
# depth: computing the age inside SQL with the database's own
# ``now()`` keeps both operands in one frame regardless of session
# settings or driver bind rendering, and also removes app-host vs
# DB-host clock skew. SQLite's ``julianday`` handles the offset
# stored in the timestamp string the same way.
#
# Stop-frozen heartbeat amnesty (incident r-20260929-170301-0cb2):
# the MAX() only aggregates heartbeats at/after the daemon boot
# epoch (``:boot_epoch`` bind). A beat older than the boot epoch was
# written by a dead process — it cannot prove a CURRENT stall, so it
# is invisible to freshness accounting. When every RUNNING task is
# stop-frozen the aggregate is NULL → age None → fresh (empty-set
# semantics), letting a promote commit with a busy daemon. Callers
# that never captured an epoch bind ``BOOT_EPOCH_FLOOR`` (year 1) —
# every real beat passes the CASE, i.e. byte-equivalent semantics to
# the pre-amnesty aggregate. See daemon/services/boot_epoch.py.
_QUEUE_MAX_AGE_SQL_POSTGRES = sa_text(
    "SELECT EXTRACT(EPOCH FROM (now() - MAX(CASE WHEN last_heartbeat_at >= :boot_epoch"
    " THEN last_heartbeat_at END))) "
    "FROM task WHERE status = :status_running"
)
_QUEUE_MAX_AGE_SQL_SQLITE = sa_text(
    "SELECT (julianday('now') - julianday(MAX(CASE WHEN last_heartbeat_at >= :boot_epoch"
    " THEN last_heartbeat_at END))) * 86400.0 "
    "FROM task WHERE status = :status_running"
)

# Advisory inflight-turns count (promote-quiesce commission, v0.16.6
# component 1): RUNNING tasks whose heartbeat is FRESH — within the
# queue-freshness threshold AND at/after the boot epoch (a frozen
# pre-epoch beat is not "alive-and-working"). Purely advisory: the
# count rides ``detail.inflight_turns`` in the /readyz payload and
# NEVER feeds the readiness status or reasons. A parallel consumer
# (promote.sh preflight) prints it as a non-blocking INFO note;
# absent field = older-consumer tolerance.
#
# Freshness cutoff is computed DB-side (same clock as the age
# aggregate). NULL heartbeats fail both arms of the CASE and never
# count — no beat, no liveness. In SQLite the cutoff comparison runs
# through ``julianday`` on both sides (a TEXT column compared
# against a REAL bound is a type-ordering trap — text always sorts
# above numbers); the epoch arm stays a TEXT-vs-TEXT lexicographic
# compare, which is well-formed for the uniform ISO bind rendering.
_INFLIGHT_COUNT_SQL_POSTGRES = sa_text(
    "SELECT COUNT(CASE WHEN last_heartbeat_at >= now() - (:fresh_threshold_secs * interval '1 second')"
    " AND last_heartbeat_at >= :boot_epoch THEN 1 END) "
    "FROM task WHERE status = :status_running"
)
_INFLIGHT_COUNT_SQL_SQLITE = sa_text(
    "SELECT COUNT(CASE WHEN julianday(last_heartbeat_at) >= julianday('now') - :fresh_threshold_secs / 86400.0"
    " AND last_heartbeat_at >= :boot_epoch THEN 1 END) "
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
            Stop-frozen pre-boot heartbeats are excluded from the
            aggregate (see daemon/services/boot_epoch.py).
        services: critical services (job_processor, live_hub) are
            bound on ``app.state``.
        reasons: human-readable degraded reasons, one per failing
            component (empty when ready).
        queue_max_age_seconds: age of the newest RUNNING heartbeat in
            seconds, computed SQL-side; None when no RUNNING tasks
            exist (or all RUNNING tasks are stop-frozen pre-boot).
        checked_at: UTC timestamp captured at refresh start — the
            composite may be served for up to one refresh interval
            after this moment.
        forced_degraded: drill knob (``ENSEMBLE_READINESS_FORCE_
            DEGRADED``) forced the aggregate degraded while PRESERVING
            the real component readings. Readiness-only surface: the
            components stay truthful, the reason list names the knob,
            and the aggregate flips. Never set by real probe paths.
        inflight_turns: ADVISORY count of RUNNING tasks with fresh
            heartbeats (alive-and-working turns: within the freshness
            threshold AND at/after the boot epoch). Never affects
            ``ready`` or ``reasons``; surfaced in ``detail`` for
            consumers (e.g. a promote preflight printing a
            non-blocking INFO note). None when the count is unknown
            (probe timeout/failure or no threshold bound) — absent
            value is the older-consumer tolerance contract.
    """

    database: bool
    queue_freshness: bool
    services: bool
    # incident 2026-10-10: checkpoint-saver probe. The probe runs
    # ``AsyncConnectionPool.check()`` against the pool backing the
    # LangGraph saver (see ``make_checkpoint_saver_probe``). ``True``
    # means the saver's pool was reachable; ``False`` degrades
    # /readyz (503 + reason). On SQLite installs this is always True
    # (no probe runs; SQLite saver has no long-lived conn to monitor).
    checkpoint_saver: bool = True
    reasons: list[str] = field(default_factory=list)
    queue_max_age_seconds: Optional[float] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    forced_degraded: bool = False
    inflight_turns: Optional[int] = None

    @property
    def ready(self) -> bool:
        """All components pass (and no drill knob forcing degraded)."""
        return (
            self.database
            and self.queue_freshness
            and self.services
            and self.checkpoint_saver
            and not self.forced_degraded
        )

    def to_payload(self, *, draining: bool = False) -> dict:
        """Serialize to the ``ReadyzResponse`` body shape.

        ``draining`` is a reserved Phase-4 drain-controller field; the
        caller passes false in Phase 1. ``inflight_turns`` rides
        ``detail`` as an ADVISORY field — it never participates in
        the status/components/reasons contract.
        """
        return {
            "status": "ready" if self.ready else "degraded",
            "components": {
                "database": self.database,
                "queue_freshness": self.queue_freshness,
                "services": self.services,
                "checkpoint_saver": self.checkpoint_saver,
            },
            "detail": {
                "reasons": list(self.reasons),
                "queue_max_age_seconds": self.queue_max_age_seconds,
                "checked_at": self.checked_at.isoformat(),
                "inflight_turns": self.inflight_turns,
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
    inflight_turns: Optional[int] = None,
    checkpoint_saver_ok: bool = True,
) -> ReadinessComposite:
    """Assemble a composite from component outcomes.

    Reasons are derived mechanically from failing components so the
    degraded body always explains itself. ``inflight_turns`` is
    advisory-only: it is carried verbatim onto the composite and
    never consulted for ``ready`` or ``reasons``.

    ``checkpoint_saver_ok`` defaults to True (the SQLite / no-probe
    case): callers that DO run the probe (PG path, incident 2026-10-10
    fix) pass the probe outcome; callers that don't run it (SQLite,
    or tests that don't construct a probe) get the safe default.
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
    if not checkpoint_saver_ok:
        reasons.append(
            "checkpoint_saver: AsyncConnectionPool.check() failed or timed out"
        )
    return ReadinessComposite(
        database=database_ok,
        queue_freshness=queue_fresh_ok,
        services=services_ok,
        checkpoint_saver=checkpoint_saver_ok,
        reasons=reasons,
        queue_max_age_seconds=queue_max_age_seconds,
        checked_at=checked_at or datetime.now(timezone.utc),
        inflight_turns=inflight_turns,
    )


def forced_degradation_active(env_value: Optional[str]) -> bool:
    """Pure evaluation of the drill-knob env value.

    Truthy spellings (case-insensitive): ``1``, ``true``, ``yes``,
    ``on``, ``degraded``. Everything else — ``None``, empty, ``0``,
    ``false``, garbage — is OFF, so a malformed value can never
    degrade prod by accident.
    """
    if not env_value:
        return False
    return env_value.strip().lower() in _FORCED_DEGRADED_VALUES


def apply_forced_degradation(
    composite: ReadinessComposite,
    *,
    env_value: Optional[str],
) -> ReadinessComposite:
    """Apply the drill knob to a computed composite (pure).

    Returns the composite unchanged unless the knob is active. When
    active, returns a degraded copy that PRESERVES the real component
    readings and ``checked_at`` (the drill must not falsify what the
    probes actually saw) and appends an honest reason naming the knob —
    the degraded body always explains itself. One-way: never turns a
    degraded composite ready.
    """
    if not forced_degradation_active(env_value):
        return composite
    if not composite.ready:
        # Already degraded by real causes — add the drill marker so the
        # reason list stays truthful about every contributor.
        return ReadinessComposite(
            database=composite.database,
            queue_freshness=composite.queue_freshness,
            services=composite.services,
            checkpoint_saver=composite.checkpoint_saver,
            reasons=[
                *composite.reasons,
                f"readiness: degraded forced by {READINESS_FORCE_DEGRADED_ENV} (drill)",
            ],
            queue_max_age_seconds=composite.queue_max_age_seconds,
            checked_at=composite.checked_at,
            forced_degraded=True,
        )
    return ReadinessComposite(
        database=composite.database,
        queue_freshness=composite.queue_freshness,
        services=composite.services,
        checkpoint_saver=composite.checkpoint_saver,
        reasons=[
            f"readiness: degraded forced by {READINESS_FORCE_DEGRADED_ENV} (drill)",
        ],
        queue_max_age_seconds=composite.queue_max_age_seconds,
        checked_at=composite.checked_at,
        forced_degraded=True,
    )


async def refresh_readiness_composite(
    *,
    db_probe: Optional[Callable[[], bool]],
    queue_probe: Optional[Callable[[], Optional[float]]],
    services_ok: bool,
    queue_freshness_threshold_seconds: float,
    now: Optional[Callable[[], datetime]] = None,
    checkpoint_saver_probe: Optional[Callable[[], bool]] = None,
) -> ReadinessComposite:
    """Run one refresh cycle and return the composite.

    Probes are injected sync callables (see :func:`make_db_probe` /
    :func:`make_queue_probe` / :func:`make_checkpoint_saver_probe`);
    they run in worker threads so a hung database never blocks the
    event loop. A probe that is ``None`` or raises is a failed
    component — readiness fails closed.

    ``checkpoint_saver_probe`` is OPTIONAL (default ``None`` → treated
    as healthy / SQLite / "nothing to probe"). When provided, the
    composite gains the ``checkpoint_saver`` component (incident
    2026-10-10 fix). When not provided, the field defaults to True
    so SQLite installs and tests don't need to construct a probe.
    A timed-out / failed probe degrades ``checkpoint_saver`` (fail
    closed) — readiness reports degraded rather than green-when-dead.

    Timeout semantics differ per component. A timed-out DATABASE
    probe degrades ``database`` (false). A timed-out QUEUE probe
    degrades ``queue_freshness`` with the reason "queue probe timed
    out" — it must NOT read as the empty-set default (None age =
    fresh): no answer is not "no running tasks". The age stays None
    because no honest number exists. Exceptions from the queue probe
    still follow the historical empty-set default (None probe /
    raised error → fresh) — an unreachable database already reports
    through the ``database`` component and must not double-report
    here.

    The queue probe may return either the legacy scalar age (older
    probes / pure-seam tests) or a :class:`QueueProbeResult` carrying
    the advisory ``inflight_turns`` count alongside the age. A
    timeout or failure leaves the count None (unknown — advisory
    fields never guess).
    """
    checked_at = (now or (lambda: datetime.now(timezone.utc)))()

    async def _guarded(probe, timeout_s, default):
        """Run one probe; return ``(timed_out, value)``.

        ``value`` is the probe result, the component default when the
        probe is ``None``/raises, or ``None`` on timeout; ``timed_out``
        disambiguates a timeout from a legitimate ``None`` default so
        each component can apply its own timeout semantics.
        """
        if probe is None:
            return False, default
        try:
            return False, await asyncio.wait_for(
                asyncio.to_thread(probe), timeout=timeout_s
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return True, None
        except Exception as exc:
            # W2 (incident 2026-10-10): probe exceptions can carry
            # libpq server identifiers — redact before logging.
            logger.warning(
                "Readiness probe failed: %s", redact_exc_str(exc)
            )
            return False, default

    db_timed_out, database_ok = await _guarded(db_probe, DB_PROBE_TIMEOUT_S, False)
    # A DB-probe timeout MUST degrade ``database`` — the guard returns
    # None as the value, but the flag is authoritative (m4: a truthy
    # sentinel leaking into database_ok would fail OPEN).
    if db_timed_out:
        database_ok = False
    queue_timed_out, queue_result = await _guarded(
        queue_probe, QUEUE_PROBE_TIMEOUT_S, None
    )

    # Checkpoint-saver probe (incident 2026-10-10 fix). Optional: None
    # means SQLite / no-probe path → healthy (the default field value).
    # A timeout / failure / missing probe degrades ``checkpoint_saver``
    # in the same fail-closed way as ``database``: a truthy sentinel
    # leaking into the field would fail OPEN.
    if checkpoint_saver_probe is None:
        checkpoint_saver_ok = True
        ckpt_extra_reason = ""
    else:
        ckpt_timed_out, ckpt_value = await _guarded(
            checkpoint_saver_probe, CHECKPOINT_SAVER_PROBE_TIMEOUT_S, False
        )
        if ckpt_timed_out:
            checkpoint_saver_ok = False
            ckpt_extra_reason = (
                "checkpoint_saver: sentinel checkpoint read (aget_tuple) "
                f"timed out after {CHECKPOINT_SAVER_PROBE_TIMEOUT_S}s"
            )
        elif not ckpt_value:
            checkpoint_saver_ok = False
            ckpt_extra_reason = (
                "checkpoint_saver: sentinel checkpoint read failed "
                "(checkpoint path cannot serve reads — see readiness logs)"
            )
        else:
            checkpoint_saver_ok = True
            ckpt_extra_reason = ""

    inflight_turns: Optional[int] = None
    extra_reasons: list[str] = []
    if queue_timed_out:
        # Distinguish "no RUNNING tasks" (None → fresh) from "never
        # got an answer" (timeout → degraded, age unknown).
        queue_max_age_seconds = None
        fresh = False
        extra_reasons.append("queue_freshness: queue probe timed out")
    elif isinstance(queue_result, QueueProbeResult):
        queue_max_age_seconds = queue_result.max_age_seconds
        inflight_turns = queue_result.inflight_turns
        # Negative ages clamp to 0.0 inside evaluate_queue_freshness —
        # see the freshness-clamp docstring at :245.
        fresh, age = evaluate_queue_freshness(
            queue_max_age_seconds,
            threshold_seconds=queue_freshness_threshold_seconds,
        )
        queue_max_age_seconds = age
    else:
        queue_max_age_seconds = queue_result
        # Negative ages clamp to 0.0 inside evaluate_queue_freshness —
        # see the freshness-clamp docstring at :245.
        fresh, age = evaluate_queue_freshness(
            queue_max_age_seconds,
            threshold_seconds=queue_freshness_threshold_seconds,
        )
        queue_max_age_seconds = age
    if ckpt_extra_reason:
        extra_reasons.append(ckpt_extra_reason)
    return compute_readiness_composite(
        database_ok=database_ok,
        queue_fresh_ok=fresh,
        services_ok=services_ok,
        queue_max_age_seconds=queue_max_age_seconds,
        checked_at=checked_at,
        extra_reasons=extra_reasons,
        inflight_turns=inflight_turns,
        checkpoint_saver_ok=checkpoint_saver_ok,
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


def make_checkpoint_saver_probe(checkpointer: Any) -> Callable[[], bool]:
    """Build a sync probe that reads a SENTINEL CHECKPOINT through the
    saver path.

    Incident 2026-10-10 fix (W1, round 2 redesign): the first revision
    probed ``AsyncConnectionPool.check()`` — which self-heals by
    discarding and replacing dead connections and NEVER raises. It
    could PREVENT the outage class but could not DETECT it (a probe
    that always returns True is not observability). This probe instead
    performs a trivial O(1) sentinel checkpoint read
    (``aget_tuple`` on a fixed synthetic thread) through the SAME code
    path user checkpoint ops take (``raw_saver`` → retry proxy →
    ``AsyncPostgresSaver``): any checkpoint path that cannot serve
    reads raises or times out → the composite degrades (ADR-005 503 +
    reason), never restarts.

    Sentinel design (write-vs-no-write — write-once chosen):

    * Thread/config are fixed and discoverable
      (``daemon.constants.CHECKPOINT_SENTINEL_THREAD_ID`` =
      ``"__ensemble_readiness_probe__"``; synthetic thread no pregel
      turn ever mints).
    * FIRST tick of the process writes the sentinel ONCE via
      ``aput_writes`` with a fixed (thread_id, checkpoint_ns,
      checkpoint_id, task_id) key — idempotent (upsert semantics on
      the writes table; safe to re-run, e.g. process restart). The
      write goes through the retry proxy, so it exercises the real
      hot write path, and it lands ONLY in ``checkpoint_writes`` —
      no ``checkpoints`` row is created, so instance lifecycle /
      pause-resume / prune tools never see the sentinel (the PG
      adapter's ``list_thread_ids`` additionally excludes the
      sentinel thread as defense-in-depth).
    * EVERY tick (including the first, after the write) READS:
      ``await saver.aget_tuple(sentinel_config)``. A ``None`` return
      (row absent — e.g. a manual cleanup removed the sentinel) is a
      HEALTHY answer: the probe detects a path that cannot SERVE
      READS, not row presence. The probe stays O(1) per tick: one
      write once per process, then a single bounded SELECT.

    Dead-pool tick cost: while the pool is down, every 10s refresh
    tick still pays ~0.9s — the sentinel read blocks on the dead-pool
    connection attempt inside the 1.0s probe budget before failing.

    The probe is a SYNC callable (matches ``make_db_probe`` /
    ``make_queue_probe`` shape). It captures the running event loop
    at construction time and uses ``asyncio.run_coroutine_threadsafe``
    to schedule the sentinel cycle on that loop from the worker
    thread that ``asyncio.to_thread`` runs the probe in.

    Topology detection: the pool object is identified by
    ``getattr(checkpointer.raw_saver, "conn", None)`` — the saver
    stores its connection (or pool) under ``.conn`` (aio.py:57).
    When the saver is pool-backed (incident 2026-10-10 fix), the
    object is an ``AsyncConnectionPool`` and the sentinel probe is
    installed. When the saver is the historical single-connection
    shape (or SQLite), there is no pool to monitor: the probe
    returns True (healthy) so readiness is never falsely degraded on
    SQLite installs or on PG installs that pre-date this fix.

    Args:
        checkpointer: A ``CheckpointerAdapter`` exposing
            ``raw_saver`` (a ``PostgresCheckpointerAdapter`` or
            ``SqliteCheckpointerAdapter``). The PG adapter's
            ``raw_saver`` is the retry PROXY (it must stay that
            way so the sentinel rides the exact hot path LangGraph
            uses, including the one-shot connection retry) —
            ``raw_saver.conn`` resolves through the proxy's
            ``__getattr__`` passthrough to the wrapped saver's
            ``.conn`` (an attribute ABSENT from the base saver
            surface, so the MRO rule does not shadow it).
            ``daemon.checkpoint_adapter.PostgresCheckpointerAdapter._raw_saver``
            holds the unwrapped saver for tests that need it.

    Returns:
        A sync callable returning ``True`` when the sentinel read
        succeeded and raising otherwise (the readiness orchestrator's
        guard converts the failure into a degraded component with a
        sentinel-read reason string).
    """
    # Capture the running loop. ``asyncio.run_coroutine_threadsafe``
    # requires a running loop in the destination thread; the
    # orchestrator runs the probe via ``asyncio.to_thread`` from the
    # event loop, so this is the right loop to schedule against.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop at construction time (e.g. test seam).
        # The probe becomes a no-op True so it never degrades a
        # refresh cycle that runs without a loop.
        def _noop_probe() -> bool:
            return True

        return _noop_probe

    saver = getattr(checkpointer, "raw_saver", None)
    conn = getattr(saver, "conn", None) if saver is not None else None
    # Topology detection: AsyncConnectionPool has ``get_stats`` and
    # ``min_size``; AsyncConnection has neither. SQLite's
    # AsyncSqliteSaver has a ``conn`` attribute too — but the probe
    # never reaches that branch in production (SQLite installs don't
    # pass a checkpointer here, see readiness wiring in api.py).
    is_pool = bool(conn) and hasattr(conn, "get_stats") and hasattr(conn, "min_size")
    if not is_pool:
        # SQLite / single-connection / not-pool-backed: nothing to
        # monitor. Return a True-returning callable so readiness
        # never degrades for SQLite installs (the spec-required
        # pass-through). This is intentionally NOT a "skip" — the
        # component is set True so the composite stays ready.
        def _passthrough_probe() -> bool:
            return True

        return _passthrough_probe

    # Sentinel configs (fixed + discoverable; see daemon.constants).
    sentinel_write_config = {
        "configurable": {
            "thread_id": CHECKPOINT_SENTINEL_THREAD_ID,
            "checkpoint_ns": "",
            "checkpoint_id": CHECKPOINT_SENTINEL_CHECKPOINT_ID,
        }
    }
    sentinel_read_config = {
        "configurable": {
            "thread_id": CHECKPOINT_SENTINEL_THREAD_ID,
            "checkpoint_ns": "",
        }
    }
    sentinel_initialized = False

    async def _sentinel_cycle() -> None:
        """One probe cycle: write-once init, then a read-only tick."""
        nonlocal sentinel_initialized
        if not sentinel_initialized:
            # First tick of this process: plant the sentinel through
            # the saver's real write path. Fixed key → idempotent.
            await saver.aput_writes(
                sentinel_write_config,
                [(CHECKPOINT_SENTINEL_CHANNEL, CHECKPOINT_SENTINEL_VALUE)],
                CHECKPOINT_SENTINEL_TASK_ID,
            )
            sentinel_initialized = True
        # Read-only tick. None is HEALTHY (no row ≠ broken path);
        # only an exception (or the outer timeout) degrades.
        await saver.aget_tuple(sentinel_read_config)

    def _probe() -> bool:
        # Schedule the sentinel cycle on the captured loop from this
        # worker thread and wait for it synchronously with a timeout
        # that is slightly under ``CHECKPOINT_SAVER_PROBE_TIMEOUT_S``
        # so ``asyncio.wait_for`` in ``_guarded`` is the budget
        # authority (defense in depth — both layers enforce it).
        try:
            future = asyncio.run_coroutine_threadsafe(_sentinel_cycle(), loop)
            future.result(timeout=CHECKPOINT_SAVER_PROBE_TIMEOUT_S - 0.1)
            return True
        except Exception:
            # NO logging here — ``_guarded`` in
            # refresh_readiness_composite is the single log site for
            # probe failures (the prior revision logged here AND in
            # _guarded: a double log). Re-raise so the orchestrator
            # degrades the component with the sentinel-read reason.
            raise

    return _probe


class QueueProbeResult(NamedTuple):
    """Outcome of one queue-freshness probe cycle.

    ``max_age_seconds``: age of the newest RUNNING heartbeat at/after
        the boot epoch, SQL-side; ``None`` when no RUNNING task has a
        post-epoch heartbeat (empty set — or all stop-frozen, the
        amnesty case) → fresh.
    ``inflight_turns``: ADVISORY count of RUNNING tasks with fresh
        heartbeats (within the freshness threshold AND at/after the
        boot epoch); ``None`` when unknown (no threshold bound, or
        the count query failed).
    """

    max_age_seconds: Optional[float]
    inflight_turns: Optional[int]


def make_queue_probe(
    engine: Engine,
    *,
    boot_epoch: Optional[datetime] = None,
    freshness_threshold_seconds: Optional[float] = None,
) -> Callable[[], QueueProbeResult]:
    """Build the queue-freshness component probe bound to a sync engine.

    Returns the age in seconds of the newest ``last_heartbeat_at``
    among RUNNING tasks, computed SQL-side (see the module-level
    dialect notes), or None when there are none (or MAX() is NULL).
    Dialect selection is per-call so a test engine swapped under the
    refresher picks the right SQL without reconstruction. Numeric
    coercion is lenient because the drivers disagree on the return
    type: psycopg's EXTRACT comes back as ``decimal.Decimal``, SQLite
    arithmetic as ``float``.

    Stop-frozen heartbeat amnesty (incident r-20260929-170301-0cb2):
    ``boot_epoch`` bounds the MAX() aggregate to heartbeats at/after
    the daemon boot epoch — a beat written by a dead process cannot
    prove a stall of THIS daemon. ``None`` binds ``BOOT_EPOCH_FLOOR``
    (year 1), making the epoch filter a no-op — pre-amnesty
    semantics.

    ``freshness_threshold_seconds`` additionally arms the ADVISORY
    ``inflight_turns`` count (RUNNING tasks with fresh heartbeats);
    without it the count is ``None`` (unknown), never a guess.
    """
    epoch_bind = boot_epoch if boot_epoch is not None else BOOT_EPOCH_FLOOR

    def _probe() -> QueueProbeResult:
        is_postgres = engine.dialect.name == "postgresql"
        age_statement = (
            _QUEUE_MAX_AGE_SQL_POSTGRES if is_postgres else _QUEUE_MAX_AGE_SQL_SQLITE
        )
        inflight: Optional[int] = None
        with engine.connect() as conn:
            value = conn.execute(
                age_statement,
                {"status_running": TASK_STATUS_RUNNING, "boot_epoch": epoch_bind},
            ).scalar()
            if freshness_threshold_seconds is not None:
                count_statement = (
                    _INFLIGHT_COUNT_SQL_POSTGRES
                    if is_postgres
                    else _INFLIGHT_COUNT_SQL_SQLITE
                )
                raw_count = conn.execute(
                    count_statement,
                    {
                        "status_running": TASK_STATUS_RUNNING,
                        "boot_epoch": epoch_bind,
                        "fresh_threshold_secs": float(freshness_threshold_seconds),
                    },
                ).scalar()
                try:
                    inflight = int(raw_count)
                except (TypeError, ValueError):
                    logger.warning(
                        "Inflight-turns probe returned non-numeric count: %r",
                        raw_count,
                    )
                    inflight = None
        if value is None:
            return QueueProbeResult(max_age_seconds=None, inflight_turns=inflight)
        try:
            return QueueProbeResult(
                max_age_seconds=float(value), inflight_turns=inflight
            )
        except (TypeError, ValueError):
            logger.warning("Queue probe returned non-numeric age: %r", value)
            return QueueProbeResult(max_age_seconds=None, inflight_turns=inflight)

    return _probe
