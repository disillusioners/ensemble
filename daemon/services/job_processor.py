"""JobProcessor - Background worker for processing queued jobs."""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.dispatch_event_bus import DispatchEventBus
    from daemon.manager import InstanceManager

from daemon.models.instance import InstanceStatus
from daemon.services.job_queue_service import DemandState, JobQueueService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.message_job_handler import MessageJobHandler
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository

logger = logging.getLogger(__name__)


class JobProcessor:
    """Background worker that processes queued jobs.
    
    Continuously polls for pending jobs across all queues and processes them.
    Uses two-level pause checks: project-level (job_queue_paused) and queue-level
    (is_paused) to control job processing.
    
    The processing order is:
    1. Iterate through all projects
    2. Skip if project.job_queue_paused is True (master pause)
    3. For each active queue in the project, skip if queue.is_paused is True
    4. Get next pending job for the queue
    5. Acquire per-queue lock and start job
    
    Attributes:
        _queue_service: JobQueueService instance for job operations.
        _instance_manager: InstanceManager instance for spawning instances.
        _project_repo: SQLModelProjectRepository for checking project pause state.
        _queue_repo: JobQueueRepository for listing queues and their pause state.
        _poll_interval: Time in seconds between poll cycles.
        _running: Flag to control the processing loop.
        _dispatch_bus: Optional DispatchEventBus for event-driven wakeup.
        _event_dispatch_enabled: Whether to use event-driven dispatch.
        _jobs_dispatched_immediately: Counter for jobs dispatched via events.
        _jobs_dispatched_polling: Counter for jobs dispatched via polling.
    """
    
    def __init__(
        self,
        queue_service: JobQueueService,
        instance_manager: InstanceManager,
        project_repo: SQLModelProjectRepository,
        queue_repo: JobQueueRepository,
        poll_interval: float = 30.0,
        dispatch_bus: "DispatchEventBus" | None = None,
        event_dispatch_enabled: bool = True,
    ):
        """Initialize the JobProcessor.
        
        Args:
            queue_service: JobQueueService for job operations.
            instance_manager: InstanceManager for spawning instances.
            project_repo: SQLModelProjectRepository for checking project pause state.
            queue_repo: JobQueueRepository for listing queues and checking pause state.
            poll_interval: Seconds between poll cycles (default: 30.0).
            dispatch_bus: Optional DispatchEventBus for event-driven job dispatch.
            event_dispatch_enabled: Whether to use event-driven dispatch (default: True).
        """
        self._queue_service = queue_service
        self._instance_manager = instance_manager
        self._project_repo = project_repo
        self._queue_repo = queue_repo
        self._poll_interval = poll_interval
        self._running = False
        self._job: asyncio.Task | None = None
        self._dispatch_bus = dispatch_bus
        self._event_dispatch_enabled = event_dispatch_enabled
        self._jobs_dispatched_immediately = 0
        self._jobs_dispatched_polling = 0
        self._message_job_handler: MessageJobHandler | None = None

    def setup_message_job_handler(self) -> None:
        """Set up the MessageJobHandler after all services are initialized.

        Called from manager initialization after _queue_service is available.
        """
        if self._message_job_handler is None:
            self._message_job_handler = MessageJobHandler(
                manager=self._instance_manager,
                job_queue_service=self._queue_service,
                job_repository=self._queue_service._repository,
            )
            self._queue_service._message_job_handler = self._message_job_handler
    
    async def start(self) -> None:
        """Start the background processing loop."""
        if self._running:
            return
        
        self._running = True
        self._job = asyncio.create_task(self._process_loop())
        logger.info("JobProcessor started")
    
    async def stop(self) -> None:
        """Stop the background processing loop gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        if self._job is not None:
            self._job.cancel()
            try:
                await self._job
            except asyncio.CancelledError:
                pass
            self._job = None
        
        logger.info("JobProcessor stopped")
    
    async def _process_loop(self) -> None:
        """Main processing loop - polls for and processes jobs with optional event-driven wakeup."""
        while self._running:
            try:
                # Event-driven dispatch: wait for job event with polling fallback
                if self._event_dispatch_enabled and self._dispatch_bus is not None:
                    # Wait for event with poll_interval as timeout
                    event_received = await self._dispatch_bus.wait_for_job(
                        project_id=None,  # Global event for now (could optimize per-project later)
                        timeout=self._poll_interval
                    )
                    if event_received:
                        self._jobs_dispatched_immediately += 1
                        logger.debug(
                            f"JobProcessor woken by event (immediate={self._jobs_dispatched_immediately}, "
                            f"polling={self._jobs_dispatched_polling})"
                        )
                    else:
                        self._jobs_dispatched_polling += 1
                else:
                    # Fallback: pure polling
                    await asyncio.sleep(self._poll_interval)
                    self._jobs_dispatched_polling += 1
                
                await self._process_next_job()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error in processing loop: {e}")
    
    async def _process_next_job(self) -> None:
        """Get the next pending job from any active queue and process it.
        
        Implements two-level pause checking:
        1. Project-level pause (job_queue_paused) - master override that stops ALL queues
        2. Queue-level pause (is_paused) - individual queue control
        
        Processing order:
        1. Get all projects
        2. Skip if project.job_queue_paused is True (master pause)
        3. Get all queues for the project
        4. Skip if queue.is_paused is True (individual queue pause)
        5. Get next pending job for the queue
        6. Acquire per-queue lock and start job
        """
        # Get all projects
        projects = await asyncio.to_thread(self._project_repo.list_projects)
        
        for project in projects:
            # Level 1 pause check: Master pause (project-level)
            # This is the master override that stops ALL queues for a project
            if project.job_queue_paused:
                continue
            
            # Get all queues for this project
            queues = await asyncio.to_thread(
                self._queue_repo.list_by_project, project.project_id
            )
            
            for queue in queues:
                # Level 2 pause check: Individual queue pause
                # This allows pausing specific queues while others continue
                if queue.is_paused:
                    continue

                # Defer queue check: only process when project is completely idle
                # Only applies to queues with queue_type attribute (skip mock/test objects)
                pending = await asyncio.to_thread(
                    self._queue_service._repository.list_pending_by_queue, queue.queue_id
                )
                
                if queue.queue_type == "defer" and pending:
                    # Count active jobs in NON-defer queues only to avoid deadlock.
                    # 
                    # TOCTOU Trade-off: Between counting active jobs and actual dequeue,
                    # a new job could be enqueued to a non-defer queue. This is acceptable because:
                    # - Periodic polling inherently tolerates slight staleness
                    # - Lock-first pattern prevents double-processing
                    # - Self-corrects on next poll cycle
                    non_defer_active = await asyncio.to_thread(
                        self._queue_service._repository.count_active_jobs_in_non_defer_queues, queue.project_id
                    )
                    if non_defer_active > 0:
                        # Project has active work in non-defer queues, skip this defer queue
                        continue

                if not pending:
                    # Also check for PROCESSING jobs that have instance_id set but no
                    # spawned instance yet. These jobs were transitioned to PROCESSING
                    # by trigger_next_job() but the JobProcessor missed them due to
                    # event-driven or polling timing gaps.
                    processing, _ = await asyncio.to_thread(
                        self._queue_service._repository.list_by_queue, queue.queue_id,
                        statuses=["processing"]
                    )
                    for proc_job in (processing or []):
                        # >>> FIRST: Guard MESSAGE jobs — fail, don't re-spawn <<<
                        # MUST be before the `if proc_job.instance_id:` check below
                        if getattr(proc_job, 'job_type', 'task') == 'message':
                            # Fix 2: Check instance liveness before declaring orphan
                            if proc_job.instance_id:
                                try:
                                    instance = await self._instance_manager.get_instance(proc_job.instance_id)
                                    # Instance exists — check if it's still alive or finished
                                    if instance.status in (InstanceStatus.COMPLETED, InstanceStatus.TERMINATED):
                                        # Instance finished its work — complete the job (not orphan).
                                        # The JobFeedbackObserver event may have missed firing due to
                                        # race condition, event bus issue, etc.
                                        logger.info(
                                            f"JobProcessor: MESSAGE job {proc_job.job_id[:8]}... "
                                            f"completed by finished instance (status={instance.status.value})"
                                        )
                                        await self._queue_service.complete_job(
                                            proc_job.job_id,
                                            demand_state=DemandState.COMPLETED,
                                        )
                                        continue
                                    elif instance.status == InstanceStatus.ERROR:
                                        # Instance errored — the message may not have been fully processed.
                                        # Fail the job rather than orphan it.
                                        logger.warning(
                                            f"JobProcessor: MESSAGE job {proc_job.job_id[:8]}... "
                                            f"failed due to instance error (status=error)"
                                        )
                                        await self._queue_service.complete_job(
                                            proc_job.job_id,
                                            demand_state=DemandState.FAILED,
                                            error="Instance errored during message processing",
                                        )
                                        continue
                                    # Instance is alive and processing — not orphaned
                                    continue
                                except KeyError:
                                    pass  # Instance truly gone — proceed to fail
                                except Exception as e:
                                    logger.warning(
                                        "Instance check failed for job %s (instance %s): %s",
                                        proc_job.job_id[:8],
                                        proc_job.instance_id[:8] if proc_job.instance_id else "N/A",
                                        e,
                                    )
                                    continue  # Don't crash on transient errors

                            # Fix 3: Re-read current state from DB before failing
                            current_job = await asyncio.to_thread(
                                self._queue_service._repository.get, proc_job.job_id
                            )
                            if current_job is None or current_job.status != "processing":
                                # Job already transitioned (completed/failed/cancelled) — skip
                                continue

                            logger.info(
                                f"JobProcessor: orphan MESSAGE job {proc_job.job_id[:8]}... "
                                f"(instance {proc_job.instance_id[:8] if proc_job.instance_id else 'N/A'}...) "
                                f"— failing (no re-spawn)"
                            )
                            await self._queue_service.complete_job(
                                proc_job.job_id,
                                demand_state=DemandState.FAILED,
                                error="Instance gone or unreachable, message job orphaned",
                            )
                            continue
                        # <<< END FIRST GUARD >>>

                        # Skip if instance already spawned (normal case).
                        # If instance_id is set but get_instance raises KeyError,
                        # the instance might be in the process of being spawned
                        # (e.g., by JobFeedbackObserver). Skip and let it complete.
                        if proc_job.instance_id:
                            try:
                                await self._instance_manager.get_instance(proc_job.instance_id)
                                continue  # Instance exists, skip
                            except KeyError:
                                # Instance has instance_id but doesn't exist in DB.
                                # This is an orphan from a crash between start_job_atomic()
                                # and spawn_instance(). Recover it now.
                                logger.info(
                                    f"JobProcessor: recovering orphan PROCESSING job {proc_job.job_id[:8]}... "
                                    f"(instance {proc_job.instance_id[:8]}... missing)"
                                )
                                try:
                                    instance_id = await self._instance_manager.spawn_instance_with_mcp(
                                        agent_id=proc_job.agent_id,
                                        instance_id=proc_job.instance_id,  # Reuse existing valid UUID
                                        project_id=proc_job.project_id,
                                    )
                                    await self._instance_manager.enqueue_message(
                                        instance_id=instance_id,
                                        message=proc_job.message,
                                        source=proc_job.source,
                                    )
                                    logger.info(
                                        f"Job {proc_job.job_id} recovered for instance {instance_id} "
                                        f"on queue {queue.queue_name}"
                                    )
                                    continue  # Successfully recovered
                                except Exception as e:
                                    # Failed to recover - mark as failed to prevent permanent orphan
                                    logger.error(
                                        f"Failed to recover orphan job {proc_job.job_id[:8]}...: {e}"
                                    )
                                    await self._queue_service.complete_job(
                                        proc_job.job_id, demand_state=DemandState.FAILED, error=str(e)
                                    )
                                    continue
                            except Exception as e:
                                logger.warning(
                                    "Instance check failed for job %s (instance %s): %s",
                                    proc_job.job_id[:8],
                                    proc_job.instance_id[:8] if proc_job.instance_id else "N/A",
                                    e,
                                )
                                continue  # Don't crash on transient errors
                        # No instance_id: this is a genuine orphan (shouldn't happen
                        # in normal operation, but kept as safety net)
                        # This job was started by trigger_next_job() but instance not spawned
                        logger.info(
                            f"JobProcessor: resuming orphan PROCESSING job {proc_job.job_id[:8]}... "
                            f"on queue {queue.queue_name}"
                        )
                        try:
                            instance_id = await self._instance_manager.spawn_instance_with_mcp(
                                agent_id=proc_job.agent_id,
                                instance_id=proc_job.instance_id,
                                project_id=proc_job.project_id,
                            )
                            await self._instance_manager.enqueue_message(
                                instance_id=instance_id,
                                message=proc_job.message,
                                source=proc_job.source,
                            )
                            logger.info(
                                f"Job {proc_job.job_id} resumed for instance {instance_id} "
                                f"on queue {queue.queue_name}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to resume orphan job {proc_job.job_id[:8]}...: {e}")
                            await self._queue_service.complete_job(
                                proc_job.job_id, demand_state=DemandState.FAILED, error=str(e)
                            )
                    continue

                job = pending[0]

                # >>> NEW: Pre-check for MESSAGE jobs — DB-level concurrency gate <<<
                # Check BEFORE start_job() to avoid unnecessary lock acquisition
                # Use getattr with default for test mock objects
                if getattr(job, 'job_type', 'task') == "message":
                    if job.instance_id:
                        active = await asyncio.to_thread(
                            self._queue_service._repository.find_processing_message_jobs_by_instance,
                            job.instance_id,
                        )
                        if active:
                            # Another MESSAGE is processing for this instance — skip this poll cycle
                            logger.debug(
                                f"JobProcessor: MESSAGE job {job.job_id[:8]}... skipped — "
                                f"instance {job.instance_id[:8]}... busy with another message"
                            )
                            continue  # Skip to next queue, job stays PENDING
                # <<< END NEW >>>

                # Try to start the job (acquires per-queue lock internally)
                try:
                    started_job = await self._queue_service.start_job(job.job_id)
                    if started_job is None:
                        # Lock acquisition failed or job was cancelled
                        continue

                    # >>> NEW: Route MESSAGE jobs to MessageJobHandler <<<
                    # Use getattr with default for safety
                    if getattr(started_job, 'job_type', 'task') == "message":
                        if self._message_job_handler is not None:
                            await self._message_job_handler.handle(started_job)
                            continue
                    # <<< END NEW >>>

                    # Spawn instance for this job
                    try:
                        instance_id = await self._instance_manager.spawn_instance_with_mcp(
                            agent_id=job.agent_id,
                            instance_id=started_job.instance_id,
                            project_id=job.project_id,
                        )
                    except Exception as e:
                        logger.error(f"Failed to spawn instance for job {job.job_id}: {e}")
                        await self._queue_service.complete_job(
                            job.job_id, demand_state=DemandState.FAILED, error=str(e)
                        )
                        continue

                    # Send the job message to the instance
                    try:
                        await self._instance_manager.enqueue_message(
                            instance_id=instance_id,
                            message=job.message,
                            source=job.source,
                        )
                    except Exception as e:
                        logger.error(f"Failed to enqueue message for job {job.job_id}: {e}")
                        await self._queue_service.complete_job(
                            job.job_id, demand_state=DemandState.FAILED, error=str(e)
                        )
                        continue

                    logger.info(
                        f"Job {job.job_id} queued for instance {instance_id} "
                        f"on queue {queue.queue_name}"
                    )
                except Exception as e:
                    logger.exception(f"Failed to process job {job.job_id}: {e}")
                    try:
                        await self._queue_service.complete_job(
                            job.job_id, demand_state=DemandState.FAILED, error=str(e)
                        )
                    except Exception:
                        pass

        # C5 orphan fallback removed (Phase 2): All jobs now have normalized project_id,
        # so there are no longer any orphan jobs without project_id to handle.


# Backward compatibility alias
TaskProcessor = JobProcessor
