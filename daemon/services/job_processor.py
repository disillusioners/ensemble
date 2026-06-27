"""JobProcessor - Background worker for processing queued jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.dispatch_event_bus import DispatchEventBus
    from daemon.services.job_feedback_observer import JobFeedbackObserver
    from daemon.manager import InstanceManager

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.job_queue_service import (
    DemandState,
    JobQueueService,
    TERMINAL_CANCEL_STATUSES,
)
from daemon.services.job_lock_manager import JobLockManager
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
        # C7: Optional reference to the JobFeedbackObserver. Wired in
        # ``setup_job_feedback_observer`` (called from ``daemon/api.py``
        # after both the processor and the observer are constructed).
        # The unified observer → Task → WorkerPool path is the SOLE
        # execution path for message work.
        self._job_feedback_observer: "JobFeedbackObserver" | None = None
        # Throttle/dedup state for in_progress notifications.
        # Keyed by job_id. ``_last_in_progress`` records (timestamp, pending_count) of
        # the most recent in_progress emit so we can skip re-emit when nothing has
        # changed. ``_in_progress_since`` records the FIRST time we saw this job in
        # the guard, so the escape-hatch timer (``_child_timeout_seconds``) measures
        # total stuck-children wall time, not time since last emit.
        self._last_in_progress: dict[str, tuple[float, int]] = {}
        self._in_progress_since: dict[str, float] = {}
        self._child_timeout_seconds: int = 3600  # 1 hour default — force-fail stuck jobs

    def setup_job_feedback_observer(
        self, observer: "JobFeedbackObserver"
    ) -> None:
        """Wire the JobFeedbackObserver into the processor.

        C7: called from ``daemon/api.py`` after both the processor and the
        observer are constructed. With this reference in place, the
        processor routes MESSAGE-type work through the unified
        ``JobFeedbackObserver`` lifecycle path (Task → WorkerPool →
        ``_process_event`` → ``_finalize_job``). The observer is the SOLE
        dispatch authority for MESSAGE jobs (legacy handler path
        removed).

        Idempotent: setting the same observer twice is a no-op.

        Args:
            observer: The :class:`JobFeedbackObserver` instance.
        """
        if self._job_feedback_observer is None:
            self._job_feedback_observer = observer
            logger.info(
                "JobProcessor: JobFeedbackObserver wired "
                "(MESSAGE jobs route through observer — sole dispatch path post-Phase-D)"
            )
        elif self._job_feedback_observer is not observer:
            # Hot-swap (rare; only happens in test setup). Update the
            # reference and log a warning.
            logger.warning(
                "JobProcessor.setup_job_feedback_observer: replacing "
                "existing observer reference"
            )
            self._job_feedback_observer = observer

    async def _capture_result_summary(self, instance_id: str, job_id: str, job_type_label: str) -> str | None:
        """Try to capture agent response content for result_summary."""
        result_summary = None
        if hasattr(self._instance_manager, '_get_last_assistant_message_raw'):
            try:
                result_summary = await self._instance_manager._get_last_assistant_message_raw(instance_id)
            except Exception as e:
                logger.warning(f"Failed to get result_summary for {job_type_label} job {job_id[:8]}...: {e}")
        return result_summary

    def _cleanup_in_progress_tracking(self, job_id: str) -> None:
        """Drop in-progress tracking state for a job that reached a terminal state.

        Called from the normal (non-guard) ``complete_job`` paths so the throttle
        and escape-hatch timers do not leak across job lifetimes. Idempotent.
        """
        self._last_in_progress.pop(job_id, None)
        self._in_progress_since.pop(job_id, None)

    async def _emit_in_progress_if_children_pending(
        self,
        instance_meta,
        proc_job,
        job_type_label: str,
        status_display: str,
    ) -> bool:
        """Centralized guard for the 6 in_progress-emit call sites.

        If the job's instance is in a would-be terminal state but still has
        child agent reports outstanding, emit an ``in_progress`` notification
        instead of completing the job.

        Phase 4 / Phase 5 (2026-06-23): the control-flow
        decision consults the DependencyBus (DB-backed pending
        watchers) — the SOLE completion authority after the
        CorrelationManager was removed in Phase 5.

        A9 hard error: the legacy SELECT fallback is the exact bug
        we are fixing (TOCTOU) and MUST NOT be reachable. The bus is
        the SOLE completion authority — when the bus is None, this
        is an invalid state and raises ``RuntimeError``. Mirrors A8
        in ``child_reports.py``.

        Adds two safety nets that the original 6 inline blocks did not have:
        * **Throttle/dedup** — within a 300s window we skip re-emitting when
          pending count is unchanged, so a hot poll loop cannot spam
          watchers.
        * **Escape hatch** — if a job has been sitting in the guard for more
          than ``_child_timeout_seconds`` (default 1h), force-complete it as
          FAILED so a stuck child never permanently blocks the job.

        Args:
            instance_meta: The Instance metadata.
            proc_job: The JobItem (must expose ``job_id``).
            job_type_label: Human label for logs, e.g. ``"MESSAGE"`` or ``"TASK"``.
            status_display: Human-readable instance status for the log line
                (e.g. ``"completed"``, ``"terminated"``, ``"errored"``).

        Returns:
            ``True`` if the guard fired (caller should ``continue`` / skip normal
            processing). ``False`` if normal processing should proceed.
        """
        # Phase 4: prefer the CM's in-memory pending count when available.
        # Phase 5 (2026-06-23): the CM ``get_pending_count`` is replaced
        # by ``bus.count_pending_for_target`` on the DependencyBus
        # (the SOLE completion authority after CM removal). The
        # ``_maybe_emit_in_progress_guard`` is async, so we use the
        # async ``count_pending_for_target`` variant.
        assert hasattr(instance_meta, "instance_id"), "instance_meta must be an InstanceModel"
        instance_id = instance_meta.instance_id
        bus = get_dependency_bus()
        if bus is not None:
            wf = int(await bus.count_pending_for_target(instance_id) or 0)
        else:
            # ─── A9: HARD ERROR (not graceful degradation) ───
            # Bus is None is an INVALID state. The legacy SELECT
            # fallback (TOCTOU) is the exact bug we are fixing — it
            # MUST NOT be reachable. The bus must be initialized for
            # the new architecture to work; we raise rather than
            # silently degrade into the TOCTOU fallback. See ADR-011.
            raise RuntimeError(
                "DependencyBus is None — invalid state. "
                "The bus must be initialized (see ADR-011)."
            )
        if wf <= 0:
            return False

        job_id = proc_job.job_id
        now = time.time()

        # --- Escape hatch: force-fail jobs stuck waiting for children ---
        if job_id in self._in_progress_since:
            elapsed = now - self._in_progress_since[job_id]
            if elapsed > self._child_timeout_seconds:
                logger.warning(
                    f"JobProcessor: {job_type_label} job {job_id[:8]}... has been waiting "
                    f"{int(elapsed)}s for {wf} child agent(s); forcing FAILED"
                )
                try:
                    progress_text = await self._capture_result_summary(
                        instance_meta.instance_id, job_id, job_type_label
                    )
                except Exception:
                    progress_text = None
                try:
                    await self._queue_service.complete_job(
                        job_id,
                        demand_state=DemandState.FAILED,
                        error=f"Children did not report within timeout ({int(elapsed)}s)",
                        result_summary=progress_text,
                    )
                except Exception as e:
                    logger.warning(
                        f"JobProcessor: failed to force-fail stuck {job_type_label} job "
                        f"{job_id[:8]}... after {int(elapsed)}s: {e}"
                    )
                # Cleanup so next guard visit starts a fresh window.
                self._in_progress_since.pop(job_id, None)
                self._last_in_progress.pop(job_id, None)
                return True
        else:
            # First time we see this job_id in the guard — start the clock.
            self._in_progress_since[job_id] = now

        # --- Throttle/dedup: skip re-emit within 300s if pending_count unchanged ---
        last = self._last_in_progress.get(job_id)
        if last is not None:
            last_ts, last_wf = last
            if (now - last_ts) < 300 and last_wf == wf:
                # Same state, within throttle window — consume this poll silently.
                return True
        self._last_in_progress[job_id] = (now, wf)

        # --- Emit the in_progress notification (failures are non-fatal) ---
        try:
            progress_text = await self._capture_result_summary(
                instance_meta.instance_id, job_id, job_type_label
            )
        except Exception:
            progress_text = None
        try:
            await self._queue_service.notify_watchers(
                job_id,
                status="in_progress",
                progress=progress_text,
            )
        except Exception as e:
            logger.warning(
                f"JobProcessor: failed to emit in_progress for {job_type_label} job "
                f"{job_id[:8]}...: {e}"
            )
        logger.info(
            f"JobProcessor: {job_type_label} job {job_id[:8]}... instance "
            f"{getattr(instance_meta, 'instance_id', '?')[:8]}... "
            f"emitted '{status_display}' but has {wf} pending child agent(s); deferring"
        )
        return True

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
        logger.debug("[TRACE] _process_loop: started")
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
                            f"[TRACE] _process_loop: woken by event (immediate={self._jobs_dispatched_immediately}, "
                            f"polling={self._jobs_dispatched_polling}), processing next job"
                        )
                    else:
                        self._jobs_dispatched_polling += 1
                        logger.debug(
                            f"[TRACE] _process_loop: poll timeout, processing next job "
                            f"(immediate={self._jobs_dispatched_immediately}, polling={self._jobs_dispatched_polling})"
                        )
                else:
                    # Fallback: pure polling
                    await asyncio.sleep(self._poll_interval)
                    self._jobs_dispatched_polling += 1
                    logger.debug(
                        f"[TRACE] _process_loop: pure polling wakeup "
                        f"(polling={self._jobs_dispatched_polling})"
                    )
                
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
        logger.debug("[TRACE] _process_next_job: waking up to check for jobs")
        
        # Get all projects
        projects = await asyncio.to_thread(self._project_repo.list_projects)
        
        logger.debug(f"[TRACE] _process_next_job: checking {len(projects)} project(s)")
        
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
                    # Also check for ACTIVE-admission jobs that have instance_id set but
                    # no spawned instance yet. These jobs were transitioned to
                    # admission_state='active' (via status=PROCESSING) by
                    # trigger_next_job() but the JobProcessor missed them due to
                    # event-driven or polling timing gaps.
                    #
                    # Phase 3 admission-decision migration: filter on
                    # ``admission_state='active'`` rather than
                    # ``statuses=['processing']``. This catches PAUSED jobs too
                    # (admission_state='active' under the new model — pause is
                    # an Instance concern, lock still held), which the legacy
                    # ``status='processing'`` filter silently dropped.
                    active_jobs, _ = await asyncio.to_thread(
                        self._queue_service._repository.list_by_queue, queue.queue_id,
                        admission_states=[AdmissionState.ACTIVE.value]
                    )
                    for proc_job in (active_jobs or []):
                        # NOTE: The legacy MESSAGE-specific orphan guard
                        # was removed in D11. The unified dispatcher owns message
                        # completion end-to-end — orphan PROCESSING rows are
                        # resolved by the JobFeedbackObserver event subscription
                        # (``_finalize_job``).
                        # A stuck PROCESSING MESSAGE row that survives both paths
                        # is recovered by ``JobRecoveryService`` at startup; we
                        # no longer attempt inline re-spawn or fail here.

                        # Skip if instance already spawned (normal case).
                        # If instance_id is set but get_instance raises KeyError,
                        # the instance might be in the process of being spawned
                        # (e.g., by JobFeedbackObserver). Skip and let it complete.
                        if proc_job.instance_id:
                            try:
                                await self._instance_manager.get_instance(proc_job.instance_id)
                                continue  # Instance exists, skip
                            except KeyError:
                                # Instance not in memory — check if it was terminated/completed/errored
                                # before attempting to re-spawn
                                if (
                                    hasattr(self._instance_manager, '_instance_repository')
                                    and self._instance_manager._instance_repository is not None
                                ):
                                    try:
                                        instance_meta = await asyncio.to_thread(
                                            self._instance_manager._instance_repository.get,
                                            proc_job.instance_id
                                        )
                                        if instance_meta is not None:
                                            # Instance exists in DB — check its status
                                            if instance_meta.status == InstanceStatus.COMPLETED.value:
                                                if await self._emit_in_progress_if_children_pending(
                                                    instance_meta, proc_job, "TASK", "completed"
                                                ):
                                                    continue
                                                logger.info(
                                                    f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                    f"instance {proc_job.instance_id[:8]}... is completed, "
                                                    f"completing job"
                                                )
                                                result_summary = await self._capture_result_summary(
                                                    proc_job.instance_id, proc_job.job_id, "TASK"
                                                )
                                                await self._queue_service.complete_job(
                                                    proc_job.job_id,
                                                    demand_state=DemandState.COMPLETED,
                                                    result_summary=result_summary,
                                                )
                                                self._cleanup_in_progress_tracking(proc_job.job_id)
                                                continue
                                            elif instance_meta.status in TERMINAL_CANCEL_STATUSES:
                                                status_display = instance_meta.status.value if hasattr(instance_meta.status, 'value') else instance_meta.status
                                                if await self._emit_in_progress_if_children_pending(
                                                    instance_meta, proc_job, "TASK", status_display
                                                ):
                                                    continue
                                                logger.info(
                                                    f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                    f"instance {proc_job.instance_id[:8]}... is {status_display}, "
                                                    f"cancelling job"
                                                )
                                                await self._queue_service.complete_job(
                                                    proc_job.job_id,
                                                    demand_state=DemandState.CANCELLED,
                                                    error=f"Instance is {status_display}",
                                                )
                                                self._cleanup_in_progress_tracking(proc_job.job_id)
                                                continue
                                            elif instance_meta.status == InstanceStatus.ERROR.value:
                                                if await self._emit_in_progress_if_children_pending(
                                                    instance_meta, proc_job, "TASK", "errored"
                                                ):
                                                    continue
                                                logger.warning(
                                                    f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                    f"instance {proc_job.instance_id[:8]}... errored, failing job"
                                                )
                                                await self._queue_service.complete_job(
                                                    proc_job.job_id,
                                                    demand_state=DemandState.FAILED,
                                                    error="Instance errored",
                                                )
                                                self._cleanup_in_progress_tracking(proc_job.job_id)
                                                continue
                                            elif instance_meta.status == InstanceStatus.PAUSED.value:
                                                logger.debug(
                                                    f"JobProcessor: TASK job {proc_job.job_id[:8]}... "
                                                    f"instance {proc_job.instance_id[:8]}... is paused, skipping"
                                                )
                                                continue
                                            # Instance is in a non-terminal state but not in memory —
                                            # genuine crash, proceed to re-spawn below
                                    except Exception as e:
                                        logger.warning(
                                            f"JobProcessor: failed to check instance status for "
                                            f"{proc_job.instance_id[:8]}...: {e}"
                                        )
                                        continue  # Don't crash on transient errors

                                # Instance genuinely crashed or missing — re-spawn
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
                                    self._cleanup_in_progress_tracking(proc_job.job_id)
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
                            self._cleanup_in_progress_tracking(proc_job.job_id)
                    continue

                job = pending[0]

                # [TRACE] Log job found
                job_type = getattr(job, 'job_type', 'task')
                logger.info(
                    f"[TRACE] _process_next_job: found PENDING job {job.job_id[:8]}... "
                    f"job_type={job_type} instance={job.instance_id[:8] if job.instance_id else 'N/A'}..."
                )

                # NOTE: The legacy MESSAGE-specific DB-level
                # sibling pre-check was removed in D11. The unified
                # observer owns message dispatch end-to-end — it
                # runs the concurrency gate via the Execution Gate.
                # JobQueue no longer needs a DB-level sibling check
                # for MESSAGE jobs.
                #
                # Instance-level pause, however, must be evaluated here
                # (BEFORE ``start_job``) so the test contract holds:
                # ``TestJobProcessorInstancePause::test_skips_job_for_paused_instance``
                # asserts that ``start_job`` is NOT called when the
                # target instance is paused. The downstream pause check
                # inside ``JobQueueService.start_job`` would return None
                # too late — we want to avoid the lock acquisition
                # attempt entirely. Errors here fall through to
                # ``start_job`` (which has its own pause check) so a
                # transient repo error doesn't wedge the queue.
                if (
                    job.instance_id
                    and getattr(self._instance_manager, "_instance_repository", None) is not None
                ):
                    try:
                        instance_meta = await asyncio.to_thread(
                            self._instance_manager._instance_repository.get,
                            job.instance_id,
                        )
                        if instance_meta is not None and instance_meta.status == InstanceStatus.PAUSED.value:
                            status_display = (
                                instance_meta.status.value
                                if hasattr(instance_meta.status, "value")
                                else instance_meta.status
                            )
                            logger.info(
                                f"JobProcessor: SKIP {job_type} job "
                                f"{job.job_id[:8]}... — instance "
                                f"{job.instance_id[:8]}... is {status_display}, "
                                f"staying PENDING"
                            )
                            continue
                    except Exception as e:
                        logger.warning(
                            f"JobProcessor: instance pause pre-check failed "
                            f"for job {job.job_id[:8]}... (instance "
                            f"{job.instance_id[:8] if job.instance_id else 'N/A'}...): "
                            f"{e}. Falling through to start_job."
                        )

                # Try to start the job (acquires per-queue lock internally)
                # Note: JobQueueService.start_job() also performs an
                # instance pause check — that's the second line of defense
                # if the pre-check above raced or errored.
                logger.debug(f"[TRACE] _process_next_job: attempting to start job {job.job_id[:8]}...")
                try:
                    started_job = await self._queue_service.start_job(job.job_id)
                    if started_job is None:
                        # Lock acquisition failed or job was cancelled
                        logger.debug(f"[TRACE] _process_next_job: SKIP job {job.job_id[:8]}... — start_job returned None (lock contention or cancelled)")
                        continue

                    logger.debug(
                        f"[TRACE] _process_next_job: started_job {started_job.job_id[:8]}... "
                        f"instance={started_job.instance_id[:8] if started_job.instance_id else 'N/A'}... "
                        f"status={started_job.status}"
                    )

                    # D13 (Phase 2): the legacy MESSAGE dispatch branch
                    # via JobFeedbackObserver has been removed. Messages
                    # no longer create JobItem rows (see
                    # InstanceMessagingService.enqueue_message) — they
                    # write only Task + MessageQueue rows. The
                    # JobProcessor therefore only handles TASK-type
                    # dispatch-queue jobs from this point forward.
                    # Phase 3 (D11) completed the cleanup of legacy
                    # admission-path code in the observer.

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
                        self._cleanup_in_progress_tracking(job.job_id)
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
                        self._cleanup_in_progress_tracking(job.job_id)
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
                        self._cleanup_in_progress_tracking(job.job_id)
                    except Exception:
                        pass

        # C5 orphan fallback removed (Phase 2): All jobs now have normalized project_id,
        # so there are no longer any orphan jobs without project_id to handle.


# Backward compatibility alias
TaskProcessor = JobProcessor
