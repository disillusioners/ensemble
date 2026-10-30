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

SCOPE NOTE (why this module is large): four readiness concerns are
CANONICAL RESIDENTS here and must evolve together — the A1
failure-CLASS table (soak-gate-mcp-warmup, requirements §4.0), the
class-gated grace + leak state machine (``GraceTracker`` /
``GraceObservation``, §4.3-§4.4), the P1/P2 pool-state samplers
(advisory SQLAlchemy + oracle psycopg), and the ADR-003 composite
itself. They share one failure-class vocabulary and one
observation-relative clock; the size is cohesion, not accretion —
do not split the class table away from its consumers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, NamedTuple, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from daemon._redact import redact_exc_str
from daemon.checkpoint_metrics import checkpoint_leak_signal_total
from daemon.constants import (
    CHECKPOINT_SENTINEL_CHANNEL,
    CHECKPOINT_SENTINEL_CHECKPOINT_ID,
    CHECKPOINT_SENTINEL_TASK_ID,
    CHECKPOINT_SENTINEL_THREAD_ID,
    CHECKPOINT_SENTINEL_VALUE,
    READINESS_GRACE_HORIZON_S,
    READINESS_LEAK_TICK_THRESHOLD,
)
from daemon.services.boot_epoch import BOOT_EPOCH_FLOOR

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════
# Failure classes (soak-gate-mcp-warmup, A1)
# ════════════════════════════════════════════════════════════════════════
#
# The failure-CLASS table is NORMATIVE in ``requirements.md`` §4.0 (the
# table) and §4.2 (P2 — eligibility + aggregation + the counter kinds).
# This module only fixes WHERE the classifier lives and WHAT it returns:
# the class names below are the §4.0 names, unmodified, so no consumer can
# drift into a private vocabulary.
#
# GRACE-ELIGIBLE (row 1 — "the system was simply too busy to answer
# within its budget"):
FAILURE_CLASS_PROBE_TIMEOUT = "probe_timeout"
FAILURE_CLASS_POOL_BUSY = "pool_busy"
FAILURE_CLASS_QUEUE_STALE = "queue_stale"
FAILURE_CLASS_READ_TIMEOUT = "read_timeout"
#
# NEVER grace-eligible (rows 2-4 — hard failure / identity / boot / leak):
FAILURE_CLASS_BOOT_INCOMPLETE = "boot_incomplete"
FAILURE_CLASS_IDENTITY_MISMATCH = "identity_mismatch"
FAILURE_CLASS_HARD_COMPONENT_RED = "hard_component_red"
FAILURE_CLASS_NOT_SERVING = "not_serving"
FAILURE_CLASS_POOL_LEAK = "pool_leak"
FAILURE_CLASS_UNKNOWN = "unknown"
#
# The healthy marker (not a §4.0 row — a component that passed carries no
# failure class).
FAILURE_CLASS_OK = "ok"

#: The GRACE-ELIGIBLE class set (requirements.md §4.2 P2). Anything NOT in
#: this set is fail-closed: never held, always a counter RESET.
GRACE_ELIGIBLE_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        FAILURE_CLASS_PROBE_TIMEOUT,
        FAILURE_CLASS_POOL_BUSY,
        FAILURE_CLASS_QUEUE_STALE,
        FAILURE_CLASS_READ_TIMEOUT,
    }
)

#: The readiness component names (the composite's four components). Used
#: for the per-component class map and the failure streak.
READINESS_COMPONENT_NAMES: tuple[str, ...] = (
    "database",
    "queue_freshness",
    "services",
    "checkpoint_saver",
)

# ── The queue-age provenance vocabulary ──────────────────────────────────────
#
# WHY: ``queue_max_age_seconds`` is ``None`` for THREE different facts,
# and the payload used to render all three as the same bare ``null``
# next to a ``queue_freshness: true`` component — so every consumer
# read "no age" as "fresh". The facts are:
#
#   1. the probe ANSWERED and there was no RUNNING task to age
#      (the empty set — freshness is VACUOUS, not measured);
#   2. the probe TIMED OUT (no answer — the age was never determined);
#   3. the probe FAILED (raised — the age was never determined).
#
# The rendered label says WHICH, so an absent age never reads as
# "fresh". The vocabulary deliberately reuses the wedge-capture
# collector's words where the two surfaces overlap (``empty`` /
# ``probe_timeout`` — ``daemon/services/wedge_capture/collector.py``);
# ``measured`` and ``unknown`` are the /readyz additions.

#: A real age was computed (the probe answered with a number).
QUEUE_AGE_REASON_MEASURED = "measured"
#: The probe answered; no RUNNING task had a post-epoch heartbeat (the
#: empty set — or every RUNNING task was stop-frozen pre-boot). The
#: freshness verdict is vacuous: there was nothing to age.
QUEUE_AGE_REASON_EMPTY = "empty"
#: The probe did not answer (timed out) — the age was never determined.
QUEUE_AGE_REASON_PROBE_TIMEOUT = "probe_timeout"
#: The probe FAILED (raised) — the age was never determined.
QUEUE_AGE_REASON_UNKNOWN = "unknown"

#: The GRACE SCOPE published in ``detail.grace.scope`` — the only scope
#: today ("load"). Boot age and burst state are published CONTEXT, never
#: arming preconditions (requirements.md §4.2 P2).
READINESS_GRACE_SCOPE_LOAD = "load"


# Hard timeout for the SELECT 1 liveness query against the manager
# engine. The existing engine is sync SQLAlchemy, so the query runs in
# a worker thread via asyncio.to_thread and asyncio.wait_for enforces
# the budget. An abandoned probe thread does NOT leak a transaction
# (measured, Phase-0 BASELINE.md T5b): (i) under pool starvation it
# never acquires a connection at all — it is a pool WAITER bounded by
# the engine-level pool_timeout (30 s; the F4-A admission pre-check
# below refuses entry at capacity so no stranded waiter is created);
# (ii) when it DOES hold a connection the backend is ``active`` (NOT
# ``idle in transaction``) and the F4-B probe-scoped
# ``SET LOCAL statement_timeout = 2x budget`` cancels the statement
# server-side (57014 / QueryCanceled) — the backend row is EXPECTED TO
# PERSIST in pg_stat_activity with state='idle' after the statement is
# cancelled and the connection returns to the pool. The manager engine
# sets no engine-wide execute timeout (the URL's connect_timeout
# bounds the connect phase only) — the "the driver's own
# connect/execute timeout releases it" premise is FALSE at base.
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

# F4-A (D-57/D-59): the manager engine's DEFAULT-TIER TOTAL CAPACITY
# DECLARATION — the default pool tier (15 = ``pool_size``'s default,
# ``daemon/constants.py::MANAGER_POOL_SIZE_DEFAULT``, the default tier
# of the managed ``limits.manager_pool_size`` knob) + ``max_overflow``
# 10, as passed to ``create_engine`` at
# ``daemon/repositories/factory.py`` (the construction site) ⇒ 25.
#
# MEANING (2026-10-29 merge-semantics fix — the manager-pool-size knob
# hotfix made the pool size CONFIG-DRIVEN, so the pre-merge reading
# "15 = the whole ceiling" is WRONG): this is the DEFAULT-TIER
# DECLARATION ONLY — it is NOT the refusal threshold. The EFFECTIVE
# threshold is DERIVED at call time from the pool the engine actually
# holds (configured pool size + configured max overflow; see
# ``_manager_pool_effective_capacity``), because
# ``limits.manager_pool_size`` is operator-raisable and the engine's
# overflow slots are servable: a STATIC threshold of 15 would refuse
# admission while 10 overflow slots are still free — a self-inflicted
# outage. A capacity-equality pin reconciles THIS declaration against
# the construction site, so a drift in the default tier or in the
# overflow REDs the pack (D-57's purpose, re-derived for the
# config-driven construction).
#
# Deliberately NOT read from the pool at import time (the engine does
# not exist yet) and NOT from the private ``_max_overflow`` alone —
# the derivation happens per call, defensively.
MANAGER_POOL_CONFIGURED_CAPACITY: int = 25

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
        queue_max_age_seconds_reason: WHY the age is (or is not)
            there — ``measured`` (a real age), ``empty`` (the probe
            answered; the RUNNING set was empty — the freshness verdict
            is VACUOUS, not a measurement), ``probe_timeout`` (the
            probe did not answer) or ``unknown`` (the probe failed).
            An absent age is therefore NEVER rendered as an
            unqualified "fresh" — the label says which of the three
            "no age" facts it is. ``None`` on a hand-built composite
            ⇒ derived at render time (an age ⇒ ``measured``; no age ⇒
            ``empty``).
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
    # WHY the age is (or is not) there — see the QUEUE_AGE_REASON_*
    # vocabulary. ``None`` ⇒ derived at render time (an age ⇒
    # "measured"; no age ⇒ "empty" — the legacy empty-set reading).
    queue_max_age_seconds_reason: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    forced_degraded: bool = False
    inflight_turns: Optional[int] = None
    # OAuth2 auth advisory sub-field (P3 T3.9 / FR-30 enum). None =
    # auth disabled (field absent from the payload — OFF-state body
    # byte-identical) or state unknown. NEVER feeds `ready`/`reasons`.
    auth_dependency_status: Optional[str] = None
    # ── A1 additive fields (soak-gate-mcp-warmup) ─────────────────────
    # Every field below is ADDITIVE and OPTIONAL: ``None``/default ⇒ the
    # key is either absent from the payload or carries its default. None
    # of them EVER feeds ``ready``/``reasons``/``components`` — the
    # daemon's verdict is untouched by the grace (decisions §3a A1.1:
    # "the daemon never lies; the gate decides to wait").
    #
    # ``component_classes`` — the §4.0 failure class of EACH component
    # (``"ok"`` for a healthy one). The class travels WITH the verdict so
    # no consumer has to re-derive it from free-text reason strings (the
    # legacy reason string is stale — decisions §2 row 23).
    component_classes: dict[str, str] = field(default_factory=dict)
    # ``first_seen_degraded`` — when the composite FIRST went degraded in
    # the current degraded episode (None when ready).
    first_seen_degraded: Optional[datetime] = None
    # ``failure_streak`` — per-component consecutive-failure counts.
    failure_streak: dict[str, int] = field(default_factory=dict)
    # ``pool_state_sqlalchemy`` — ADVISORY pool (P1): {checkedout, size,
    # overflow}. Published for the operator; it MUST NOT arm the leak
    # class (requirements.md §4.4 P4).
    pool_state_sqlalchemy: Optional[dict] = None
    # ``pool_state_psycopg`` — ORACLE pool (P2): {pool_size,
    # pool_available, requests_waiting}. Its signals DO arm the leak
    # class (requirements.md §4.4 P4).
    pool_state_psycopg: Optional[dict] = None
    # ``leak_signal`` — {detected, checkedout_rising, streak, reason}.
    leak_signal: Optional[dict] = None
    # ``grace_active`` — is a class-gated grace currently held. The grace
    # NEVER renders the daemon healthy: ``ready``/``reasons``/
    # ``components`` are unchanged while it is held.
    grace_active: bool = False
    # ``grace_reason`` — the failure class that armed the grace.
    grace_reason: Optional[str] = None
    # ``grace_expires_at`` — the hard expiry (observation-relative clock).
    grace_expires_at: Optional[datetime] = None
    # ``grace_scope`` — the grace scope (today only "load").
    grace_scope: Optional[str] = None
    # ``grace_holds`` — how many ticks the grace has been held.
    grace_holds: int = 0
    # ``resume_hold`` — the A3 promote-txn hold's observable counters.
    resume_hold: Optional[dict] = None
    # ``burst_signals`` — the published burst CONTEXT (warmup pending,
    # resume-deferral counters). CONTEXT ONLY: never an arming
    # precondition (requirements.md §4.2 P2).
    burst_signals: Optional[dict] = None
    # ── C4 additive field (soak-gate-mcp-warmup) ──────────────────
    # ``mcp_servers`` — the per-built-in MCP server health map
    # (``{server: {state, reason, since, init_attempts,
    # last_error_class, breaker_state, recoverable}}``). ADVISORY ONLY:
    # it NEVER feeds ``ready`` / ``reasons`` / ``components`` — a
    # degraded (or quarantined) MCP server must NEVER flip a readiness
    # component or make ``/readyz`` 503 (decisions §3h H2/H4). The
    # daemon stays up and says so; the GATE decides what to do with it.
    mcp_servers: Optional[dict] = None
    # ``mcp_buffered_transitions`` — how many MCP state-transition journal
    # entries are currently BUFFERED (suppressed because a promote
    # transaction was live). ADVISORY: it says whether the daemon is
    # holding observability back — it never feeds the verdict. Published
    # so the buffer is not invisible (council A1).
    mcp_buffered_transitions: Optional[int] = None

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

    def queue_age_reason(self) -> str:
        """The rendered provenance of ``queue_max_age_seconds``.

        The explicit field (set by the refresh path, which KNOWS what
        the probe did) wins. A hand-built composite (no field) derives
        it: an age ⇒ ``measured``; no age ⇒ ``empty`` (the legacy
        empty-set reading).

        Never returns "fresh": an absent age is one of three facts —
        the set was empty, the probe timed out, or the probe failed —
        and the label says which.
        """
        explicit = self.queue_max_age_seconds_reason
        if explicit:
            return explicit
        if self.queue_max_age_seconds is not None:
            return QUEUE_AGE_REASON_MEASURED
        return QUEUE_AGE_REASON_EMPTY

    def to_payload(self, *, draining: bool = False) -> dict:
        """Serialize to the ``ReadyzResponse`` body shape.

        ``draining`` is a reserved Phase-4 drain-controller field; the
        caller passes false in Phase 1. ``inflight_turns`` rides
        ``detail`` as an ADVISORY field — it never participates in
        the status/components/reasons contract.

        ``auth_dependency_status`` (P3 T3.9) rides BOTH as the formal
        optional top-level field (ReadyzResponse schema) and as the
        additive ``detail`` key (inflight_turns precedent); it is OMITTED
        entirely when None (auth disabled) so the OFF-state body stays
        byte-identical (NFR-1). NEVER affects the verdict (OD-9).
        """
        payload = {
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
                # WHY the age is (or is not) there. An absent age used
                # to render as a bare ``null`` next to
                # ``queue_freshness: true`` — every consumer read it as
                # "fresh". The label names the fact: "measured" (a real
                # age), "empty" (the RUNNING set was empty — the
                # verdict is vacuous), "probe_timeout" / "unknown" (the
                # age was never determined).
                "queue_max_age_seconds_reason": self.queue_age_reason(),
                "checked_at": self.checked_at.isoformat(),
                "inflight_turns": self.inflight_turns,
            },
            "draining": draining,
        }
        if self.auth_dependency_status is not None:
            payload["auth_dependency_status"] = self.auth_dependency_status
            payload["detail"]["auth_dependency_status"] = (
                self.auth_dependency_status
            )
        # ── A1 additive detail keys (soak-gate-mcp-warmup) ─────────────
        # ``detail.grace`` — the class-gated grace state. It NEVER changes
        # ``status``/``components``/``detail.reasons``: a grace-held
        # composite is still ``degraded`` with its reasons intact (a
        # suppressed reason string is a DEFECT — decisions §4 invariant 2).
        # The block is ALWAYS published so the gate can read
        # ``grace_active`` explicitly; when the grace is NOT held the block
        # carries ONLY ``grace_active: false`` (no null-padded
        # ``grace_reason``/``grace_expires_at``). An OLDER daemon that
        # publishes NO ``grace`` block is read by the gate as the
        # ``unknown`` class ⇒ fail-closed ⇒ no grace (R-MASK-8).
        grace_block: dict = {"grace_active": bool(self.grace_active)}
        if self.grace_active:
            grace_block["grace_reason"] = self.grace_reason
            grace_block["grace_expires_at"] = (
                self.grace_expires_at.isoformat()
                if self.grace_expires_at is not None
                else None
            )
            grace_block["scope"] = self.grace_scope
            grace_block["holds"] = int(self.grace_holds)
        payload["detail"]["grace"] = grace_block
        # ``detail.leak_signal`` — the leak predicate's verdict. The
        # ORACLE pool (P2) arms it; the ADVISORY pool (P1) is published
        # but MUST NOT arm it (requirements.md §4.4 P4).
        leak = self.leak_signal
        if leak is not None:
            payload["detail"]["leak_signal"] = {
                "detected": bool(leak.get("detected", False)),
                "checkedout_rising": bool(leak.get("checkedout_rising", False)),
                "streak": int(leak.get("streak", 0) or 0),
            }
            if leak.get("reason"):
                payload["detail"]["leak_signal"]["reason"] = leak["reason"]
        # ``detail.pool_state`` — the pool-health inputs (P1 advisory /
        # P2 oracle). Published whenever the pool objects are observable,
        # so an operator can SEE the poison instead of inferring it from
        # timeouts (decisions §3d D5).
        pool_state: dict = {}
        if self.pool_state_sqlalchemy is not None:
            pool_state["sqlalchemy"] = dict(self.pool_state_sqlalchemy)
        if self.pool_state_psycopg is not None:
            pool_state["psycopg"] = dict(self.pool_state_psycopg)
        if pool_state:
            payload["detail"]["pool_state"] = pool_state
        # ``detail.component_classes`` — the §4.0 failure class per
        # component. The gate consumes these instead of re-deriving a
        # class from free-text reason strings.
        if self.component_classes:
            payload["detail"]["component_classes"] = dict(self.component_classes)
        # ``detail.resume_hold`` / ``detail.burst_signals`` — the A3 hold
        # counters and the published burst CONTEXT (never arming
        # preconditions).
        if self.resume_hold is not None:
            payload["detail"]["resume_hold"] = dict(self.resume_hold)
        if self.burst_signals is not None:
            payload["detail"]["burst_signals"] = dict(self.burst_signals)
        # ── C4 additive detail key (soak-gate-mcp-warmup) ───────────
        # ``detail.mcp`` — the per-built-in MCP server health map. It is
        # ADVISORY ONLY: it NEVER changes ``status`` / ``components`` /
        # ``detail.reasons``. A degraded or quarantined MCP server is
        # VISIBLE here while the daemon stays READY (decisions §3h H2:
        # "a degraded daemon must stay visible" — and a degraded MCP
        # server must not take the daemon down with it).
        if self.mcp_servers:
            payload["detail"]["mcp"] = {
                name: dict(entry) for name, entry in self.mcp_servers.items()
            }
        if self.mcp_buffered_transitions is not None:
            payload["detail"]["mcp_buffered_transitions"] = int(
                self.mcp_buffered_transitions
            )
        return payload


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


# ════════════════════════════════════════════════════════════════════════
# Pool-state sampling (A1 — leak predicate inputs)
# ════════════════════════════════════════════════════════════════════════


def resolve_psycopg_pool(checkpointer: Any) -> Any:
    """Resolve the psycopg checkpoint-saver pool object (P2 — the ORACLE).

    The saver stores its pool under ``.conn`` (``langgraph/checkpoint/
    postgres/aio.py:57``); the daemon's ``raw_saver`` is the retry PROXY,
    whose ``__getattr__`` passes ``.conn`` through unchanged. The pool is
    duck-typed on the pool-only ``get_stats`` + ``min_size`` attributes
    (an ``AsyncConnection`` has neither) — the same detection the
    sentinel probe uses.

    Returns ``None`` when the saver is not pool-backed (SQLite / the
    historical single-connection shape) — the caller then treats the pool
    objects as UNAVAILABLE (the leak predicate's streak-arm fallback).
    """
    saver = getattr(checkpointer, "raw_saver", None) if checkpointer is not None else None
    if saver is None:
        return None
    conn = getattr(saver, "conn", None)
    if conn is None:
        return None
    if hasattr(conn, "get_stats") and hasattr(conn, "min_size"):
        return conn
    return None


def sample_sqlalchemy_pool_state(pool: Any) -> Optional[dict]:
    """Sample the ADVISORY pool (P1 — the SQLAlchemy manager engine).

    Returns ``{"checkedout": int, "size": int, "overflow": int}`` or
    ``None`` when the pool object is absent / not a QueuePool-shaped pool.

    P1 is ADVISORY (requirements.md §4.4 P4): its signals are PUBLISHED
    in ``detail.pool_state`` but MUST NOT arm the leak class — with
    ``pool_size=5`` / ``max_overflow=10``, ``overflow() > 0`` is non-zero
    on a busy-but-healthy pool BY DESIGN, so a P1-armed leak class would
    refuse the grace on the incident's own busy shape.

    The read is IN-MEMORY (no DB round trip): an extra query on a
    poisoned pool would amplify the disease.
    """
    if pool is None:
        return None
    try:
        return {
            "checkedout": int(pool.checkedout()),
            "size": int(pool.size()),
            "overflow": int(pool.overflow()),
        }
    except Exception:  # noqa: BLE001 — a pool without this API is "unknown"
        return None


def sample_psycopg_pool_state(pool: Any) -> Optional[dict]:
    """Sample the ORACLE pool (P2 — the psycopg checkpoint-saver pool).

    Returns ``{"pool_size": int, "pool_available": int,
    "requests_waiting": int}`` or ``None`` when the pool object is
    absent or its ``get_stats()`` plumbing is unavailable.

    P2 is the ORACLE (requirements.md §4.4 P4): its signals ARM the leak
    class. The read is IN-MEMORY (``get_stats()`` reads the pool's own
    counters; it never touches PostgreSQL).
    """
    if pool is None:
        return None
    getter = getattr(pool, "get_stats", None)
    if not callable(getter):
        return None
    try:
        stats = getter() or {}
    except Exception:  # noqa: BLE001 — unreadable stats ⇒ "unavailable"
        return None
    if not isinstance(stats, dict):
        return None
    try:
        return {
            "pool_size": int(stats.get("pool_size", 0) or 0),
            "pool_max": int(stats.get("pool_max", 0) or 0),
            "pool_available": int(stats.get("pool_available", 0) or 0),
            "requests_waiting": int(stats.get("requests_waiting", 0) or 0),
        }
    except (TypeError, ValueError):
        return None


# ════════════════════════════════════════════════════════════════════════
# The grace state machine (A1 — requirements.md §4.2 P2 / §4.3 P3 /
# §4.4 P4)
# ════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GraceObservation:
    """One tick's grace verdict (the tracker's output).

    ``grace_active`` — the CONJUNCTION of §4.2 P2: every degraded
    component is grace-eligible AND no leak signal is present. It is
    False whenever the composite is fully healthy (the grace is then
    irrelevant) and False whenever ANY component carries a
    never-eligible class.
    ``grace_reason`` — the failure class that armed the grace (the class
    of the first degraded component).
    ``grace_expires_at`` — the OBSERVATION-RELATIVE expiry
    (``first_grace_eligible_observation + GRACE_HORIZON_S``). It is
    NEVER extended: a re-arm starts a NEW window with a NEW expiry.
    ``holds`` — how many ticks the grace has been held so far.
    ``leak_signal`` — the leak predicate's verdict for this tick.
    ``failure_streak`` — the per-component consecutive-failure counts.
    ``first_seen_degraded`` — when the current degraded episode began.
    """

    grace_active: bool = False
    grace_reason: Optional[str] = None
    grace_expires_at: Optional[datetime] = None
    grace_scope: Optional[str] = None
    holds: int = 0
    leak_signal: dict = field(default_factory=dict)
    failure_streak: dict = field(default_factory=dict)
    first_seen_degraded: Optional[datetime] = None


class GraceTracker:
    """Stateful, class-gated grace-window tracker.

    The daemon-side half of A1. It is deliberately SEPARATE from the
    verdict: the tracker NEVER changes ``ready`` / ``reasons`` /
    ``components`` — it only publishes whether a class-gated grace is
    currently held, so the GATE can decide to wait (decisions §3a A1.1:
    "the daemon never lies; the gate decides to wait").

    THE STATE MACHINE (requirements.md §4.2 P2 + §4.3 P3 + §4.4 P4):

    * **Arming** — the FIRST tick whose degraded components are ALL
      grace-eligible (and no leak signal is present) arms the window and
      records ``armed_at``. The expiry is
      ``armed_at + GRACE_HORIZON_S`` — ONE clock, OBSERVATION-RELATIVE.
      There is NO boot-relative ceiling.
    * **Holding** — while the window is open, the grace is held only on
      ticks where the CONJUNCTION still holds. Any never-eligible class
      or any leak signal ⇒ ``grace_active`` is False THIS tick (the
      conjunction is not a vote and there is no per-component grace).
    * **Expiry** — at ``armed_at + GRACE_HORIZON_S`` the window is
      lapsed: ``grace_active`` is False. The expiry is NEVER extended.
    * **Re-arm** — re-arming requires the grace to have FULLY lapsed: a
      NEW window starts at the next grace-eligible observation, with a
      NEW expiry. A new window is NOT an extension of the old one (the
      old expiry is never moved forward).
    * **Disengage** — a leak signal (or any never-eligible class)
      disengages the grace immediately; the window's expiry is NOT
      moved.

    The tracker is a plain in-process object (no schema, no migration);
    it lives on ``app.state`` so the readiness detail and the operators
    can observe it.
    """

    def __init__(
        self,
        *,
        horizon_s: float = READINESS_GRACE_HORIZON_S,
        leak_tick_threshold: int = READINESS_LEAK_TICK_THRESHOLD,
    ) -> None:
        self._horizon_s = float(horizon_s)
        self._leak_tick_threshold = int(leak_tick_threshold)
        self._armed_at: Optional[datetime] = None
        self._holds = 0
        self._failure_streak: dict[str, int] = {}
        self._first_seen_degraded: Optional[datetime] = None
        # Leak-predicate history (per-process, in-memory).
        self._available_history: list[int] = []
        self._checkedout_history: list[int] = []
        self._waiting_streak = 0

    # ── observability ──────────────────────────────────────────────
    @property
    def horizon_s(self) -> float:
        """The grace horizon (seconds) — ONE number, config-free."""
        return self._horizon_s

    @property
    def armed_at(self) -> Optional[datetime]:
        """When the current window was armed (None when never armed)."""
        return self._armed_at

    @property
    def holds(self) -> int:
        """How many ticks the grace has been held (all windows)."""
        return self._holds

    # ── the tick ───────────────────────────────────────────────────
    def observe(
        self,
        *,
        component_classes: dict[str, str],
        degraded_components: list[str],
        pool_state_psycopg: Optional[dict] = None,
        pool_state_sqlalchemy: Optional[dict] = None,
        now: Optional[datetime] = None,
    ) -> GraceObservation:
        """Evaluate one readiness tick and return the grace verdict.

        ``component_classes`` — the §4.0 class of each component
        (``"ok"`` for a healthy one). ``degraded_components`` — the names
        of the components that failed this tick. ``pool_state_*`` — the
        sampled pool states (P2 = oracle, P1 = advisory).
        """
        moment = now or datetime.now(timezone.utc)

        # ── 1. per-component failure streak ────────────────────────
        streak = dict(self._failure_streak)
        for name in READINESS_COMPONENT_NAMES:
            cls = component_classes.get(name)
            if cls and cls != FAILURE_CLASS_OK:
                streak[name] = int(streak.get(name, 0)) + 1
            else:
                streak[name] = 0
        self._failure_streak = streak
        max_streak = max(streak.values()) if streak else 0

        # ── 2. leak predicate (requirements.md §4.4 P4) ────────────
        leak = self._evaluate_leak(
            pool_state_psycopg=pool_state_psycopg,
            pool_state_sqlalchemy=pool_state_sqlalchemy,
            max_streak=max_streak,
        )
        # Plan Track-1 item 6: the alerting consumer rides the emission
        # here — it gates on ``detected`` itself, so non-firing ticks
        # are a no-op. The emission is UNCHANGED: the verdict dict, the
        # grace conjunction and their semantics/frequency are untouched.
        consume_leak_signal(leak)

        # ── 3. degraded episode bookkeeping ────────────────────────
        degraded = [c for c in degraded_components if c]
        if degraded:
            if self._first_seen_degraded is None:
                self._first_seen_degraded = moment
        else:
            self._first_seen_degraded = None

        # ── 4. the CONJUNCTION (§4.2 P2 — not a vote) ─────────────
        degraded_classes = [
            component_classes.get(name) or FAILURE_CLASS_UNKNOWN
            for name in degraded
        ]
        eligible = (
            bool(degraded)
            and all(c in GRACE_ELIGIBLE_FAILURE_CLASSES for c in degraded_classes)
            and not leak.get("detected")
        )

        # ── 5. arming / expiry (§4.3 P3 — ONE observation-relative clock) ──
        # (Re)arm: the FIRST grace-eligible observation of an EPISODE
        # arms the window. A tick INSIDE the window never moves
        # ``armed_at`` (the grace is NEVER extended). Once the window has
        # FULLY lapsed the grace stays OFF — a re-arm requires the
        # episode to be reset, which happens only when the composite
        # becomes fully healthy again (the daemon recovered). A new
        # window then starts a NEW expiry (a re-arm, never an extension
        # of the old one).
        if not degraded:
            # Fully healthy ⇒ the episode ends (the grace is reset).
            self._armed_at = None
        elif eligible and self._armed_at is None:
            self._armed_at = moment
        expires_at = (
            self._armed_at + timedelta(seconds=self._horizon_s)
            if self._armed_at is not None
            else None
        )
        grace_active = bool(
            eligible
            and expires_at is not None
            and moment < expires_at
        )
        if grace_active:
            self._holds += 1

        return GraceObservation(
            grace_active=grace_active,
            grace_reason=(degraded_classes[0] if degraded_classes else None),
            grace_expires_at=expires_at,
            grace_scope=READINESS_GRACE_SCOPE_LOAD if grace_active else None,
            holds=self._holds,
            leak_signal=leak,
            failure_streak=dict(streak),
            first_seen_degraded=self._first_seen_degraded,
        )

    # ── the leak predicate (requirements.md §4.4 P4) ───────────────
    def _evaluate_leak(
        self,
        *,
        pool_state_psycopg: Optional[dict],
        pool_state_sqlalchemy: Optional[dict],
        max_streak: int,
    ) -> dict:
        """Compute the leak signal for one tick.

        The ORACLE is the P2 psycopg saver pool: it arms on
        ``requests_waiting > 0`` sustained, or ``pool_available`` strictly
        decreasing across 3 consecutive ticks, or the pool size exhausted.

        The P1 SQLAlchemy pool is ADVISORY: its signals are PUBLISHED but
        MUST NOT arm the class.

        The STREAK arm (the same-component-failing-on-every-tick
        fallback) fires ONLY when the pool objects are UNAVAILABLE
        (S9): with the pool objects available, a sustained busy-but-
        healthy burst — the incident's own shape — must never be
        classified as a leak.

        ADJUDICATION — §4.4's TREND ARM IS DELIBERATELY SUBSUMED (this
        is a DEVIATION from §4.4's literal text and is FLAGGED FOR
        LEADER RATIFICATION — reviewer finding F1):

        * §4.4 lists three arms for the ORACLE pool: ``requests_waiting
          > 0`` sustained, ``pool_available`` strictly decreasing across
          3 consecutive ticks, and the pool size exhausted. Only the
          first and the third are implemented as independent arms; the
          second is SUBSUMED by the exhaustion arm.
        * WHY: read literally, the trend arm is UNSATISFIABLE in-band
          alongside §5(i)'s healthy-burst mandate. §5(i)'s own healthy
          series is a checked-out count that RISES AND FALLS — e.g.
          ``[1, 3, 5, 7, 5, 3, 1, 0, ...]`` — and its rising phase is
          three consecutive availability DECREASES before the first
          recovery tick, so a literal 3-tick trend arm fires on it. Any
          "≥3-tick episode with no recovery" variant fires on it too:
          no in-band sub-ceiling rule separates a long healthy ramp from
          a slow leak, because the two are the same observation until
          one of them recovers.
        * WHAT IS IMPLEMENTED: the exhaustion arm (``in_use >= pool_max``
          — every connection checked out), with the availability TREND
          reported inside its reason token (so the trend stays
          OBSERVABLE) rather than as a second, unreachable arm.
        * THE SUB-CEILING EXPOSURE IS BOUNDED, NOT OPEN-ENDED: a
          sub-ceiling monotonic no-recovery series does NOT arm the leak
          class, but it also cannot hold the grace open — the grace
          EXPIRES at ``first_grace_eligible_observation + GRACE_HORIZON_S``
          (120 s, §4.3), the soak's absolute bound is
          ``soak_entry + ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S``
          (300 s, §4.3), and the never-green trigger is byte-identical
          and UNCONDITIONAL (§4.1) — so a daemon that never greens still
          reaches cap-reach with ``consecutive_green < 3`` and is rolled
          back. A5(iv) therefore holds ON THE TIME BOUNDS.
        * The pin that proves the bounded property is
          ``test_sub_ceiling_no_recovery_series_cannot_hold_the_grace_open``
          in ``tests/unit/tools/test_readiness_failure_class.py`` (and its
          real-pool twin in
          ``tests/integration/test_checkpoint_pool_burst_shapes.py``).
        """
        checkedout_rising = self._checkedout_rising(pool_state_sqlalchemy)

        # ── ORACLE pool (P2) ────────────────────────────────────────
        if pool_state_psycopg is not None:
            waiting = int(pool_state_psycopg.get("requests_waiting", 0) or 0)
            available = pool_state_psycopg.get("pool_available")
            pool_size = pool_state_psycopg.get("pool_size")
            pool_max = pool_state_psycopg.get("pool_max")

            if waiting > 0:
                self._waiting_streak += 1
            else:
                self._waiting_streak = 0
            if self._waiting_streak >= self._leak_tick_threshold:
                return {
                    "detected": True,
                    "reason": (
                        "psycopg pool requests_waiting>0 sustained "
                        f"({self._waiting_streak} ticks)"
                    ),
                    "checkedout_rising": checkedout_rising,
                    "streak": int(max_streak),
                }

            if available is not None:
                self._available_history.append(int(available))
                while len(self._available_history) > self._leak_tick_threshold:
                    self._available_history.pop(0)

            if (
                pool_size is not None
                and available is not None
                and pool_max is not None
                and int(pool_max) > 0
            ):
                in_use = int(pool_size) - int(available)
                # "the pool size exhausted (all connections checked out)"
                # — EVERY connection of the pool is checked out (the
                # in-use count reached the pool's ceiling), NOT merely
                # "no idle connection right now" (psycopg's
                # ``pool_available`` is the IDLE count: it is 0 during
                # ANY busy period, so reading it alone would classify a
                # healthy burst as a leak).
                if in_use >= int(pool_max):
                    return {
                        "detected": True,
                        "reason": (
                            "psycopg pool exhausted (every connection "
                            "checked out)"
                            + (
                                "; availability strictly decreasing across "
                                f"{self._leak_tick_threshold} ticks"
                                if self._availability_decreasing()
                                else ""
                            )
                        ),
                        "checkedout_rising": checkedout_rising,
                        "streak": int(max_streak),
                    }

                # ── §4.4's "pool_available strictly decreasing across 3
                #    consecutive ticks" arm — DELIBERATELY SUBSUMED (see
                #    the ADJUDICATION in the docstring above). It is NOT
                #    implemented as an
                #    independent arm: there is no in-band precondition
                #    that separates a long healthy ramp from a slow leak,
                #    so any live form of it would arm the leak class on
                #    the healthy-burst shape requirements.md §5(i)
                #    mandates NOT be classified as a leak. The trend is
                #    therefore reported as PART OF the exhaustion arm's
                #    reason token (above) instead of as a second arm.

            # Pool objects ARE available ⇒ the streak arm MUST NOT fire.
            return {
                "detected": False,
                "reason": None,
                "checkedout_rising": checkedout_rising,
                "streak": int(max_streak),
            }

        # ── pool objects UNAVAILABLE ⇒ the streak arm is eligible (S9) ──
        if int(max_streak) >= self._leak_tick_threshold:
            return {
                "detected": True,
                "reason": (
                    "same component failing on every tick with pool objects "
                    f"unavailable ({max_streak} ticks)"
                ),
                "checkedout_rising": checkedout_rising,
                "streak": int(max_streak),
            }
        return {
            "detected": False,
            "reason": None,
            "checkedout_rising": checkedout_rising,
            "streak": int(max_streak),
        }

    def _availability_decreasing(self) -> bool:
        """Has the ORACLE pool's availability been strictly decreasing?

        This is the §4.4 TREND observation, reported as PART OF the
        exhaustion arm's reason token (the trend arm itself is
        DELIBERATELY SUBSUMED — see the ADJUDICATION in
        :meth:`_evaluate_leak`). The availability history is therefore
        live state, not a dead buffer: it is what makes the exhaustion
        verdict's trend evidence observable.
        """
        if len(self._available_history) < self._leak_tick_threshold:
            return False
        return (
            self._available_history[-1]
            < self._available_history[-2]
            < self._available_history[-3]
        )

    def _checkedout_rising(self, pool_state_sqlalchemy: Optional[dict]) -> bool:
        """ADVISORY signal: the P1 ``checkedout()`` count rising monotonically.

        PUBLISHED in ``detail.leak_signal.checkedout_rising`` and NEVER
        arming the leak class (requirements.md §4.4 P4).
        """
        if pool_state_sqlalchemy is None:
            return False
        checkedout = pool_state_sqlalchemy.get("checkedout")
        if checkedout is None:
            return False
        try:
            self._checkedout_history.append(int(checkedout))
        except (TypeError, ValueError):
            return False
        while len(self._checkedout_history) > self._leak_tick_threshold:
            self._checkedout_history.pop(0)
        if len(self._checkedout_history) < self._leak_tick_threshold:
            return False
        return all(
            self._checkedout_history[i] < self._checkedout_history[i + 1]
            for i in range(len(self._checkedout_history) - 1)
        )


# ── the leak-signal CONSUMER (plan Track-1 item 6) ─────────────────
def consume_leak_signal(leak: Optional[dict]) -> None:
    """Alerting consumer for one tick's leak-predicate verdict.

    The emission (``GraceTracker._evaluate_leak`` →
    ``GraceObservation.leak_signal`` → ``/readyz`` ``detail.leak_signal``)
    already existed — but nothing consumed it at alertable severity and
    nothing counted it, so the 2026-10-28 idle-in-transaction incident
    family produced leak signals nobody saw. This consumer closes that
    gap without touching the emission:

    * ONE ``logger.warning`` per fire, carrying the signal's context.
      The reason token names the pool identity (both pool arms are the
      oracle psycopg pool; the streak arm fires only when the pool
      objects are UNAVAILABLE — §4.4 S9), and ``streak`` /
      ``checkedout_rising`` carry the corroborating context.
    * ONE monotonic increment of ``checkpoint_leak_signal_total``
      (rendered by ``GET /api/metrics`` — always, even at 0).

    FAIL-OPEN: any consumer failure is swallowed at debug — the
    emitter's verdict, the grace conjunction and the composite are
    untouched (the same discipline as the read instrumentation's
    ``checkpoint_read_measurement_errors_total`` tripwire). Non-firing
    ticks (``detected`` falsy / non-dict) return without logging or
    counting.
    """
    try:
        if not isinstance(leak, dict) or not leak.get("detected"):
            return
        checkpoint_leak_signal_total.inc()
        logger.warning(
            "Checkpoint pool LEAK SIGNAL: reason=%s streak=%s "
            "checkedout_rising=%s",
            leak.get("reason"),
            leak.get("streak"),
            leak.get("checkedout_rising"),
        )
    except Exception as exc:  # noqa: BLE001 — the emitter must never break
        logger.debug(
            "Leak-signal consumer failed: %s — the emitter is unaffected",
            redact_exc_str(exc),
        )


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
    auth_dependency_status: Optional[str] = None,
    component_classes: Optional[dict[str, str]] = None,
    first_seen_degraded: Optional[datetime] = None,
    failure_streak: Optional[dict[str, int]] = None,
    pool_state_sqlalchemy: Optional[dict] = None,
    pool_state_psycopg: Optional[dict] = None,
    leak_signal: Optional[dict] = None,
    grace_active: bool = False,
    grace_reason: Optional[str] = None,
    grace_expires_at: Optional[datetime] = None,
    grace_scope: Optional[str] = None,
    grace_holds: int = 0,
    resume_hold: Optional[dict] = None,
    burst_signals: Optional[dict] = None,
    mcp_servers: Optional[dict] = None,
    mcp_buffered_transitions: Optional[int] = None,
    queue_max_age_seconds_reason: Optional[str] = None,
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

    ``auth_dependency_status`` (P3 T3.9) is advisory-only in the same
    way: the FR-30 enum value (``ok``/``degraded``/``unconfigured``)
    rides the composite verbatim and never enters ``ready``/``reasons``.

    The A1 keyword arguments are ADDITIVE and advisory in exactly the
    same way: the grace state, the leak signal, the pool state and the
    failure classes ride the composite but NEVER enter
    ``ready``/``reasons``/``components``. The daemon never lies: a
    grace-held composite is still ``degraded`` with its reasons intact.

    ``mcp_servers`` (C4) is additive and advisory in the same way: the
    per-built-in MCP health map rides the composite (published as
    ``detail.mcp``) and NEVER enters ``ready``/``reasons``/``components``
    — a degraded or quarantined MCP server must not take the readiness
    gate down with it.

    ``queue_max_age_seconds_reason`` is the rendered provenance of the
    age (see the ``QUEUE_AGE_REASON_*`` vocabulary): it says WHY the
    age is (or is not) there, so an absent age never reads as
    "fresh". It is ADVISORY — it never enters ``ready``/``reasons``/
    ``components`` (the verdict is unchanged by it).
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
        # The reason names the SENTINEL READ — the probe was replaced by
        # the sentinel ``aget_tuple`` cycle (the old string still named
        # the removed ``AsyncConnectionPool.check()`` probe; decisions §2
        # row 23).
        reasons.append(
            "checkpoint_saver: sentinel checkpoint read (aget_tuple) "
            "failed or timed out"
        )
    return ReadinessComposite(
        database=database_ok,
        queue_freshness=queue_fresh_ok,
        services=services_ok,
        checkpoint_saver=checkpoint_saver_ok,
        reasons=reasons,
        queue_max_age_seconds=queue_max_age_seconds,
        queue_max_age_seconds_reason=queue_max_age_seconds_reason,
        checked_at=checked_at or datetime.now(timezone.utc),
        inflight_turns=inflight_turns,
        auth_dependency_status=auth_dependency_status,
        component_classes=dict(component_classes or {}),
        first_seen_degraded=first_seen_degraded,
        failure_streak=dict(failure_streak or {}),
        pool_state_sqlalchemy=pool_state_sqlalchemy,
        pool_state_psycopg=pool_state_psycopg,
        leak_signal=leak_signal,
        grace_active=grace_active,
        grace_reason=grace_reason,
        grace_expires_at=grace_expires_at,
        grace_scope=grace_scope,
        grace_holds=grace_holds,
        resume_hold=resume_hold,
        burst_signals=burst_signals,
        mcp_servers=mcp_servers,
        mcp_buffered_transitions=mcp_buffered_transitions,
    )


#: The A1 additive fields, extracted from a composite so a rebuilt copy
#: (the forced-degradation drill) carries them forward instead of
#: dropping them.
_A1_COMPOSITE_FIELD_NAMES = (
    "component_classes",
    "first_seen_degraded",
    "failure_streak",
    "pool_state_sqlalchemy",
    "pool_state_psycopg",
    "leak_signal",
    "grace_active",
    "grace_reason",
    "grace_expires_at",
    "grace_scope",
    "grace_holds",
    "resume_hold",
    "burst_signals",
    # C4 (soak-gate-mcp-warmup): the MCP health map is ADDITIVE too and
    # must survive the forced-degradation rebuild (the drill must not
    # silently drop the operator's MCP visibility).
    "mcp_servers",
    "mcp_buffered_transitions",
)


def _a1_composite_fields(composite: "ReadinessComposite") -> dict[str, Any]:
    """The A1 additive fields of ``composite`` as a kwargs dict."""
    return {
        name: getattr(composite, name) for name in _A1_COMPOSITE_FIELD_NAMES
    }


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
    # The shared degraded-copy kwargs — the drill preserves every real
    # reading; only the `reasons` list differs between the two shapes.
    degraded_copy = dict(
        database=composite.database,
        queue_freshness=composite.queue_freshness,
        services=composite.services,
        checkpoint_saver=composite.checkpoint_saver,
        queue_max_age_seconds=composite.queue_max_age_seconds,
        checked_at=composite.checked_at,
        forced_degraded=True,
        auth_dependency_status=composite.auth_dependency_status,
        **_a1_composite_fields(composite),
    )
    if not composite.ready:
        # Already degraded by real causes — add the drill marker so the
        # reason list stays truthful about every contributor.
        degraded_copy["reasons"] = [
            *composite.reasons,
            f"readiness: degraded forced by {READINESS_FORCE_DEGRADED_ENV} (drill)",
        ]
    else:
        degraded_copy["reasons"] = [
            f"readiness: degraded forced by {READINESS_FORCE_DEGRADED_ENV} (drill)",
        ]
    return ReadinessComposite(**degraded_copy)


async def refresh_readiness_composite(
    *,
    db_probe: Optional[Callable[[], Any]],
    queue_probe: Optional[Callable[[], Any]],
    services_ok: bool,
    queue_freshness_threshold_seconds: float,
    now: Optional[Callable[[], datetime]] = None,
    checkpoint_saver_probe: Optional[Callable[[], Any]] = None,
    auth_dependency_status: Optional[str] = None,
    grace_tracker: Optional["GraceTracker"] = None,
    sqlalchemy_pool: Any = None,
    psycopg_pool: Any = None,
    resume_hold: Optional[dict] = None,
    burst_signals: Optional[dict] = None,
    mcp_servers: Optional[dict] = None,
    mcp_buffered_transitions: Optional[int] = None,
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

    PROBE RETURN CONTRACT (A1): a probe may return EITHER the legacy
    bare bool OR a ``(verdict, failure_class)`` pair. The pair form is
    what the shipped factories return (decisions §3d D1a: "the probe
    callable's signature goes from ``Callable[[], bool]`` to a
    ``(bool, class)`` pair, so the orchestrator can consume the class
    without re-deriving it from free-text reasons"). The legacy bool
    form stays ACCEPTED so existing injected callables (and the
    pure-seam tests) keep working unchanged: a bare ``True`` means
    healthy with the class ``ok``, a bare ``False`` means failed with
    the class ``unknown`` — which is NEVER grace-eligible (fail-closed).
    """
    checked_at = (now or (lambda: datetime.now(timezone.utc)))()

    def _unpack(value: Any) -> tuple[Any, Optional[str]]:
        """Split a probe result into ``(verdict, failure_class)``.

        The pair form is ``(verdict, class)``. A ``QueueProbeResult`` is a
        2-field NamedTuple and is NOT a pair — it is returned verbatim by
        the queue probe, which is handled on its own path below.

        A LEGACY bare bool carries NO class (``None``): the caller
        attributes the class, so the orchestrator's own attribution (a
        TIMEOUT ⇒ ``probe_timeout``; an EXCEPTION ⇒
        ``hard_component_red``) is never overridden by the legacy
        default.
        """
        if isinstance(value, QueueProbeResult):
            return value, None
        if isinstance(value, tuple) and len(value) == 2:
            verdict, cls = value
            return verdict, (cls if isinstance(cls, str) else None)
        return value, None

    async def _guarded(probe, timeout_s, default):
        """Run one probe; return ``(timed_out, raw_value, failure_class)``.

        ``raw_value`` is the probe result, the component default when the
        probe is ``None``/raises, or ``None`` on timeout; ``timed_out``
        disambiguates a timeout from a legitimate ``None`` default so
        each component can apply its own timeout semantics;
        ``failure_class`` is the §4.0 class the ORCHESTRATOR attributes to
        the failure (a TIMEOUT ⇒ ``probe_timeout``; an EXCEPTION ⇒
        ``hard_component_red``). A probe that returns a ``(verdict, class)``
        PAIR overrides the orchestrator's attribution with the pair's own
        class — that is the D1a contract (the probe knows more than the
        orchestrator does).
        """
        if probe is None:
            return False, default, None
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(probe), timeout=timeout_s
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return True, None, FAILURE_CLASS_PROBE_TIMEOUT
        except Exception as exc:
            # W2 (incident 2026-10-10): probe exceptions can carry
            # libpq server identifiers — redact before logging.
            logger.warning(
                "Readiness probe failed: %s", redact_exc_str(exc)
            )
            # An EXCEPTION (not a timeout) is row 2 — NEVER grace-
            # eligible (requirements.md §4.0).
            return False, default, FAILURE_CLASS_HARD_COMPONENT_RED
        return False, raw, None

    db_timed_out, database_raw, db_class = await _guarded(
        db_probe, DB_PROBE_TIMEOUT_S, False
    )
    database_ok, db_pair_class = _unpack(database_raw)
    if db_pair_class:
        db_class = db_pair_class
    if db_timed_out:
        database_ok = False
        db_class = FAILURE_CLASS_PROBE_TIMEOUT
    if database_ok and db_class is None:
        db_class = FAILURE_CLASS_OK
    elif not database_ok and db_class in (None, FAILURE_CLASS_OK):
        db_class = FAILURE_CLASS_UNKNOWN

    queue_timed_out, queue_raw, queue_guard_class = await _guarded(
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
        ckpt_class = FAILURE_CLASS_OK
    else:
        ckpt_timed_out, ckpt_raw, ckpt_class = await _guarded(
            checkpoint_saver_probe, CHECKPOINT_SAVER_PROBE_TIMEOUT_S, False
        )
        ckpt_value, ckpt_pair_class = _unpack(ckpt_raw)
        if ckpt_pair_class:
            ckpt_class = ckpt_pair_class
        if ckpt_timed_out:
            checkpoint_saver_ok = False
            ckpt_class = FAILURE_CLASS_PROBE_TIMEOUT
            ckpt_extra_reason = (
                "checkpoint_saver: sentinel checkpoint read (aget_tuple) "
                f"timed out after {CHECKPOINT_SAVER_PROBE_TIMEOUT_S}s"
            )
        elif not ckpt_value:
            checkpoint_saver_ok = False
            if ckpt_class in (None, FAILURE_CLASS_OK):
                ckpt_class = FAILURE_CLASS_HARD_COMPONENT_RED
            ckpt_extra_reason = (
                "checkpoint_saver: sentinel checkpoint read failed "
                "(checkpoint path cannot serve reads — see readiness logs)"
            )
        else:
            checkpoint_saver_ok = True
            if ckpt_class is None:
                ckpt_class = FAILURE_CLASS_OK
            ckpt_extra_reason = ""

    inflight_turns: Optional[int] = None
    extra_reasons: list[str] = []
    # WHY the age is (or is not) there — the rendered provenance. An
    # absent age is one of three facts (empty set / probe timeout /
    # probe failure) and the payload used to render all three as a
    # bare ``null`` next to ``queue_freshness: true`` — i.e. "fresh".
    queue_age_reason: str = QUEUE_AGE_REASON_UNKNOWN
    if queue_timed_out:
        # Distinguish "no RUNNING tasks" (None → fresh) from "never
        # got an answer" (timeout → degraded, age unknown).
        queue_max_age_seconds = None
        fresh = False
        queue_age_reason = QUEUE_AGE_REASON_PROBE_TIMEOUT
        extra_reasons.append("queue_freshness: queue probe timed out")
    else:
        if isinstance(queue_raw, QueueProbeResult):
            queue_max_age_seconds = queue_raw.max_age_seconds
            inflight_turns = queue_raw.inflight_turns
        else:
            queue_max_age_seconds = queue_raw
        # Negative ages clamp to 0.0 inside evaluate_queue_freshness —
        # see the freshness-clamp docstring at :245.
        fresh, queue_max_age_seconds = evaluate_queue_freshness(
            queue_max_age_seconds,
            threshold_seconds=queue_freshness_threshold_seconds,
        )
        if queue_guard_class == FAILURE_CLASS_HARD_COMPONENT_RED:
            # The probe RAISED — the age was never determined. (The
            # historical empty-set default left the age None next to a
            # "fresh" component: the render now says "unknown".)
            queue_age_reason = QUEUE_AGE_REASON_UNKNOWN
        elif queue_max_age_seconds is not None:
            queue_age_reason = QUEUE_AGE_REASON_MEASURED
        else:
            # The probe answered; the RUNNING set was empty (or every
            # RUNNING task was stop-frozen pre-boot) — the verdict is
            # VACUOUS, not a measurement.
            queue_age_reason = QUEUE_AGE_REASON_EMPTY
    if ckpt_extra_reason:
        extra_reasons.append(ckpt_extra_reason)

    # ── A1: per-component failure classes (§4.0) ────────────────────
    # The class travels WITH the verdict so no consumer re-derives it
    # from the (stale) free-text reason strings.
    component_classes: dict[str, str] = {
        "database": db_class or FAILURE_CLASS_OK,
        "services": (
            FAILURE_CLASS_OK if services_ok else FAILURE_CLASS_HARD_COMPONENT_RED
        ),
        "checkpoint_saver": ckpt_class or FAILURE_CLASS_OK,
    }
    if queue_timed_out:
        queue_class = FAILURE_CLASS_PROBE_TIMEOUT
    elif fresh:
        queue_class = FAILURE_CLASS_OK
    else:
        queue_class = FAILURE_CLASS_QUEUE_STALE
    component_classes["queue_freshness"] = queue_class

    degraded_components = [
        name
        for name in READINESS_COMPONENT_NAMES
        if component_classes.get(name, FAILURE_CLASS_UNKNOWN)
        != FAILURE_CLASS_OK
    ]

    # ── A1: pool-state sampling + the class-gated grace ────────────
    pool_state_sqlalchemy = sample_sqlalchemy_pool_state(sqlalchemy_pool)
    pool_state_psycopg = sample_psycopg_pool_state(psycopg_pool)

    observation = None
    if grace_tracker is not None:
        # F5: the observe call is GUARDED. An exception here would abort
        # the whole tick and leave the composite STALE — a bug in the
        # grace code would then break the EXISTING readiness reporting,
        # the most safety-critical surface in this commission. The guard
        # fails TOWARD the pre-grace behaviour (no grace for this
        # observation) and NEVER toward suppressing readiness: the real
        # component readings and reasons still ride the composite.
        try:
            observation = grace_tracker.observe(
                component_classes=component_classes,
                degraded_components=degraded_components,
                pool_state_psycopg=pool_state_psycopg,
                pool_state_sqlalchemy=pool_state_sqlalchemy,
                now=checked_at,
            )
        except Exception as exc:  # noqa: BLE001 — the tick must complete
            logger.error(
                "Readiness grace observation failed (%s: %s) — the tick "
                "continues WITHOUT grace for this observation (the "
                "component readings and reasons are unaffected)",
                type(exc).__name__,
                redact_exc_str(exc),
            )
            observation = None

    return compute_readiness_composite(
        database_ok=database_ok,
        queue_fresh_ok=fresh,
        services_ok=services_ok,
        queue_max_age_seconds=queue_max_age_seconds,
        queue_max_age_seconds_reason=queue_age_reason,
        checked_at=checked_at,
        extra_reasons=extra_reasons,
        inflight_turns=inflight_turns,
        checkpoint_saver_ok=checkpoint_saver_ok,
        auth_dependency_status=auth_dependency_status,
        component_classes=component_classes,
        first_seen_degraded=(observation.first_seen_degraded if observation else None),
        failure_streak=(observation.failure_streak if observation else {}),
        pool_state_sqlalchemy=pool_state_sqlalchemy,
        pool_state_psycopg=pool_state_psycopg,
        leak_signal=(observation.leak_signal if observation else None),
        grace_active=bool(observation.grace_active) if observation else False,
        grace_reason=(observation.grace_reason if observation else None),
        grace_expires_at=(observation.grace_expires_at if observation else None),
        grace_scope=(observation.grace_scope if observation else None),
        grace_holds=(observation.holds if observation else 0),
        resume_hold=resume_hold,
        burst_signals=burst_signals,
        mcp_servers=mcp_servers,
        # F2: the buffered-transition count MUST be forwarded here —
        # accepting it on the orchestrator and dropping it here made
        # ``detail.mcp_buffered_transitions`` DEAD on the live path
        # (the "true in unit, dead in production" family).
        mcp_buffered_transitions=mcp_buffered_transitions,
    )


def _manager_pool_effective_capacity(pool: Any) -> Optional[int]:
    """The pool's EFFECTIVE total capacity (configured size + overflow).

    Resolved from the pool object the engine ACTUALLY holds, AT CALL
    TIME — the manager engine's size is config-driven
    (``limits.manager_pool_size``) and can differ between boots, so a
    module-level threshold is never authoritative:

    * ``pool.size()`` — the PUBLIC ``QueuePool`` accessor for the
      CONFIGURED pool size (SQLAlchemy returns ``_pool.maxsize``);
    * ``pool._max_overflow`` — PRIVATE (no public accessor) ⇒ read
      defensively via ``getattr``.

    Returns ``None`` when the capacity cannot be resolved (a pool
    without ``size()`` — StaticPool/NullPool/test doubles — or an
    unreadable/negative overflow). The caller MUST then NOT refuse:
    an unresolvable capacity is NOT proof of exhaustion, and refusing
    on a guess would strand a probe that still had servable slots
    (fail-open ON REFUSAL — the safe direction; the probe then behaves
    exactly as it did before the pre-check existed).
    """
    size_accessor = getattr(pool, "size", None)
    if size_accessor is None:
        return None
    try:
        base = int(size_accessor())
    except (TypeError, ValueError):
        return None
    overflow = getattr(pool, "_max_overflow", None)
    if overflow is None:
        return None
    try:
        overflow = int(overflow)
    except (TypeError, ValueError):
        return None
    if overflow < 0:
        return None
    return base + overflow


def _refuse_pool_admission_at_capacity(engine: Engine) -> None:
    """F4-A: probe-scoped acquisition admission pre-check.

    Runs at the top of each probe body, immediately BEFORE
    ``engine.connect()``. When the manager pool is at its EFFECTIVE
    capacity (the engine's own configured pool size + configured
    overflow — resolved per call by
    ``_manager_pool_effective_capacity``) the probe raises
    ``TimeoutError`` SYNCHRONOUSLY instead of entering the blocking
    checkout — ``asyncio.wait_for``'s cancellation cannot stop a
    running ``to_thread`` worker, so a probe that enters a saturated
    pool becomes a stranded waiter bounded only by the engine-level
    ``pool_timeout`` (30 s). The raised ``TimeoutError`` is classified
    by the EXISTING ``except TimeoutError`` branch in ``_guarded``
    (``FAILURE_CLASS_PROBE_TIMEOUT``) ⇒ grace-eligibility preserved,
    no verdict-contract change, and no thread is stranded because the
    refusal happens before the checkout.

    Engine-agnostic — no statement is issued, so no dialect guard is
    needed. Residual (disclosed): a checkout that loses the race
    between this pre-check and ``engine.connect()`` can still strand
    the probe thread for up to ``pool_timeout`` = 30 s.

    Two shape guards, both fail-safe (NEVER refuse on a guess):
    a pool WITHOUT ``checkedout`` cannot be "at capacity" in the
    QueuePool sense (StaticPool — SQLAlchemy's REAL sqlite pool,
    production-shaped; NullPool; test doubles) ⇒ the pre-check SKIPS
    for such pools; and a pool whose EFFECTIVE capacity cannot be
    resolved (no ``size()``; unreadable overflow) ⇒ the pre-check
    SKIPS as well (fail-open ON REFUSAL). The manager engine is a
    QueuePool and keeps the current behavior.

    The threshold is NEVER a static literal (2026-10-29): the manager
    pool size is config-driven and operator-raisable, so a fixed
    threshold would refuse admission while overflow slots are still
    servable — a self-inflicted outage.
    """
    pool = getattr(engine, "pool", None)
    if pool is None:  # pragma: no cover — defensive (Engine always has one)
        return
    checkedout = getattr(pool, "checkedout", None)
    if checkedout is None:
        # Shape guard — non-QueuePool pools cannot be "at capacity":
        # skip, never crash (2026-10-29 functional gate: 11 net-new
        # reds from StaticPool/FakeEngine shapes; see docstring).
        return
    capacity = _manager_pool_effective_capacity(pool)
    if capacity is None:
        # Fail-open ON REFUSAL: an unresolvable capacity is not proof
        # of exhaustion — never refuse on a guess.
        return
    in_use = checkedout()
    if in_use >= capacity:
        raise TimeoutError(
            "readiness probe admission refused: manager pool at "
            f"effective capacity ({in_use}/{capacity})"
        )


def _probe_statement_timeout_ms(client_budget_s: float) -> int:
    """F4-B: the probe-scoped server-side statement bound, in ms.

    ``2 x`` the probe's own asyncio client budget (``asyncio.wait_for``
    in ``_guarded`` fires FIRST at 1x, so the class stays
    ``probe_timeout``; at <= 1x the server error would race the client
    and could reclassify a timeout as hard-component-red). The ONE
    derivation site so the two probes cannot drift.
    """
    return int(2 * client_budget_s * 1000)


def make_db_probe(engine: Engine) -> Callable[[], tuple[bool, Optional[str]]]:
    """Build the database component probe bound to a sync engine.

    Raises on any connectivity/execute failure — the orchestrator's
    timeout/exception guard turns that into ``database=False`` AND
    classifies it as ``hard_component_red`` (an EXCEPTION is row 2, never
    grace-eligible).

    The probe returns the A1 ``(verdict, failure_class)`` PAIR: the
    verdict and its §4.0 class travel together so the orchestrator never
    re-derives a class from a free-text reason string.
    """

    def _probe() -> tuple[bool, Optional[str]]:
        # F4-A — admission pre-check BEFORE the blocking checkout.
        _refuse_pool_admission_at_capacity(engine)
        with engine.connect() as conn:
            # F4-B — probe-scoped statement bound, FIRST statement
            # (it opens the autobegun transaction that scopes the
            # setting). PG-only, the same house idiom as the per-tx
            # bound in ens_db_repair_postgres_select (see
            # SELECT_TX_STATEMENT_TIMEOUT_MS in daemon/tools/ens_db_tools.py);
            # SQLite does not support SET LOCAL.
            # Shape guard: an engine without ``url`` (test doubles) is
            # not postgres-shaped — the PG-only bound skips for it.
            _engine_url = getattr(engine, "url", None)
            if (
                _engine_url is not None
                and _engine_url.get_backend_name().startswith("postgres")
            ):
                conn.execute(
                    sa_text(
                        "SET LOCAL statement_timeout = "
                        f"{_probe_statement_timeout_ms(DB_PROBE_TIMEOUT_S)}"
                    )
                )
            conn.execute(sa_text("SELECT 1"))
            # D-07 — explicit end-of-transaction before the connection
            # returns to the pool (documented no-op when no transaction
            # is active, SQLAlchemy 2.0.48). Shape guard: a connection
            # without ``rollback`` (test doubles) skips the no-op.
            if hasattr(conn, "rollback"):
                conn.rollback()
        return True, None

    return _probe


def make_checkpoint_saver_probe(
    checkpointer: Any,
) -> Callable[[], tuple[bool, Optional[str]]]:
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
        def _noop_probe() -> tuple[bool, Optional[str]]:
            return True, None

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
        def _passthrough_probe() -> tuple[bool, Optional[str]]:
            return True, None

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
        # ``cache_bypass=True`` (read-cache plan §4.1): the sentinel
        # must exercise the REAL production path (retry proxy + lock
        # + charge), skipping ONLY the cache layer — a cache hit would
        # prove the cache healthy, not the checkpoint path.
        await saver.aget_tuple(sentinel_read_config, cache_bypass=True)

    def _saver_lock_held() -> bool:
        """The NON-BLOCKING saver-lock observation (D1a).

        ``checkpointer.raw_saver.lock.locked()`` — a cheap, synchronous
        boolean read of the saver's single ``asyncio.Lock``. The probe
        NEVER ACQUIRES the lock: the saver's lock is a plain
        NON-REENTRANT ``asyncio.Lock`` that every production checkpoint
        op also takes (``langgraph/checkpoint/postgres/aio.py:374``), so
        an acquire-and-read variant would self-deadlock the probe AND
        queue every production write behind it (SAFETY #5).

        Returns ``False`` when the attribute cannot be resolved — an
        unresolvable expression must never silently degrade the class
        to "pool busy" (it fails toward the UNKNOWN class, which is
        never grace-eligible).
        """
        lock_obj = getattr(saver, "lock", None)
        locked = getattr(lock_obj, "locked", None)
        if not callable(locked):
            return False
        try:
            return bool(locked())
        except Exception:  # noqa: BLE001 — unreadable ⇒ "not held"
            return False

    def _probe() -> tuple[bool, Optional[str]]:
        # Schedule the sentinel cycle on the captured loop from this
        # worker thread and wait for it synchronously with a timeout
        # that is slightly under ``CHECKPOINT_SAVER_PROBE_TIMEOUT_S``
        # so ``asyncio.wait_for`` in ``_guarded`` is the budget
        # authority (defense in depth — both layers enforce it).
        #
        # D1a (decisions §3d): the probe distinguishes "the saver's LOCK
        # is saturated" (``pool_busy`` — grace-eligible) from "the
        # checkpoint path is broken" (``hard_component_red`` — never
        # grace-eligible) from "the read timed out with the lock FREE"
        # (``read_timeout`` — grace-eligible). The lock is OBSERVED, never
        # acquired.
        future = asyncio.run_coroutine_threadsafe(_sentinel_cycle(), loop)
        try:
            future.result(timeout=CHECKPOINT_SAVER_PROBE_TIMEOUT_S - 0.1)
        except FuturesTimeout:
            # GHOST-WAITER WITHDRAWAL (decisions §3d D1b): the budget
            # expired while the scheduled cycle was still queued/running.
            # Cancel it so the pool does NOT retain a queued acquisition
            # until the pool deadline (the library default was 30s — the
            # pool's own ``timeout`` kwarg now bounds that too). Whether
            # cancelling removes psycopg_pool's internal waiter is a
            # LIBRARY-BEHAVIOUR question pinned by the ghost-waiter
            # integration pin, not assumed here.
            future.cancel()
            if _saver_lock_held():
                return False, FAILURE_CLASS_POOL_BUSY
            return False, FAILURE_CLASS_READ_TIMEOUT
        except Exception:
            # NO logging here — ``_guarded`` in
            # refresh_readiness_composite is the single log site for
            # probe failures (the prior revision logged here AND in
            # _guarded: a double log). Re-raise so the orchestrator
            # degrades the component with the sentinel-read reason and
            # classifies it as ``hard_component_red`` (an EXCEPTION is
            # row 2 — NEVER grace-eligible).
            raise
        return True, None

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
        # F4-A — admission pre-check BEFORE the blocking checkout.
        _refuse_pool_admission_at_capacity(engine)
        with engine.connect() as conn:
            # F4-B — probe-scoped statement bound, FIRST statement
            # (it opens the autobegun transaction that scopes the
            # setting). PG-only, the same house idiom as the per-tx
            # bound in ens_db_repair_postgres_select (see
            # SELECT_TX_STATEMENT_TIMEOUT_MS in daemon/tools/ens_db_tools.py);
            # SQLite does not support SET LOCAL.
            # Shape guard: an engine without ``url`` (test doubles) is
            # not postgres-shaped — the PG-only bound skips for it.
            _engine_url = getattr(engine, "url", None)
            if (
                _engine_url is not None
                and _engine_url.get_backend_name().startswith("postgres")
            ):
                conn.execute(
                    sa_text(
                        "SET LOCAL statement_timeout = "
                        f"{_probe_statement_timeout_ms(QUEUE_PROBE_TIMEOUT_S)}"
                    )
                )
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
            # D-07 — explicit end-of-transaction before the connection
            # returns to the pool (documented no-op when no transaction
            # is active, SQLAlchemy 2.0.48). Shape guard: a connection
            # without ``rollback`` (test doubles) skips the no-op.
            if hasattr(conn, "rollback"):
                conn.rollback()
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
