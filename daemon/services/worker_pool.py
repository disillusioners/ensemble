"""Worker pool for message queue redesign - stateless worker threads."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from daemon.cancellation import CancellationReason, OperationCancelledError
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from daemon.services.task_processor import Task

logger = logging.getLogger(__name__)

# Default poll interval: 0.5 seconds (responsive but not aggressive)
DEFAULT_POLL_INTERVAL = 0.5
# Default task timeout: 5 minutes (300 seconds)
DEFAULT_TASK_TIMEOUT = 300.0


class Worker(threading.Thread):
    """Worker thread that polls the database for tasks and processes them.
    
    Workers are completely stateless — no in-memory state, no persistent
    connections to other services. All state is in the database.
    
    Each worker:
    1. Polls the database for pending tasks (atomic claim via UPDATE-RETURNING)
    2. Runs the task asynchronously via the main event loop
    3. Updates task status in the database (complete or fail)
    4. Repeats
    """
    
    def __init__(
        self,
        worker_id: str,
        task_processor,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout_minutes: float = 45.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
    ):
        """Initialize a worker thread.
        
        Args:
            worker_id: Unique identifier for this worker.
            task_processor: TaskProcessor instance to delegate task processing.
            poll_interval: How often to poll for tasks (seconds).
            timeout_minutes: Task timeout in minutes.
            max_retries: Maximum number of retry attempts.
            retry_backoff_base: Base for exponential backoff (seconds).
            retry_backoff_max: Maximum backoff time (seconds).
        """
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self._task_processor = task_processor
        self._poll_interval = poll_interval
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._stop_event = threading.Event()
        self._tasks_claimed = 0
        self._tasks_completed = 0
        self._tasks_failed = 0
    
    def run(self) -> None:
        """Main loop: poll for tasks and process them."""
        logger.info(f"Worker {self.worker_id} started")
        
        while not self._stop_event.is_set():
            task = None
            try:
                # Attempt to atomically claim a pending task
                task = self._task_processor.claim_task(self.worker_id)
                
                if task is not None:
                    self._tasks_claimed += 1
                    logger.debug(
                        f"Worker {self.worker_id} claimed task {task.id} "
                        f"(type={task.task_type}, instance={task.instance_id[:8]}...)"
                    )
                    
                    # Run the task asynchronously via the main event loop
                    # This is the FIX: C1 pattern - thread to async bridge
                    self._process_with_timeout(task)
                else:
                    # No work available, sleep and retry
                    self._stop_event.wait(timeout=self._poll_interval)
                    
            except Exception as e:
                logger.error(f"Worker {self.worker_id} unexpected error: {e}", exc_info=True)
                # Brief sleep to avoid tight error loop
                self._stop_event.wait(timeout=1.0)
        
        logger.info(
            f"Worker {self.worker_id} stopped: "
            f"claimed={self._tasks_claimed}, "
            f"completed={self._tasks_completed}, "
            f"failed={self._tasks_failed}"
        )
    
    def stop(self, timeout: float = 10.0) -> None:
        """Signal the worker to stop and wait for it to finish."""
        logger.debug(f"Stopping worker {self.worker_id}...")
        self._stop_event.set()
        self.join(timeout=timeout)
        if self.is_alive():
            logger.warning(f"Worker {self.worker_id} did not stop within {timeout}s")
    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "worker_id": self.worker_id,
            "tasks_claimed": self._tasks_claimed,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "is_alive": self.is_alive(),
        }
    
    def _process_with_timeout(self, task: "Task") -> None:
        """Process a task with timeout monitoring and retry logic."""
        from daemon.cancellation import CancellationTokenSource
        from daemon.services.timeout_monitor import TimeoutMonitor
        
        # Create cancellation infrastructure
        source = CancellationTokenSource()
        token = source.token
        timeout_seconds = self._timeout_minutes * 60
        
        monitor = TimeoutMonitor(
            task_id=task.id,
            source=source,
            timeout_seconds=timeout_seconds,
        )
        monitor.start()
        
        try:
            # Run the task with cancellation token
            self._task_processor.run_task(task, cancellation_token=token)
            self._tasks_completed += 1
            logger.debug(f"Worker {self.worker_id} completed task {task.id}")
            
        except OperationCancelledError as e:
            # Task was cancelled (timeout or other reason)
            logger.warning(
                f"Worker {self.worker_id}: task {task.id} cancelled: {e.message}"
            )
            self._handle_cancellation(task, e.reason)
            
        except TimeoutError:
            # MainLoopBridge safety timeout (shouldn't happen normally)
            logger.error(
                f"Worker {self.worker_id}: task {task.id} hit safety timeout"
            )
            self._handle_cancellation(
                task, CancellationReason.TIMEOUT
            )
            
        except Exception as e:
            # Other error — decide retry vs permanent fail
            logger.error(
                f"Worker {self.worker_id} failed task {task.id}: {e}",
                exc_info=True
            )
            self._handle_task_failure(task, str(e))
            
        finally:
            monitor.stop()
    
    def _handle_cancellation(
        self, task: "Task", reason: "CancellationReason"
    ) -> None:
        """Handle task cancellation — schedule retry or permanent fail."""
        if reason == CancellationReason.TIMEOUT:
            # Try to schedule a retry
            retry_task = self._task_processor._task_repo.schedule_retry(
                task_id=task.id,
                max_retries=self._max_retries,
                backoff_base=self._retry_backoff_base,
                backoff_max=self._retry_backoff_max,
            )
            
            if retry_task:
                logger.info(
                    f"Worker {self.worker_id}: scheduled retry {retry_task.id} "
                    f"for task {task.id} (attempt {retry_task.retry_count}/{self._max_retries})"
                )
                self._tasks_failed += 1  # Count original task as failed
            else:
                # Max retries exceeded
                self._task_processor._task_repo.fail_task(
                    task.id,
                    f"Task cancelled after {self._max_retries} retries"
                )
                self._tasks_failed += 1
                logger.warning(
                    f"Worker {self.worker_id}: task {task.id} permanently failed "
                    f"after {self._max_retries} retries"
                )
        else:
            # Non-timeout cancellation (shutdown, user request, etc.)
            self._task_processor._task_repo.cancel_task(
                task.id, reason=f"Cancelled: {reason.value}"
            )
            self._tasks_failed += 1
    
    def _handle_task_failure(self, task: "Task", error: str) -> None:
        """Handle task failure — schedule retry or permanent fail."""
        # For now: fail permanently. Retry-on-error is a separate feature.
        # Timeout cancellation already handles retry.
        self._task_processor._task_repo.fail_task(task.id, error)
        self._tasks_failed += 1


class WorkerPool:
    """Manages a pool of worker threads for processing tasks.
    
    The worker pool manages the lifecycle of multiple worker threads:
    - start(): Creates and starts N worker threads
    - stop(): Gracefully stops all workers
    - get_stats(): Returns statistics for monitoring
    """
    
    def __init__(
        self,
        task_processor,
        num_workers: int = 4,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout_minutes: float = 45.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
    ):
        """Initialize the worker pool.
        
        Args:
            task_processor: TaskProcessor instance for task processing.
            num_workers: Number of worker threads to spawn.
            poll_interval: How often workers poll for tasks (seconds).
            timeout_minutes: Task timeout in minutes.
            max_retries: Maximum number of retry attempts.
            retry_backoff_base: Base for exponential backoff (seconds).
            retry_backoff_max: Maximum backoff time (seconds).
        """
        self._task_processor = task_processor
        self._num_workers = num_workers
        self._poll_interval = poll_interval
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._workers: list[Worker] = []
        self._started = False
        self._stopped = False
    
    def start(self) -> None:
        """Start all worker threads."""
        if self._started:
            logger.warning("WorkerPool already started")
            return
        
        if self._stopped:
            raise RuntimeError("WorkerPool was stopped and cannot be restarted")
        
        logger.info(f"Starting WorkerPool with {self._num_workers} workers...")
        
        for i in range(self._num_workers):
            worker = Worker(
                worker_id=f"worker-{i}",
                task_processor=self._task_processor,
                poll_interval=self._poll_interval,
                timeout_minutes=self._timeout_minutes,
                max_retries=self._max_retries,
                retry_backoff_base=self._retry_backoff_base,
                retry_backoff_max=self._retry_backoff_max,
            )
            worker.start()
            self._workers.append(worker)
        
        self._started = True
        logger.info(f"WorkerPool started: {len(self._workers)} workers")
    
    def stop(self, timeout: float = 30.0) -> None:
        """Gracefully stop all workers.
        
        Each worker finishes its current task before stopping.
        
        Args:
            timeout: Maximum time to wait for all workers to stop.
        """
        if not self._started:
            logger.warning("WorkerPool not started, nothing to stop")
            return
        
        logger.info(f"Stopping WorkerPool ({len(self._workers)} workers)...")
        
        # Signal all workers to stop
        for worker in self._workers:
            worker.stop(timeout=0)  # Signal only, don't wait
        
        # Wait for all workers to stop
        per_worker_timeout = timeout / max(len(self._workers), 1)
        for worker in self._workers:
            worker.join(timeout=per_worker_timeout)
        
        self._stopped = True
        logger.info("WorkerPool stopped")
    
    def is_running(self) -> bool:
        """Check if the pool is running."""
        return self._started and not self._stopped and all(w.is_alive() for w in self._workers)
    
    def get_stats(self) -> dict:
        """Get statistics for the pool and all workers."""
        return {
            "num_workers": len(self._workers),
            "started": self._started,
            "stopped": self._stopped,
            "is_running": self.is_running(),
            "workers": [w.get_stats() for w in self._workers],
            "pool_pending_tasks": self._task_processor.get_pending_count(),
        }
