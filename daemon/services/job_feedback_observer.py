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
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.project.repository import SQLModelProjectRepository
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

    async def _process_event(self, event: dict) -> None:
        """Process a single instance_lifecycle event.

        Filters for instance_lifecycle events and maps instance status to
        job completion/failure using atomic transitions.

        Args:
            event: Event dict with event_type and data fields.
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
        # Another actor (e.g., terminate_instance) may have already transitioned it
        if job.status != JobStatus.PROCESSING.value:
            logger.debug(
                f"Job {job.job_id[:8]}... not in PROCESSING state "
                f"(current: {job.status}), skipping"
            )
            return

        # Map status to action using atomic transition
        try:
            if status == "completed":
                # Use atomic_transition for PROCESSING -> COMPLETED
                self._job_repo.atomic_transition(
                    job_id=job.job_id,
                    from_status=JobStatus.PROCESSING.value,
                    to_status=JobStatus.COMPLETED.value,
                )
                logger.info(
                    f"Observer: completed job {job.job_id[:8]}... "
                    f"for instance {instance_id[:8]}..."
                )

            elif status == "error":
                # Use atomic_transition for PROCESSING -> FAILED with error
                error_message = error if error else "Unknown error"
                self._job_repo.atomic_transition(
                    job_id=job.job_id,
                    from_status=JobStatus.PROCESSING.value,
                    to_status=JobStatus.FAILED.value,
                    error_message=error_message,
                )
                logger.info(
                    f"Observer: failed job {job.job_id[:8]}... "
                    f"for instance {instance_id[:8]}... error: {error_message}"
                )
            else:
                # Unknown status - skip silently
                logger.warning(
                    f"Unknown instance status '{status}' for instance {instance_id[:8]}..."
                )
                return

        except InvalidTransitionError as e:
            # Race condition: another actor (e.g., terminate_instance) already
            # transitioned the job. This is expected behavior - skip silently.
            logger.debug(
                f"Race condition: job {job.job_id[:8]}... already transitioned "
                f"(current: {e.from_status} -> {e.to_status}), skipping"
            )
            return
        except Exception as e:
            # Unexpected error during transition - log and skip
            logger.error(
                f"Failed to transition job {job.job_id[:8]}... status={status}: {e}",
                exc_info=True,
            )
            return

        # Release locks held by this instance
        # This is done AFTER successful transition to ensure we only release
        # locks for jobs that were actually completed/failed.
        # Database is the single source of truth - lock_repo releases DB records directly.
        try:
            released_count = self._lock_repo.release_by_instance(instance_id)
            if released_count > 0:
                logger.debug(
                    f"Released {released_count} lock(s) for instance {instance_id[:8]}..."
                )
        except Exception as e:
            # Lock release failure is not critical - log and continue
            logger.warning(
                f"Failed to release locks for instance {instance_id[:8]}...: {e}"
            )

        # FIX: Trigger the next pending job immediately instead of waiting for
        # the JobProcessor polling interval. This ensures zero-delay handoff
        # between consecutive jobs in the same queue.
        # Full spawn flow is done here so the orphan check in JobProcessor can
        # safely skip jobs that already have a spawned instance (instance_id set
        # but instance may not be in memory yet is fine — orphan check will skip).
        try:
            if job.project_id:
                # Get the next pending job without transitioning it yet
                next_job = await self._job_queue_service._get_next_job(job.project_id)
                if next_job is None:
                    return

                # Transition to PROCESSING and get instance_id
                # Pause check is centralized in start_job()
                started_job = await self._job_queue_service.start_job(next_job.job_id)
                if started_job is None:
                    # Couldn't start (lock not acquired, cancelled, etc.)
                    return

                # Spawn the instance using the instance_id from start_job
                instance_id = started_job.instance_id
                try:
                    self._instance_manager.spawn_instance(
                        agent_id=started_job.agent_id,
                        instance_id=instance_id,
                        project_id=started_job.project_id,
                    )
                except Exception as e:
                    logger.error(f"Observer: failed to spawn instance for job {started_job.job_id[:8]}...: {e}")
                    await self._job_queue_service.complete_job(
                        started_job.job_id, success=False, error=str(e)
                    )
                    return

                # Send the job message
                try:
                    await self._instance_manager.enqueue_message(
                        instance_id=instance_id,
                        message=started_job.message,
                        source=started_job.source,
                    )
                except Exception as e:
                    logger.error(f"Observer: failed to enqueue message for job {started_job.job_id[:8]}...: {e}")
                    await self._job_queue_service.complete_job(
                        started_job.job_id, success=False, error=str(e)
                    )
                    return

                logger.info(
                    f"Observer: triggered next job {started_job.job_id[:8]}... "
                    f"for project {job.project_id[:8]}..."
                )
        except Exception as e:
            logger.warning(
                f"Failed to trigger next job for project {job.project_id[:8]}...: {e}"
            )
