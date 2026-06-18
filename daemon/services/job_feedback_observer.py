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
from typing import TYPE_CHECKING, NamedTuple

from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.correlation_manager import get_correlation_manager
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

        Side effects on success:
          1. ``atomic_transition`` moves the job from PROCESSING to COMPLETED
             or FAILED.
          2. ``notify_watchers`` fires the terminal watcher event.
          3. ``lock_repo.release_by_instance`` releases any DB-backed locks
             held by this instance.
          4. The next pending job for the same project is admitted and
             spawned (zero-delay handoff).

        Args:
            job: The JobItem to transition (must be in PROCESSING).
            instance_id: The parent instance ID.
            terminal_status: ``"completed"`` or ``"error"``.
            error: Error message for FAILED transitions (ignored for COMPLETED).
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            if terminal_status == InstanceStatus.COMPLETED.value:
                result_summary = (
                    await self._instance_manager._get_last_assistant_message_raw(
                        instance_id
                    )
                )
                if not result_summary:
                    result_summary = "Job completed (no agent response captured)"
                # C1 fix (TOCTOU race introduced by W1): after the LLM fetch
                # (last await before the transition), re-check the CM pending
                # count. A concurrent ``register_message_send`` could have
                # fired during the fetch (parent agent spawned another child
                # via a tool call while the callback was running). If new
                # pending correlations appeared, abort the terminal transition
                # — the CM will fire the callback again when those new
                # children resolve. The check itself is synchronous and is the
                # LAST operation before ``atomic_transition`` (no await in
                # between), so no new registrations can sneak past.
                cm = get_correlation_manager()
                if cm is not None:
                    cm_pending = cm.get_pending_count(instance_id)
                    if cm_pending > 0:
                        logger.info(
                            f"Observer: aborting terminal transition for "
                            f"{instance_id[:8]}... — {cm_pending} new "
                            f"pending correlations appeared during callback"
                        )
                        return
                self._job_repo.atomic_transition(
                    job_id=job.job_id,
                    from_status=JobStatus.PROCESSING.value,
                    to_status=JobStatus.COMPLETED.value,
                    completed_at=now,
                    result_summary=result_summary,
                )
                # NOTE: ``atomic_transition`` above is intentionally NOT
                # wrapped in ``asyncio.to_thread`` because the C1 TOCTOU
                # comment block above requires it to be the LAST operation
                # before the notification — no ``await`` between the CM
                # re-check and the write. Wrapping the write in a thread
                # would re-introduce a window where new ``register_message_send``
                # calls can sneak past. The C1 invariant is the trade-off;
                # this single sync write is a fast indexed UPDATE and has not
                # been observed to deadlock in practice (the deadlock chain
                # documented in the experience docs is the WriteGuardSession
                # commit in ``_finalize_instance``, which IS wrapped).
                logger.info(
                    f"Observer: completed job {job.job_id[:8]}... "
                    f"for instance {instance_id[:8]}..."
                )
                await self._job_queue_service.notify_watchers(
                    job.job_id, "completed"
                )
            elif terminal_status == InstanceStatus.ERROR.value:
                error_message = error if error else "Unknown error"
                # C1 fix (TOCTOU race introduced by W1): same re-check as the
                # completed branch. New pending correlations may have been
                # registered during the path between CM callback dispatch and
                # our arrival here. Abort if so; CM will fire the callback
                # again when the new children resolve. No ``await`` between
                # this check and ``atomic_transition``.
                cm = get_correlation_manager()
                if cm is not None:
                    cm_pending = cm.get_pending_count(instance_id)
                    if cm_pending > 0:
                        logger.info(
                            f"Observer: aborting terminal transition for "
                            f"{instance_id[:8]}... — {cm_pending} new "
                            f"pending correlations appeared during callback"
                        )
                        return
                self._job_repo.atomic_transition(
                    job_id=job.job_id,
                    from_status=JobStatus.PROCESSING.value,
                    to_status=JobStatus.FAILED.value,
                    completed_at=now,
                    error_message=error_message,
                )
                logger.info(
                    f"Observer: failed job {job.job_id[:8]}... "
                    f"for instance {instance_id[:8]}... error: {error_message}"
                )
                await self._job_queue_service.notify_watchers(
                    job.job_id, "failed", error_message
                )
            else:
                logger.warning(
                    f"Unknown terminal status '{terminal_status}' for "
                    f"instance {instance_id[:8]}..."
                )
                return

        except InvalidTransitionError as e:
            # Race condition: another actor (e.g., terminate_instance, a
            # previous CM callback) already transitioned the job. Expected —
            # skip silently. This is the primary idempotency mechanism.
            logger.debug(
                f"Race condition: job {job.job_id[:8]}... already transitioned "
                f"(current: {e.from_status} -> {e.to_status}), skipping"
            )
            return
        except Exception as e:
            logger.error(
                f"Failed to transition job {job.job_id[:8]}... "
                f"status={terminal_status}: {e}",
                exc_info=True,
            )
            # W3 fix (fail-safe): if finalization failed (e.g., the LLM fetch
            # raised, the DB write failed), the CM has already deleted
            # ``_pending[parent_id]`` — the callback will not fire again.
            # Without a fail-safe, the job would sit in PROCESSING forever.
            # Transition to FAILED so the queue can advance and watchers see
            # a terminal state. If even this fails (e.g., job is already in a
            # terminal state from another actor), swallow silently — there
            # is nothing more we can do.
            try:
                # Wrap this fail-safe write in ``asyncio.to_thread`` — unlike
                # the COMPLETED/FAILED happy-path writes above, the C1 TOCTOU
                # invariant does NOT apply here: the primary finalization has
                # already failed and the CM has deleted ``_pending[parent_id]``,
                # so there is no ``register_message_send`` race to defend
                # against. Under SQLite WAL write contention this recovery
                # write would otherwise wedge the event loop on the same
                # deadlock chain documented for the happy paths.
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

        # Phase 3 (Cascade Unification): perform the FULL instance terminal
        # transition now that the JOB is terminal. Mirrors the inline cascade
        # in ``child_reports.py`` and ``error_reporting.py`` (CM-disabled
        # path) on the CM-active path — sets ``instance.status``, signals
        # ``CompletionRegistry`` (unblocks ``invoke_agent_and_wait()``),
        # publishes the lifecycle event, and emits the SSE ``status_change``.
        # Wrapped in its own try/except so an instance-side failure does NOT
        # trigger the W3 fail-safe above (the job is already terminal).
        try:
            await self._finalize_instance(instance_id, terminal_status, error=error)
        except Exception as e:
            logger.warning(
                f"Observer: instance finalization failed for "
                f"{instance_id[:8]}...: {e}"
            )

        # Release locks held by this instance. Wrap the sync DB write in
        # ``asyncio.to_thread`` so SQLite WAL write contention cannot block
        # the event loop (the deadlock chain documented in the experience
        # docs is rooted in sync writes on the loop thread).
        try:
            released_count = await asyncio.to_thread(
                self._lock_repo.release_by_instance, instance_id
            )
            if released_count > 0:
                logger.debug(
                    f"Released {released_count} lock(s) for instance "
                    f"{instance_id[:8]}..."
                )
        except Exception as e:
            logger.warning(
                f"Failed to release locks for instance "
                f"{instance_id[:8]}...: {e}"
            )

        # Trigger the next pending job immediately instead of waiting for
        # the JobProcessor polling interval. This ensures zero-delay handoff
        # between consecutive jobs in the same queue.
        await self._trigger_next_job(job)

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
                last_content = (
                    await self._instance_manager._get_last_assistant_message_raw(
                        instance_id
                    )
                )
                get_completion_registry().complete(
                    instance_id, result=last_content
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

    async def _trigger_next_job(self, job) -> None:
        """Admit and spawn the next pending job for the same project.

        Best-effort: any failure is logged at WARNING and swallowed. The
        :class:`JobProcessor` polling loop is a safety net that will eventually
        pick up the next job even if this handoff fails.

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
            try:
                instance_id = await self._instance_manager.spawn_instance_with_mcp(
                    agent_id=started_job.agent_id,
                    instance_id=instance_id,
                    project_id=started_job.project_id,
                )
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
