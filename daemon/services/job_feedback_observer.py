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
- **Phase 2 (CorrelationManager)**: terminal transitions for parents with pending
  children are driven by ``handle_correlation_complete`` (CM callback), NOT by
  the lifecycle event handler. The lifecycle handler only emits ``in_progress``
  notifications for partial completions; terminal transitions happen via the
  authoritative CM callback (no TOCTOU window — eliminates Race #1).
- **Phase 3 (Cascade Unification)**: terminal transitions now perform the FULL
  instance-side fan-out (status update, CompletionRegistry signal, lifecycle
  event publish, SSE status_change). Without this, instances stay in RUNNING
  while their jobs show COMPLETED — breaking ``invoke_agent_and_wait()`` callers
  and orphan-job detection. Mirrors the inline cascade in ``child_reports.py``
  and ``error_reporting.py`` (CM-disabled path) on the CM-active path.

Architecture (Phase 2):
  - ``handle_correlation_complete(parent_id, terminal_status)`` is registered as
    ``CorrelationManager.completion_callback``. It is the SOLE path for terminal
    transitions when a parent has pending children tracked by CM.
  - ``_process_event`` emits ``in_progress`` notifications when a child completes
    but other responses are still pending (CM authoritative). When CM has no
    pending entry (no children / already resolved), the handler falls through to
    the shared terminal transition (same as the graceful-degradation path).
  - **Graceful degradation**: when ``get_correlation_manager()`` returns ``None``
    (CM disabled / not wired), the observer falls back to the legacy
    ``waiting_for``-based check. This keeps the system safe even if CM is broken.
  - **N4 constraint**: ``handle_correlation_complete`` runs AFTER the per-parent
    lock is released (W1 fix). It must NOT call any CM method for the same
    parent_id — would deadlock. If cascade work is needed, schedule via
    ``asyncio.create_task()`` (not needed in Phase 2 — terminal logic is
    self-contained).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlmodel import Session, select, update as sqlmodel_update
from sqlalchemy import text as _sa_text

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobLock
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.correlation_manager import (
    get_correlation_manager,
    notify_corr_rearm,
    notify_corr_resolve_job,
)
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


# Bounded defer counter for the waiting_for gate's exception fallback
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
    * ``gate_deferred`` — True ONLY when the waiting_for gate returned
      ``skip=True`` (either the ``waiting_for > 0`` row-lock re-check or
      the ``SELECT ... FOR UPDATE`` exception fallback). The caller scopes
      ``notify_corr_rearm`` to this flag so the other ``skip`` paths
      (unknown terminal_status, CM re-check aborted) don't create spurious
      empty ``_pending[parent_id]`` entries — C2-N1 fix.
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

        Shared lookup used by both the CorrelationManager callback path
        (:meth:`handle_correlation_complete`) and the legacy lifecycle-event
        path (:meth:`_process_event`). Both paths previously duplicated the
        ``get_by_instance → status check → optional re-query`` dance inline;
        that asymmetry left :meth:`_process_event` unprotected by the
        defense-in-depth re-query that the CM callback got in
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

        Phase 2: replaces the old ``waiting_for``-based terminal check. Because
        the CM's pending count is authoritative and updated atomically under
        its per-parent lock, there is no TOCTOU window between "is everything
        resolved?" and "transition the job to terminal" (Race #1 is eliminated).

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

    async def _process_event(self, event: dict) -> None:
        """Process a single instance_lifecycle event.

        Phase 2: this method is the ``in_progress`` notification path ONLY.
        Terminal transitions for parents with pending children are handled by
        :meth:`handle_correlation_complete` (CM callback). This handler still
        drives terminal transitions in two cases:

          1. **No pending correlations in CM** (``cm_pending == 0``) — the
             instance either never spawned children, or all children already
             resolved (CM callback already fired, or is about to fire). The
             idempotency guard in ``_finalize_job`` (``job.status !=
             PROCESSING``) prevents double-completion.

          2. **Graceful degradation — CM is not wired** (``get_correlation_manager()``
             returns ``None``). Falls back to the legacy ``waiting_for``-based
             check from the DB. This keeps the system safe even if CM is broken
             or disabled.

        Race #1 is eliminated because when ``cm_pending > 0``, we do NOT do a
        terminal transition here — we defer to the authoritative CM callback.

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
        # handle_correlation_complete already had).
        job = await self._get_processing_job_for_instance(instance_id)
        if job is None:
            return  # No active PROCESSING job for this instance

        # Phase 2: decide between in_progress and terminal based on CM state.
        if status in (InstanceStatus.COMPLETED.value, InstanceStatus.ERROR.value):
            cm = get_correlation_manager()
            if cm is not None:
                # CM is active and authoritative.
                cm_pending = cm.get_pending_count(instance_id)
                if cm_pending > 0:
                    # Children still resolving → emit in_progress, defer terminal
                    # to the CM callback (handle_correlation_complete). This is
                    # the Race #1 fix: no LLM fetch, no TOCTOU — we simply notify
                    # watchers and wait for CM.
                    await self._emit_in_progress(
                        job, instance_id, pending_count=cm_pending
                    )
                    return
                # cm_pending == 0: no pending correlations in CM.
                # Fall through to the shared terminal transition. This handles:
                #   a) Untracked parents (no children) — safe, no race possible.
                #   b) Tracked parents whose callback already fired — idempotency
                #      guard in _finalize_job catches the no-op.
                #   c) Race window where callback is about to fire — first writer
                #      wins via atomic_transition; the callback's idempotency
                #      guard catches the second.
            else:
                # Graceful degradation: CM is None / disabled.
                # Use the legacy waiting_for check from the DB. This preserves
                # the pre-Phase-2 behavior when CM is not wired up.
                #
                # Phase 4: this read is INTENTIONALLY retained. Control-flow
                # READS of ``waiting_for`` were deprecated in favor of
                # ``cm.get_pending_count()`` (see the ``if cm is not None``
                # branch above), but the rebuild cache (ADR-011) must remain
                # queryable for ``rebuild_from_db()``. This branch is the
                # legitimate fallback when CM is absent.
                try:
                    instance_meta = await asyncio.to_thread(
                        self._instance_manager._instance_repository.get,
                        instance_id,
                    )
                    if instance_meta is not None:
                        wf = getattr(instance_meta, "waiting_for", None) or 0
                        if wf > 0:
                            logger.info(
                                f"Observer (CM-disabled): instance "
                                f"{instance_id[:8]}... emitted '{status}' "
                                f"but has {wf} pending child agent(s); "
                                f"deferring job {job.job_id[:8]}... completion"
                            )
                            await self._emit_in_progress(
                                job, instance_id, pending_count=wf
                            )
                            return
                except Exception as e:
                    logger.warning(
                        f"Observer: failed to check waiting_for for "
                        f"instance {instance_id[:8]}...: {e}"
                    )
                    # Fall through to terminal — better to complete than to
                    # silently drop the event.

        # Shared terminal transition path. Reached when:
        #   - CM is None (graceful degradation) AND waiting_for == 0, or
        #   - CM is active AND cm_pending == 0 (no children / already resolved).
        await self._finalize_job(job, instance_id, status, error=error)

    async def _emit_in_progress(
        self, job, instance_id: str, pending_count: int
    ) -> None:
        """Emit an ``in_progress`` watcher notification.

        Best-effort: failures are logged at WARNING and swallowed. The terminal
        transition will still fire via CM callback (or the shared terminal path)
        regardless of whether this notification succeeds.

        Args:
            job: The JobItem for the parent instance.
            instance_id: The parent instance ID (for LLM checkpoint fetch).
            pending_count: Number of children still resolving (for the
                ``waiting_for`` field in the notification).
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
                waiting_for=pending_count,
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
          * :meth:`handle_correlation_complete` (CM callback, authoritative).
          * :meth:`_process_event` (lifecycle handler, when CM is disabled or
            has no pending entry for the instance).

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
        # ``asyncio.to_thread`` call in the CorrelationManager's per-parent
        # ``asyncio.Lock`` when CM is active. The lock is held on the EVENT
        # LOOP for the entire duration of the worker-thread sync helper,
        # blocking ``register_message_send`` (which also acquires
        # ``cm._get_lock(parent_id)``) from running on the loop and
        # registering a new pending child between the pre-check at line ~509
        # and the commit inside ``_finalize_job_db_sync``.
        #
        # Why this works (no deadlock risk):
        #   * ``asyncio.Lock`` is event-loop-bound — the worker thread that
        #     ``asyncio.to_thread`` runs in NEVER acquires it; it only runs
        #     the sync SQLAlchemy code while the GIL is released during I/O.
        #   * The lock serializes coroutines on the loop, not threads.
        #   * When CM is None (legacy path / not initialized), no lock is
        #     needed — the legacy ``SELECT ... FOR UPDATE`` row-lock gate in
        #     ``_finalize_job_db_sync`` provides the same protection.
        #
        # Orphan-race fix (2026-06-20, post-commit re-check): capture the
        # CM generation counter BEFORE acquiring the lock. ``register_message_send``
        # bumps the counter OUTSIDE its per-parent lock acquisition, so the
        # bump is visible to a reader that holds the lock. After the lock
        # is released (and the job is committed to COMPLETED), we read the
        # counter again. If it changed, a register was in-flight during
        # finalization — we must re-arm the job (COMPLETED → PROCESSING)
        # so the late child's eventual resolve can find a PROCESSING job.
        # Without this, the late child is orphaned: its resolve callback
        # finds a COMPLETED job and silently skips.
        try:
            cm = get_correlation_manager()
            # Pre-commit generation snapshot — read BEFORE the lock so we
            # can detect any bump that happens during the critical section
            # (lock acquire → to_thread → commit → lock release).
            pre_gen = cm.get_generation(instance_id) if cm is not None else 0
            if cm is not None:
                # CM is active — hold the per-parent lock across the to_thread
                # call so concurrent ``register_message_send`` coroutines must
                # wait until finalization has committed.
                async with cm._get_lock(instance_id):
                    db_result = await asyncio.to_thread(
                        self._finalize_job_db_sync,
                        job.job_id,
                        instance_id,
                        terminal_status,
                        result_summary,
                        error_message,
                    )
            else:
                # Legacy path / CM not wired — no lock needed; the FOR UPDATE
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
            # schedule a ``notify_corr_rearm`` for the wave 2 case.
            if (
                cm is not None
                and not db_result.skip
                and not db_result.gate_deferred
            ):
                post_gen = cm.get_generation(instance_id)
                if post_gen > pre_gen:
                    # A register_message_send bumped the generation during
                    # finalization. The register is either blocked on the
                    # lock (just released) or already enqueued on the
                    # event loop and about to acquire the lock. Either
                    # way, a new child is on its way into CM. Re-arm the
                    # job so the late child's resolve can find it.
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

            # ─── Handle gate-deferred skip (waiting_for > 0 or gate SELECT failed) ───
            # C2-N1 fix: ``notify_corr_rearm`` fires ONLY on gate-deferred
            # paths, NOT on every ``skip=True``. The other skip paths
            # (unknown terminal_status, CM re-check aborted) must NOT
            # create spurious empty ``_pending[parent_id]`` entries. The
            # gate itself (``SELECT ... FOR UPDATE`` row lock + waiting_for
            # counter) is the authoritative signal that children may still
            # be running — only there do we re-arm CM for wave 2.
            if db_result.gate_deferred:
                # C2-PartA fix: re-arm CM ``_pending[parent_id]`` so wave 2
                # children whose :func:`notify_corr_resolve` calls arrive
                # BEFORE their :func:`notify_corr_register` calls (e.g. wave
                # 2 spawned via ``job_continue`` / ``watch_job``) still find
                # the parent in CM and don't silently no-op. Without this,
                # multi-wave scenarios where the wave 2 register sequence is
                # not via ``send_message`` can wedge the job in PROCESSING
                # forever — ``resolve_response`` returns ``False`` for
                # missing parents, so the CM callback never re-fires.
                #
                # N4 compliance: ``_finalize_job`` runs as the
                # ``completion_callback`` when invoked from
                # :meth:`handle_correlation_complete`, and the N4 constraint
                # forbids re-entering CM for the same ``parent_id`` from
                # inside the callback. We therefore schedule via
                # ``asyncio.create_task`` rather than awaiting directly —
                # the re-arm acquires the per-parent lock once it actually
                # runs, well after ``_finalize_job`` has returned and the
                # original callback context has unwound. When called from
                # :meth:`_process_event` (the lifecycle-event fall-through,
                # no N4 concern) the same ``create_task`` is harmless —
                # the re-arm is just deferred by one event-loop iteration.
                asyncio.create_task(notify_corr_rearm(instance_id))
                logger.debug(
                    f"Observer: _finalize_job gate-deferred for job "
                    f"{job.job_id[:8]}... instance {instance_id[:8]}... — "
                    f"scheduled CM rearm_parent for wave 2"
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

            # B4: Resolve watched jobs in CM. For each instance that was
            # watching this job, notify the CM that the job is terminal.
            # The CM removes ``child_job_id`` from ``_pending[parent_id].pending_jobs``
            # and checks ``is_complete()`` — if both ``pending`` and
            # ``pending_jobs`` are empty, the completion callback fires.
            # Status mapping: completed → "responded" (clean terminal);
            # error → "error" (conservative rule, mirrors
            # ``_determine_terminal_status`` for message responses).
            try:
                cm_status = (
                    "responded"
                    if db_result.terminal_status == InstanceStatus.COMPLETED.value
                    else "error"
                )
                for watcher in terminal_watchers:
                    # ``watcher.instance_id`` is the parent (the instance
                    # that called ``watch_job``). This is the parent whose
                    # CM ``pending_jobs`` set tracks this child_job_id.
                    await notify_corr_resolve_job(
                        parent_id=watcher.instance_id,
                        child_job_id=job.job_id,
                        status=cm_status,
                    )
            except Exception as e:
                # A CM failure must NOT affect the post-commit outbox.
                # The watcher notifications have already fired; the
                # worst case is that the parent's ``pending_jobs`` is
                # not drained for this round (recoverable via the next
                # ``rebuild_from_db`` or via the wave-2 ``notify_corr_rearm``
                # path on the next ``_finalize_job`` invocation).
                logger.warning(
                    f"Observer: notify_corr_resolve_job failed for job "
                    f"{job.job_id[:8]}...: {e}"
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
            # previous CM callback) already transitioned the job. Expected —
            # skip silently. This is the primary idempotency mechanism.
            logger.debug(
                f"Race condition: job {job.job_id[:8]}... already transitioned "
                f"(current: {e.from_status} -> {e.to_status}), skipping"
            )
            return
        except RuntimeError:
            # W2 fix (2026-06-20): A8 hard errors (CM is None under
            # ``USE_LEGACY_WAITING_FOR_CASCADE=OFF`` — raised in
            # ``_finalize_job_db_sync`` and at the two A8 call sites in
            # ``child_reports.py``) propagate as configuration errors.
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
            #   1. CM-callback path — when invoked from
            #      :meth:`CorrelationManager.handle_correlation_complete` (the
            #      completion_callback at correlation_manager.py:387), the
            #      CM's own ``except Exception`` (H7 restoration handler at
            #      correlation_manager.py:388) catches the RuntimeError, logs
            #      it at EXCEPTION level, and restores ``_pending[parent_id]``
            #      so a subsequent retry can recover the completion.
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

        Phase 3 (Cascade Unification) fix. The CM callback path (and the
        lifecycle-event fall-through when ``cm_pending == 0``) transitions
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
        (CM-disabled path) and :class:`ErrorReportingService._send_error_report`
        (CM-disabled path), so the CM-active path is now symmetrical with
        the CM-disabled path.

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

        # ─── C1 TOCTOU re-check (Phase 2 invariant preserved) ───
        # Sync, inside the worker thread, IMMEDIATELY before the UPDATE.
        # No await between this read and the UPDATE below.
        cm = get_correlation_manager()
        if cm is not None:
            cm_pending = cm.get_pending_count(instance_id)
            if cm_pending > 0:
                logger.info(
                    f"Observer: aborting terminal transition for "
                    f"{instance_id[:8]}... — {cm_pending} new pending "
                    f"correlations appeared during callback"
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

        # ─── Single WriteGuardSession for ALL three DB writes ───
        with WriteGuardSession(
            Session(self._instance_manager.engine),
            self._instance_manager.write_guard,
        ) as session:
            # ─── In-session waiting_for gate (premature-completion fix) ───
            # The CM tracks per-message-batch correlations. When its pending
            # set reaches zero it fires the callback — but ``send_message``
            # increments ``waiting_for`` BEFORE registering the CM
            # correlation, creating a window where ``waiting_for > 0`` and
            # ``cm_pending == 0`` simultaneously.
            #
            # ─── USE_LEGACY_WAITING_FOR_CASCADE flag (A7, 2026-06-20) ───────
            # When OFF (default), ``waiting_for`` is NOT incremented by
            # ``send_message`` (A5) and the register-before/increment-after
            # window is structurally closed. A12 register-window proof
            # tests (23/23) confirm it is safe to replace the FOR UPDATE
            # row-lock gate with a CM ``is_complete()`` check here — the
            # CM is authoritative and there is no concurrent writer to
            # race against. The FOR UPDATE row lock was defence-in-depth
            # against the now-closed window.
            #
            # When ON (kill switch / rollback), the legacy M0 path runs
            # unchanged — ``SELECT ... FOR UPDATE`` takes a pessimistic
            # row-level lock (READ COMMITTED isolation) to close the
            # TOCTOU window between the ``waiting_for`` read and the
            # finalization UPDATE below. WriteGuardSession is a
            # Python-level write-pause counter, NOT a database-level
            # lock — on row-lock-supporting dialects another thread
            # could commit ``waiting_for=1`` between a non-locking read
            # and the subsequent UPDATE, re-opening the TOCTOU window
            # this gate exists to close. ``FOR UPDATE`` blocks
            # concurrent writers on this row until our transaction
            # commits or rolls back, eliminating the race.
            #
            # C1 fix (TOCTOU hardening, legacy path only): on
            # row-lock-supporting dialects (PostgreSQL, MySQL InnoDB,
            # MariaDB), ``SELECT ... FOR UPDATE`` takes a pessimistic
            # row-level lock (READ COMMITTED isolation).
            #
            # SQLite has NO ``FOR UPDATE`` syntax — the raw keyword
            # triggers ``sqlite3.OperationalError: near "FOR": syntax
            # error``. SQLite relies on its global database-level write
            # lock for serialisation; row-level locking is meaningless
            # there. We therefore branch on dialect and emit ``FOR
            # UPDATE`` on PostgreSQL, MySQL, and MariaDB (matching the
            # pattern used in ``SQLModelInstanceRepository.delete_by_project``,
            # ``ExecutionLeaseRepository.try_acquire``, and
            # ``SQLModelProjectRepository.delete``).
            #
            # If the orchestrator still has active children, defer
            # finalization for BOTH the job and the instance — they stay
            # coupled. The CM will fire the callback again when the new
            # wave resolves (send_message registers a fresh correlation),
            # at which point ``waiting_for`` will be 0 and the transition
            # proceeds normally. The async caller
            # (``_finalize_job``) re-arms the CM ``_pending[parent_id]``
            # slot on ``skip=True`` so wave 2 children whose resolves
            # arrive before their registers (e.g. via ``job_continue`` /
            # ``watch_job``) still find the parent — C2-PartA fix.
            #
            # ─── Flag OFF (default): CM is authoritative, no FOR UPDATE ──
            # CM ``is_complete()`` is sync-safe — reads ``_pending`` dict
            # under GIL protection (no asyncio.Lock taken, safe to call
            # from the worker thread that ``asyncio.to_thread`` runs us
            # in). Under flag OFF the legacy ``waiting_for`` writer is
            # disabled (A5), so there is no concurrent writer to race
            # against and the row lock would be pure overhead.
            use_legacy_cascade = bool(
                self._config.use_legacy_waiting_for_cascade
            ) if self._config is not None else False

            if not use_legacy_cascade:
                # CM path: when CM says NOT complete, defer finalization.
                # CM is the SOLE completion authority under flag OFF; if
                # it still tracks pending work for this parent, the
                # children are still running and we must wait.
                cm_gate = get_correlation_manager()
                if cm_gate is None:
                    # CM unavailable under flag OFF is a hard error per
                    # ADR-011 — the CM is authoritative and there is no
                    # fallback path. Deferring here would wedge the job.
                    raise RuntimeError(
                        f"USE_LEGACY_WAITING_FOR_CASCADE=OFF but "
                        f"CorrelationManager is not initialised — "
                        f"cannot gate finalization for "
                        f"{instance_id[:8]}... (CM is authoritative "
                        f"under flag OFF, see ADR-011)"
                    )
                if not cm_gate.is_complete(instance_id):
                    _cm_pending_gate = cm_gate.get_pending_count(instance_id)
                    logger.info(
                        f"Observer: aborting terminal transition for "
                        f"{instance_id[:8]}... — CM pending="
                        f"{_cm_pending_gate} > 0 (orchestrator has active "
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
                # Gate passed cleanly under CM authority — fall through
                # to the UPDATE / Step 1 / Step 2 / Step 3 / commit path.
                # Reset the bounded defer counter so any future transient
                # failure starts counting from zero rather than inheriting
                # stale state from a prior incident.
                _gate_defer_counts.pop(instance_id, None)
            else:
                # ─── Legacy path: SELECT ... FOR UPDATE row-lock gate ───
                # ─── Dialect detection for row-level locking (F2, 2026-06-20) ───
                # PostgreSQL, MySQL InnoDB, and MariaDB all support
                # ``SELECT ... FOR UPDATE`` natively. Earlier versions gated
                # this on a binary PG-vs-everything check, which silently
                # disabled row locking on MySQL/MariaDB and reintroduced the
                # TOCTOU window. SQLite has no ``FOR UPDATE`` syntax and
                # relies on its global database-level write lock.
                _dialect_name = (
                    session.bind.dialect.name
                    if session.bind is not None
                    else ""
                )
                _supports_row_lock = _dialect_name in (
                    "postgresql", "mysql", "mariadb",
                )
                try:
                    _gate_sql = (
                        "SELECT waiting_for FROM instances "
                        "WHERE instance_id = :iid FOR UPDATE"
                        if _supports_row_lock
                        else (
                            "SELECT waiting_for FROM instances "
                            "WHERE instance_id = :iid"
                        )
                    )
                    _gate_row = session.execute(
                        _sa_text(_gate_sql),
                        {"iid": instance_id},
                    ).first()
                    if _gate_row is not None:
                        _wf_gate = _gate_row[0] or 0
                        if _wf_gate > 0:
                            logger.info(
                                f"Observer: aborting terminal transition for "
                                f"{instance_id[:8]}... — waiting_for="
                                f"{_wf_gate} > 0 (orchestrator has active "
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
                    # Gate passed cleanly (no exception, waiting_for == 0).
                    # Reset the bounded defer counter so any future transient
                    # failure starts counting from zero rather than inheriting
                    # stale state from a prior incident.
                    _gate_defer_counts.pop(instance_id, None)
                except Exception as e:
                    # C1-N1 fix (F1 escape valve, 2026-06-20): on the first
                    # few deferrals, return ``skip=True`` with
                    # ``gate_deferred=True`` so the wave-2 callback can
                    # re-attempt finalization (C2-PartA rearm path fires).
                    # However, the bounded counter (``_MAX_GATE_DEFERS``) is
                    # the ONLY safety net here — existing recovery mechanisms
                    # do NOT cover gate-deferred jobs:
                    #   * ``StaleTaskRecovery`` operates on the ``task``
                    #     table, not ``job_queue``.
                    #   * ``JobProcessor`` orphan recovery skips instances
                    #     that are alive (gate-deferred jobs have RUNNING
                    #     instances).
                    #   * ``JobRecoveryService.recover_on_startup`` only runs
                    #     at daemon restart.
                    # Without this counter, a persistent gate failure would
                    # silently wedge the job in PROCESSING forever — a
                    # SILENT LIVENESS bug that's operationally worse than
                    # the noisy correctness bug the deferral was originally
                    # added to prevent. After ``_MAX_GATE_DEFERS`` consecutive
                    # deferrals we fall through to the UPDATE, accepting
                    # potential premature completion as the lesser evil. If
                    # the outer transaction is poisoned, the UPDATE raises
                    # and the W3 fail-safe in the async caller transitions
                    # the job to FAILED — still better than permanent
                    # PROCESSING.
                    _defer_key = instance_id
                    _defer_count = _gate_defer_counts.get(_defer_key, 0) + 1
                    if _defer_count >= _MAX_GATE_DEFERS:
                        logger.error(
                            f"Observer: gate defer limit ({_MAX_GATE_DEFERS}) "
                            f"reached for instance {_defer_key[:8]}... — "
                            f"falling through to finalize to prevent permanent "
                            f"stuck-job (this may indicate persistent DB "
                            f"issues: {e})"
                        )
                        _gate_defer_counts.pop(_defer_key, None)
                        # Fall through to the UPDATE / Step 1 / Step 2 /
                        # Step 3 / commit path below — do NOT return
                        # ``skip=True``. The W3 fail-safe in the async caller
                        # handles the case where the poisoned outer
                        # transaction prevents the UPDATE from committing.
                    else:
                        _gate_defer_counts[_defer_key] = _defer_count
                        logger.warning(
                            f"Observer: failed to read instance for "
                            f"waiting_for gate ({_defer_key[:8]}...): {e} "
                            f"— deferring ({_defer_count}/{_MAX_GATE_DEFERS})"
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
