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
from typing import TYPE_CHECKING

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.services.correlation_manager import get_correlation_manager
from daemon.services.job_queue_service import DemandState, JobQueueService
from daemon.services.job_state_machine import InvalidTransitionError

if TYPE_CHECKING:
    from daemon.config import JobSystemConfig
    from daemon.services.event_bus import EventBus
    from daemon.services.job_queue_service import JobQueueService

logger = logging.getLogger(__name__)


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
        job = await self._job_queue_service.get_job_by_instance(parent_id)
        if job is None:
            logger.debug(
                f"CM callback: no job for parent {parent_id[:8]}..., skipping"
            )
            return
        # Idempotency guard: if job is no longer PROCESSING, another actor
        # (terminate_instance, previous callback, etc.) already transitioned it.
        if job.status != JobStatus.PROCESSING.value:
            logger.debug(
                f"CM callback: job {job.job_id[:8]}... already {job.status}, "
                f"skipping (idempotency guard)"
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
        if status == "terminated":
            logger.debug(
                f"Skipping terminated event for instance {instance_id[:8]}... "
                "(handled by terminate_instance)"
            )
            return

        # Look up job by instance using job_queue_service
        job = await self._job_queue_service.get_job_by_instance(instance_id)
        if job is None:
            return  # No job associated with this instance

        # Skip if job is not in PROCESSING state
        # Another actor (e.g., terminate_instance, CM callback) may have
        # already transitioned it.
        if job.status != JobStatus.PROCESSING.value:
            logger.debug(
                f"Job {job.job_id[:8]}... not in PROCESSING state "
                f"(current: {job.status}), skipping"
            )
            return

        # Phase 2: decide between in_progress and terminal based on CM state.
        if status in ("completed", "error"):
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
            if terminal_status == "completed":
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
                logger.info(
                    f"Observer: completed job {job.job_id[:8]}... "
                    f"for instance {instance_id[:8]}..."
                )
                await self._job_queue_service.notify_watchers(
                    job.job_id, "completed"
                )
            elif terminal_status == "error":
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
                self._job_repo.atomic_transition(
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

        # Release locks held by this instance
        try:
            released_count = self._lock_repo.release_by_instance(instance_id)
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
