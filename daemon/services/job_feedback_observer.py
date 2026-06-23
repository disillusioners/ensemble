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
- **Phase 2 (DependencyBus)**: terminal transitions for parents with pending
  children are driven by ``_retrigger_parent_finalize`` (bus callback), NOT by
  the lifecycle event handler. The lifecycle handler only emits ``in_progress``
  notifications for partial completions; terminal transitions happen via the
  authoritative bus callback (no TOCTOU window — eliminates Race #1).
- **Phase 3 (Cascade Unification)**: terminal transitions now perform the FULL
  instance-side fan-out (status update, CompletionRegistry signal, lifecycle
  event publish, SSE status_change). Without this, instances stay in RUNNING
  while their jobs show COMPLETED — breaking ``invoke_agent_and_wait()`` callers
  and orphan-job detection. Mirrors the inline cascade in ``child_reports.py``
  and ``error_reporting.py`` on the bus path.

Architecture:
  - ``_retrigger_parent_finalize(parent_id, terminal_status)`` is the bus
    callback wired in via ``_bus_emit_terminal`` FollowUp fan-out. It is the
    SOLE path for terminal transitions when a parent has pending children
    tracked by the bus.
  - ``_process_event`` emits ``in_progress`` notifications when a child completes
    but other responses are still pending. When no children are still pending
    (none / already resolved), the handler falls through to the shared
    terminal transition (same as the bus-singleton-missing path below).
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
from daemon.repositories.job_queue import JobItem, JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobLock
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


# Terminal instance statuses — instance is no longer active. Mirrors the
# ``_TERMINAL_INSTANCE_STATUSES`` set in ``daemon.services.job_recovery_service``
# (kept local to avoid a hard import cycle through the recovery service).
_TERMINAL_INSTANCE_STATUSES: frozenset[str] = frozenset({
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
    InstanceStatus.FAILED.value,
})


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

    def _is_dependency_bus_enabled(self) -> bool:
        """Read the ``use_dependency_bus`` flag from the JobSystemConfig.

        Defensive ``getattr`` chain mirrors the sibling helper in
        ``ChildReportsService._is_dependency_bus_enabled`` so test
        mocks that bypass ``InstanceManager.__init__`` (e.g.
        ``MagicMock()`` without explicit ``config``) don't crash.
        The default is False (bus disabled — flag slated for removal
        in Phase 8 cleanup), matching the config field's default.

        Returns:
            True if the operator has enabled the DB-backed
            DependencyBus completion-delivery path; False otherwise.
        """
        return bool(
            getattr(self._config, "use_dependency_bus", False)
        )

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
        session bus gate inside ``WriteGuardSession``, a parent
        ``cm.is_complete`` check, etc.). The **authoritative bus
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
        impossible there) to consult the bus under
        ``use_dependency_bus=ON``. When the bus flag is ON, the
        CM's in-memory pending set is starved (send_message skips
        ``cm.register_message_send``), so the CM-side gates
        (``cm.get_pending_count`` / ``cm.is_complete``) return 0
        / True for parents tracked via the bus — and the job would
        be finalized prematurely while children tracked via the
        bus are still running. The bus DB is the authoritative
        source of pending-children truth on the bus path, and
        this gate MUST consult it to prevent premature job
        finalization.

        Fallback semantics: bus singleton missing or flag OFF → returns 0.
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
            not wired, the flag is OFF, or the DB query fails.
        """
        from daemon.services.dependency_bus import get_dependency_bus

        bus = get_dependency_bus()
        if bus is None:
            return 0

        # Mirror the W1-fix defensive flag check from the call
        # sites in ``child_reports._emit_terminal_via_bus`` callers:
        # the flag must be ON for the bus to be the source of
        # truth. If the operator flipped the flag OFF mid-flight,
        # treat the bus as inert (the CM path is the fallback).
        if not self._is_dependency_bus_enabled():
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
        self, instance_id: str
    ) -> JobItem | None:
        """Get the PROCESSING job for an instance, with stale-job defense.

        Shared lookup used by both the bus callback path
        (``_retrigger_parent_finalize``) and the lifecycle-event path
        (:meth:`_process_event`). Both paths previously duplicated the
        ``get_by_instance → status check → optional re-query`` dance inline;
        that asymmetry left :meth:`_process_event` unprotected by the
        defense-in-depth re-query that the bus callback got in
        ``fix/revive-stale-job-lookup`` (commit b1218739).

        Behavior:
          1. First lookup via the job queue service wrapper (functionally
             identical to ``await asyncio.to_thread(self._job_repo.get_by_instance,
             instance_id)`` — the service layer just adds the
             ``asyncio.to_thread`` indirection required to keep sync DB calls
             off the event loop).
          2. If the returned row is already PROCESSING, return it directly —
             the happy path skips the re-query entirely.
          3. Otherwise (stale CANCELLED / COMPLETED / FAILED row from a prior
             cycle), re-query the repository for the active (PENDING or
             PROCESSING) row. Only a PROCESSING row is considered "safe to
             finalize" — a PENDING row would fail ``atomic_transition`` from
             PROCESSING and is treated as "no active job".
          4. Returns ``None`` when no PROCESSING job exists for the instance.
             Callers use this to skip finalization silently.

        The re-query is defense-in-depth only: in production, the
        ``ORDER BY created_at DESC, job_id`` ordering in
        :meth:`JobRepository.get_by_instance` (Fix 1 of the
        fix/revive-stale-job-lookup branch) already returns the active row in
        the terminate→revive scenario, since ``JobItem.created_at`` is set
        ONCE at row insert and NEVER updated by transitions — the revived
        PROCESSING job ALWAYS has a newer ``created_at`` than the stale
        CANCELLED job left behind. The re-query here future-proofs against
        manual DB operations or synthetic test mocks where that ordering may
        not hold.

        Args:
            instance_id: The instance ID to look up.

        Returns:
            The active PROCESSING :class:`JobItem`, or ``None`` if no such
            job exists.
        """
        # First lookup via the existing service wrapper. Equivalent to
        # ``await asyncio.to_thread(self._job_repo.get_by_instance, instance_id)``
        # — preserved as the service call so the existing test mock surface
        # (``mock_jqs.get_job_by_instance``) keeps working.
        job = await self._job_queue_service.get_job_by_instance(instance_id)
        if job is None:
            return None
        if job.status == JobStatus.PROCESSING.value:
            return job
        # Defense-in-depth: future-proofing against manual DB operations or
        # synthetic test mocks where created_at ordering may not reflect the
        # active job. The real terminate→revive scenario is already covered
        # by the ``ORDER BY created_at DESC, job_id`` ordering in
        # JobRepository.get_by_instance — created_at is immutable post-insert,
        # so the revived PROCESSING row always sorts after the stale
        # CANCELLED row.
        active_job = await asyncio.to_thread(
            self._job_repo.get_active_by_instance, instance_id
        )
        if (
            active_job is not None
            and active_job.status == JobStatus.PROCESSING.value
        ):
            return active_job
        return None

    async def handle_correlation_complete(
        self, parent_id: str, terminal_status: str
    ) -> None:
        """Called by CorrelationManager when ALL message responses are resolved.

        This is the SOLE terminal-transition path for a parent that has children
        tracked by CM. The CM only invokes this callback when its per-parent
        pending set reaches zero (inside its lock; the callback itself runs
        AFTER the lock is released — W1 fix).

        Phase 2: the CM's pending count is authoritative and updated
        atomically under its per-parent lock, so there is no TOCTOU
        window between "is everything resolved?" and "transition the
        job to terminal" (Race #1 is eliminated).

        The callback contract:
          * ``parent_id`` — the parent instance whose children have all responded.
          * ``terminal_status`` — ``"completed"`` (all children responded cleanly)
            or ``"error"`` (at least one child errored; conservative rule from
            :class:`CorrelationManager._determine_terminal_status`).

        **N4 constraint**: this method runs outside the per-parent lock. It MUST
        NOT call any CorrelationManager method for the same ``parent_id`` —
        re-entering CM would deadlock. All terminal logic below is self-contained
        and touches the DB / job queue / lock repo only, never CM.

        Args:
            parent_id: The parent instance ID whose correlations just completed.
            terminal_status: ``"completed"`` or ``"error"``.
        """
        job = await self._get_processing_job_for_instance(parent_id)
        if job is None:
            logger.info(
                f"CM callback: no active PROCESSING job for instance "
                f"{parent_id[:8]}..., skipping"
            )
            return

        await self._finalize_job(job, parent_id, terminal_status, error=None)

    async def _admit_via_worker_pool(self, job) -> None:
        """C6: Route a MESSAGE-type JobItem through the WorkerPool.

        Phase C (decouple-architecture) replaces the legacy JobQueue
        execution path (``MessageJobHandler.handle``) with the unified
        observer → Task → WorkerPool path. When JobProcessor admits a
        ``job_type='message'`` job, it calls this method. The method:

          1. Extracts ``message_id`` from ``job.job_metadata`` (set by
             :meth:`InstanceMessagingService.enqueue_message` with
             ``dispatch_path="jobqueue"``).
          2. Creates a ``Task`` row pointing at the same ``message_id``
             (same pattern as :meth:`InstanceMessagingService.enqueue_message`).
          3. Calls ``worker_pool.notify_work()`` to wake a worker.
          4. The ``JobItem`` is already ``PROCESSING`` (``start_job``
             transitioned it in :class:`JobProcessor`); the observer's
             existing event subscription (instance_lifecycle →
             :meth:`_process_event` → :meth:`_finalize_job`) handles the
             terminal transition when the Task completes.

        The job is NOT marked ``FAILED`` here on error paths: those
        exceptions propagate up to ``JobProcessor._process_next_job``,
        whose ``except Exception`` handler calls ``complete_job(FAILED)``
        and ``_cleanup_in_progress_tracking`` so the per-queue lock is
        released. Silently returning on these failure modes (the
        pre-fix behaviour) wedges the JobItem in ``PROCESSING`` because
        the caller ``continue``s without doing any cleanup — see the
        regression test ``test_16_*`` in
        ``tests/test_unified_dispatcher_shadow.py``.

        Args:
            job: The ``JobItem`` in ``PROCESSING`` status. Must have
                ``job.job_metadata['message_id']`` and ``job.instance_id``
                set; otherwise a ``RuntimeError`` is raised so the
                caller's failure handler can mark the job FAILED.
        """
        instance_id = job.instance_id
        message_id: str | None = None
        if job.job_metadata:
            message_id = job.job_metadata.get("message_id")
        if not message_id:
            logger.error(
                f"Observer._admit_via_worker_pool: MESSAGE job "
                f"{job.job_id[:8]}... missing message_id in job_metadata; "
                f"caller should mark FAILED"
            )
            raise RuntimeError(
                f"Cannot admit job {job.job_id}: missing message_id in job_metadata"
            )
        if not instance_id:
            logger.error(
                f"Observer._admit_via_worker_pool: MESSAGE job "
                f"{job.job_id[:8]}... missing instance_id; "
                f"caller should mark FAILED"
            )
            raise RuntimeError(
                f"Cannot admit job {job.job_id}: missing instance_id"
            )

        # Resolve the task repository from the InstanceManager facade.
        # The manager wires ``_task_repo`` in ``setup_worker_pool``;
        # ``getattr`` keeps the observer safe in tests that bypass
        # full manager initialization.
        task_repo = getattr(self._instance_manager, "_task_repo", None)
        if task_repo is None:
            logger.error(
                f"Observer._admit_via_worker_pool: InstanceManager has no "
                f"_task_repo; cannot create Task for MESSAGE job "
                f"{job.job_id[:8]}..."
            )
            raise RuntimeError(
                f"Cannot admit job {job.job_id}: task_repo is None "
                f"(repository unavailable)"
            )

        # Create the Task row. ``TaskRepository.create`` opens its own
        # ``SQLModelSession`` and commits — we wrap in ``asyncio.to_thread``
        # so the sync DB call does not block the event loop (same pattern
        # as ``_prepare_enqueued_message`` in instance_messaging.py).
        try:
            task = await asyncio.to_thread(
                task_repo.create,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
            )
        except Exception as e:
            logger.error(
                f"Observer._admit_via_worker_pool: failed to create Task "
                f"for MESSAGE job {job.job_id[:8]}... "
                f"instance={instance_id[:8]}... message_id={message_id[:8]}...: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Cannot admit job {job.job_id}: Task creation failed"
            ) from e

        # Notify the WorkerPool so an idle worker wakes immediately
        # rather than waiting for the next poll (default 0.5s).
        # ``notify_work`` is thread-safe; it can be called from any
        # coroutine (the condition variable handles cross-thread
        # signaling). Defensive ``getattr`` for tests that mock
        # the manager without a real worker pool.
        worker_pool = getattr(self._instance_manager, "_worker_pool", None)
        if worker_pool is not None:
            try:
                worker_pool.notify_work()
            except Exception as e:
                logger.warning(
                    f"Observer._admit_via_worker_pool: worker_pool.notify_work() "
                    f"failed for MESSAGE job {job.job_id[:8]}... (non-fatal): {e}"
                )

        logger.info(
            f"Observer._admit_via_worker_pool: admitted MESSAGE job "
            f"{job.job_id[:8]}... instance={instance_id[:8]}... "
            f"message_id={message_id[:8]}... task_id={task.id} "
            f"dispatch_path=jobqueue_local"
        )

    async def _process_event(self, event: dict) -> None:
        """Process a single instance_lifecycle event.

        Phase 2: this method is the ``in_progress`` notification path ONLY.
        Terminal transitions for parents with pending children are handled by
        the DependencyBus (``_retrigger_parent_finalize`` callback). This
        handler still drives terminal transitions in two cases:

          1. **No pending watchers in the bus** (``bus_pending == 0``) — the
             instance either never spawned children, or all children already
             resolved (bus callback already fired, or is about to fire). The
             idempotency guard in ``_finalize_job`` (``job.status !=
             PROCESSING``) prevents double-completion.

          2. **Bus singleton missing** (``get_dependency_bus()`` returns
             ``None``) — invalid state, the in-process check falls through
             and the in-session gate raises a hard error below.

        Race #1 is eliminated because when ``bus_pending > 0``, we do NOT do
        a terminal transition here — we defer to the authoritative bus
        callback.

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
        job = await self._get_processing_job_for_instance(instance_id)
        if job is None:
            return  # No active PROCESSING job for this instance

        # Phase 2: decide between in_progress and terminal based on bus state.
        if status in (InstanceStatus.COMPLETED.value, InstanceStatus.ERROR.value):
            bus = get_dependency_bus()
            if bus is not None:
                # Bus is active and authoritative (the bus is the
                # SOLE completion authority; CM was removed).
                # ASYNC context — use the awaitable variant.
                bus_pending = await bus.count_pending_for_target(instance_id)
                if bus_pending > 0:
                    # Children still resolving → emit in_progress, defer terminal
                    # to the bus callback (``_retrigger_parent_finalize``).
                    # This is the Race #1 fix: no LLM fetch, no TOCTOU —
                    # we simply notify watchers and wait for the bus.
                    await self._emit_in_progress(job, instance_id)
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
        await self._finalize_job(job, instance_id, status, error=error)

    async def _emit_in_progress(
        self, job, instance_id: str
    ) -> None:
        """Emit an ``in_progress`` watcher notification.

        Best-effort: failures are logged at WARNING and swallowed. The terminal
        transition will still fire via CM callback (or the shared terminal path)
        regardless of whether this notification succeeds.

        Args:
            job: The JobItem for the parent instance.
            instance_id: The parent instance ID (for LLM checkpoint fetch).
        """
        try:
            progress_text = (
                await self._instance_manager._get_last_assistant_message_raw(
                    instance_id
                )
            )
            await self._job_queue_service.notify_watchers(
                job.job_id,
                status="in_progress",
                progress=progress_text,
            )
        except Exception as e:
            logger.warning(
                f"Observer: failed to emit in_progress notification for "
                f"instance {instance_id[:8]}...: {e}"
            )

    async def _finalize_job(
        self,
        job,
        instance_id: str,
        terminal_status: str,
        error: str | None = None,
    ) -> None:
        """Shared terminal transition path.

        Used by both:
          * The bus callback (``_retrigger_parent_finalize``, authoritative).
          * :meth:`_process_event` (lifecycle handler, when the bus has
            no pending entry for the instance).

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

        Args:
            job: The JobItem to transition (must be in PROCESSING).
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
                async with await bus._get_parent_lock(instance_id):
                    db_result = await asyncio.to_thread(
                        self._finalize_job_db_sync,
                        job.job_id,
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
                    job.job_id,
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
            if (
                bus is not None
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
                        f"{job.job_id[:8]} from COMPLETED to PROCESSING."
                    )
                    rearmed = False
                    try:
                        await asyncio.to_thread(
                            self._job_repo.atomic_transition,
                            job_id=job.job_id,
                            from_status=JobStatus.COMPLETED.value,
                            to_status=JobStatus.PROCESSING.value,
                        )
                        rearmed = True
                    except InvalidTransitionError as ite:
                        # The job was transitioned by another actor
                        # (e.g. terminate_instance, a manual admin
                        # operation) between our commit and this re-arm.
                        # Log and continue — the post-commit outbox below
                        # is still valid for whatever state the job is
                        # actually in.
                        logger.info(
                            f"Observer: re-arm skipped — job "
                            f"{job.job_id[:8]} no longer COMPLETED "
                            f"(current: {ite.from_status} → "
                            f"{ite.to_status})"
                        )
                    except Exception as rearm_exc:
                        # Defensive: never let a re-arm failure crash the
                        # observer. The job is already in COMPLETED — the
                        # late child may be orphaned, which is strictly
                        # better than a partially-finalized state.
                        logger.warning(
                            f"Observer: re-arm failed for job "
                            f"{job.job_id[:8]} "
                            f"(COMPLETED → PROCESSING): {rearm_exc}. "
                            f"The late child may be orphaned."
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
                    f"{job.job_id[:8]}... instance {instance_id[:8]}... — "
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
            terminal_watchers: list[Any] = []
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
                        watcher_repo.get_watchers_for_job, job.job_id
                    )
            except Exception as e:
                # Defensive: never let a watcher-repo failure abort the
                # post-commit outbox. Log at WARNING and continue with
                # an empty list (the watcher notifications already fired;
                # we just won't drive CM resolution for them).
                logger.warning(
                    f"Observer: pre-fetch watchers failed for job "
                    f"{job.job_id[:8]}...: {e}"
                )

            # notify_watchers (terminal notification) — fires AFTER commit so
            # watchers see a consistent state.
            if db_result.terminal_status == InstanceStatus.COMPLETED.value:
                try:
                    await self._job_queue_service.notify_watchers(
                        job.job_id, "completed"
                    )
                except Exception as e:
                    logger.warning(
                        f"Observer: notify_watchers failed for job "
                        f"{job.job_id[:8]}...: {e}"
                    )
            elif db_result.terminal_status == InstanceStatus.ERROR.value:
                try:
                    await self._job_queue_service.notify_watchers(
                        job.job_id, "failed", db_result.error_message
                    )
                except Exception as e:
                    logger.warning(
                        f"Observer: notify_watchers failed for job "
                        f"{job.job_id[:8]}...: {e}"
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
                    f"resolved for job {job.job_id[:8]}... "
                    f"(terminal_status={db_result.terminal_status}, "
                    f"cm_status=removed-phase5)"
                )

            # ─── Instance-side post-commit (SSE / CompletionRegistry / lifecycle) ───
            # Only fire if the instance was NOT already terminal when we wrote it.
            # If ``instance_was_terminal=True``, the side effects were already fired
            # by whoever set the instance terminal first (CM-disabled inline cascade
            # or a prior callback). If the instance row was missing, there is no
            # consumer to notify.
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
            await self._trigger_next_job(job)

            logger.info(
                f"Observer: finalized job {job.job_id[:8]}... "
                f"status={db_result.terminal_status} for instance {instance_id[:8]}... "
                f"(released {db_result.locks_released} lock(s))"
            )

        except InvalidTransitionError as e:
            # Race condition: another actor (e.g., terminate_instance, a
            # previous bus callback) already transitioned the job. Expected —
            # skip silently. This is the primary idempotency mechanism.
            logger.debug(
                f"Race condition: job {job.job_id[:8]}... already transitioned "
                f"(current: {e.from_status} -> {e.to_status}), skipping"
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
            #   1. Bus-callback path — when invoked from the bus
            #      ``_retrigger_parent_finalize``, the bus's own
            #      ``except Exception`` handler catches the RuntimeError,
            #      logs it at EXCEPTION level, and rolls back the watcher
            #      state so a subsequent retry can recover the completion.
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
                f"Failed to finalize job {job.job_id[:8]}... "
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
            try:
                await asyncio.to_thread(
                    self._job_repo.atomic_transition,
                    job_id=job.job_id,
                    from_status=JobStatus.PROCESSING.value,
                    to_status=JobStatus.FAILED.value,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    error_message=f"Job finalization failed: {e}",
                )
                logger.info(
                    f"Observer: fail-safe transitioned job "
                    f"{job.job_id[:8]}... to FAILED after finalization error"
                )
            except Exception:
                pass  # atomic_transition itself failed — nothing more we can do
            return

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
             observer via ``_process_event``, but the ``job.status != PROCESSING``
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
        # ``_process_event`` re-entry is caught by the ``job.status !=
        # PROCESSING`` idempotency guard (the job is already terminal at
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
        job_id: str,
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
          * CM ``get_pending_count > 0`` — new pending correlations appeared
            during the callback (C1 abort). The CM will fire the callback
            again when those resolve.
          * Job not found (deleted concurrently).

        Raises ``InvalidTransitionError`` for the concurrent-transition
        case (job status no longer PROCESSING — race with another actor).
        The caller logs at DEBUG and returns silently (idempotency).

        Any other exception propagates to the caller, which fires the W3
        fail-safe transition.

        Args:
            job_id: The job to transition.
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
            to_status = JobStatus.COMPLETED.value
        elif terminal_status == InstanceStatus.ERROR.value:
            to_status = JobStatus.FAILED.value
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
        # (``_bus_count_pending_for_target_sync``) covers both
        # ``use_dependency_bus=ON`` and OFF paths (the helper returns
        # 0 when the bus singleton is None — bus singleton missing is a
        # hard error). If the gate below is moved
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
                f"(use_dependency_bus=ON, CM starved on bus path), "
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
            # ─── Bus pending-children gate (Phase 5 — CM removed) ───
            # Phase 5: the CM's ``is_complete()`` / ``get_pending_count()``
            # are REMOVED. The bus is the SOLE completion authority.
            # The in-session bus gate (below, immediately before the
            # UPDATE) is the authoritative check; this early-exit
            # ``_bus_count_pending_for_target_sync`` is a defense-
            # in-depth pre-check that catches obvious "children
            # still running" cases before the function does more
            # work. Both share the same DB-backed source of truth
            # (``dependency_watchers`` PENDING rows).
            if self._bus_count_pending_for_target_sync(instance_id) > 0:
                _bus_pending_gate = self._bus_count_pending_for_target_sync(instance_id)
                logger.info(
                    f"Observer: aborting terminal transition for "
                    f"{instance_id[:8]}... — bus pending="
                    f"{_bus_pending_gate} > 0 (orchestrator has active "
                    f"children, deferring finalization)"
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
            # Defensive wiring check: only consult the bus when
            # BOTH the flag is ON AND the bus singleton is
            # wired. Mirrors the original
            # ``_bus_count_pending_for_target_sync`` helper
            # semantics — when the singleton is None (testing,
            # missing init, config drift), the gate is dormant
            # so we don't defer a finalization that should
            # proceed. Without this guard, a config that leaves
            # the flag ON without wiring the bus singleton
            # would still execute the inline COUNT against an
            # empty table — usually harmless, but in degraded
            # states (mock MagicMock truthiness, partial
            # migrations) it could defer a finalization that
            # should proceed.
            from daemon.services.dependency_bus import (
                get_dependency_bus as _get_bus_for_gate,
            )
            if (
                self._is_dependency_bus_enabled()
                and _get_bus_for_gate() is not None
            ):
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
                        f"{instance_id[:8]}... — CM says complete but "
                        f"bus has {_bus_pending} PENDING watchers "
                        f"(use_dependency_bus=ON, in-session gate), "
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
            update_values: dict[str, Any] = {
                "status": to_status,
                "completed_at": now,
            }
            if terminal_status == InstanceStatus.COMPLETED.value:
                summary = result_summary or "Job completed (no agent response captured)"
                update_values["result_summary"] = summary
            else:
                update_values["error_message"] = error_message or "Unknown error"

            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.status == JobStatus.PROCESSING.value)
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
                    from_status=existing.status,
                    to_status=to_status,
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
            f"Observer: finalized job {job_id[:8]}... status={terminal_status} "
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
                await self._instance_manager.enqueue_message(
                    instance_id=instance_id,
                    message=started_job.message,
                    source=started_job.source,
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
