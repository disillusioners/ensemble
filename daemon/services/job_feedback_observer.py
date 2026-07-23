"""JobFeedbackObserver: Subscribes to EventBus and propagates instance lifecycle events to job completion.

This service is the PRIMARY job completion mechanism. It subscribes to the EventBus
and listens for instance_lifecycle events, mapping instance completion to job completion
using atomic transitions and releasing locks via the lock repository.

Key behaviors:
- Subscribes to EventBus for instance_lifecycle events
- Uses atomic_transition() to safely update job status
- Releases locks via lock_repo after job completion
- Handles race conditions gracefully (e.g., with terminate_instance())
- Provides health monitoring with periodic logging
- **Phase 1 (2026-06-24, report-lane decoupling)**: terminal transitions for
  parents with pending children are driven by ``_process_event`` (the lifecycle
  event handler), NOT by a bus callback. The bus is a pure state machine that
  transitions PENDING → FIRED watchers; the report ``Task`` (PROCESS_REPORT)
  claims the work, drives a parent graph turn, and emits the lifecycle event
  that ``_process_event`` consumes to finalize. This eliminates the orphan-Task
  bug (bus finalize killed the job while its report Task was still PENDING).
- **Phase 2 (DependencyBus)**: ``_process_event`` consults the bus's per-parent
  pending count to decide between ``in_progress`` (children still resolving) and
  terminal (no children / already resolved). Per-child error status is threaded
  from the bus's ``had_parent_error`` + new ``parent_error_message`` to
  ``_finalize_job`` (Step 1.7 "any error → error" rule).
- **Phase 3 (Cascade Unification)**: terminal transitions now perform the FULL
  instance-side fan-out (status update, CompletionRegistry signal, lifecycle
  event publish, SSE status_change). Without this, instances stay in RUNNING
  while their jobs show COMPLETED — breaking ``invoke_agent_and_wait()`` callers
  and orphan-job detection. Mirrors the inline cascade in ``child_reports.py``
  and ``error_reporting.py`` on the bus path.

Architecture:
  - The bus tracks per-parent state (pending count, error flag, error message,
    generation counter) in ``dependency_watchers`` + in-memory dicts. It does
    NOT directly call back into the observer — the report Task is the bridge.
  - ``_process_event`` is the SOLE path for terminal transitions when a parent
    has children tracked by the bus. It emits ``in_progress`` notifications
    when a child completes but other responses are still pending; when no
    children are still pending (none / already resolved), the handler falls
    through to the shared terminal transition (same as the bus-singleton-
    missing path below).
  - **Bus singleton missing**: when ``get_dependency_bus()`` returns ``None``
    (bus singleton missing — hard error), the gate is treated as a no-op
    (returns 0). The caller's own in-session gate remains the authoritative
    decision point.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlmodel import Session, select, update as sqlmodel_update
from sqlalchemy import func, text as _sa_text

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobLock
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState
from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.job_queue_service import DemandState, JobQueueService
from daemon.services.job_state_machine import InvalidTransitionError
from daemon.write_pause_guard import WriteGuardSession

if TYPE_CHECKING:
    from daemon.config import JobSystemConfig
    from daemon.services.event_bus import EventBus
    from daemon.services.job_queue_service import JobQueueService

logger = logging.getLogger(__name__)


# Statuses where the observer must NOT run completion/finalize
# processing — includes PAUSED as a defense against the question()
# tool's PAUSED → COMPLETED overwrite race (2026-07-21 bug).
#
# PAUSED is a user-intervention checkpoint; the pause cascade commits
# PAUSED to the DB before the lifecycle event returns, but a timing
# gap between the cascade and the observer's completion path can let
# ``_finalize_job`` / ``_finalize_instance`` fire afterwards and
# overwrite PAUSED → COMPLETED. Adding PAUSED here makes every guard
# below skip finalize for paused instances, so the in-flight answer
# submission / resume flow is preserved. When the user resumes
# (PAUSED → running) the instance leaves this set and normal
# completion processing resumes.
#
# This is intentionally a separate set from the one in
# ``daemon.services.job_recovery_service`` — that one means
# "permanently stopped at startup, safe to clean up", where PAUSED
# must remain alive/recoverable. Here the semantic is
# "skip finalize / idempotency guard", where PAUSED belongs.
_TERMINAL_INSTANCE_STATUSES: frozenset[str] = frozenset({
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
    InstanceStatus.FAILED.value,
    InstanceStatus.PAUSED.value,  # question() tool — guard against PAUSED → COMPLETED overwrite
})

# Fallback error string when the bus has a parent-error flag set but no
# explicit ``parent_error_message`` (e.g. the bus was never told which
# child failed). Used by :func:`_resolve_finalize_status` to keep the
# finalize call deterministic in the absence of message context.
CHILD_AGENT_ERROR_FALLBACK = "child agent error"


def _resolve_finalize_status(
    bus,
    instance_id: str,
    default_status: str,
    default_error: str | None = None,
) -> tuple[str, str | None]:
    """Apply the parent-error override for finalize.

    The conservative rule: a child error propagates to the parent job's
    terminal status, overriding the parent's own (possibly successful)
    turn. If the bus has ``had_parent_error(instance_id)`` set, the
    returned status is ``InstanceStatus.ERROR.value`` and the error
    string is the bus's ``parent_error_message(instance_id)`` (or
    :data:`CHILD_AGENT_ERROR_FALLBACK` when the bus did not record a
    message). Otherwise the ``default_status`` / ``default_error`` pair
    is returned unchanged.

    Centralized so both :meth:`JobFeedbackObserver._process_event` and
    the crash-recovery path in ``daemon/api.py`` apply the same rule
    from a single source.

    Args:
        bus: The :class:`DependencyBus` singleton (or ``None`` when
            the bus is uninitialized — treated as "no error override").
        instance_id: The parent instance id whose sticky error flag
            to consult.
        default_status: The status to return when no error override
            applies.
        default_error: The error string to return when no error
            override applies (may be ``None``).

    Returns:
        A ``(status, error)`` tuple suitable for passing directly to
        :meth:`JobFeedbackObserver._finalize_job`.
    """
    if bus is not None and bus.had_parent_error(instance_id):
        status = InstanceStatus.ERROR.value
        error = bus.parent_error_message(instance_id) or CHILD_AGENT_ERROR_FALLBACK
        return status, error
    return default_status, default_error


# Bounded defer counter for the bus gate's exception fallback
# (F1 fix, 2026-06-20). When the gate keeps raising exceptions (e.g.,
# persistent DB connectivity issues, replica failover, deadlock storms),
# naively returning ``skip=True`` on every exception would silently wedge
# the job in PROCESSING forever. Existing recovery mechanisms do NOT
# cover this case: ``StaleTaskRecovery`` operates on the ``task`` table
# (not ``job_queue``); ``JobProcessor`` orphan recovery skips instances
# that are alive (gate-deferred jobs have RUNNING instances); and
# ``JobRecoveryService.recover_on_startup`` only runs at daemon restart.
# This counter is the escape valve: after ``_MAX_GATE_DEFERS``
# consecutive deferrals for the same instance we fall through to
# finalize, accepting potential premature completion as the lesser evil
# compared to permanent stuck-job invisibility.
_gate_defer_counts: dict[str, int] = {}
_MAX_GATE_DEFERS = 5


class _InstanceFinalizeResult(NamedTuple):
    """Result of the sync DB half of ``_finalize_instance``.

    Carries the values the async caller needs after the WriteGuardSession
    block has run on a worker thread (via ``asyncio.to_thread``):

    * ``skip`` — when True, the caller should return early without firing
      the post-commit side effects (SSE / CompletionRegistry / lifecycle
      event). Set when the instance row is missing or already terminal.
    * ``parent_id`` / ``agent_id`` — captured from the instance row before
      the session closes (the instance is detached after commit).
    """

    skip: bool
    parent_id: str | None
    agent_id: str | None


class _ProcessingJobContext(NamedTuple):
    """Lightweight finalize context passed through the observer's terminal chain.

    Phase 2.5 (2026-06-27, D13 consumption-site rewrite). Replaces the
    direct ``JobItem`` reference that the observer previously returned
    from :meth:`JobFeedbackObserver._get_processing_job_for_instance`.

    Pre-D13: messages created ``JobItem`` rows, so every finalization had
    a JobItem to update. ``_finalize_job`` took a ``JobItem`` directly and
    used ``job.job_id`` throughout.

    Post-D13: messages create ``Task`` rows instead of ``JobItem`` rows.
    The observer's terminal chain still needs the ``job_id`` (used by
    ``_finalize_job_db_sync`` Step 1 — the JobItem UPDATE — and by
    notify_watchers / _trigger_next_job downstream side effects), but
    there is no ``JobItem`` to attach to.

    Two semantic modes:

      * ``job_id is not None`` — TASK-type jobs and any pre-D13 legacy
        JobItem that still exists. Step 1 of ``_finalize_job_db_sync``
        runs as before; downstream side effects
        (``notify_watchers``, ``_trigger_next_job``) fire with this
        ``job_id``.
      * ``job_id is None`` — MESSAGE-driven instances in the post-D13
        world. Step 1 is skipped (no JobItem to UPDATE); Steps 2+3
        (instance status + lock release) ALWAYS run; downstream
        ``notify_watchers`` and ``_trigger_next_job`` are skipped
        (no JobItem to notify watchers of, and the next job, if any,
        is claimed by the WorkerPool path).

    ``instance_id`` is always set (even when ``job_id`` is ``None``) —
    it is the canonical identifier the finalize chain operates on.
    """

    instance_id: str
    job_id: str | None


class _FinalizeJobResult(NamedTuple):
    """Result of the sync DB half of ``_finalize_job`` (H15 fix).

    Outbox-style payload: carries everything the async caller needs to fire
    post-commit side effects (SSE / notify_watchers / CompletionRegistry /
    lifecycle event / _trigger_next_job) on the event loop after the
    WriteGuardSession commits.

    * ``skip`` — True means no-op (unknown terminal_status, CM re-check
      aborted, job not found, gate defer). Caller returns silently without
      firing any side effects.
    * ``terminal_status`` — ``"completed"`` or ``"error"`` (for SSE /
      CompletionRegistry).
    * ``job_id`` / ``instance_id`` — IDs for side effects.
    * ``parent_id`` / ``agent_id`` — captured from the instance row before
      the session closes (instance is detached after commit).
    * ``result_summary`` / ``error_message`` — for ``notify_watchers``.
    * ``locks_released`` — count of ``job_locks`` rows deleted (for DEBUG log).
    * ``instance_was_terminal`` — True when the instance row was already
      in a terminal status before this transition (or missing). The caller
      uses this to decide whether to fire instance-side side effects (SSE
      / CompletionRegistry / lifecycle event): they were already fired by
      whoever set the instance terminal first, OR the instance row is
      missing and there is no consumer to notify.
    * ``gate_deferred`` — True ONLY when the bus gate returned
      ``skip=True`` (bus PENDING > 0 or the in-session gate SELECT
      exception fallback). The caller scopes the bus re-arm to this
      flag so the other ``skip`` paths (unknown terminal_status,
      gate check aborted) don't create spurious empty watcher
      entries — C2-N1 fix.
    """

    skip: bool = False
    terminal_status: str | None = None
    job_id: str | None = None
    instance_id: str | None = None
    parent_id: str | None = None
    agent_id: str | None = None
    result_summary: str | None = None
    error_message: str | None = None
    locks_released: int = 0
    instance_was_terminal: bool = False
    gate_deferred: bool = False


class JobFeedbackObserver:
    """Observes instance lifecycle events and propagates them to job completion.

    This service is the primary job completion mechanism. It listens for instance_lifecycle
    events from the EventBus and maps them to job completions using atomic transitions.

    Attributes:
        _event_bus: EventBus instance for subscribing to events.
        _job_queue_service: JobQueueService instance for looking up jobs.
        _job_repo: JobRepository for atomic job transitions.
        _lock_repo: LockRepository for releasing locks.
        _config: JobSystemConfig for configuration values.
        _queue: asyncio.Queue for receiving events.
        _task: asyncio.Task running the observer loop.
        _running: Whether the observer is running.
    """

    def __init__(
        self,
        event_bus: "EventBus",
        job_queue_service: "JobQueueService",
        job_repo: JobRepository,
        lock_repo: LockRepository,
        project_repo: SQLModelProjectRepository,
        instance_manager,
        config: "JobSystemConfig" | None = None,
    ) -> None:
        """Initialize the JobFeedbackObserver.

        Args:
            event_bus: EventBus instance for subscribing to events.
            job_queue_service: JobQueueService for get_job_by_instance().
            job_repo: JobRepository for atomic_transition().
            lock_repo: LockRepository for releasing locks.
            project_repo: SQLModelProjectRepository for pause state checks.
            instance_manager: InstanceManager for spawning instances and enqueuing messages.
            config: Optional JobSystemConfig for health check interval.
        """
        self._event_bus = event_bus
        self._job_queue_service = job_queue_service
        self._job_repo = job_repo
        self._lock_repo = lock_repo
        self._project_repo = project_repo
        self._instance_manager = instance_manager
        self._config = config

        # Health monitoring configuration
        if config is not None:
            self._health_check_interval = config.observer_health_check_interval_seconds
        else:
            self._health_check_interval = 300  # Default 5 minutes

        # Lifecycle state
        self._running: bool = False
        self._subscriber_id: str = "job_feedback_observer"
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        # Strong references to in-flight ``_deferred_finalize_check``
        # background tasks (B1 fix, 2026-06-27). Python's event loop
        # only keeps weak references to ``asyncio.Task`` objects, so
        # a fire-and-forget ``asyncio.create_task(...)`` whose return
        # value is discarded can be garbage-collected mid-flight.
        # Storing the task here (a set of strong refs) keeps the
        # task alive until completion; the ``add_done_callback`` at
        # every call site auto-removes the entry when the task
        # finishes. The set is also drained and cancelled in
        # :meth:`stop` (B2 fix) so deferred checks do not fire
        # against a torn-down observer.
        self._deferred_finalize_tasks: set[asyncio.Task] = set()

    def _bus_count_pending_for_target_sync(
        self, target_instance_id: str
    ) -> int:
        """Sync helper: count PENDING watchers targeting ``target_instance_id`` in the bus.

        **Fail-OPEN semantics (warning)**: this helper catches all
        exceptions and returns ``0`` (treated as "no pending
        watchers"). This is fail-OPEN — a transient DB failure
        passes the gate and may allow premature finalization.

        **Caller contract**: only use this helper for **defense-in-
        depth checks** that have a separate safety net (the in-
        session bus gate inside ``WriteGuardSession``, a parent bus-pending
        check, etc.). The **authoritative bus
        gate** at the finalization decision point must use the
        inline COUNT query directly on the ``WriteGuardSession``'s
        session object (see ``_finalize_job_db_sync``), so the
        COUNT and UPDATE share one transaction. The inline query
        lets exceptions propagate to the caller's fail-safe path
        instead of silently returning ``0``.

        Kept for: (a) tests that exercise the bus counter path
        directly without going through the finalization gate, (b)
        the early defense-in-depth check above
        ``_finalize_job_db_sync`` (which has its own in-session
        gate as the authoritative decision point).

        Used by the sync job-finalization gate in
        :meth:`_finalize_job_db_sync` (which runs inside
        ``WriteGuardSession`` on a worker thread — an ``await`` is
        impossible there) to consult the bus. The bus DB is the
        authoritative source of pending-children truth, and this
        sync helper lets the finalization gate check it without an
        ``await``. The bus DB MUST be consulted to prevent
        premature job finalization while children tracked via the
        bus are still running.

        Fallback semantics: bus singleton missing → returns 0.
        This treats the gate as a no-op when the bus is not wired, so the
        caller falls through to its own safe default rather than blocking
        on an unavailable authority.

        The implementation delegates to
        :meth:`DependencyBus.count_pending_for_target_sync` which
        wraps the sync repository's ``count_pending_for_target``
        COUNT(*) query (dialect-portable — works on both SQLite
        and PostgreSQL).

        Args:
            target_instance_id: The parent instance ID whose
                PENDING watcher count is being queried.

        Returns:
            Non-negative integer count of PENDING watchers for
                the given target. Returns 0 when the bus singleton is
                not wired or the DB query fails.
        """
        from daemon.services.dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return 0

        try:
            return bus.count_pending_for_target_sync(target_instance_id)
        except Exception as e:
            # FAIL-OPEN (see method docstring): a DB failure here
            # returns 0 and PASSES the gate. Callers that use this
            # helper at the finalization decision point MUST have a
            # separate safety net (typically the in-session bus
            # gate inline query that shares a transaction with the
            # UPDATE). Logged at warning level so persistent
            # failures surface in observability without taking
            # down the finalization path.
            logger.warning(
                f"bus.count_pending_for_target_sync failed for "
                f"{target_instance_id[:8]}...: {e} — treating as 0 "
                f"(FAIL-OPEN: bus pending-children check skipped, "
                f"may cause premature job finalization if persistent)"
            )
            return 0

    def _count_pending_tasks_for_instance_sync(
        self, instance_id: str
    ) -> int:
        """Sync helper: count PENDING ``task`` rows for ``instance_id``.

        F14 (defer-seam bugfix Phase 3, 2026-07-01). The premature-
        finalization gate used to count ``dependency_watchers`` rows
        only — but a child Task whose ``send_message`` failed before
        ``bus.watch`` ran never registers a watcher row, leaving the
        parent invisible to the gate. The parent JobItem then
        finalizes to ``done`` prematurely, while the orphan Task is
        later force-cancelled + retried against a terminal instance
        (F10 / P1 root cause family).

        This helper closes that seam by ALSO counting non-bus-registered
        PENDING Tasks for the instance. The gate that consumes this
        helper (see :meth:`_finalize_job_db_sync`) defers finalization
        if EITHER the bus has PENDING watchers OR the ``task`` table
        has PENDING rows for the instance — so a child whose
        ``bus.watch`` hasn't fired yet still keeps the parent alive.

        **Fail-OPEN semantics**: this helper catches all exceptions
        and returns ``0`` (treated as "no pending tasks"). This is
        fail-OPEN — a transient DB failure passes the gate and may
        allow premature finalization. Callers that use this helper
        at the finalization decision point MUST have a separate
        safety net (the in-session ``task``-table inline query that
        shares a transaction with the JobItem UPDATE).

        Implementation: opens a fresh ``SQLModelSession`` against the
        same engine the ``InstanceManager`` uses, runs the parameterized
        COUNT against the indexed ``ix_task_instance_id`` and
        ``ix_task_status`` columns. The parameterized ``IN`` clause
        with named binds is dialect-portable (works on SQLite and
        PostgreSQL).

        Args:
            instance_id: The instance ID whose PENDING ``task`` count
                is being queried.

        Returns:
            Non-negative integer count of PENDING tasks for the
            given instance. Returns 0 when the DB query fails.
        """
        from sqlmodel import Session as _SQLModelSession, select as _select
        from daemon.repositories.task.models import Task, TaskStatus

        engine = getattr(self._instance_manager, "engine", None)
        if engine is None:
            return 0
        try:
            with _SQLModelSession(engine) as db_session:
                stmt = (
                    _select(func.count())
                    .select_from(Task)
                    .where(Task.instance_id == instance_id)
                    .where(Task.status == TaskStatus.PENDING.value)
                )
                return int(db_session.scalar(stmt) or 0)
        except Exception as e:
            # FAIL-OPEN (see method docstring): a DB failure here
            # returns 0 and PASSES the gate. The in-session inline
            # query below is the authoritative safety net — exceptions
            # there propagate to the W3 fail-safe path in
            # ``_finalize_job`` (which transitions the job to FAILED).
            # Logged at WARNING so persistent failures surface in
            # observability without taking down the finalization path.
            logger.warning(
                f"_count_pending_tasks_for_instance_sync failed for "
                f"{instance_id[:8]}...: {e} — treating as 0 "
                f"(FAIL-OPEN: task pending-children check skipped, "
                f"may cause premature job finalization if persistent)"
            )
            return 0

    async def start(self) -> None:
        """Start the observer.

        Subscribes to the EventBus and starts the event processing loop
        as a background task.
        """
        # Subscribe to all events from the EventBus
        self._queue = self._event_bus.subscribe_all(self._subscriber_id)

        # Mark as running
        self._running = True

        # Start the event processing loop
        self._task = asyncio.create_task(self._event_loop())

        logger.info("JobFeedbackObserver started")

    async def stop(self) -> None:
        """Stop the observer.

        Drains any pending events from the queue before cancelling the background
        task and unsubscribing from the EventBus.
        """
        self._running = False

        # Drain remaining events from the queue before cancelling
        if self._queue is not None:
            drained = 0
            while drained < 1000:  # Safety limit to prevent infinite loop
                try:
                    event = self._queue.get_nowait()
                    drained += 1
                    try:
                        await self._process_event(event)
                    except Exception:
                        # Don't crash during drain - log if needed
                        pass
                except asyncio.QueueEmpty:
                    break
                except Exception:
                    # Handle edge cases (e.g., mock objects that don't raise QueueEmpty)
                    break

        # Cancel the background task if running
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # B2 fix (2026-06-27): cancel any in-flight deferred
        # finalize tasks (see ``self._deferred_finalize_tasks`` in
        # ``__init__``). These background tasks sleep for ``delay``
        # seconds (default 5s) before re-checking the bus. Without
        # explicit cancellation at shutdown they would either (a)
        # fire a stale finalize against a torn-down observer, or
        # (b) leak task references that the event loop cannot
        # clean up. ``_deferred_finalize_check`` propagates
        # ``CancelledError`` so cancellation is clean.
        for task in list(self._deferred_finalize_tasks):
            if not task.done():
                task.cancel()
        if self._deferred_finalize_tasks:
            # ``return_exceptions=True`` lets a stale ``CancelledError``
            # not mask other unexpected errors raised by the tasks'
            # cleanup paths (the deferred check swallows everything
            # except ``CancelledError``).
            await asyncio.gather(
                *self._deferred_finalize_tasks, return_exceptions=True
            )
            self._deferred_finalize_tasks.clear()

        # Unsubscribe from EventBus
        self._event_bus.unsubscribe_all(self._subscriber_id)

        logger.info("JobFeedbackObserver stopped")

    async def _event_loop(self) -> None:
        """Main event processing loop with robust error handling.

        Uses asyncio.wait_for for timeout to allow periodic health checks.
        Each event is wrapped in try/except to prevent a single bad event
        from crashing the observer.
        """
        self._running = True
        events_processed = 0
        last_event_time: float | None = None

        while self._running:
            try:
                # Use asyncio.wait_for for timeout to allow health checks
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=self._health_check_interval
                    )
                except asyncio.TimeoutError:
                    # Health check: log if no events received in a while
                    if events_processed == 0:
                        logger.info("JobFeedbackObserver: waiting for events...")
                    elif last_event_time and (time.time() - last_event_time) > self._health_check_interval * 2:
                        logger.warning(
                            f"JobFeedbackObserver: no events in {self._health_check_interval * 2}s"
                        )
                    continue

                # Process the event with exception handling
                try:
                    await self._process_event(event)
                    events_processed += 1
                    last_event_time = time.time()
                except Exception as e:
                    # CRITICAL: Never let a single event crash the observer
                    logger.error(
                        f"JobFeedbackObserver: error processing event: {e}", exc_info=True
                    )
                    continue

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(
                    f"JobFeedbackObserver: unexpected error in event loop: {e}",
                    exc_info=True,
                )
                # Don't break — keep the loop running
                continue

        logger.info(f"JobFeedbackObserver stopped after processing {events_processed} events")

    async def _get_processing_job_for_instance(
        self, instance_id: str, job_id: str | None = None
    ) -> _ProcessingJobContext | None:
        """Return the finalize context for ``instance_id``, or ``None``.

        Phase 2.5 (2026-06-27, D13 consumption-site rewrite). Replaced
        the pre-D13 ``JobItem | None`` return type with
        :class:`_ProcessingJobContext` — a lightweight NamedTuple
        carrying ``instance_id`` and ``job_id`` (``None`` when no
        JobItem exists for the instance).

        F13 (defer-seam bugfix Phase 3, 2026-07-01): the helper now
        accepts an optional ``job_id`` parameter. When provided, the
        defense-in-depth ``get_active_by_instance`` re-query resolves
        by exact ``JobItem.job_id`` instead of the freshest-by-
        ``created_at`` ordering. This prevents finalizing the WRONG
        sibling when two ACTIVE JobItems exist for the same instance
        (e.g. from a `job_continue`/`watch_job` race during the
        deferred-finalize sleep window — see F15 — or from manual DB
        operations / synthetic test mocks).

        Behavior:

          1. Look up the most-recent ``JobItem`` for the instance via
             the job queue service wrapper. ``get_by_instance`` orders
             by ``created_at DESC`` so the freshest row comes first.
          2. If a PROCESSING row is found → return ``_ProcessingJobContext(
             instance_id, job.job_id)`` (TASK-type job or legacy
             pre-D13 MESSAGE JobItem that still exists). Callers will
             run the full Step 1 (JobItem UPDATE) → Step 2 (instance
             status) → Step 3 (lock release) cascade.
          3. Otherwise (no PROCESSING row) → return
             ``_ProcessingJobContext(instance_id, job_id=None)`` — the
             post-D13 MESSAGE path. ``_finalize_job_db_sync`` will
             skip Step 1 (no JobItem to UPDATE) but still run Steps 2+3
             (instance status + lock release are critical).
          4. If no JobItem row exists at all (only Task rows; pure
             post-D13 path) → same as case 3, return the context with
             ``job_id=None``. The observer's terminal chain
             (Steps 2+3) is the authoritative transition path for the
             instance — the absence of a JobItem does not mean the
             instance should stay RUNNING forever.

        Stale-job defense (preserved from pre-D13): if the freshest
        row is in a non-PROCESSING terminal status (CANCELLED /
        COMPLETED / FAILED / DEAD_LETTER), re-query
        ``get_active_by_instance`` to find any still-active row. If
        no active row exists, return the no-JobItem context (case 3
        above) — the instance should still finalize even without a
        JobItem, because the Task row drives the instance's
        lifecycle post-D13.

        F13 (2026-07-01): when ``job_id`` is supplied (typically by the
        caller when the event payload carries a ``job_id`` field, or by
        the deferred-finalize TOCTOU guard that captured the job_id at
        scheduling time), the ``get_active_by_instance`` re-query
        resolves by exact ID. When ``job_id`` is ``None``, the legacy
        freshest-by-``created_at`` ordering is preserved.

        Phase 2 audit (2026-06-25, pause/resume redesign) carries
        forward: PAUSED jobs (introduced in Phase 1) are excluded by
        construction — the ``status == 'processing'`` checks in this
        method require PROCESSING status. A paused instance has no
        PROCESSING job visible to this helper, so ``_process_event``
        falls into case 3 (no JobItem context) and the lifecycle event
        short-circuits via the no-active-job branch downstream.

        Args:
            instance_id: The instance ID to look up.
            job_id: Optional exact ``JobItem.job_id`` to resolve.
                When provided, the ``get_active_by_instance`` re-query
                filters on this exact ID (F13). When ``None``, falls
                back to the freshest-by-``created_at`` ordering.

        Returns:
            A :class:`_ProcessingJobContext` carrying ``instance_id``
            and ``job_id`` (``None`` when no JobItem exists for the
            instance). Returns ``None`` only when the lookup itself
            raises — callers treat this as "no finalize context
            available".
        """
        # First lookup via the existing service wrapper. Equivalent to
        # ``await asyncio.to_thread(self._job_repo.get_by_instance, instance_id)``
        # — preserved as the service call so the existing test mock surface
        # (``mock_jqs.get_job_by_instance``) keeps working.
        job = await self._job_queue_service.get_job_by_instance(instance_id)
        # Finalize-on-completion fallback: match BOTH ``queued`` and
        # ``active``. A message-JobItem whose post-claim activation
        # UPDATE (``queued`` → ``active``) missed (best-effort UPDATE
        # failed or raced) stays in ``queued``. Without matching it
        # here, the observer returns ``job_id=None`` (case 3 below)
        # and the instance finalizes WITHOUT a JobItem transition —
        # the row leaks as ``queued`` forever. ``done`` and ``dead``
        # are still excluded (operator path is the JobProcessor poll
        # loop / stale-recovery). Paused jobs are unaffected because
        # pause keeps the job in ``admission_state='active'`` (its
        # lock is held) — not ``queued``.
        if job is not None and job.admission_state == AdmissionState.ACTIVE.value:
            # Happy path: ACTIVE JobItem exists. Pre-D13 returned the
            # JobItem directly; post-D13 we wrap it in the context so
            # downstream code (which uses ``job_id`` not the whole
            # JobItem) keeps working.
            return _ProcessingJobContext(
                instance_id=instance_id, job_id=job.job_id
            )
        if job is not None and job.admission_state == AdmissionState.QUEUED.value:
            # C1 fix (broadened 2026-07-06): only match ``queued``
            # JobItems whose Task row has progressed past ``pending``
            # (i.e. ``status != PENDING``). Previously the gate was
            # ``== RUNNING`` which leaked mirrors as ``queued``
            # forever when ``complete_task`` committed BEFORE the
            # observer read Task state (the Task was already
            # COMPLETED / FAILED / CANCELLED at read time). The
            # broadened gate is correct because:
            # - PENDING means the Task hasn't been claimed yet → the
            #   JobItem must stay queued (the turn hasn't started)
            # - Any non-PENDING status means the Task was claimed
            #   (or will never be claimed) → the JobItem can be
            #   finalized safely.
            task_row = await self._get_task_row_by_work_id(job.job_id)
            if task_row is not None and task_row.status != TaskStatus.PENDING.value:
                return _ProcessingJobContext(
                    instance_id=instance_id, job_id=job.job_id
                )
        # Defense-in-depth: future-proofing against manual DB operations or
        # synthetic test mocks where created_at ordering may not reflect the
        # active job. The real terminate→revive scenario is already covered
        # by the ``ORDER BY created_at DESC, job_id`` ordering in
        # JobRepository.get_by_instance — created_at is immutable post-insert,
        # so the revived PROCESSING row always sorts after the stale
        # CANCELLED row.
        #
        # F13 (2026-07-01): when ``job_id`` is supplied, the re-query
        # resolves by exact ID. This prevents the wrong-sibling bug
        # when two ACTIVE JobItems exist for the same instance
        # (e.g. a freshly-created JobItem from ``job_continue`` plus
        # a stale leftover). Without the exact-ID filter, the
        # freshest-by-created_at ordering could return the wrong row.
        if job is not None:
            active_job = await asyncio.to_thread(
                self._job_repo.get_active_by_instance, instance_id, job_id
            )
            # Finalize-on-completion fallback: mirror the first check
            # above. Match both ``queued`` and ``active`` so a stuck-
            # queued JobItem is still found via the defense-in-depth
            # re-query (e.g. when ``get_by_instance`` returned a
            # terminal sibling and we need the still-active / still-
            # queued row for finalization).
            if (
                active_job is not None
                and active_job.admission_state == AdmissionState.ACTIVE.value
            ):
                return _ProcessingJobContext(
                    instance_id=instance_id, job_id=active_job.job_id
                )
            if (
                active_job is not None
                and active_job.admission_state == AdmissionState.QUEUED.value
            ):
                # C1 fix (broadened 2026-07-06, defense-in-depth
                # re-query path): same broadened Task-status gate as
                # the first lookup. A queued JobItem only counts as
                # "the active processing job" when its Task row has
                # progressed past ``pending`` — see the comment on the
                # first-lookup ``QUEUED`` branch above for the full
                # data-loss-bug rationale and the rationale for
                # widening the gate from ``== RUNNING`` to
                # ``!= PENDING``.
                task_row = await self._get_task_row_by_work_id(active_job.job_id)
                if (
                    task_row is not None
                    and task_row.status != TaskStatus.PENDING.value
                ):
                    return _ProcessingJobContext(
                        instance_id=instance_id, job_id=active_job.job_id
                    )

        # Post-D13 MESSAGE path (Task 2.5.3): no JobItem exists for the
        # instance. The terminal chain must STILL run — Steps 2+3
        # (instance status + lock release) are critical and depend on
        # this finalize call reaching them. ``job_id=None`` tells
        # ``_finalize_job_db_sync`` to skip Step 1 (no JobItem to
        # UPDATE) and run Steps 2+3 unconditionally.
        return _ProcessingJobContext(instance_id=instance_id, job_id=None)

    async def _get_task_row_by_work_id(self, work_id: str) -> Any | None:
        """Look up a ``task`` row by its stable cross-system ``work_id``.

        C1 helper — used by :meth:`_get_processing_job_for_instance`
        to verify that a ``queued`` JobItem is paired with a Task
        row that is actually RUNNING before treating the JobItem as
        "the active processing job" for finalize. Without this gate,
        a freshly-created ``queued`` JobItem whose message Task is
        still ``pending`` (worker hasn't claimed it yet) can be found
        and finalized by ANY unrelated lifecycle event arriving for
        the same instance — the observer flips JobItem
        ``queued→done``, marks the instance terminal, and the
        message Task is silently dropped (data-loss bug).

        Access pattern: ``self._instance_manager._task_repo``
        (mirrors the existing access at line 1063
        ``_resolve_watchable_work_id`` and line 1546 in the finalize
        post-commit watcher notify block — same dependency, same
        lifecycle).

        Returns:
            The ``Task`` row for ``work_id``, or ``None`` when the
            manager / repository is unavailable, the repository has
            no ``get_by_work_id`` method (older test fixtures), or
            the lookup itself raises. ``None`` is treated by callers
            as "no Task-row confirmation available" — they fall
            through to the no-JobItem context and skip finalize,
            which is the conservative behavior (avoiding the data-
            loss bug is the priority; the C2 defense-in-depth
            activation fence will close the race on the next worker
            claim cycle).
        """
        task_repo = getattr(self._instance_manager, "_task_repo", None)
        if task_repo is None or not hasattr(task_repo, "get_by_work_id"):
            return None
        try:
            return await asyncio.to_thread(task_repo.get_by_work_id, work_id)
        except Exception as e:  # noqa: BLE001 — never raise from a C1 safety gate
            logger.warning(
                f"_get_task_row_by_work_id failed for work_id="
                f"{work_id[:8]}...: {type(e).__name__}: {e} — "
                "treating as 'no Task-row confirmation' (conservative: "
                "skip finalize to avoid the C1 data-loss bug)"
            )
            return None

    async def _process_event(self, event: dict) -> None:
        """Process a single instance_lifecycle event.

        Phase 1 (2026-06-24, report-lane decoupling): this method is
        the SOLE finalize path — it drives BOTH the ``in_progress``
        notification AND the terminal transition. The historical
        DependencyBus ``_retrigger_parent_finalize`` callback was
        removed (the source of the orphan-Task bug). Finalization
        now flows naturally: the report ``Task`` (PROCESS_REPORT)
        claims the work, drives a parent graph turn, and emits the
        lifecycle event that this method consumes to finalize.

        Two cases still route to the terminal transition here:

          1. **No pending watchers in the bus** (``bus_pending == 0``) —
             the instance either never spawned children, or all children
             already resolved. The idempotency guard in ``_finalize_job``
             (``job.admission_state != ACTIVE``) prevents double-completion.

          2. **Bus singleton missing** (``get_dependency_bus()`` returns
             ``None``) — invalid state, the in-process check falls through
             and the in-session gate raises a hard error below.

        Race #1 is eliminated because when ``bus_pending > 0``, we do NOT do
        a terminal transition here — we emit ``in_progress`` and wait for
        the report Task to fire its own lifecycle event (which will see
        the now-zero pending count and finalize).

        Args:
            event: Event dict with ``event_type`` and ``data`` fields.
        """
        # Filter: only process instance_lifecycle events
        # The event["event_type"] field is the correct filter field, NOT "kind"
        if event.get("event_type") != "instance_lifecycle":
            return

        data = event.get("data")
        if data is None:
            return

        # Extract instance info from event data
        instance_id = data.get("instance_id")
        status = data.get("status")
        error = data.get("error")
        # F13 (defer-seam bugfix Phase 3, 2026-07-01): when the event
        # payload carries a ``job_id``, pass it through to the lookup
        # helper so the ``get_active_by_instance`` re-query resolves by
        # exact ID instead of freshest-by-``created_at``. Prevents
        # finalizing the WRONG sibling when two ACTIVE JobItems exist
        # for the same instance. The field is optional — legacy event
        # payloads that omit ``job_id`` continue to use the freshest
        # ordering (backward-compatible).
        event_job_id: str | None = data.get("job_id") if isinstance(data, dict) else None

        if not instance_id or not status:
            return

        # Skip "terminated" — already handled by terminate_instance()
        if status == InstanceStatus.TERMINATED.value:
            logger.debug(
                f"Skipping terminated event for instance {instance_id[:8]}... "
                "(handled by terminate_instance)"
            )
            return

        # Look up the active PROCESSING job (with stale-job defense; the
        # helper encapsulates the get_by_instance + optional re-query for
        # terminate→revive scenarios, matching the protection that
        # the bus callback already had).
        #
        # Phase 2.5 (Task 2.5.6): the helper now returns a
        # :class:`_ProcessingJobContext` (instance_id + job_id) rather
        # than a ``JobItem | None``. The context carries ``job_id=None``
        # when no ``JobItem`` exists for the instance (post-D13 MESSAGE
        # path) — we still proceed with finalize in that case (the
        # instance status transition + lock release are critical).
        # The helper ONLY returns ``None`` when the lookup itself
        # raises; callers treat ``None`` as "skip silently".
        #
        # F13 (2026-07-01): thread ``event_job_id`` (when present)
        # through to the helper so the ``get_active_by_instance``
        # re-query resolves by exact ID.
        ctx = await self._get_processing_job_for_instance(
            instance_id, event_job_id
        )
        if ctx is None:
            return  # Lookup raised; skip silently.

        # Phase 2: decide between in_progress and terminal based on bus state.
        if status in (InstanceStatus.COMPLETED.value, InstanceStatus.ERROR.value):
            bus = get_dependency_bus()
            if bus is not None:
                # Bus is active and authoritative (the bus is the
                # SOLE completion authority; CM was removed).
                # ASYNC context — use the awaitable variant.
                bus_pending = await bus.count_pending_for_target(instance_id)
                if bus_pending > 0:
                    # Children still resolving → emit in_progress and
                    # wait for the report Task (PROCESS_REPORT) to fire
                    # its own lifecycle event (which will see the
                    # now-zero pending count and finalize). This is the
                    # Race #1 fix: no LLM fetch, no TOCTOU — we simply
                    # notify watchers and let the report Task drive
                    # the next finalize attempt.
                    #
                    # Phase 2.5: ``_emit_in_progress`` requires a
                    # ``job`` argument for the ``job_id`` it passes to
                    # ``notify_watchers``. When ``ctx.job_id is None``
                    # (post-D13 MESSAGE path) we skip the
                    # in_progress emission — there is no JobItem to
                    # notify watchers of. The lifecycle event will
                    # fire again when children resolve and bus_pending
                    # drops to 0; the terminal transition will then
                    # proceed without in_progress. This is benign —
                    # the bus still tracks the per-parent pending count
                    # and will not finalize until all children resolve.
                    if ctx.job_id is None:
                        logger.debug(
                            f"Skipping in_progress emit for "
                            f"{instance_id[:8]}... — no JobItem to "
                            f"notify (post-D13 MESSAGE path); "
                            f"bus_pending={bus_pending} — scheduling deferred finalize check"
                        )
                        # B3 fix (2026-06-27): the silent return that
                        # previously lived here is the same
                        # phantom-completion race that
                        # ``_process_resume_finalize`` just patched in
                        # Phase 2.5. The natural
                        # child-completion → PROCESS_REPORT → lifecycle
                        # event path SHOULD drive the finalize, but if
                        # that chain breaks (e.g., the report Task
                        # fires its lifecycle event before the
                        # child-completion signal reaches the bus
                        # watcher) the parent is left permanently
                        # stuck. Schedule a deferred finalize check as
                        # a safety net. The task reference is stored
                        # in ``self._deferred_finalize_tasks`` (B1
                        # fix) so the GC does not collect it during
                        # the 5s sleep; ``add_done_callback`` auto-
                        # removes the entry when the task completes;
                        # ``stop()`` (B2 fix) cancels any in-flight
                        # deferred tasks at observer shutdown.
                        # F15 (defer-seam bugfix Phase 3,
                        # 2026-07-01): capture the JobItem id at
                        # scheduling time. After the 5s sleep, the
                        # deferred check verifies the SAME job_id is
                        # still the active job; if a ``job_continue``
                        # or ``watch_job`` created a new JobItem on
                        # this instance during the sleep window, the
                        # deferred check skips finalization so the
                        # fresh job is not finalized prematurely.
                        # When ``ctx.job_id is None`` (post-D13
                        # MESSAGE path — no JobItem exists), we pass
                        # ``None`` and the guard short-circuits to
                        # the legacy freshest-by-``created_at``
                        # behavior.
                        task = asyncio.create_task(
                            self._deferred_finalize_check(
                                instance_id,
                                delay=5.0,
                                expected_job_id=ctx.job_id,
                            )
                        )
                        self._deferred_finalize_tasks.add(task)
                        task.add_done_callback(
                            self._deferred_finalize_tasks.discard
                        )
                        return
                    await self._emit_in_progress(ctx, instance_id)
                    return
                # bus_pending == 0: no pending watchers in bus.
                # Fall through to the shared terminal transition. This handles:
                #   a) Untracked parents (no children) — safe, no race possible.
                #   b) Tracked parents whose callback already fired — idempotency
                #      guard in _finalize_job catches the no-op.
                #   c) Race window where callback is about to fire — first writer
                #      wins via atomic_transition; the callback's idempotency
                #      guard catches the second.
            else:
                # The bus is None — invalid state. Fall through to the
                # shared terminal transition, which will raise on the
                # in-session gate check below (hard error).
                pass

        # Shared terminal transition path. Reached when:
        #   - bus is None (hard error path), or
        #   - bus is active AND bus_pending == 0 (no children / already resolved).
        #
        # Phase 1 (2026-06-24, report-lane decoupling): the
        # "any error → error" rule now lives here at the single
        # remaining finalize decision point. The previous
        # direct-finalize path (the deleted
        # ``ChildReportsService._retrigger_parent_finalize``) was
        # removed (Step 1.4) because it was the source of the
        # orphan-Task bug — it terminated the parent's job while
        # its report Task was still PENDING. The rule is now
        # centralized in :func:`_resolve_finalize_status` so both
        # this handler and the bus crash-recovery path in
        # ``daemon/api.py`` apply the same override from a single
        # source. We consult the bus's per-parent error flag
        # (``had_parent_error``) and the new
        # ``parent_error_message`` so a parent whose last child
        # errored still finalizes as ``ERROR`` even though the
        # parent's own report turn completed cleanly. Sticky
        # ``_parent_errored`` is cleared after finalize so a
        # revived instance does not inherit the flag.
        # Phase 3 (2026-06-25, pause/resume redesign): the
        # ``_process_resume_finalize`` method also calls
        # ``_finalize_job`` from the resume path. Both paths converge
        # on this single line — the ``_finalize_job_db_sync`` atomic
        # transition ``WHERE status = 'processing'`` ensures only the
        # first writer wins, so a racing lifecycle event + resume
        # finalize cannot double-transition. The second caller's
        # ``atomic_transition`` raises ``InvalidTransitionError`` and
        # the helper short-circuits via the existing
        # ``except InvalidTransitionError`` branch below.
        status_to_finalize, error_for_finalize = _resolve_finalize_status(
            bus, instance_id, status, error
        )
        # Phase 2.5 (Task 2.5.6): pass the _ProcessingJobContext to
        # ``_finalize_job``. ``ctx.job_id`` may be ``None`` (post-D13
        # MESSAGE path) — ``_finalize_job_db_sync`` handles that by
        # skipping Step 1 (no JobItem UPDATE) and running Steps 2+3
        # (instance status + lock release) unconditionally. The
        # terminal transition fires regardless.
        await self._finalize_job(
            ctx, instance_id, status_to_finalize, error=error_for_finalize
        )
        # Phase 1: clear the sticky error flag AFTER finalize so a
        # future revive / re-spawn of the same instance id does not
        # inherit the error state from its previous incarnation.
        # The clear is idempotent and safe — the flag's only
        # purpose was the override above, which has been applied.
        if bus is not None and bus.had_parent_error(instance_id):
            bus.clear_parent_error(instance_id)

    async def _emit_in_progress(
        self, ctx: _ProcessingJobContext, instance_id: str
    ) -> None:
        """Emit an ``in_progress`` watcher notification.

        Best-effort: failures are logged at WARNING and swallowed. The terminal
        transition will still fire via the bus completion callback (or the
        shared terminal path) regardless of whether this notification succeeds.

        Phase 2.5 (Task 2.5.3 + Task 2.5.6): the parameter is a
        :class:`_ProcessingJobContext` rather than a ``JobItem``. The
        ``job_id`` field is passed to ``notify_watchers``. Both
        callers (the lifecycle handler ``_process_event`` and the
        resume finalize ``_process_resume_finalize``) gate on
        ``ctx.job_id is not None`` before calling this method, so
        when invoked, ``ctx.job_id`` is guaranteed to be a real
        JobItem id (TASK-type jobs and pre-D13 MESSAGE JobItems
        that still exist). Post-D13 MESSAGE-driven instances
        (``ctx.job_id is None``) skip emission entirely — there is
        no JobItem to notify watchers of.

        Args:
            ctx: The finalize context. ``ctx.job_id`` is guaranteed
                non-None by callers (both gating sites return early
                when ``ctx.job_id is None``).
            instance_id: The parent instance ID (for LLM checkpoint fetch).
        """
        try:
            progress_text = (
                await self._instance_manager._get_last_assistant_message_raw(
                    instance_id
                )
            )
            await self._job_queue_service.notify_watchers(
                ctx.job_id,
                status="in_progress",
                progress=progress_text,
            )
        except Exception as e:
            logger.warning(
                f"Observer: failed to emit in_progress notification for "
                f"instance {instance_id[:8]}...: {e}"
            )

    async def _resolve_watchable_work_id(self, instance_id: str) -> str | None:
        """Resolve the instance's active watchable ``work_id``.

        Post-D13, an instance's work may be a ``JobItem`` (``job_create``,
        still carrying a row) OR a bare ``Task`` (``job_continue`` /
        message-driven — no ``JobItem``). A watcher registered via
        ``watch_job`` is keyed on a ``work_id`` that must match whichever
        of these is the instance's current work, or the ``in_progress`` /
        ``completed`` notifications never reach the orchestrator.

        Returns the ``JobItem.job_id`` when one is active, otherwise the
        freshest ``Task.work_id`` (the one whose turn most recently ran),
        or ``None`` if neither resolves.
        """
        ctx = await self._get_processing_job_for_instance(instance_id)
        work_id = ctx.job_id if ctx is not None else None
        if not work_id:
            task_repo = getattr(self._instance_manager, "_task_repo", None)
            if task_repo is not None and hasattr(task_repo, "get_by_instance"):
                tasks = await asyncio.to_thread(
                    task_repo.get_by_instance, instance_id
                )
                if tasks:
                    work_id = getattr(tasks[0], "work_id", None)
        return work_id or None

    async def emit_in_progress_if_job(self, instance_id: str) -> None:
        """Emit an ``in_progress`` notification for an instance's active job.

        Called by the message-processing pipeline when a parent instance
        finishes a graph turn but still has pending children (transition
        to ``WAITING_CHILDREN``). Resolves the instance's active
        ``work_id`` (JobItem OR Task) and fires the ``in_progress``
        watcher notification so an orchestrator that ``watch_job``-ed
        this work sees the ``⟳`` event while children resolve.

        Best-effort and idempotent: no-op when there is no watchable
        work, when the instance has no watchers, or when resolution
        fails. The terminal notification still fires via the normal
        lifecycle path regardless.

        Args:
            instance_id: The parent instance that just transitioned to
                ``WAITING_CHILDREN``.
        """
        try:
            work_id = await self._resolve_watchable_work_id(instance_id)
            if not work_id:
                return
            ctx = _ProcessingJobContext(instance_id=instance_id, job_id=work_id)
            await self._emit_in_progress(ctx, instance_id)
        except Exception as e:
            logger.warning(
                f"Observer: emit_in_progress_if_job failed for "
                f"instance {instance_id[:8]}... (non-fatal): {e}"
            )

    async def _finalize_job(
        self,
        ctx: _ProcessingJobContext,
        instance_id: str,
        terminal_status: str,
        error: str | None = None,
    ) -> None:
        """Shared terminal transition path.

        Phase 1 (2026-06-24, report-lane decoupling): the SOLE
        caller is :meth:`_process_event` (the lifecycle handler).
        The historical bus-callback
        (``_retrigger_parent_finalize``) was removed because it
        short-circuited the natural finalize path (source of the
        orphan-Task bug). The lifecycle handler is reached from
        EITHER the report Task's emitted ``instance_lifecycle``
        event (PROCESS_REPORT drove a parent graph turn) OR the
        original message Task's emitted event (no children).

        Phase 2.5 (2026-06-27, D13 consumption-site rewrite): the
        ``job`` parameter is now a :class:`_ProcessingJobContext`
        (instance_id + job_id) rather than a ``JobItem``. When
        ``ctx.job_id is None`` (post-D13 MESSAGE path — no
        ``JobItem`` exists for the message-driven instance),
        ``_finalize_job_db_sync`` skips Step 1 (JobItem UPDATE)
        and runs Steps 2+3 (instance status + lock release)
        unconditionally. The downstream side effects
        (``notify_watchers``, ``_trigger_next_job``) are also
        skipped — there is no JobItem to notify watchers of, and
        any follow-up work is claimed via the WorkerPool path,
        not via the JobQueue handoff.

        The method is a no-op (returns silently) for unknown terminal_status
        values. Race conditions (job already transitioned by another actor) are
        caught by ``InvalidTransitionError`` and logged at DEBUG.

        H15 fix: consolidates the 5-step terminal cascade into a single
        WriteGuardSession transaction. The sequence is:

          1. Pre-fetch ``result_summary`` / ``error_message`` on the event loop
             (needed by both the job row and post-commit side effects).
          2. Call ``_finalize_job_db_sync`` via ``asyncio.to_thread`` — this
             is the single WriteGuardSession that atomically performs: job
             atomic transition (PROCESSING → COMPLETED/FAILED), instance status
             update, and lock release. C1 TOCTOU re-check happens INSIDE the
             sync helper, immediately before the in-session UPDATE.
          3. Fire post-commit outbox side effects on the event loop after the
             thread returns: ``notify_watchers`` (terminal notification),
             SSE/CompletionRegistry/lifecycle event (via the extracted dispatcher),
             and ``_trigger_next_job`` (zero-delay handoff).

        This eliminates the partial-failure gap where ``atomic_transition``
        succeeded but ``release_by_instance`` failed — the queue slot would be
        leaked permanently.

        W3 fix (fail-safe) is preserved: if the sync helper or any pre-fetch
        raises, the method attempts a fail-safe ``atomic_transition`` to FAILED
        via ``asyncio.to_thread`` (C1 TOCTOU invariant does not apply here —
        the CM has already cleaned up its pending state for this parent).
        When ``ctx.job_id is None``, the W3 fail-safe is skipped — there is
        no JobItem to transition. The instance status update from Step 2
        has already committed inside ``_finalize_job_db_sync``, so the
        observable failure mode is just the missing W3 transition (no
        PROCESSING row to flip).

        Args:
            ctx: The finalize context. ``ctx.job_id`` may be ``None``
                for post-D13 MESSAGE-driven instances (no JobItem
                exists).
            instance_id: The parent instance ID.
            terminal_status: ``"completed"`` or ``"error"``.
            error: Error message for FAILED transitions (ignored for COMPLETED).
        """
        # ─── Pre-fetch data needed for the DB write and post-commit side effects ───
        result_summary: str | None = None
        error_message: str | None = None
        if terminal_status == InstanceStatus.COMPLETED.value:
            try:
                result_summary = (
                    await self._instance_manager._get_last_assistant_message_raw(
                        instance_id
                    )
                )
                if not result_summary:
                    result_summary = "Job completed (no agent response captured)"
            except Exception as e:
                # LLM fetch failed — use the fallback; the W3 fail-safe below
                # will transition the job to FAILED if even the DB write fails.
                result_summary = "Job completed (no agent response captured)"
                error = f"LLM fetch failed during finalization: {e}"
                terminal_status = InstanceStatus.ERROR.value
                error_message = error
        elif terminal_status == InstanceStatus.ERROR.value:
            error_message = error if error else "Unknown error"
        else:
            logger.warning(
                f"Unknown terminal status '{terminal_status}' for "
                f"instance {instance_id[:8]}..."
            )
            return

        # ─── Call the unified sync helper — single WriteGuardSession transaction ───
        # C1 fix (cross-thread race hardening, 2026-06-20): wrap the
        # ``asyncio.to_thread`` call in the per-parent ``asyncio.Lock``
        # when the bus is active (Phase 1, 2026-06-23: the lock moved
        # from CM to the bus). The lock is held on the EVENT LOOP for
        # the entire duration of the worker-thread sync helper,
        # blocking ``bus.watch()`` (which also acquires
        # ``bus._get_parent_lock(parent_id)``) from running on the loop
        # and registering a new pending child between the pre-check at
        # line ~509 and the commit inside ``_finalize_job_db_sync``.
        #
        # Why this works (no deadlock risk):
        #   * ``asyncio.Lock`` is event-loop-bound — the worker thread that
        #     ``asyncio.to_thread`` runs in NEVER acquires it; it only runs
        #     the sync SQLAlchemy code while the GIL is released during I/O.
        #   * The lock serializes coroutines on the loop, not threads.
        #   * When the bus is None (legacy path / not initialized), no
        #     lock is needed — the legacy ``SELECT ... FOR UPDATE``
        #     row-lock gate in ``_finalize_job_db_sync`` provides the
        #     same protection.
        #
        # Orphan-race fix (2026-06-20, post-commit re-check; Phase 1
        # 2026-06-23: counter now lives on the bus): capture the
        # per-parent generation counter BEFORE acquiring the lock.
        # ``DependencyBus.watch`` bumps the counter OUTSIDE its
        # per-parent lock acquisition, so the bump is visible to a
        # reader that holds the lock. After the lock is released (and
        # the job is committed to COMPLETED), we read the counter
        # again. If it changed, a watch was in-flight during
        # finalization — we must re-arm the job (COMPLETED →
        # PROCESSING) so the late child's eventual resolve can find a
        # PROCESSING job. Without this, the late child is orphaned:
        # its resolve callback finds a COMPLETED job and silently
        # skips.
        try:
            # Phase 1 (2026-06-23): generation counter + per-parent
            # locking moved from CM onto the bus. The bus now owns
            # the generation signal; CM's ``get_generation`` is a
            # thin passthrough (deprecated — Phase 5 will remove it).
            # Read the generation snapshot BEFORE acquiring the lock
            # so we can detect any bump that happens during the
            # critical section (lock acquire → to_thread → commit →
            # lock release).
            from daemon.services.dependency_bus import get_dependency_bus
            bus = get_dependency_bus()
            pre_gen = bus.get_generation(instance_id) if bus is not None else 0
            if bus is not None:
                # Bus is wired — hold the per-parent lock across the
                # to_thread call so concurrent ``watch()`` coroutines
                # must wait until finalization has committed. This is
                # the same critical-section shape the previous CM-based
                # code used (``async with cm._get_lock(instance_id)``).
                #
                # Phase 2.5 (Task 2.5.4): ``_finalize_job_db_sync``
                # now accepts ``job_id=None`` and skips Step 1
                # (JobItem UPDATE) when no ``JobItem`` exists for the
                # instance — the post-D13 MESSAGE path. The bus lock
                # is still acquired regardless of ``ctx.job_id``
                # because the lock's purpose is to serialize against
                # ``bus.watch()`` which is orthogonal to the
                # JobItem / Task distinction.
                async with await bus._get_parent_lock(instance_id):
                    db_result = await asyncio.to_thread(
                        self._finalize_job_db_sync,
                        ctx.job_id,
                        instance_id,
                        terminal_status,
                        result_summary,
                        error_message,
                    )
            else:
                # Legacy path / bus not wired — no lock needed; the FOR UPDATE
                # gate inside ``_finalize_job_db_sync`` is the only defence.
                db_result = await asyncio.to_thread(
                    self._finalize_job_db_sync,
                    ctx.job_id,
                    instance_id,
                    terminal_status,
                    result_summary,
                    error_message,
                )

            # ─── Post-commit re-arm (orphan-race fix, 2026-06-20) ───
            # After the lock is released, check if a register_message_send
            # bumped the generation counter during finalization. If yes,
            # the job was committed to COMPLETED but a new child is now
            # pending in CM — re-arm the job to PROCESSING so the late
            # child's resolve can find a PROCESSING job. Without this,
            # the late child is orphaned: its callback sees COMPLETED
            # and silently no-ops.
            #
            # We only re-arm when the job was ACTUALLY committed (not
            # skip / gate_deferred). Skipped paths mean the job was not
            # moved to COMPLETED by us, and gate-deferred paths already
            # schedule a bus re-arm for the wave 2 case.
            #
            # Phase 2.5 (Task 2.5.7): the re-arm is **conditional on
            # ``ctx.job_id is not None``**. In the post-D13 MESSAGE
            # path, there is no ``JobItem`` to re-arm — the
            # ``DependencyBus``'s own watcher/generation mechanism is
            # the authoritative recovery path for late children: a
            # late child's ``DependencyBus.watch`` that lands during
            # the critical section bumps ``bus.generation``, and the
            # bus's own watcher/generation state drives a new finalize
            # cycle on the next lifecycle event.
            # The JobItem-only re-arm below is skipped when no
            # ``JobItem`` exists. The instance-level side effects
            # (Steps 2+3 in ``_finalize_job_db_sync``) have already
            # committed inside the WriteGuardSession; the per-instance
            # lock release is already done — there is nothing further
            # to re-arm at the JobItem layer. The bus's own
            # watcher/generation state IS the re-arm signal.
            if (
                bus is not None
                and ctx.job_id is not None
                and not db_result.skip
                and not db_result.gate_deferred
            ):
                post_gen = bus.get_generation(instance_id)
                if post_gen > pre_gen:
                    # The orphan-race re-arm reads ``bus.get_generation()``
                    # DIRECTLY (this line). Any external writes to the
                    # bus's generation counter are visible to this read,
                    # so the only ``post_gen > pre_gen`` trigger is a
                    # ``DependencyBus.watch`` (line 360 of
                    # dependency_bus.py) that landed during the
                    # critical section.
                    #
                    # A ``DependencyBus.watch`` bumped the generation
                    # during finalization. The watch is either blocked
                    # on the per-parent lock (just released) or already
                    # enqueued on the event loop and about to acquire
                    # the lock. Either way, a new child is on its way
                    # into the bus. Re-arm the job so the late child's
                    # resolve can find a PROCESSING job.
                    logger.info(
                        f"Observer: orphan-race post-commit re-check "
                        f"detected generation change for instance="
                        f"{instance_id[:8]} (pre_gen={pre_gen}, "
                        f"post_gen={post_gen}). Re-arming job "
                        f"{ctx.job_id[:8]} from COMPLETED to PROCESSING "
                        f"via rearm_with_lock (F9 trigger-safe)."
                    )
                    rearmed = False
                    try:
                        # F9 fix: route the orphan-race re-arm through
                        # ``rearm_with_lock`` (single-transaction lock
                        # INSERT + admission_state UPDATE) instead of the
                        # bare ``atomic_transition`` from the pre-fix
                        # flow. The pre-fix code committed the
                        # ``done → active`` UPDATE alone, which violates
                        # the PostgreSQL
                        # ``trg_job_queue_items_active_lock_guard``
                        # trigger because the lock was already released
                        # by ``_finalize_job_db_sync`` Step 3. The
                        # exception was caught by the broad ``except
                        # Exception`` and the late child was silently
                        # orphaned. ``rearm_with_lock`` collapses both
                        # writes into one ``engine.begin()`` so the
                        # trigger sees the lock row AND the active
                        # admission_state at COMMIT.
                        await asyncio.to_thread(
                            self._job_repo.rearm_with_lock,
                            job_id=ctx.job_id,
                            instance_id=instance_id,
                        )
                        rearmed = True
                    except ValueError as rearm_exc:
                        # The job was transitioned by another actor
                        # (e.g. terminate_instance, a manual admin
                        # operation, or a concurrent terminal event)
                        # between our commit and this re-arm, OR the
                        # re-arm encountered a TOCTOU race (the SELECT
                        # saw ``done`` but the UPDATE matched 0 rows
                        # because the row flipped off ``done`` mid-
                        # transaction). The transaction — including the
                        # lock INSERT — is rolled back atomically so
                        # there is no lock leak. Log and continue — the
                        # post-commit outbox below is still valid for
                        # whatever state the job is actually in.
                        #
                        # NOTE: this catch is deliberately broad.
                        # ``rearm_with_lock`` raises ``ValueError`` for
                        # the expected concurrent-race case (the guard
                        # in ``repository.py`` matches 0 rows because
                        # ``admission_state`` flipped off ``done``
                        # between the SELECT and the UPDATE), but the
                        # same handler will also swallow any
                        # ``ValueError`` raised deeper in the call
                        # chain — for example, a ``ValueError`` from
                        # coercing ``job_queues.concurrency_limit`` if
                        # that row was corrupted mid-re-arm. That
                        # breadth is intentional: the follow-up
                        # ``except Exception`` below is the last-resort
                        # defensive handler, and this block is simply
                        # the expected-race fast-path that logs at INFO
                        # rather than WARNING. A re-arm failure always
                        # leaves the JobItem in its current state — the
                        # late child may be orphaned regardless of
                        # which exception type fires, and the periodic
                        # orphan-reconciliation sweep handles that
                        # case. Narrowing the catch here would risk
                        # masking a genuine ``ValueError`` that should
                        # bubble up; leaving it broad keeps the
                        # observer resilient to data-corruption
                        # surprises without losing the WARN-level
                        # signal for unrelated failures.
                        logger.info(
                            f"Observer: re-arm skipped — job "
                            f"{ctx.job_id[:8]} no longer COMPLETED "
                            f"(concurrent transition): {rearm_exc}"
                        )
                    except Exception as rearm_exc:
                        # Defensive: never let a re-arm failure crash the
                        # observer. The job is already in COMPLETED — the
                        # late child may be orphaned, which is strictly
                        # better than a partially-finalized state.
                        logger.warning(
                            f"Observer: re-arm failed for job "
                            f"{ctx.job_id[:8]} "
                            f"(COMPLETED → PROCESSING via rearm_with_lock): "
                            f"{rearm_exc}. The late child may be orphaned."
                        )

                    if rearmed:
                        # Re-arm succeeded — job is back to PROCESSING.
                        # Skip the post-commit outbox: no notify_watchers,
                        # no SSE, no CompletionRegistry, no _trigger_next_job.
                        # Those side effects are only valid for terminal jobs.
                        return

            # ─── Handle gate-deferred skip (bus PENDING > 0 or gate SELECT failed) ───
            # C2-N1 fix: bus re-arm fires ONLY on gate-deferred
            # paths, NOT on every ``skip=True``. The other skip paths
            # (unknown terminal_status, gate check aborted) must NOT
            # create spurious empty watcher entries. The
            # bus gate is the authoritative signal that children may still
            # be running — only there do we re-arm for wave 2.
            if db_result.gate_deferred:
                # C2-PartA fix: re-arm the gate so wave 2 children whose
                # bus terminal events arrive BEFORE their watch
                # registrations (e.g. wave 2 spawned via
                # ``job_continue`` / ``watch_job``) still find the parent
                # tracked and don't silently no-op. Without this, multi-wave
                # scenarios where the wave 2 register sequence is not via
                # ``send_message`` can wedge the job in PROCESSING forever.
                #
                # N4 compliance: ``_finalize_job`` runs as the
                # bus completion callback, and the N4 constraint
                # forbids re-entering bus state for the same
                # ``parent_id`` from inside the callback. We therefore
                # schedule via ``asyncio.create_task`` rather than
                # awaiting directly — the re-arm acquires the
                # per-parent lock once it actually runs, well after
                # ``_finalize_job`` has returned and the original
                # callback context has unwound. When called from
                # :meth:`_process_event` (the lifecycle-event fall-through,
                # no N4 concern) the same ``create_task`` is harmless —
                # the re-arm is just deferred by one event-loop iteration.
                #
                # C2-N1 fix: the bus re-arm is the orphan-race re-arm via
                # ``bus.generation`` (see the post-commit re-arm
                # block above) is the current mechanism: if a
                # ``DependencyBus.watch`` lands during the critical
                # section, the post-commit generation check
                # (``post_gen > pre_gen``) re-arms the job from
                # COMPLETED → PROCESSING so the late child's
                # resolve can find a PROCESSING job. No
                # ``create_task(rearm)`` is needed — the bus state
                # itself encodes the re-arm signal.
                logger.debug(
                    f"Observer: _finalize_job gate-deferred for job "
                    f"{ctx.job_id[:8] if ctx.job_id else 'no_job'}... "
                    f"instance {instance_id[:8]}... — "
                    f"bus re-arm via orphan-race generation check "
                    f"(Phase 5: bus is the SOLE completion authority)"
                )
                return

            # ─── Handle other skip paths (unknown terminal_status, CM re-check) ───
            if db_result.skip:
                return

            # ─── Post-commit outbox: fire side effects on the event loop ───
            # B4 fix: Pre-fetch the list of watching instances BEFORE
            # ``notify_watchers`` is called. ``JobQueueService.notify_watchers``
            # removes all ``JobWatcher`` rows for this job in terminal states
            # (see ``notify_watchers`` lines 271-277), so we capture the
            # list now and use it to drive CM resolution below. Without this
            # capture, the post-``notify_watchers`` ``get_watchers_for_job``
            # call would return an empty list and the CM ``pending_jobs``
            # set would NEVER be drained for watch-based parents — the
            # completion callback would never fire and the parent would
            # hang in ``PROCESSING`` forever.
            #
            # Phase 2.5 (Task 2.5.4): the watcher fetch is conditional
            # on ``ctx.job_id is not None`` — post-D13 MESSAGE-driven
            # instances have no ``JobItem``, so there is no
            # ``JobWatcher`` rows to fetch.
            terminal_watchers: list[Any] = []
            if ctx.job_id is not None:
                try:
                    # ``JobQueueService`` holds the watcher repo (set via
                    # ``set_watcher_repo`` in ``daemon/api.py``). When the
                    # service is mocked in tests, this attribute is typically
                    # not set — ``getattr`` returns ``None`` and we skip the
                    # fetch cleanly. Defensive: a missing or non-callable
                    # ``get_watchers_for_job`` is also treated as "no repo".
                    watcher_repo = getattr(
                        self._job_queue_service, "_watcher_repo", None
                    )
                    if watcher_repo is not None and hasattr(
                        watcher_repo, "get_watchers_for_job"
                    ):
                        terminal_watchers = await asyncio.to_thread(
                            watcher_repo.get_watchers_for_job, ctx.job_id
                        )
                except Exception as e:
                    # Defensive: never let a watcher-repo failure abort the
                    # post-commit outbox. Log at WARNING and continue with
                    # an empty list (the watcher notifications already fired;
                    # we just won't drive CM resolution for them).
                    logger.warning(
                        f"Observer: pre-fetch watchers failed for job "
                        f"{ctx.job_id[:8]}...: {e}"
                    )

            # notify_watchers (terminal notification) — fires AFTER commit so
            # watchers see a consistent state. A parent instance may carry
            # MULTIPLE work_ids (a JobItem from ``job_create`` PLUS Task
            # work_ids from ``job_continue`` / report-driven turns), and the
            # orchestrator ``watch_job``-ed exactly one of them. We notify
            # every candidate work_id for this instance —
            # ``notify_work_watchers`` is a no-op where no watcher exists,
            # so this is idempotent and still exactly-once (it claims
            # watchers only on the work_id that actually has one).
            # Without this, ``job_continue``-driven work (Task, no JobItem)
            # never received its ``completed ✓`` and the orchestrator hung.
            candidate_work_ids: set[str] = set()
            if ctx.job_id:
                candidate_work_ids.add(ctx.job_id)
            try:
                task_repo = getattr(self._instance_manager, "_task_repo", None)
                if task_repo is not None and hasattr(task_repo, "get_by_instance"):
                    inst_tasks = await asyncio.to_thread(
                        task_repo.get_by_instance, instance_id
                    )
                    for _t in inst_tasks:
                        _wid = getattr(_t, "work_id", None)
                        if _wid:
                            candidate_work_ids.add(_wid)
            except Exception:
                pass

            for _work_id in candidate_work_ids:
                if db_result.terminal_status == InstanceStatus.COMPLETED.value:
                    try:
                        await self._job_queue_service.notify_watchers(
                            _work_id, "completed",
                            result_summary=db_result.result_summary,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Observer: notify_watchers failed for job "
                            f"{_work_id[:8]}...: {e}"
                        )
                elif db_result.terminal_status == InstanceStatus.ERROR.value:
                    try:
                        await self._job_queue_service.notify_watchers(
                            _work_id, "failed", db_result.error_message
                        )
                    except Exception as e:
                        logger.warning(
                            f"Observer: notify_watchers failed for job "
                            f"{_work_id[:8]}...: {e}"
                        )

            # B4: Resolve watched jobs. The bus is task-keyed, not
            # job-keyed, so there is no bus equivalent for job-level
            # correlation tracking. The watcher notifications
            # (``_job_queue_service.notify_watchers`` above) are
            # sufficient — they transition the parent jobs and
            # remove the ``JobWatcher`` rows. The bus still tracks
            # message-level children via ``DependencyWatcher`` rows
            # on a different code path
            # (``child_reports._emit_terminal_via_bus``). Status
            # mapping: completed → "responded" (clean terminal);
            # error → "error" (conservative rule, mirrors
            # ``_determine_terminal_status`` for message responses).
            # Kept as a no-op pass through ``terminal_watchers`` for
            # log/observability symmetry with the pre-Phase-5 code;
            # the loop body no longer performs correlation tracking.
            for watcher in terminal_watchers:
                # ``watcher.instance_id`` is the parent (the instance
                # that called ``watch_job``). The bus does not track
                # job-level correlations — the watcher row was
                # already removed by ``notify_watchers`` above, and
                # the parent will see the terminal event through the
                # normal job-queue path. This is a no-op pass for
                # observability; if no watchers, no action.
                logger.debug(
                    f"Observer: watcher {watcher.instance_id[:8]}... "
                    f"resolved for job {ctx.job_id[:8]}... "
                    f"(terminal_status={db_result.terminal_status}, "
                    f"cm_status=removed-phase5)"
                )

            # ─── Instance-side post-commit (SSE / CompletionRegistry / lifecycle) ───
            # Only fire if the instance was NOT already terminal when we wrote it.
            # If ``instance_was_terminal=True``, the side effects were already fired
            # by whoever set the instance terminal first (CM-disabled inline cascade
            # or a prior callback). If the instance row was missing, there is no
            # consumer to notify.
            #
            # Phase 2.5 (Task 2.5.4): this fires regardless of
            # ``ctx.job_id`` — Steps 2+3 (instance status + lock release)
            # committed inside ``_finalize_job_db_sync`` already, so
            # the instance-side fan-out must run to keep
            # ``CompletionRegistry`` / SSE / lifecycle event in sync
            # with the DB. The ``job_id=None`` path is benign — the
            # dispatcher's inputs are ``instance_id``,
            # ``terminal_status``, ``result_summary``, etc., none of
            # which depend on the JobItem.
            if not db_result.instance_was_terminal:
                await self._dispatch_instance_post_commit_side_effects(
                    instance_id=instance_id,
                    terminal_status=db_result.terminal_status,
                    error=error,
                    parent_id=db_result.parent_id,
                    agent_id=db_result.agent_id,
                    last_content=result_summary,
                )

            # ─── Trigger next job (zero-delay handoff) ───
            # Phase 2.5 (Task 2.5.4): skipped when ``ctx.job_id is
            # None``. ``_trigger_next_job`` requires a ``JobItem`` to
            # look up ``project_id`` and find the next pending job in
            # the same project. Post-D13 MESSAGE-driven instances have
            # no JobItem; the next work (if any) is picked up by the
            # WorkerPool via its own claim loop, not by the JobQueue
            # handoff. Skipping here is correct, not lossy.
            if ctx.job_id is not None:
                await self._trigger_next_job_by_id(
                    ctx.job_id, instance_id
                )

            logger.info(
                f"Observer: finalized job {ctx.job_id[:8] if ctx.job_id else 'no_job'}... "
                f"status={db_result.terminal_status} for instance {instance_id[:8]}... "
                f"(released {db_result.locks_released} lock(s))"
            )

        except InvalidTransitionError as e:
            # Race condition: another actor (e.g., terminate_instance, a
            # previous bus callback) already transitioned the job. Expected —
            # skip silently. This is the primary idempotency mechanism.
            logger.debug(
                f"Race condition: job {ctx.job_id[:8] if ctx.job_id else 'no_job'}... already transitioned "
                f"(current: {e.from_state} -> {e.to_state}), skipping"
            )
            return
        except RuntimeError:
            # W2 fix (2026-06-20): A8 hard errors (bus singleton missing —
            # raised in ``_finalize_job_db_sync`` and at the two A8 call
            # sites in ``child_reports.py``) propagate as configuration
            # errors.
            #
            # Re-raise so the W3 ``except Exception`` below cannot silently
            # convert the misconfiguration into a per-job FAILED transition.
            # This preserves the A8 invariant at the direct (non-callback)
            # call boundary: any code that invokes ``_finalize_job`` directly
            # sees the hard error.
            #
            # Honest propagation note (W2 honest-documentation fix): the
            # RuntimeError typically does NOT reach a process-level crash in
            # production paths, because broader ``except Exception`` handlers
            # catch it one or two frames up:
            #
            #   1. Bus terminal-event path — when invoked after
            #      ``DependencyBus.emit_terminal`` fires a watcher
            #      transition, the bus's own ``except Exception`` handler
            #      catches the RuntimeError, logs it at EXCEPTION level,
            #      and rolls back the watcher state so a subsequent
            #      retry can recover the completion. (Phase 1: the
            #      deleted ``_retrigger_parent_finalize`` callback
            #      that used to drive direct finalize from the bus is
            #      gone; the bus no longer calls into the observer
            #      directly.)
            #
            #   2. Event-loop path — when invoked from
            #      :meth:`JobFeedbackObserver._process_event` via
            #      :meth:`_event_loop`, the loop's ``except Exception`` at
            #      line 313 (and the stop-drain ``except Exception`` at
            #      line 258) catches the RuntimeError, logs it at ERROR
            #      level, and continues processing the next event.
            #
            # Net effect: the "hard error" is per-job FAILED (via the W3
            # path) or per-event ERROR log + retry-via-restoration, NOT a
            # process crash. This is INTENTIONAL fail-safe behavior — the
            # daemon stays alive and affected jobs fail individually rather
            # than cascading a configuration bug into a full process outage.
            #
            # For true hard-error semantics (process abort) the CM must be
            # initialized before any traffic — the production invariant
            # enforced at startup (``cm.start()`` runs in
            # ``daemon/main.py`` before the EventBus is open for business).
            raise
        except Exception as e:
            logger.error(
                f"Failed to finalize job {ctx.job_id[:8] if ctx.job_id else 'no_job'}... "
                f"status={terminal_status}: {e}",
                exc_info=True,
            )
            # W3 fix (fail-safe): if finalization failed, the CM has already
            # deleted ``_pending[parent_id]`` — the callback will not fire again.
            # Without a fail-safe, the job would sit in PROCESSING forever.
            # Transition to FAILED so the queue can advance and watchers see a
            # terminal state. Wrapped in ``asyncio.to_thread`` to keep sync
            # DB off the event loop (C1 TOCTOU invariant does NOT apply here —
            # the primary finalization has already failed, so there is no
            # ``register_message_send`` race to defend against).
            #
            # Phase 2.5 (Task 2.5.4): the W3 fail-safe is conditional on
            # ``ctx.job_id is not None``. There is no ``JobItem`` to
            # transition when ``ctx.job_id is None`` — the post-D13
            # MESSAGE path. The instance status update from
            # ``_finalize_job_db_sync`` Step 2 has already committed
            # inside the WriteGuardSession (before any failure would
            # have surfaced here), so the observable failure mode for
            # the no-JobItem path is just the missing W3 transition —
            # no PROCESSING row exists to flip. Logged at DEBUG so the
            # absence is visible in observability without an ERROR
            # log line for an absent JobItem.
            if ctx.job_id is not None:
                try:
                    await asyncio.to_thread(
                        self._job_repo.atomic_transition,
                        job_id=ctx.job_id,
                        from_status="processing",
                        to_status="failed",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        error_message=f"Job finalization failed: {e}",
                    )
                    logger.info(
                        f"Observer: fail-safe transitioned job "
                        f"{ctx.job_id[:8]}... to FAILED after finalization error"
                    )
                except Exception:
                    pass  # atomic_transition itself failed — nothing more we can do
            else:
                logger.debug(
                    f"Observer: W3 fail-safe skipped — no JobItem "
                    f"for instance {instance_id[:8]}... (post-D13 MESSAGE path); "
                    f"instance status was already committed by "
                    f"_finalize_job_db_sync Step 2"
                )
            return

    async def _process_resume_finalize(
        self,
        instance_id: str,
        job_id: str,
        result_summary: str | None = None,
    ) -> None:
        """Deterministic finalize trigger called after every resume graph turn.

        Phase 3 (2026-06-25, pause/resume redesign) — C1 fix. Replaces
        the old direct ``complete_job()`` call in the resume path
        (manager.py:2858-2905 pre-Phase 3). Routes through the SAME
        transactional bus gate as :meth:`_process_event`, eliminating
        the TOCTOU race (the pre-Phase 3 code did a non-transactional
        bus check followed by a direct ``complete_job`` call). Fires
        even on no-op graph turns (fixing C1 — the previous "let
        ``_process_event`` handle it" design never fired finalize for
        no-op turns because the lifecycle event filter
        ``status IN (COMPLETED, ERROR)`` short-circuited, leaving the
        job stuck in PROCESSING forever).

        The authoritative gate is ``_finalize_job_db_sync``'s
        in-session ``COUNT`` (a re-check of
        ``bus.count_pending_for_target`` immediately before the
        atomic transition). The pre-check here is an optimization
        (skip the sync helper if children are obviously pending) —
        it is NOT authoritative; the in-session gate in
        ``_finalize_job_db_sync`` makes the final call.

        Double-finalize prevention: both this method and
        :meth:`_process_event` call :meth:`_finalize_job`, which
        delegates to ``_finalize_job_db_sync`` whose atomic
        transition uses ``WHERE status = 'processing'``. If a
        lifecycle event-driven finalize lands first, the second
        caller's transition rowcount drops to 0 and the helper
        returns ``skip=True`` — only the first writer wins.

        A9 hard-error carries forward: when ``get_dependency_bus()``
        returns ``None``, this method raises ``RuntimeError`` (same
        invariant as :meth:`_process_event` and the resume path's
        pre-Phase 3 bus check). The bus must be initialized for
        finalization to make a safe decision.

        Args:
            instance_id: The instance whose resume graph turn just
                completed.
            job_id: The job_id (for logging/fallback; the
                authoritative job is looked up via
                :meth:`_get_processing_job_for_instance`).
                Phase 2.5 (Task 2.5.2): after D13, ``job_id`` is
                the WorkerPool Task ID (a stringified int), NOT a
                ``JobItem.job_id``. The lookup helper may return
                ``ctx.job_id=None`` when no JobItem exists for the
                instance — the terminal transition still fires
                through ``_finalize_job_db_sync`` Steps 2+3 (instance
                status + lock release).
            result_summary: Optional result text from the graph turn.
                The current ``_finalize_job`` API does not accept
                ``result_summary`` from the caller (it does its own
                LLM fetch via ``_get_last_assistant_message_raw``),
                so this parameter is accepted for forward-compatibility
                and ignored today. The pause/resume Phase 4 plan may
                thread it through.
        """
        # A9 hard-error: bus must be initialized. The legacy SELECT
        # fallback (TOCTOU) is the exact bug Phase 3 is fixing — it
        # MUST NOT be reachable when the bus is None.
        bus = get_dependency_bus()
        if bus is None:
            raise RuntimeError(
                "DependencyBus is None during resume finalize — invalid state. "
                "The bus must be initialized (see ADR-011)."
            )

        # Look up the finalize context for this instance. Phase 2.5
        # (Task 2.5.5): the helper now returns a
        # :class:`_ProcessingJobContext` (instance_id + job_id) rather
        # than a ``JobItem | None``. The ``ctx`` is ``None`` ONLY when
        # the lookup itself raises — callers treat ``None`` as "skip
        # silently". When ``ctx.job_id is None`` (post-D13 MESSAGE
        # path), we still proceed with finalize — the instance status
        # transition + lock release are critical and depend on this
        # finalize call reaching them. The pre-D13 short-circuit
        # (``if job is None: return``) is gone because in the
        # post-D13 world, ``ctx`` is non-None whenever the instance
        # row exists (the helper returns a context with
        # ``job_id=None`` when no JobItem exists, instead of None).
        ctx = await self._get_processing_job_for_instance(instance_id)
        if ctx is None:
            logger.debug(
                f"_process_resume_finalize: lookup failed for instance "
                f"{instance_id[:8]}... — skipping silently"
            )
            return

        # Pre-check (NON-AUTHORITATIVE optimization): if children are
        # obviously pending, emit in_progress and defer. The
        # authoritative gate is _finalize_job_db_sync's in-session
        # COUNT — the pre-check is just an optimization that lets us
        # skip the thread-hop when the result is obvious. Even if a
        # race makes this count stale by the time the sync helper
        # runs, the in-session re-check closes the gap.
        #
        # Phase 2.5 (Task 2.5.5): ``_emit_in_progress`` requires a
        # ``job_id``; when ``ctx.job_id is None`` (post-D13 MESSAGE
        # path) we skip the in_progress emission — there is no
        # JobItem to notify watchers of. The lifecycle event will
        # fire again when children resolve and bus_pending drops to
        # 0; the terminal transition will then proceed without
        # in_progress.
        bus_pending = await bus.count_pending_for_target(instance_id)
        if bus_pending > 0:
            if ctx.job_id is None:
                logger.debug(
                    f"Skipping in_progress emit for "
                    f"{instance_id[:8]}... — no JobItem to notify "
                    f"(post-D13 MESSAGE path); bus_pending={bus_pending}; "
                    f"scheduling deferred finalize check as safety net"
                )
                # Schedule a deferred finalize as a safety net. The natural
                # child-completion → PROCESS_REPORT → lifecycle event path
                # SHOULD drive the finalize, but if that chain breaks (e.g.,
                # the phantom completion race observed in the pause/resume
                # E2E suite), this deferred check ensures the parent still
                # reaches a terminal state. The check is idempotent:
                # _finalize_job's internal guards (instance-status + bus
                # gate re-check) prevent double-finalization.
                #
                # B1 fix (2026-06-27): the task reference MUST be stored
                # in ``self._deferred_finalize_tasks``. Python's event loop
                # only keeps weak references to ``asyncio.Task`` — a
                # fire-and-forget task whose return value is discarded can
                # be garbage-collected mid-flight (during the 5s sleep
                # below). The strong reference in the set keeps the task
                # alive; ``add_done_callback`` auto-removes the entry
                # when the task finishes so the set does not grow
                # unbounded.
                #
                # F15 (defer-seam bugfix Phase 3,
                # 2026-07-01): capture ``ctx.job_id`` at scheduling time
                # so the deferred check can verify, after the 5s sleep,
                # that the SAME job_id is still the active JobItem.
                # Without this guard, a ``job_continue`` or ``watch_job``
                # that created a new JobItem on this instance during the
                # sleep window would be finalized prematurely by the
                # deferred check (TOCTOU bug). When ``ctx.job_id is None``
                # (post-D13 MESSAGE path — no JobItem exists), we pass
                # ``None`` and the guard short-circuits to the legacy
                # freshest-by-``created_at`` behavior.
                task = asyncio.create_task(
                    self._deferred_finalize_check(
                        instance_id,
                        delay=5.0,
                        expected_job_id=ctx.job_id,
                    )
                )
                self._deferred_finalize_tasks.add(task)
                task.add_done_callback(self._deferred_finalize_tasks.discard)
                return
            await self._emit_in_progress(ctx, instance_id)
            return

        # REUSE _finalize_job — do NOT reimplement finalize logic.
        # The method delegates to _finalize_job_db_sync which runs the
        # authoritative in-session bus gate. If children race in
        # between this pre-check and the sync helper, the in-session
        # gate transitions the job to terminal anyway — and the
        # bus's late-child resolve (which fires a watcher) re-arms
        # the job via the generation-counter check in
        # _finalize_job.
        #
        # We pass "completed" (string) matching the existing
        # _process_event path which also passes the string form via
        # _resolve_finalize_status → _finalize_job. The method maps
        # the string to InstanceStatus.COMPLETED.value internally.
        await self._finalize_job(ctx, instance_id, "completed", error=None)

    async def _deferred_finalize_check(
        self,
        instance_id: str,
        delay: float = 5.0,
        expected_job_id: str | None = None,
    ) -> None:
        """Deferred safety net for resume finalize when ``ctx.job_id is None``.

        Phase 2.5 (2026-06-27, D13): when a resume graph turn spawns a
        child (``bus_pending > 0``) and there is no JobItem (post-D13
        MESSAGE path), :meth:`_process_resume_finalize` cannot emit
        ``in_progress`` (no JobItem to notify) and historically
        returned silently — assuming a subsequent
        child-completion → PROCESS_REPORT → lifecycle event chain
        would finalize the parent.

        That assumption can break (the phantom completion race observed
        in the pause/resume E2E suite, where the report Task fires its
        lifecycle event before the child-completion signal reaches the
        bus watcher). When that happens the parent is left
        permanently stuck in ``WAITING_CHILDREN``.

        This method is a defense-in-depth backstop: after ``delay``
        seconds, re-check the bus. If children are no longer pending
        AND the instance is not already terminal, drive
        :meth:`_finalize_job` ourselves. The internal guards in
        :meth:`_finalize_job` (instance-status pre-check + bus gate
        re-check inside :meth:`_finalize_job_db_sync`) make this
        safe to call multiple times — a duplicate fire is a no-op.

        F15 (defer-seam bugfix Phase 3, 2026-07-01): the deferred
        check previously re-queried ``_get_processing_job_for_instance``
        after the sleep window without remembering which job_id was
        active at scheduling time. A ``job_continue`` or ``watch_job``
        that created a new JobItem on this instance during the sleep
        window would be finalized prematurely by this safety net — a
        classic TOCTOU bug.

        To close the gap, callers now pass ``expected_job_id`` (the
        ``ctx.job_id`` captured when the deferred task was scheduled).
        After the sleep, we re-query with the exact-ID lookup (F13
        helper) and verify the same job_id is still the active job.
        If a new job was created during the sleep window, we skip
        finalization and let the new job's natural lifecycle-event
        path drive its own finalize.

        When ``expected_job_id is None`` (legacy / post-D13 MESSAGE
        path — no JobItem existed at scheduling time), the TOCTOU
        guard short-circuits: we fall back to the legacy
        freshest-by-``created_at`` lookup, preserving backward
        compatibility for callers that cannot supply a job_id.

        The method is a background safety net: every code path is
        wrapped in try/except so an exception here can never
        propagate into the observer's main loop or wedge the daemon.
        ``CancelledError`` IS propagated so the task is cleaned up
        cleanly when the observer shuts down.

        Args:
            instance_id: The instance to re-check.
            delay: Seconds to wait before the re-check (default 5.0).
            expected_job_id: The job_id that was active when the
                deferred check was scheduled. When provided, the
                post-sleep re-query verifies the same job_id is
                still the active JobItem before driving finalize
                (F15 TOCTOU guard). When ``None``, falls back to
                the legacy freshest-by-``created_at`` lookup.
        """
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # Observer is shutting down — propagate so the task is cleaned up.
            raise
        except Exception as e:
            logger.warning(
                f"Observer: deferred finalize sleep raised for "
                f"{instance_id[:8]}...: {e}"
            )
            return

        try:
            bus = get_dependency_bus()
            if bus is None:
                logger.debug(
                    f"Observer: deferred finalize skipped for "
                    f"{instance_id[:8]}... — DependencyBus is None"
                )
                return

            bus_pending = await bus.count_pending_for_target(instance_id)
            if bus_pending > 0:
                # Children still resolving — the natural
                # lifecycle-event path will drive the finalize when
                # the last child resolves (or another deferred check
                # will fire if it does not). Nothing to do.
                logger.debug(
                    f"Observer: deferred finalize for "
                    f"{instance_id[:8]}... found bus_pending={bus_pending} "
                    f"— natural path or another deferred check will handle it"
                )
                return

            # F15 (defer-seam bugfix Phase 3, 2026-07-01): resolve the
            # processing context by the EXACT ``expected_job_id`` (when
            # provided). The helper now accepts an optional ``job_id``
            # parameter (F13) that filters ``get_active_by_instance``
            # by exact ID. This closes the TOCTOU window: if a
            # ``job_continue``/``watch_job`` created a new JobItem on
            # this instance during the sleep, the new JobItem has a
            # different ``job_id`` and the helper returns either
            # ``None`` (no exact match) or a context with the new
            # job_id. Either way, the mismatch below catches it and
            # skips finalization for the OLD job.
            #
            # When ``expected_job_id is None`` (legacy / post-D13
            # MESSAGE path — no JobItem at scheduling time), the
            # ``job_id`` argument is ``None`` and the helper falls
            # back to the legacy freshest-by-``created_at`` lookup.
            ctx = await self._get_processing_job_for_instance(
                instance_id, expected_job_id
            )
            if ctx is None:
                logger.debug(
                    f"Observer: deferred finalize for "
                    f"{instance_id[:8]}... — no processing context "
                    f"available, skipping"
                )
                return

            # F15 TOCTOU guard: verify the active job_id at
            # re-query time matches the one captured at scheduling
            # time. A mismatch means a new JobItem was created on
            # this instance during the sleep window — defer to the
            # new job's natural lifecycle-event path and skip our
            # finalize.
            #
            # Conservative: when ``expected_job_id is None`` (legacy
            # post-D13 MESSAGE path), the guard short-circuits — we
            # cannot verify against an unknown ID, so we proceed with
            # the legacy behavior.
            if (
                expected_job_id is not None
                and ctx.job_id is not None
                and ctx.job_id != expected_job_id
            ):
                logger.info(
                    f"Observer: deferred finalize for "
                    f"{instance_id[:8]}... — expected_job_id="
                    f"{expected_job_id[:8]}... no longer matches "
                    f"active job_id={ctx.job_id[:8]}... (new JobItem "
                    f"created during sleep window, F15 TOCTOU guard), "
                    f"skipping finalize"
                )
                return

            # Pre-check instance terminal status to avoid redundant
            # work. The sync helper inside :meth:`_finalize_job` also
            # has this guard, but checking here lets us emit a precise
            # DEBUG log and skip the async finalize chain entirely
            # when the lifecycle-event path already ran.
            instance_status = await asyncio.to_thread(
                self._read_instance_status_sync, instance_id
            )
            if instance_status is None:
                logger.debug(
                    f"Observer: deferred finalize for "
                    f"{instance_id[:8]}... — instance row missing, skipping"
                )
                return
            if instance_status in _TERMINAL_INSTANCE_STATUSES:
                logger.debug(
                    f"Observer: deferred finalize for "
                    f"{instance_id[:8]}... — instance already terminal "
                    f"(status='{instance_status}'), skipping"
                )
                return

            logger.info(
                f"Observer: deferred finalize firing for "
                f"{instance_id[:8]}... (bus_pending=0, "
                f"status='{instance_status}') — natural lifecycle event "
                f"path did not arrive in {delay}s; driving finalize "
                f"as safety net"
            )
            # B4 fix (2026-06-27): consult ``_resolve_finalize_status``
            # so the parent-error override is applied here just as it
            # is in :meth:`_process_event`. The "any child errored →
            # parent finalizes as ERROR" rule must be uniform across
            # every finalize path; without this consultation a parent
            # whose last child errored but whose bus pending count
            # happened to drop to 0 (e.g., the child completed with
            # an error recorded) would be silently finalized as
            # "completed" instead of "error". The default
            # ``"completed"``/``None`` pair is the safe fallback for
            # the no-bus-pending case — same hardcoded value as the
            # prior call site, now routed through the central
            # resolver so future overrides apply automatically.
            status_to_finalize, error_for_finalize = _resolve_finalize_status(
                bus, instance_id, "completed", None
            )
            await self._finalize_job(
                ctx,
                instance_id,
                status_to_finalize,
                error=error_for_finalize,
            )
            # Hardening 1 (2026-06-27): mirror the ``_process_event``
            # happy path — clear the sticky parent-error flag AFTER
            # finalize so a future revive / re-spawn of the same
            # instance id does not inherit the error state from its
            # previous incarnation. ``bus`` is guaranteed non-None
            # here (the early-return at the top of this try-block
            # already filtered the None-bus case), so the inline
            # ``had_parent_error`` guard avoids an unconditional
            # method call. The clear is idempotent and safe — the
            # flag's only purpose was the ``_resolve_finalize_status``
            # override above, which has been applied. Any exception
            # raised here propagates to the outer ``except Exception``
            # branch and is logged at WARNING; we do NOT want a
            # ``clear_parent_error`` failure to wedge the deferred
            # safety net (the terminal transition has already fired).
            if bus.had_parent_error(instance_id):
                bus.clear_parent_error(instance_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Safety net: never propagate. Log at WARNING so the
            # failure surfaces in operator logs without breaking the
            # observer's main loop.
            logger.warning(
                f"Observer: deferred finalize raised for "
                f"{instance_id[:8]}...: {e}"
            )

    def _read_instance_status_sync(self, instance_id: str) -> str | None:
        """Read ``Instance.status`` for ``instance_id``. Returns ``None`` if missing.

        Sync helper for use with ``asyncio.to_thread`` from
        :meth:`_deferred_finalize_check`. Uses a plain ``Session``
        (no ``WriteGuardSession``) because this is a read-only
        pre-check — the authoritative terminal-state guard lives
        inside :meth:`_finalize_job_db_sync` Step 2.

        Args:
            instance_id: The instance ID to look up.

        Returns:
            The ``status`` string (e.g. ``"running"``, ``"completed"``),
            or ``None`` when the row is missing.
        """
        with Session(self._instance_manager.engine) as session:
            instance = session.get(Instance, instance_id)
            return instance.status if instance is not None else None

    async def _finalize_instance(
        self,
        instance_id: str,
        terminal_status: str,
        error: str | None = None,
    ) -> None:
        """Transition the instance to terminal state and fire instance-side side effects.

        Phase 3 (Cascade Unification) fix. The bus callback path (and the
        lifecycle-event fall-through when ``bus_pending == 0``) transitions
        the JOB to terminal via ``_finalize_job`` — but until Phase 3, the
        instance itself was left in RUNNING. That broke:

          * Instance lifecycle SSE stream — no terminal ``status_change``.
          * ``CompletionRegistry`` signaling — ``invoke_agent_and_wait()``
            callers hung waiting for an event that never fired.
          * Orphan MESSAGE job detection in ``job_processor.py`` — the row
            looks "still alive" until the recovery sweep runs.
          * Status-change SSE emission on the parent.

        This method mirrors the inline cascade in
        :class:`ChildReportsService._process_child_completion_and_notify_parent`
        (bus-active path) and :class:`ErrorReportingService._send_error_report`
        (bus-active path), so the bus path is fully wired end-to-end.

        H15 refactor: this method is kept for backwards-compat (it is
        exercised by ``test_finalize_instance.py`` and may be called
        standalone by future code paths). It still performs its own DB
        write via ``_finalize_instance_db_sync`` and then dispatches the
        post-commit side effects via the shared
        ``_dispatch_instance_post_commit_side_effects`` helper. The
        ``_finalize_job`` path no longer calls this method — it does the
        instance DB write inside the unified ``_finalize_job_db_sync``
        and calls the dispatcher directly (avoiding a redundant DB write).

        Idempotency: if the instance is already in a terminal status
        (``COMPLETED`` / ``ERROR`` / ``TERMINATED`` / ``FAILED``), the method
        is a no-op. The CM-disabled inline cascade sets the status before we
        get here, so re-entry from the lifecycle-event re-publish is safe.

        Side effects on success:
          1. ``instance.status`` → ``COMPLETED`` (or ``ERROR`` for ``terminal_status="error"``).
          2. ``instance.updated_at`` / ``instance.last_activity_at`` / ``instance.version`` updated.
          3. ``session.commit()`` — DB is consistent before we signal external systems.
          4. SSE ``status_change`` broadcast via the live hub.
          5. ``CompletionRegistry.complete()`` — unblocks ``invoke_agent_and_wait()`` callers.
          6. Lifecycle event published via the EventBus — this re-enters the
             observer via ``_process_event``, but the ``job.admission_state != ACTIVE``
             idempotency guard there returns early.

        Args:
            instance_id: The parent instance ID.
            terminal_status: ``"completed"`` or ``"error"``.
            error: Optional error message for ``"error"`` transitions.
        """
        # Map terminal_status → instance status.
        if terminal_status == InstanceStatus.COMPLETED.value:
            new_status = InstanceStatus.COMPLETED.value
        elif terminal_status == InstanceStatus.ERROR.value:
            new_status = InstanceStatus.ERROR.value
        else:
            logger.warning(
                f"Observer: _finalize_instance called with unknown "
                f"terminal_status='{terminal_status}' for {instance_id[:8]}..."
            )
            return

        # Step 1: DB transition under the write-pause guard. Mirrors the
        # inline-cascade pattern in ``child_reports.py:720`` /
        # ``error_reporting.py:160``. Capture ``parent_id`` and ``agent_id``
        # before the session closes (instance is detached after commit).
        #
        # The WriteGuardSession block + ``session.commit()`` runs on a worker
        # thread via ``asyncio.to_thread`` so a sync SQLAlchemy commit cannot
        # block the event loop. Under SQLite WAL write contention
        # (busy_timeout=30s) a sync commit on the loop thread wedges the
        # loop completely — Ctrl+C ignored, all APIs frozen. The extracted
        # helper is a sync ``def`` so it runs cleanly on the worker thread.
        try:
            result = await asyncio.to_thread(
                self._finalize_instance_db_sync,
                instance_id,
                new_status,
            )
            if result.skip:
                # Either the instance row is missing or it's already terminal.
                # The helper already logged at DEBUG; just return without
                # firing the post-commit side effects (SSE / CompletionRegistry
                # / lifecycle event).
                return
            parent_id = result.parent_id
            agent_id = result.agent_id
        except Exception as e:
            # Log and re-raise so the caller (``_finalize_job``) can decide
            # what to do. The caller wraps this in its own try/except and
            # logs at WARNING — the job transition is already terminal, so
            # a missing instance transition is recoverable by the orphan
            # detector and recovery sweep.
            logger.error(
                f"Observer: failed to transition instance "
                f"{instance_id[:8]}... to {new_status}: {e}",
                exc_info=True,
            )
            raise

        # Steps 2-4: post-commit side effects (SSE / CompletionRegistry /
        # lifecycle event) via the shared dispatcher. For the COMPLETED path,
        # ``last_content=None`` triggers an on-demand fetch in the dispatcher
        # (we don't have it cached here because this method does not pre-fetch
        # the way ``_finalize_job`` does).
        await self._dispatch_instance_post_commit_side_effects(
            instance_id=instance_id,
            terminal_status=terminal_status,
            error=error,
            parent_id=parent_id,
            agent_id=agent_id,
            last_content=None,
        )

    async def _dispatch_instance_post_commit_side_effects(
        self,
        *,
        instance_id: str,
        terminal_status: str,
        error: str | None,
        parent_id: str | None,
        agent_id: str | None,
        last_content: str | None,
    ) -> None:
        """Fire post-commit instance-side side effects after the DB transition.

        Extracted from the original ``_finalize_instance`` body so both
        ``_finalize_instance`` (standalone path) and ``_finalize_job``
        (H15 unified-transaction path) can call it without re-doing the
        DB write.

        Each step is wrapped in its own ``try/except`` so a failure in one
        side effect does not block the others (Step 2 SSE / Step 3
        CompletionRegistry / Step 4 lifecycle event). Best-effort throughout
        — the DB is already committed, so the worst case is a missing
        notification (recoverable by the orphan-detector / recovery sweep).

        Args:
            instance_id: The instance ID for SSE / CompletionRegistry / lifecycle.
            terminal_status: ``"completed"`` or ``"error"``.
            error: Optional error message (for the ERROR path).
            parent_id: Captured from the instance row before commit.
            agent_id: Captured from the instance row before commit.
            last_content: Pre-fetched last assistant message (for the
                CompletionRegistry COMPLETED path). If ``None``, the
                dispatcher fetches it on-demand via
                ``_get_last_assistant_message_raw``.
        """
        # Step 1: TWO-TIER proc cleanup. Added in Phase 1 of the
        # "auto-kill background processes on root instance completion"
        # plan (2026-07-18). Closes the per-child leak window: every
        # terminal instance now has its own background processes
        # cleaned up here, regardless of parent_id.
        #
        # Tier 1 — ALWAYS clean THIS instance's own processes. Runs
        # for ANY terminal instance (root OR child). Without this, a
        # child that COMPLETED would leak its background processes
        # until the root finalized.
        #
        # Tier 2 — Root-gated tree sweep for DESCENDANTS. Only when
        # this is a ROOT instance (parent_id is None). Tier 1 already
        # cleaned the root's own processes; Tier 2 sweeps descendants.
        #
        # Known limitations (mirrors ``BackgroundProcessManager.cleanup_all``):
        #   * Truly-detached orphans (child called ``setsid``) sit
        #     outside the process group that ``cleanup_instance``
        #     reaps via ``os.killpg`` — they will not be killed here.
        #   * Crash-recovery leak: the in-memory ``_processes``
        #     registry does not survive a daemon crash. The OS
        #     subprocesses themselves survive; the manager's
        #     bookkeeping is gone on next start. This sweep cannot
        #     reach them after a hard restart.
        #
        # Best-effort throughout: every cleanup call is wrapped in its
        # own try/except so a failure in one tier does not block the
        # other or the downstream side-effects below.
        try:
            from daemon.tools.proc_tools import get_background_process_manager

            proc_mgr = get_background_process_manager()
        except Exception as e:
            # Failed to even import / look up the manager — log and
            # fall through (Tier 1 + Tier 2 become no-ops).
            logger.warning(
                f"Observer: could not load BackgroundProcessManager for "
                f"{instance_id[:8]}: {type(e).__name__}: {e}"
            )
            proc_mgr = None

        # ── TIER 1 ─────────────────────────────────────────────────────
        if proc_mgr is not None:
            try:
                await proc_mgr.cleanup_instance(instance_id)
            except Exception as e:
                logger.warning(
                    f"Observer: Tier-1 proc cleanup failed for "
                    f"{instance_id[:8]}: {type(e).__name__}: {e}"
                )

        # ── TIER 1: bash cleanup (Phase 2) ─────────────────────────────
        try:
            from daemon.tools.bash import get_bash_process_registry

            bash_reg = get_bash_process_registry()
        except Exception as e:
            logger.warning(
                f"Observer: could not load BashProcessRegistry for "
                f"{instance_id[:8]}: {type(e).__name__}: {e}"
            )
            bash_reg = None

        if bash_reg is not None:
            try:
                await bash_reg.cleanup_instance(instance_id)
            except Exception as e:
                logger.warning(
                    f"Observer: Tier-1 bash cleanup failed for "
                    f"{instance_id[:8]}: {type(e).__name__}: {e}"
                )

        # ── TIER 2 (root-gated) ────────────────────────────────────────
        # Initialize ``tree_ids`` OUTSIDE the try block so a failure
        # inside the try does not leave it undefined (Phase 1 C4 fix).
        tree_ids: list[str] = []
        if parent_id is None and proc_mgr is not None:
            tree_ids = await self._cleanup_descendants_of(instance_id)

            # Iterate tree, skipping the root (Tier 1 already cleaned it).
            for iid in tree_ids:
                if iid == instance_id:
                    continue
                try:
                    await proc_mgr.cleanup_instance(iid)
                except Exception as e:
                    logger.warning(
                        f"Observer: Tier-2 proc cleanup failed for "
                        f"{iid[:8]}: {type(e).__name__}: {e}"
                    )

                # NEW (Phase 2): bash Tier 2
                try:
                    await bash_reg.cleanup_instance(iid)
                except Exception as e:
                    logger.warning(
                        f"Observer: Tier-2 bash cleanup failed for "
                        f"{iid[:8]}: {type(e).__name__}: {e}"
                    )

        # Step 2: SSE status_change — fire AFTER commit so subscribers see
        # a state consistent with the DB. Best-effort.
        live_hub = getattr(self._instance_manager, "_live_hub", None)
        if live_hub is not None:
            try:
                await live_hub.stream_status_change(
                    instance_id, terminal_status, agent_id=agent_id
                )
            except Exception as e:
                logger.warning(
                    f"Observer: failed to emit status_change for "
                    f"{instance_id[:8]}...: {e}"
                )

        # Step 3: Signal CompletionRegistry. For "error", pass the error
        # string as the result with ``is_error=True`` (matches
        # ``error_reporting.py:380-384``). For "completed", pass the last
        # assistant message raw content (matches ``child_reports.py:855``).
        try:
            from .completion_registry import get_completion_registry

            if terminal_status == InstanceStatus.ERROR.value:
                error_message = error if error else "Unknown error"
                get_completion_registry().complete(
                    instance_id,
                    result=f"Agent error: {error_message}",
                    is_error=True,
                )
            else:
                content = last_content
                if content is None:
                    content = (
                        await self._instance_manager._get_last_assistant_message_raw(
                            instance_id
                        )
                    )
                get_completion_registry().complete(
                    instance_id, result=content
                )
        except Exception as e:
            logger.warning(
                f"Observer: failed to signal CompletionRegistry for "
                f"{instance_id[:8]}...: {e}"
            )

        # Step 4: Publish lifecycle event. The event bus broadcasts to all
        # global subscribers — including this observer. The
        # ``_process_event`` re-entry is caught by the ``job.admission_state !=
        # ACTIVE`` idempotency guard (the job is already terminal at
        # this point, set by ``_finalize_job`` immediately before this call).
        events_service = getattr(self._instance_manager, "_events_service", None)
        if events_service is not None:
            try:
                await events_service._publish_instance_lifecycle_event(
                    instance_id=instance_id,
                    status=terminal_status,
                    error=error,
                    parent_id=parent_id,
                )
            except Exception as e:
                logger.warning(
                    f"Observer: failed to publish lifecycle event for "
                    f"{instance_id[:8]}...: {e}"
                )

    async def _cleanup_descendants_of(self, root_id: str) -> list[str]:
        """Return the descendant ``instance_id`` list for ``root_id``.

        Extracted from ``_dispatch_instance_post_commit_side_effects`` so
        it can be unit-tested directly and so Phase 2's adjacent bash
        sweep can re-use the same tree-id resolution without a diff
        touching the dispatcher's body.

        Resolves ``root_id``'s full subtree via
        ``instance_repository.get_tree_ids``. That call is SYNC (it
        reads from SQLite), so we run it via ``asyncio.to_thread`` to
        avoid blocking the event loop.

        Failure modes — all return ``[]`` so the caller's Tier 2 loop
        becomes a safe no-op:

          * ``_instance_repository`` missing on the manager
            (race during early shutdown) — WARNING logged.
          * ``get_tree_ids`` raises (DB locked, table missing, etc.)
            — WARNING logged.

        Args:
            root_id: Root instance ID whose descendants to enumerate.

        Returns:
            List of ``instance_id`` strings including ``root_id`` itself
            (the caller is expected to filter it out, since Tier 1
            already cleaned it). Returns ``[]`` on any failure.
        """
        instance_repository = getattr(
            self._instance_manager, "_instance_repository", None
        )
        if instance_repository is None:
            logger.warning(
                f"Observer: no _instance_repository on manager; "
                f"skipping descendant cleanup for root {root_id[:8]}"
            )
            return []

        try:
            return await asyncio.to_thread(
                instance_repository.get_tree_ids, root_id
            )
        except Exception as e:
            logger.warning(
                f"Observer: get_tree_ids failed for root {root_id[:8]}: "
                f"{type(e).__name__}: {e}"
            )
            return []

    def _finalize_instance_db_sync(
        self,
        instance_id: str,
        new_status: str,
    ) -> _InstanceFinalizeResult:
        """Sync DB half of ``_finalize_instance``.

        Opens a ``WriteGuardSession``, applies the terminal status transition
        + bookkeeping fields, and commits. Returns the values the async
        caller needs (``parent_id``, ``agent_id``) without doing any
        post-commit fan-out (SSE / CompletionRegistry / lifecycle event) —
        those remain on the event loop so they can use ``await``.

        Runs on a worker thread via ``asyncio.to_thread`` from
        ``_finalize_instance``. This keeps ``session.commit()`` off the
        event loop so SQLite WAL write contention cannot deadlock the
        daemon (see the deadlock analysis in the experience docs).

        Idempotency: if the instance row is missing OR already in a
        terminal status, returns ``skip=True`` and the caller short-circuits
        without firing the post-commit side effects. Re-entry from the
        lifecycle-event re-publish is therefore safe.

        Args:
            instance_id: The parent instance ID.
            new_status: The canonical terminal status to apply
                (``COMPLETED.value`` or ``ERROR.value``).

        Returns:
            ``_InstanceFinalizeResult`` carrying either ``skip=True`` (no
            row / already terminal — caller short-circuits) or
            ``skip=False`` with the captured ``parent_id`` / ``agent_id``.
        """
        with WriteGuardSession(
            Session(self._instance_manager.engine),
            self._instance_manager.write_guard,
        ) as session:
            instance = session.get(Instance, instance_id)
            if instance is None:
                logger.debug(
                    f"Observer: instance {instance_id[:8]}... not found "
                    f"during finalization, skipping"
                )
                return _InstanceFinalizeResult(
                    skip=True, parent_id=None, agent_id=None
                )
            # Idempotency: if already in a terminal status, the inline
            # cascade (CM-disabled path) or a prior callback already
            # completed the instance. Re-publishing the lifecycle event
            # would be redundant and could double-signal
            # ``CompletionRegistry``.
            if instance.status in _TERMINAL_INSTANCE_STATUSES:
                logger.debug(
                    f"Observer: instance {instance_id[:8]}... already in "
                    f"terminal status '{instance.status}', skipping "
                    f"finalization (idempotency)"
                )
                return _InstanceFinalizeResult(
                    skip=True, parent_id=None, agent_id=None
                )

            parent_id = instance.parent_id
            agent_id = instance.agent_id

            instance.status = new_status
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
            session.commit()
            return _InstanceFinalizeResult(
                skip=False, parent_id=parent_id, agent_id=agent_id
            )

    def _finalize_job_db_sync(
        self,
        job_id: str | None,
        instance_id: str,
        terminal_status: str,
        result_summary: str | None,
        error_message: str | None,
    ) -> _FinalizeJobResult:
        """Sync DB half of ``_finalize_job`` (H15 fix).

        Consolidates the 5-step terminal cascade into a SINGLE
        ``WriteGuardSession`` transaction so partial failures cannot leave
        inconsistent state (job COMPLETED but lock leaked, etc.). The
        three DB operations commit together:

          1. Job atomic transition PROCESSING → COMPLETED/FAILED (in-session
             UPDATE, same semantics as ``JobRepository.atomic_transition`` but
             inside our ``WriteGuardSession`` so it commits atomically with
             steps 2/3).
          2. Instance status update to COMPLETED/ERROR (status, updated_at,
             last_activity_at, version bump). Skipped if already terminal
             OR if the row is missing.
          3. Lock release — DELETE every ``job_locks`` row where
             ``instance_id`` matches. Inlined here (instead of calling
             ``LockRepository.release_by_instance`` which opens its own
             session — a separate transaction that would defeat the
             atomicity we need).

        If any step raises, none of them commit (the
        ``WriteGuardSession.__exit__`` rolls back via the underlying
        ``Session.close``).

        Phase 2.5 (Task 2.5.4, D13 consumption-site rewrite): the
        ``job_id`` parameter is now ``str | None``. When ``job_id is
        None`` (post-D13 MESSAGE path — no ``JobItem`` exists for the
        instance), Step 1 is skipped entirely and Steps 2+3 still run.
        This is the **least disruptive** option per the plan: the
        instance transition (Step 2) and lock release (Step 3) are
        critical — they MUST fire even without a JobItem. The JobItem
        UPDATE (Step 1) is redundant in the no-JobItem case (there is
        nothing to UPDATE). The bus gate (premature-finalization
        defense) and the in-session gate are preserved regardless
        of ``job_id`` — the gates protect the instance, not the
        JobItem.

        Phase 2 audit (2026-06-25, pause/resume redesign):
          PAUSED jobs are EXCLUDED by the ``WHERE JobItem.admission_state ==
          AdmissionState.ACTIVE.value`` guard in Step 1 (see the
          ``.where(JobItem.admission_state == AdmissionState.ACTIVE.value)`` clause
          in the in-session UPDATE below). After the pause cascade
          transitions a job PROCESSING → PAUSED, this UPDATE rowcount-
          drops to 0 — the helper falls through to the
          ``InvalidTransitionError`` branch, which the async caller
          (``_finalize_job``) handles by logging at DEBUG and returning
          silently (idempotency). Net effect: a paused job is NEVER
          finalized by this path. This is the correct observable
          behavior — pausing an instance must NOT trigger premature
          job finalization via the lifecycle-event path.

        C1 TOCTOU invariant (Phase 2): ``get_pending_count`` is re-checked
        INSIDE this sync helper, immediately before the in-session UPDATE.
        There is no ``await`` between this re-check and the transition
        (both run on the same worker thread). This preserves the original
        invariant from ``_finalize_job`` even though the call is now
        off-loaded to a worker thread via ``asyncio.to_thread``. The check
        reads ``_pending`` (a Python dict, GIL-protected) so the read is
        safe from any thread; the per-parent ``asyncio.Lock`` in the CM is
        NOT taken (would raise ``RuntimeError`` off the event loop).

        Returns ``skip=True`` for the following cases (caller returns
        silently without firing side effects):
          * Unknown ``terminal_status`` (caller already validated; defensive).
          * Bus ``get_pending_count > 0`` — new pending correlations appeared
            during the callback (C1 abort). The bus will fire the callback
            again when those resolve.
          * Job not found (deleted concurrently).
          * ``job_id is None`` AND ``Step 2`` (instance row) is also
            missing — nothing to update at all.

        Raises ``InvalidTransitionError`` for the concurrent-transition
        case (job status no longer PROCESSING — race with another actor).
        The caller logs at DEBUG and returns silently (idempotency).

        Any other exception propagates to the caller, which fires the W3
        fail-safe transition.

        Args:
            job_id: The job to transition. ``None`` skips Step 1 (no
                ``JobItem`` exists for the instance — post-D13 MESSAGE
                path). Steps 2+3 still run.
            instance_id: The parent instance ID (instance update + lock release).
            terminal_status: ``"completed"`` or ``"error"``.
            result_summary: Pre-fetched result summary for the COMPLETED path.
            error_message: Pre-fetched error message for the ERROR path.

        Returns:
            ``_FinalizeJobResult`` carrying the data the async caller needs
            to fire post-commit side effects.
        """
        # ─── Validate terminal_status (caller already validated; defensive) ───
        if terminal_status == InstanceStatus.COMPLETED.value:
            to_status = "completed"
        elif terminal_status == InstanceStatus.ERROR.value:
            to_status = "failed"
        else:
            return _FinalizeJobResult(
                skip=True,
                terminal_status=None,
                job_id=None,
                instance_id=None,
                parent_id=None,
                agent_id=None,
                result_summary=None,
                error_message=None,
                locks_released=0,
                instance_was_terminal=False,
            )

        # ─── C1 TOCTOU re-check (Phase 2 invariant preserved — Phase 5: bus) ───
        # Sync, inside the worker thread, IMMEDIATELY before the UPDATE.
        # No await between this read and the UPDATE below. The bus is the
        # SOLE completion authority; the bus gate
        # (``_bus_count_pending_for_target_sync``) returns
        # 0 when the bus singleton is None — bus singleton missing is a
        # hard error. If the gate below is moved
        # or removed, this comment is the breadcrumb to restore the
        # TOCTOU re-check using ``bus.count_pending_for_target_sync``.

        # ─── Bus gate (premature-finalization fix) ─────────
        # The bus DB is the authoritative source of pending-children
        # truth; this gate MUST consult it BEFORE the terminal cascade
        # commits, or the parent job would be marked COMPLETED while
        # children tracked via the bus are still working (the exact
        # premature-finalization bug the bus was designed to prevent).
        #
        # The bus check is a sync DB query against the
        # ``dependency_watchers`` table (the bus singleton exposes
        # ``count_pending_for_target_sync`` for caller contexts that
        # can't await, which is this sync gate inside
        # ``WriteGuardSession`` on a worker thread).
        #
        # If the bus reports PENDING watchers, defer finalization
        # with ``skip=True, gate_deferred=True`` — same shape the
        # existing CM-busy branch returns. The async caller in
        # ``_finalize_job`` re-arms the job (COMPLETED → PROCESSING
        # via ``atomic_transition`` in the post-commit re-check path)
        # so a late child's resolve can find a PROCESSING job. The
        # generation counter is already bumped by ``DependencyBus.
        # watch`` (C2 fix), so the orphan-race re-arm path detects
        # the in-flight register correctly.
        if self._bus_count_pending_for_target_sync(instance_id) > 0:
            logger.info(
                f"Observer: aborting terminal transition for "
                f"{instance_id[:8]}... — bus has PENDING watchers "
                f"(bus has pending watchers), "
                f"deferring finalization"
            )
            return _FinalizeJobResult(
                skip=True,
                terminal_status=None,
                job_id=None,
                instance_id=None,
                parent_id=None,
                agent_id=None,
                result_summary=None,
                error_message=None,
                locks_released=0,
                instance_was_terminal=False,
                gate_deferred=True,
            )

        # ─── F14: pending tasks gate (premature-finalization fix) ───
        # F14 (defer-seam bugfix Phase 3, 2026-07-01): the bus gate
        # above counts ``dependency_watchers`` rows only — but a child
        # Task whose ``send_message`` failed before ``bus.watch`` ran
        # never registers a watcher row, leaving the parent invisible
        # to the bus gate. The parent JobItem would then finalize to
        # ``done`` prematurely, while the orphan Task is later
        # force-cancelled + retried against a terminal instance.
        #
        # Close that seam by ALSO counting non-bus-registered PENDING
        # Tasks for the instance. Defer finalization if EITHER the bus
        # has PENDING watchers OR the ``task`` table has PENDING rows
        # for the instance. Conservative: when in doubt, defer rather
        # than prematurely finalize.
        #
        # This is the early / defense-in-depth check; the
        # authoritative in-session check is below (inside
        # WriteGuardSession) and shares the same transaction as the
        # JobItem UPDATE.
        _pending_tasks = self._count_pending_tasks_for_instance_sync(
            instance_id
        )
        if _pending_tasks > 0:
            logger.info(
                f"Observer: aborting terminal transition for "
                f"{instance_id[:8]}... — instance has {_pending_tasks} "
                f"PENDING task(s) not registered in the bus (F14), "
                f"deferring finalization"
            )
            return _FinalizeJobResult(
                skip=True,
                terminal_status=None,
                job_id=None,
                instance_id=None,
                parent_id=None,
                agent_id=None,
                result_summary=None,
                error_message=None,
                locks_released=0,
                instance_was_terminal=False,
                gate_deferred=True,
            )

        # ─── Single WriteGuardSession for ALL three DB writes ───
        with WriteGuardSession(
            Session(self._instance_manager.engine),
            self._instance_manager.write_guard,
        ) as session:
            # ─── In-session completion gate (premature-completion fix) ───
            # Phase 5: the CM's ``is_complete()`` / ``get_pending_count()``
            # are superseded by the bus DB (``dependency_watchers`` table).
            # The bus is the SOLE completion authority; the in-session
            # bus gate below is the authoritative check.
            #
            # C1 fix (TOCTOU hardening, bus path): on
            # row-lock-supporting dialects (PostgreSQL, MySQL InnoDB,
            # MariaDB), the inline ``SELECT COUNT(*)`` from
            # ``dependency_watchers`` runs in the SAME transaction as
            # the in-session UPDATE below — atomic at the DB level
            # (SQLite full write lock; PostgreSQL READ COMMITTED within
            # one transaction).
            #
            # If the orchestrator still has active children, defer
            # finalization for BOTH the job and the instance — they stay
            # coupled. The bus will fire its callback when the new
            # wave resolves (a fresh watcher row is registered), at
            # which point the transition proceeds normally. The async
            # caller (``_finalize_job``) handles the wave-2 re-arm via
            # the orphan-race generation check so wave 2 children
            # whose resolves arrive before their registers still find
            # a PROCESSING job — C2-PartA fix.
            #
            # ─── Bus gate (in-session) ───────────────────────
            # The bus DB is the authoritative source of pending-
            # children truth — we MUST consult it here, INSIDE the
            # WriteGuardSession immediately before the in-session
            # UPDATE, to prevent premature finalization. Same query
            # and semantics as the early re-check above (TOCTOU
            # hardening: this is the authoritative gate; the early
            # re-check is a defense-in-depth that catches races
            # between the pre-fetch and the WriteGuardSession).
            #
            # C2 fix (TOCTOU hardening, 2026-06-22): inline the
            # COUNT query directly on the WriteGuardSession's
            # ``session`` object so the COUNT and the in-session
            # UPDATE share the SAME transaction. The previous
            # helper opened its own short-lived Session via the
            # bus repository, creating transaction A — while
            # the UPDATE below commits in transaction B (the
            # WriteGuardSession). A concurrent ``bus.watch()``
            # INSERT on a different connection could commit
            # between A and B, re-opening the premature-
            # finalization window this gate exists to close.
            # With the inline query, the COUNT and UPDATE are
            # atomic at the DB level (SQLite full write lock;
            # PostgreSQL READ COMMITTED within one transaction).
            #
            # B fix (2026-06-22): the inline query must NOT
            # catch exceptions — see MEDIUM B in the review.
            # Catching and returning 0 would silently pass the
            # gate, reintroducing the premature-finalization
            # bug on transient DB errors. Exceptions propagate
            # to the existing W3 fail-safe path in
            # ``_finalize_job``.
            #
            # The per-parent CM lock acquired in ``_finalize_job``
            # (the async caller) serializes this gate against
            # ``bus.watch()`` — a watch INSERT that commits
            # inside the lock is guaranteed visible to the COUNT
            # here, regardless of who wins the race.
            #
            # Defensive wiring check (A9 invariant, Phase 3 review W2):
            # the bus singleton MUST be wired before this gate runs —
            # we raise ``RuntimeError`` rather than silently skipping
            # the gate when the singleton is None. A dormant gate would
            # reintroduce the premature-finalization bug Phase 3 is
            # eliminating (a config that forgets to wire the bus would
            # pass every gate without consulting any pending-children
            # authority). The W3 fail-safe in ``_finalize_job`` catches
            # this RuntimeError and transitions the job to FAILED — the
            # correct observable behavior for a configuration bug.
            from daemon.services.dependency_bus import (
                get_dependency_bus as _get_bus_for_gate,
            )
            # A9 hard-error: bus must be initialized. Extending the A9
            # invariant to the in-session gate: when the singleton is
            # None (testing, missing init, config drift), we MUST NOT
            # silently pass the gate — that would reintroduce the
            # premature-finalization bug Phase 3 eliminates. The W3
            # fail-safe in ``_finalize_job`` catches this and
            # transitions the job to FAILED, which is the correct
            # observable behavior (fail-safe rather than silently
            # proceeding without a bus check).
            _bus = _get_bus_for_gate()
            if _bus is None:
                raise RuntimeError(
                    "DependencyBus is None during _finalize_job_db_sync "
                    "gate — invalid state. The bus must be initialized "
                    "(see ADR-011)."
                )
            _bus_pending_stmt = (
                select(func.count())
                .select_from(DependencyWatcher)
                .where(
                    DependencyWatcher.target_instance_id == instance_id
                )
                .where(
                    DependencyWatcher.state
                    == DependencyWatcherState.PENDING.value
                )
            )
            _bus_pending = int(session.scalar(_bus_pending_stmt) or 0)
            if _bus_pending > 0:
                logger.info(
                    f"Observer: aborting terminal transition for "
                    f"{instance_id[:8]}... — instance marked complete but "
                    f"bus has {_bus_pending} PENDING watchers "
                    f"(in-session gate), "
                    f"deferring finalization"
                )
                return _FinalizeJobResult(
                    skip=True,
                    terminal_status=None,
                    job_id=None,
                    instance_id=None,
                    parent_id=None,
                    agent_id=None,
                    result_summary=None,
                    error_message=None,
                    locks_released=0,
                    instance_was_terminal=False,
                    gate_deferred=True,
                )

            # ─── F14: in-session pending tasks gate ─────────────
            # F14 (defer-seam bugfix Phase 3, 2026-07-01): the bus
            # gate above is the SOLE completion authority for
            # bus-tracked children, but it is blind to child Tasks
            # whose ``bus.watch`` failed (or never ran) before the
            # lifecycle event fired. Such Tasks still have a
            # ``status='pending'`` row in the ``task`` table that
            # the parent JobItem must NOT finalize past.
            #
            # Authoritative check: inline the COUNT on the
            # WriteGuardSession's ``session`` so the read and the
            # JobItem UPDATE share ONE transaction (atomic at the
            # DB level — SQLite full write lock; PostgreSQL
            # READ COMMITTED within one transaction). A concurrent
            # ``task`` INSERT on a different connection commits
            # only between transactions; here the read is fresh.
            #
            # The ``Task`` import is deferred to this scope (it
            # would be a circular import at module load time — the
            # task package pulls in JobItem transitively for the
            # cross-system guard, which pulls in
            # ``job_feedback_observer``).
            from daemon.repositories.task.models import (
                Task as _Task,
                TaskStatus as _TaskStatus,
            )
            _pending_tasks_stmt = (
                select(func.count())
                .select_from(_Task)
                .where(_Task.instance_id == instance_id)
                .where(_Task.status == _TaskStatus.PENDING.value)
            )
            _pending_tasks = int(
                session.scalar(_pending_tasks_stmt) or 0
            )
            if _pending_tasks > 0:
                logger.info(
                    f"Observer: aborting terminal transition for "
                    f"{instance_id[:8]}... — instance has "
                    f"{_pending_tasks} PENDING task(s) not registered "
                    f"in the bus (F14, in-session gate), "
                    f"deferring finalization"
                )
                return _FinalizeJobResult(
                    skip=True,
                    terminal_status=None,
                    job_id=None,
                    instance_id=None,
                    parent_id=None,
                    agent_id=None,
                    result_summary=None,
                    error_message=None,
                    locks_released=0,
                    instance_was_terminal=False,
                    gate_deferred=True,
                )
            # Gate passed cleanly under CM authority — fall through
            # to the UPDATE / Step 1 / Step 2 / Step 3 / commit path.
            # Reset the bounded defer counter so any future transient
            # failure starts counting from zero rather than inheriting
            # stale state from a prior incident.
            _gate_defer_counts.pop(instance_id, None)

            now = datetime.now(timezone.utc).isoformat()
            now_dt = datetime.now(timezone.utc)

            # ─── Step 1: Job atomic transition (in-session UPDATE) ───
            # Mirrors ``JobRepository.atomic_transition`` but inside our
            # WriteGuardSession so it commits with steps 2/3 below. The UPDATE
            # uses the same ``status = :from_status`` SQL guard — concurrent
            # writers cannot both observe the predicate as true.
            #
            # Phase 2.5 (Task 2.5.4): Step 1 is **skipped** when
            # ``job_id is None`` — the post-D13 MESSAGE path where no
            # ``JobItem`` exists for the instance. Steps 2+3 (instance
            # status + lock release) still run unconditionally. The
            # ``InvalidTransitionError`` short-circuit (status-mismatch
            # on concurrent transition) does not apply in this branch
            # — there is no JobItem to mismatch on. The conditional
            # ``job_id is None`` is captured into a local flag so the
            # subsequent ``_FinalizeJobResult`` carries the correct
            # ``skip`` value.
            if job_id is not None:
                update_values: dict[str, Any] = {
                    # Phase 4 cleanup (admission_state is the sole
                    # write authority): the legacy ``status`` column
                    # is no longer written here. COMPLETED / FAILED /
                    # CANCELLED all collapse onto
                    # ``admission_state='done'`` — the per-terminal
                    # distinction is preserved on the Instance side
                    # (``Instance.status``) and surfaced to callers
                    # via the resolver's canonical mapping.
                    "admission_state": AdmissionState.DONE.value,
                    # Phase 5 migration: ``completed_at`` /
                    # ``result_summary`` / ``error_message`` were
                    # dropped from ``JobItem`` (execution state now
                    # lives on ``Instance`` + the resolver's Task
                    # parse). Writing them here raised
                    # ``CompileError: Unconsummed column names ...``,
                    # crashing ``_finalize_job`` before
                    # ``notify_watchers`` could fire and leaving
                    # watchers (e.g. jober via ``watch_job``) hung.
                    # ``result_summary`` / ``error_message`` are
                    # still carried in-memory via ``_FinalizeJobResult``
                    # for the notification payload below.
                }
                # Phase 7c: terminal_reason discriminator. Maps the
                # observer's ``terminal_status`` (which already
                # normalises COMPLETED → 'completed' / ERROR → 'failed'
                # at the top of the method) onto the corresponding
                # ``terminal_reason``. The resolver
                # (``work_resolver._job_to_record``) prioritises
                # ``terminal_reason`` over ``Instance.status`` for
                # ``admission_state='done'`` rows — so writing it
                # here is what callers will see. ``aborted`` is
                # never written by this path (the observer fires on
                # natural completion / error, not on instance
                # terminate cascade; that path lives in
                # ``instance_lifecycle._terminate_instance_db_sync``).
                update_values["terminal_reason"] = to_status

                # Finalize-on-completion fallback: snapshot the
                # previous ``admission_state`` BEFORE the UPDATE so
                # we can detect the stuck-queued case below. This is
                # only used for observability (a WARNING log); the
                # UPDATE itself is gated by the WHERE clause below
                # which matches both ``active`` and ``queued``.
                # ``session.get`` runs inside the same
                # ``WriteGuardSession`` (same transaction under
                # SQLite full write lock; PostgreSQL READ COMMITTED
                # within one transaction).
                _prev_job = session.get(JobItem, job_id)
                _prev_admission_state = (
                    _prev_job.admission_state
                    if _prev_job is not None
                    else None
                )

                stmt = (
                    sqlmodel_update(JobItem)
                    .where(JobItem.job_id == job_id)
                    # Phase 4: admission_state is the authority; the
                    # legacy ``status == 'processing'`` guard was
                    # replaced. Paused jobs are still excluded by the
                    # new predicate because pause keeps the job in
                    # admission_state='active' (its lock is held) but
                    # the ``_pause_cascade_db_sync`` UPDATE in
                    # ``instance_lifecycle.py`` no longer touches the
                    # job row — see Plan §8.1. Concurrent
                    # ``_terminate_instance_db_sync`` callers that flip
                    # admission_state to 'done' cause rowcount=0 here
                    # and the helper falls through to the disambiguation
                    # SELECT below.
                    #
                    # Finalize-on-completion fallback: match BOTH
                    # ``active`` AND ``queued`` so a message-JobItem
                    # whose post-claim activation UPDATE
                    # (``queued`` → ``active``) missed — best-effort
                    # UPDATE failed or raced — is still finalizable.
                    # Without this, Part A's
                    # ``_get_processing_job_for_instance`` finds the
                    # JobItem (matched ``queued``) but the UPDATE
                    # rowcount-drops to 0 → ``InvalidTransitionError``
                    # → silently caught by the async caller → the
                    # JobItem leaks as ``queued`` forever. ``done``
                    # and ``dead`` rows are still excluded (they were
                    # already filtered upstream / by prior
                    # finalization).
                    #
                    # RF3 safety net (2026-07-06): ALSO match the
                    # non-canonical literal ``paused`` so a JobItem
                    # whose ``admission_state`` was written to
                    # ``paused`` by a legacy / drift path (e.g. the
                    # pre-Phase-5 ``status`` mirror, or a
                    # ``job_recovery_service`` ``atomic_transition``
                    # call whose legacy ``paused`` value survived a
                    # Phase 5 column drop + backfill gap) is still
                    # finalizable. ``_resume_cascade_db_sync`` UPDATE 3
                    # normally lifts the mirror back to ``active`` so
                    # this branch is defensive — it costs one extra
                    # string in the IN-list and lets the finalize
                    # path clean up any residual ``paused`` mirror
                    # that escaped the resume cascade.
                    .where(
                        JobItem.admission_state.in_([
                            AdmissionState.ACTIVE.value,
                            AdmissionState.QUEUED.value,
                            "paused",
                        ])
                    )
                    .values(**update_values)
                )
                result = session.exec(stmt)

                if result.rowcount == 0:
                    # UPDATE matched no rows. Disambiguate with a follow-up SELECT.
                    existing = session.get(JobItem, job_id)
                    if existing is None:
                        # Job gone — idempotency skip (no side effects).
                        logger.debug(
                            f"Observer: job {job_id[:8]}... not found during "
                            f"finalize (deleted concurrently), skipping"
                        )
                        return _FinalizeJobResult(
                            skip=True,
                            terminal_status=None,
                            job_id=None,
                            instance_id=None,
                            parent_id=None,
                            agent_id=None,
                            result_summary=None,
                            error_message=None,
                            locks_released=0,
                            instance_was_terminal=False,
                        )
                    # Status mismatch — concurrent transition. Raise so the
                    # caller treats it as the idempotency-race case (DEBUG log,
                    # silent return).
                    raise InvalidTransitionError(
                        job_id=job_id,
                        from_state=existing.admission_state,
                        to_state=to_status,
                    )
                else:
                    # UPDATE succeeded (rowcount > 0). If the row was
                    # previously in ``admission_state='queued'`` (not
                    # the normal ``active``), the post-claim
                    # activation UPDATE (``queued`` → ``active``)
                    # missed somewhere — surface a WARNING so
                    # operators can spot stuck-activation bugs in the
                    # worker claim path. ``active`` is the normal
                    # path (no warning). The pre-UPDATE snapshot read
                    # above is the only way to observe this — the
                    # UPDATE itself doesn't expose the previous
                    # state.
                    if _prev_admission_state == AdmissionState.QUEUED.value:
                        logger.warning(
                            f"Observer: finalized JobItem "
                            f"{job_id[:8]}... from "
                            f"admission_state='queued' (post-claim "
                            f"activation UPDATE missed) — instance "
                            f"{instance_id[:8]}..., terminal_reason="
                            f"{to_status}"
                        )
            else:
                # Phase 2.5 (Task 2.5.4): no JobItem to update.
                # Fall through to Steps 2+3 unconditionally.
                logger.debug(
                    f"Observer: Step 1 (JobItem UPDATE) skipped — "
                    f"no JobItem for instance {instance_id[:8]}... "
                    f"(post-D13 MESSAGE path); proceeding to Steps 2+3"
                )

            # ─── Step 2: Instance status update ───
            # Read the instance inside the SAME session so the status change
            # commits with steps 1 and 3. Captures parent_id / agent_id before
            # the session closes (instance is detached after commit).
            instance = session.get(Instance, instance_id)
            if instance is None:
                # Instance missing — job transitioned but instance row gone.
                # Continue with lock release (step 3); instance-side side
                # effects are skipped downstream (instance_was_terminal=True
                # signals the caller to skip SSE / CompletionRegistry / event).
                parent_id = None
                agent_id = None
                instance_was_terminal = True
            elif instance.status in _TERMINAL_INSTANCE_STATUSES:
                # Already terminal — CM-disabled inline cascade or prior
                # callback already completed the instance. Skip the write
                # but capture parent_id / agent_id (caller may still need
                # them for the lifecycle event — though side effects should
                # have fired already, so instance_was_terminal=True).
                parent_id = instance.parent_id
                agent_id = instance.agent_id
                instance_was_terminal = True
            else:
                parent_id = instance.parent_id
                agent_id = instance.agent_id
                instance.status = terminal_status
                instance.updated_at = now
                instance.last_activity_at = now_dt
                instance.version = (instance.version or 1) + 1
                instance_was_terminal = False

            # ─── Step 3: Lock release ───
            # Inline the SQL instead of calling
            # ``LockRepository.release_by_instance`` (which opens its own
            # session — a separate transaction that would defeat the
            # atomicity we need). Same SELECT + DELETE pattern.
            lock_stmt = select(JobLock).where(JobLock.instance_id == instance_id)
            locks = session.exec(lock_stmt).all()
            released = len(locks)
            for lock in locks:
                session.delete(lock)

            # ─── Single commit for ALL three DB writes ───
            session.commit()

        logger.info(
            f"Observer: finalized job {job_id[:8] if job_id else 'no_job'}... status={terminal_status} "
            f"for instance {instance_id[:8]}... (released {released} lock(s), "
            f"instance_was_terminal={instance_was_terminal})"
        )

        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=parent_id,
            agent_id=agent_id,
            result_summary=result_summary,
            error_message=error_message,
            locks_released=released,
            instance_was_terminal=instance_was_terminal,
        )

    async def _trigger_next_job_by_id(
        self, job_id: str, instance_id: str
    ) -> None:
        """Look up the JobItem by ID and run ``_trigger_next_job``.

        Phase 2.5 (Task 2.5.4): the post-commit outbox in
        ``_finalize_job`` now operates on a :class:`_ProcessingJobContext`
        (which carries only ``job_id``, not the full ``JobItem``). The
        JobItem is needed by ``_trigger_next_job`` because that method
        reads ``job.project_id`` to find the next pending job in the
        same project.

        This helper looks up the JobItem by ``job_id`` (best-effort)
        and delegates. If the lookup fails — e.g. the JobItem was
        deleted concurrently, or the caller passed ``job_id=None``
        (handled by the caller; this method asserts non-None) — we
        skip the trigger and log at DEBUG. The :class:`JobProcessor`
        polling loop is the safety net that picks up the next pending
        job even if this handoff fails.

        Args:
            job_id: The completed job's ID. Must be non-None.
            instance_id: The instance ID (for logging).
        """
        if job_id is None:
            logger.debug(
                f"Observer: _trigger_next_job_by_id skipped — "
                f"job_id is None for instance {instance_id[:8]}..."
            )
            return
        try:
            job = await asyncio.to_thread(
                self._job_repo.get, job_id
            )
        except Exception as e:
            logger.warning(
                f"Observer: failed to look up JobItem "
                f"{job_id[:8]}... for next-job handoff: {e}"
            )
            return
        if job is None:
            logger.debug(
                f"Observer: JobItem {job_id[:8]}... not found "
                f"during next-job handoff (deleted concurrently); "
                f"JobProcessor safety net will pick up the next pending job"
            )
            return
        await self._trigger_next_job(job)

    async def _trigger_next_job(self, job) -> None:
        """Admit and spawn the next pending job for the same project.

        Best-effort: any failure is logged at WARNING and swallowed. The
        :class:`JobProcessor` polling loop is a safety net that will eventually
        pick up the next job even if this handoff fails.

        M10 fix: the 4-step pipeline ``_get_next_job`` → ``start_job`` →
        ``spawn_instance_with_mcp`` → ``enqueue_message`` had an
        instance-orphaning gap. If ``enqueue_message`` failed AFTER
        ``spawn_instance_with_mcp`` succeeded, the spawned instance sat in
        ``IDLE`` with no message queued — never picked up by any worker.
        The old code only marked the job FAILED but left the instance
        orphaned (now using a DB row + MCP connections + an in-process
        :class:`InstanceManager` entry with no consumer).

        The fix rolls back by calling :meth:`InstanceManager.terminate_instance`
        on the orphaned instance before marking the job FAILED. ``terminate_instance``
        cascades: cancels active requests, terminates children, releases
        project lock, cleans up MCP, and removes the in-memory instance
        entry. Best-effort: any cleanup failure is logged at WARNING and
        the job still gets marked FAILED (the JobProcessor safety net
        will eventually retry the spawn).

        Args:
            job: The job that just completed (used for ``project_id``).
        """
        try:
            if not job.project_id:
                return

            next_job = await self._job_queue_service._get_next_job(
                job.project_id
            )
            if next_job is None:
                return

            started_job = await self._job_queue_service.start_job(
                next_job.job_id
            )
            if started_job is None:
                return

            instance_id = started_job.instance_id
            # Track whether ``spawn_instance_with_mcp`` actually created an
            # instance. If it raised, there is no in-process instance to
            # clean up — just mark the job FAILED.
            spawn_succeeded = False
            try:
                instance_id = await self._instance_manager.spawn_instance_with_mcp(
                    agent_id=started_job.agent_id,
                    instance_id=instance_id,
                    project_id=started_job.project_id,
                )
                spawn_succeeded = True
            except Exception as e:
                logger.error(
                    f"Observer: failed to spawn instance for job "
                    f"{started_job.job_id[:8]}...: {e}"
                )
                await self._job_queue_service.complete_job(
                    started_job.job_id,
                    demand_state=DemandState.FAILED,
                    error=str(e),
                )
                return

            # M10: ``enqueue_message`` is the rollback boundary. If it fails
            # AFTER ``spawn_instance_with_mcp`` succeeded, the spawned
            # instance is orphaned — terminate it before marking the job
            # FAILED so we don't leave a no-consumer instance in the DB +
            # in-memory manager.
            try:
                result = await self._instance_manager.enqueue_message(
                    instance_id=instance_id,
                    message=started_job.message,
                    source=started_job.source,
                )
                # Stamp the message_id back onto the JobItem so the
                # cross-system guard in ``claim_pending_task`` can
                # correlate active MESSAGE JobItems with their
                # ``message_queue`` row. Mirrors the pattern in
                # ``JobProcessor._process_next_job`` (lines 865-875).
                # Best-effort: a failure here is logged at WARNING and
                # swallowed — the dispatch already succeeded, and the
                # NULL-safe guard tolerates a missing ``message_id``.
                if result is not None and getattr(result, "message_id", None):
                    try:
                        await asyncio.to_thread(
                            self._job_queue_service._repository.stamp_message_id,
                            started_job.job_id,
                            result.message_id,
                        )
                    except Exception as stamp_err:
                        logger.warning(
                            f"Observer: failed to stamp message_id for job "
                            f"{started_job.job_id[:8]}...: {stamp_err}"
                        )
            except Exception as e:
                logger.error(
                    f"Observer: failed to enqueue message for job "
                    f"{started_job.job_id[:8]}...: {e}"
                )
                # Terminate the orphaned instance (best-effort). If
                # termination itself fails, log and proceed — the
                # JobProcessor safety net will eventually retry.
                if spawn_succeeded:
                    try:
                        await self._instance_manager.terminate_instance(
                            instance_id=instance_id
                        )
                        logger.info(
                            f"Observer: M10 cleanup — terminated orphaned "
                            f"instance {instance_id[:8]}... after "
                            f"enqueue_message failure for job "
                            f"{started_job.job_id[:8]}..."
                        )
                    except Exception as cleanup_err:
                        logger.warning(
                            f"Observer: M10 cleanup failed to terminate "
                            f"orphaned instance {instance_id[:8]}... after "
                            f"enqueue_message failure: {cleanup_err}"
                        )
                await self._job_queue_service.complete_job(
                    started_job.job_id,
                    demand_state=DemandState.FAILED,
                    error=str(e),
                )
                return

            logger.info(
                f"Observer: triggered next job {started_job.job_id[:8]}... "
                f"for project {job.project_id[:8]}..."
            )
        except Exception as e:
            logger.warning(
                f"Failed to trigger next job for project "
                f"{job.project_id[:8]}...: {e}"
            )
