"""Stale task recovery service with graceful cancellation."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

from daemon.repositories.task.models import TaskStatus

logger = logging.getLogger(__name__)

DEFAULT_STALE_THRESHOLD_MINUTES = 15
DEFAULT_CHECK_INTERVAL_SECONDS = 60
DEFAULT_CANCEL_GRACE_SECONDS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_BASE = 60
DEFAULT_RETRY_BACKOFF_MAX = 3600


class StaleTaskRecovery:
    """Background service that recovers stale tasks using 5-step protocol.
    
    5-Step Recovery Protocol:
    1. Find stale running tasks (past threshold, not yet cancelled)
    2. Request cancellation (set cancel_requested flag)
    3. Wait briefly for graceful shutdown (grace period)
    4. Force cancel tasks still running after grace period
    5. Schedule retry for cancelled tasks (if under max retries)
    
    This replaces the old "reset to pending" approach which caused
    duplicate processing.
    """
    
    def __init__(
        self,
        task_repository,
        message_repository,
        threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
        check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS,
        cancel_grace_seconds: int = DEFAULT_CANCEL_GRACE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_base: int = DEFAULT_RETRY_BACKOFF_BASE,
        retry_backoff_max: int = DEFAULT_RETRY_BACKOFF_MAX,
        event_repository=None,
        on_task_permanently_failed: "Callable[[str, str, str | None], None] | None" = None,  # NEW: callback(instance_id, error, message_id)
        on_task_cancelled_and_retried: "Callable[[int, int], None] | None" = None,  # NEW: callback(cancelled_task_id, retry_task_id)
    ):
        """Initialize stale task recovery.
        
        Args:
            task_repository: TaskRepository instance for task operations.
            message_repository: MessageQueueRepository for message recovery.
            threshold_minutes: Tasks running longer than this are considered stale.
            check_interval_seconds: How often to check for stale tasks.
            cancel_grace_seconds: Maximum time to wait for graceful shutdown.
            max_retries: Maximum number of retry attempts.
            retry_backoff_base: Base for exponential backoff (seconds).
            retry_backoff_max: Maximum backoff time (seconds).
            event_repository: Optional EventRepository for logging recovery events.
            on_task_permanently_failed: Optional callback(instance_id, error, message_id)
                called when a task permanently fails.
            on_task_cancelled_and_retried: Optional callback(cancelled_task_id, retry_task_id)
                called when a task is force-cancelled and a retry task is scheduled
                (replaces the cancelled task id in any pending bus watchers). This is
                required to prevent the parent from getting stranded in waiting_children
                when a retry succeeds but the bus watcher was registered against the
                cancelled task id.
        """
        self._task_repo = task_repository
        self._message_repo = message_repository
        self._event_repo = event_repository
        self._threshold_minutes = threshold_minutes
        self._check_interval = check_interval_seconds
        self._cancel_grace_seconds = cancel_grace_seconds
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_task_permanently_failed = on_task_permanently_failed  # NEW
        self._on_task_cancelled_and_retried = on_task_cancelled_and_retried  # NEW
    
    def start(self) -> None:
        """Start the background recovery thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("StaleTaskRecovery already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="StaleTaskRecovery",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"StaleTaskRecovery started: "
            f"threshold={self._threshold_minutes}min, "
            f"interval={self._check_interval}s, "
            f"grace={self._cancel_grace_seconds}s, "
            f"max_retries={self._max_retries}"
        )
    
    def stop(self, timeout: float = 10.0) -> None:
        """Stop the background recovery thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("StaleTaskRecovery stopped")
    
    def _run_loop(self) -> None:
        """Main loop: periodically check for stale tasks."""
        while not self._stop_event.is_set():
            try:
                recovered = self.recover_stale_tasks()
                if recovered > 0:
                    logger.info(f"StaleTaskRecovery: recovered {recovered} stale tasks")
            except Exception as e:
                logger.error(f"StaleTaskRecovery: error during recovery: {e}", exc_info=True)
            
            # Wait for next check interval or stop signal
            self._stop_event.wait(timeout=self._check_interval)
    
    def recover_stale_tasks(self) -> int:
        """Execute 5-step recovery protocol.
        
        Returns:
            Number of tasks processed during recovery.
        """
        # Step 1: Find stale running tasks not yet flagged
        stale_tasks = self._task_repo.find_cancellable_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        if not stale_tasks:
            return 0
        
        logger.warning(f"Found {len(stale_tasks)} stale tasks requiring recovery")
        
        # Step 2: Request cancellation for each
        for task in stale_tasks:
            try:
                cancelled = self._task_repo.request_cancel(task.id)
                if cancelled:
                    logger.info(
                        f"Step 2: Requested cancel for stale task {task.id} "
                        f"(instance={task.instance_id[:8]}..., worker={task.worker_id})"
                    )
                    self._log_recovery_event(task, "cancel_requested")
            except Exception as e:
                logger.error(f"Failed to request cancel for task {task.id}: {e}")
        
        # Step 3: Wait briefly for graceful shutdown
        if self._cancel_grace_seconds > 0:
            logger.debug(
                f"Step 3: Waiting {self._cancel_grace_seconds}s "
                f"for graceful worker shutdown..."
            )
            self._stop_event.wait(timeout=self._cancel_grace_seconds)
            if self._stop_event.is_set():
                return 0  # Shutting down
        
        # Step 4+5: Force cancel + schedule retry for tasks still running
        # FIX: C2 — Only retry if retry_scheduled=0. If Worker already called schedule_retry()
        #      (which sets retry_scheduled=1), we skip — no duplicate retry task.
        # FIX: W1 — Use force_cancel_and_schedule_retry() for single-transaction atomicity.
        # FIX: C1 — Skip COMPLETED/FAILED tasks (worker finished during grace period).
        # FIX: W4 — Only increment recovered_count for tasks actually acted upon.
        # FIX: W6 — Guard message failing: only reach it for tasks we actually recovered.
        recovered_count = 0
        for task in stale_tasks:
            try:
                # Re-read current state
                current = self._task_repo.get(task.id)
                if current is None:
                    continue
                
                # FIX: C1 — If task completed/failed during grace period, skip entirely
                # This prevents us from incorrectly failing the associated message.
                if current.status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
                    logger.debug(f"Task {task.id} already completed/failed — skipping")
                    continue  # Don't touch the message
                
                # FIX: W4 — Track whether we actually took action
                task_acted_upon = False
                
                if current.status == TaskStatus.RUNNING.value:
                    # Task still running after grace period — force cancel + retry atomically
                    retry_task = self._task_repo.force_cancel_and_schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        reason=f"Stale task force-cancelled (>{self._threshold_minutes}min)",
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )

                    if retry_task:
                        logger.info(
                            f"Step 4+5: Force-cancelled + retry {retry_task.id} "
                            f"for stale task {task.id} (attempt {retry_task.retry_count})"
                        )
                        self._log_recovery_event(task, "force_cancelled_and_retried",
                                                   retry_task_id=retry_task.id)
                        task_acted_upon = True
                        # Notify the bus that the cancelled task is gone so
                        # parent-side watchers (registered against the cancelled
                        # ``source_task_id``) are released. Without this, the
                        # retry's natural completion fires ``emit_terminal`` for
                        # its OWN task id, which cannot match the original
                        # watcher — the parent would stay in ``waiting_children``
                        # forever. The retry registers a fresh watcher when its
                        # own ``send_message`` path runs, so cancelling the
                        # original is safe.
                        self._notify_bus_of_cancel_and_retry(task.id, retry_task.id)
                    else:
                        # Max retries exceeded or retry already scheduled — permanent fail
                        if current.retry_count >= self._max_retries:
                            self._task_repo.fail_task(
                                task.id,
                                f"Stale task permanently failed after "
                                f"{self._max_retries} retries"
                            )
                            logger.warning(
                                f"Step 4: Task {task.id} permanently failed "
                                f"(max retries {self._max_retries} exceeded)"
                            )
                            self._log_recovery_event(task, "permanently_failed")
                            task_acted_upon = True
                            # NEW: Notify parent
                            if self._on_task_permanently_failed:
                                try:
                                    self._on_task_permanently_failed(
                                        task.instance_id,
                                        f"Stale task permanently failed after {self._max_retries} retries",
                                        task.message_id,
                                    )
                                except Exception as cb_err:
                                    logger.error(
                                        f"Failed to notify parent of permanent task failure "
                                        f"(instance={task.instance_id[:8]}..., error={cb_err})"
                                    )
                
                elif current.status == TaskStatus.CANCELLED.value:
                    # Worker already cancelled it — check if retry was scheduled
                    # FIX: C2 — If retry_scheduled=True, Worker handled retry. Skip.
                    if current.retry_scheduled:
                        logger.debug(
                            f"Step 5: Task {task.id} already has retry scheduled by Worker — skipping"
                        )
                    else:
                        # Worker cancelled but didn't schedule retry — try to schedule one
                        retry_task = self._task_repo.schedule_retry(
                            task_id=task.id,
                            max_retries=self._max_retries,
                            backoff_base=self._retry_backoff_base,
                            backoff_max=self._retry_backoff_max,
                        )
                        if retry_task:
                            logger.info(
                                f"Step 5: Scheduled retry {retry_task.id} "
                                f"for Worker-cancelled task {task.id}"
                            )
                            self._log_recovery_event(task, "retry_scheduled_by_recovery",
                                                       retry_task_id=retry_task.id)
                            task_acted_upon = True
                            # Bus cancel: same reason as the force-cancel branch
                            # above. The Worker cancelled the task but didn't
                            # notify the bus; without this call the parent-side
                            # watcher stays PENDING.
                            self._notify_bus_of_cancel_and_retry(task.id, retry_task.id)
                        else:
                            self._task_repo.fail_task(
                                task.id,
                                f"Stale task permanently failed after "
                                f"{self._max_retries} retries"
                            )
                            self._log_recovery_event(task, "permanently_failed")
                            task_acted_upon = True
                            # NEW: Notify parent
                            if self._on_task_permanently_failed:
                                try:
                                    self._on_task_permanently_failed(
                                        task.instance_id,
                                        f"Stale task permanently failed after {self._max_retries} retries",
                                        task.message_id,
                                    )
                                except Exception as cb_err:
                                    logger.error(
                                        f"Failed to notify parent of permanent task failure "
                                        f"(instance={task.instance_id[:8]}..., error={cb_err})"
                                    )
                
                # FIX: W6 — Only fail the associated message if we actually acted upon the task
                if task_acted_upon and task.message_id:
                    try:
                        self._message_repo.fail(
                            task.message_id,
                            f"Task recovered: stale (>{self._threshold_minutes}min)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to update message {task.message_id[:8]}...: {e}"
                        )
                
                # FIX: W4 — Only increment for tasks we actually acted upon
                if task_acted_upon:
                    recovered_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to recover task {task.id}: {e}",
                    exc_info=True
                )
        
        return recovered_count
    
    def recover_on_startup(self) -> int:
        """Run recovery immediately on startup (skip grace period).
        
        Uses force_cancel_and_schedule_retry() for atomicity.
        Also detects orphaned CANCELLED tasks (crash between cancel and retry).
        """
        logger.info("Running startup crash recovery (no grace period)...")
        
        # Phase A: Handle stale RUNNING tasks (worker crashed mid-execution)
        stale_tasks = self._task_repo.find_stale_running_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        recovered = 0
        
        if stale_tasks:
            logger.warning(f"Startup recovery: found {len(stale_tasks)} stale RUNNING tasks")
            
            for task in stale_tasks:
                try:
                    # Force cancel + retry in single transaction
                    retry_task = self._task_repo.force_cancel_and_schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        reason="Startup recovery: worker crash",
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )
                    
                    if retry_task:
                        logger.info(
                            f"Startup recovery: task {task.id} → retry {retry_task.id}"
                        )
                        self._log_recovery_event(task, "startup_recovered",
                                                   retry_task_id=retry_task.id)
                        # Bus cancel: startup recovery force-cancelled a stale
                        # RUNNING task whose worker likely crashed mid-execution.
                        # The original task's bus watchers (if any) are now
                        # orphans — release them so the parent doesn't stay in
                        # ``waiting_children`` after the retry completes.
                        self._notify_bus_of_cancel_and_retry(task.id, retry_task.id)
                    else:
                        self._task_repo.fail_task(
                            task.id,
                            f"Startup recovery: max retries ({self._max_retries}) exceeded"
                        )
                        logger.warning(
                            f"Startup recovery: task {task.id} permanently failed"
                        )
                        self._log_recovery_event(task, "startup_permanently_failed")
                    
                    recovered += 1
                    # NEW: Notify parent
                    if self._on_task_permanently_failed:
                        try:
                            self._on_task_permanently_failed(
                                task.instance_id,
                                f"Startup recovery: max retries ({self._max_retries}) exceeded",
                                task.message_id,
                            )
                        except Exception as cb_err:
                            logger.error(
                                f"Failed to notify parent of permanent task failure "
                                f"(instance={task.instance_id[:8]}..., error={cb_err})"
                            )
                    
                except Exception as e:
                    logger.error(f"Startup recovery failed for task {task.id}: {e}")
        
        # Phase B: Detect orphaned CANCELLED tasks (crash between cancel and retry)
        orphaned_tasks = self._task_repo.find_orphaned_cancelled_tasks()
        
        if orphaned_tasks:
            logger.warning(
                f"Startup recovery: found {len(orphaned_tasks)} orphaned CANCELLED tasks"
            )
            
            for task in orphaned_tasks:
                try:
                    retry_task = self._task_repo.schedule_retry(
                        task_id=task.id,
                        max_retries=self._max_retries,
                        backoff_base=self._retry_backoff_base,
                        backoff_max=self._retry_backoff_max,
                    )
                    
                    if retry_task:
                        logger.info(
                            f"Startup recovery (orphan): task {task.id} → retry {retry_task.id}"
                        )
                        self._log_recovery_event(task, "orphan_recovered",
                                                   retry_task_id=retry_task.id)
                        # Bus cancel: orphan recovery rescheduled a task that
                        # was left CANCELLED by a prior crash. Any bus watchers
                        # against the orphaned task id are stranded — release
                        # them.
                        self._notify_bus_of_cancel_and_retry(task.id, retry_task.id)
                        recovered += 1
                    else:
                        # Max retries exceeded — mark permanent fail
                        self._task_repo.fail_task(
                            task.id,
                            f"Startup recovery (orphan): max retries ({self._max_retries}) exceeded"
                        )
                        self._log_recovery_event(task, "orphan_permanently_failed")
                        recovered += 1
                        # NEW: Notify parent
                        if self._on_task_permanently_failed:
                            try:
                                self._on_task_permanently_failed(
                                    task.instance_id,
                                    f"Startup recovery (orphan): max retries ({self._max_retries}) exceeded",
                                    task.message_id,
                                )
                            except Exception as cb_err:
                                logger.error(
                                    f"Failed to notify parent of permanent task failure "
                                    f"(instance={task.instance_id[:8]}..., error={cb_err})"
                                )
                    
                except Exception as e:
                    logger.error(
                        f"Startup recovery (orphan) failed for task {task.id}: {e}"
                    )
        
        logger.info(f"Startup recovery complete: {recovered} tasks recovered")
        return recovered
    
    def _log_recovery_event(
        self,
        task,
        action: str,
        retry_task_id: int | None = None,
    ) -> None:
        """Log recovery event to event repository."""
        if not self._event_repo:
            return
        
        try:
            self._event_repo.create_event(
                instance_id=task.instance_id,
                kind=f"task_recovery_{action}",
                data={
                    "task_id": task.id,
                    "message_id": task.message_id,
                    "worker_id": task.worker_id,
                    "retry_count": task.retry_count,
                    "retry_task_id": retry_task_id,
                }
            )
        except Exception as e:
            logger.debug(f"Failed to log recovery event: {e}")
    
    def is_running(self) -> bool:
        """Check if the recovery thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def _notify_bus_of_cancel_and_retry(
        self, cancelled_task_id: int, retry_task_id: int
    ) -> None:
        """Notify the bus that a task was force-cancelled and a retry was scheduled.

        Production incident 2026-06-26: when ``StaleTaskRecovery`` force-
        cancels a stale task and schedules a retry, the bus's PENDING
        watchers keyed on the cancelled ``source_task_id`` are orphaned.
        The retry registers its OWN watcher when it runs through
        ``send_message``, but the original watcher never fires (the bus
        only fires PENDING → FIRED via ``emit_terminal`` on the
        cancelling task's id). The parent stays in ``waiting_children``
        forever.

        Calls ``on_task_cancelled_and_retried`` (wired by the manager to
        ``bus.cancel_for_source`` via ``MainLoopBridge``) so the
        cancellation runs on the asyncio event loop — the bus is async
        and the recovery thread is a plain ``threading.Thread``.

        Failures are logged but never re-raised — the bus notification is
        a defense-in-depth measure; the recovery action itself
        (cancel + reschedule) already succeeded. A missing or failed bus
        call leaves the parent in ``waiting_children`` until manual
        intervention (matches the pre-fix behavior the user is reporting
        here). Operations that want stricter guarantees can wire their
        own retry on top.

        Args:
            cancelled_task_id: The id of the task that was just cancelled
                by this recovery action.
            retry_task_id: The id of the newly-scheduled retry task. Not
                used directly — the bus notification is keyed on the
                cancelled task id — but passed for logging context.
        """
        if self._on_task_cancelled_and_retried is None:
            return
        try:
            self._on_task_cancelled_and_retried(cancelled_task_id, retry_task_id)
        except Exception as cb_err:
            logger.error(
                f"Failed to notify bus of cancel-and-retry "
                f"(cancelled_task={cancelled_task_id}, retry_task={retry_task_id}): "
                f"{cb_err}"
            )
