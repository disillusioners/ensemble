"""Stale task recovery service for crash recovery."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Default threshold: 15 minutes (LLM calls can legitimately take 5-10 minutes)
DEFAULT_STALE_THRESHOLD_MINUTES = 15
# Default check interval: 60 seconds
DEFAULT_CHECK_INTERVAL_SECONDS = 60


class StaleTaskRecovery:
    """Background service that recovers stale tasks from worker crashes.
    
    Periodically scans for tasks that have been running longer than the
    threshold (default: 15 minutes) and resets them to pending status.
    
    This is important because:
    1. Workers can crash (OOM, SIGKILL, etc.)
    2. Tasks may be orphaned with status='running' but no active worker
    3. The crash recovery resets these to 'pending' so another worker can claim them
    """
    
    def __init__(
        self,
        task_repository,
        message_repository,
        threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES,
        check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS,
        event_repository=None,
    ):
        """Initialize stale task recovery.
        
        Args:
            task_repository: TaskRepository instance for task operations.
            message_repository: MessageQueueRepository for message recovery.
            threshold_minutes: Tasks running longer than this are considered stale.
            check_interval_seconds: How often to check for stale tasks.
            event_repository: Optional EventRepository for logging recovery events.
        """
        self._task_repo = task_repository
        self._message_repo = message_repository
        self._event_repo = event_repository
        self._threshold_minutes = threshold_minutes
        self._check_interval = check_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
    
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
            f"interval={self._check_interval}s"
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
        """Find and reset stale tasks.
        
        Returns:
            Number of tasks recovered.
        """
        # Find tasks that have been running too long
        stale_tasks = self._task_repo.find_stale_running_tasks(
            threshold_minutes=self._threshold_minutes
        )
        
        if not stale_tasks:
            return 0
        
        recovered_count = 0
        
        for task in stale_tasks:
            try:
                # Reset task to pending
                self._task_repo.reset_stale_tasks(
                    threshold_minutes=self._threshold_minutes
                )
                
                # Also reset the associated message
                if task.message_id:
                    try:
                        self._message_repo.fail(task.message_id, "Worker crashed - task reset to pending")
                    except Exception as e:
                        logger.warning(
                            f"Failed to reset message {task.message_id[:8]}...: {e}"
                        )
                
                # Log recovery event if event repository available
                if self._event_repo:
                    self._event_repo.create_event(
                        instance_id=task.instance_id,
                        kind="task_recovered",
                        data={
                            "task_id": task.id,
                            "message_id": task.message_id,
                            "worker_id": task.worker_id,
                            "reason": f"stale (>{self._threshold_minutes}min)",
                        }
                    )
                
                logger.warning(
                    f"Recovered stale task {task.id}: "
                    f"type={task.task_type}, "
                    f"instance={task.instance_id[:8]}..., "
                    f"worker={task.worker_id}"
                )
                recovered_count += 1
                
            except Exception as e:
                logger.error(f"Failed to recover task {task.id}: {e}")
        
        return recovered_count
    
    def recover_on_startup(self) -> int:
        """Run recovery immediately on startup.
        
        Called during application startup to recover from any previous crash.
        This is synchronous (blocking) since it's part of initialization.
        
        Returns:
            Number of tasks recovered.
        """
        logger.info("Running startup crash recovery...")
        recovered = self.recover_stale_tasks()
        logger.info(f"Startup recovery: {recovered} stale tasks recovered")
        return recovered
    
    def is_running(self) -> bool:
        """Check if the recovery thread is running."""
        return self._thread is not None and self._thread.is_alive()
