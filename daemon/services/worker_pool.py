"""Worker pool for message queue redesign - notification-driven worker threads."""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from typing import TYPE_CHECKING

from daemon.cancellation import CancellationReason, OperationCancelledError
from daemon.constants import MAX_ERROR_LEN
from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from daemon.services.task_processor import Task
    from daemon.repositories.task.repository import TaskRepository

logger = logging.getLogger(__name__)

# Default task timeout: 5 minutes (300 seconds)
DEFAULT_TASK_TIMEOUT = 300.0

# Default heartbeat interval: 30 seconds. The recovery service's
# stale threshold (default 5 min) is sized so a crashed worker is
# detected within ~10 missed heartbeats. 30s keeps DB write load
# low (≤2/min per active task) while keeping detection latency
# bounded.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0


def _truncate_error(error: str, max_len: int = MAX_ERROR_LEN) -> str:
    """Truncate error message, stripping HTML if present."""
    # Strip HTML tags and reduce whitespace
    if "<" in error and ">" in error:
        error = error.replace("<", " <").replace(">", "> ")
        error = re.sub(r"<[^>]+>", "", error)
        error = " ".join(error.split())
    if len(error) > max_len:
        return error[:max_len] + "..."
    return error


class TaskHeartbeat:
    """Per-worker daemon thread that updates a task's heartbeat timestamp.

    While a worker is processing a task, this thread wakes every
    ``interval_seconds`` and calls ``task_repo.update_heartbeat(task_id)``.
    The recovery service uses ``last_heartbeat_at`` to distinguish a live
    long-running task from a crashed one.

    Crash semantics:
    - If the worker process dies, the heartbeat thread dies with it.
      ``last_heartbeat_at`` stops being updated. The recovery service
      flags the task as stale within ``stale_task_recovery_threshold_minutes``.
    - If only the heartbeat thread dies (e.g. unhandled exception in
      ``_run``), the worker continues processing. ``last_heartbeat_at``
      stops being updated; the task is eventually flagged as stale
      and force-cancel-retry fires. The original worker's result is
      wasted, but the next retry succeeds. The heartbeat thread is
      re-created on the next ``set_task`` call.

    Thread safety: ``set_task`` and ``_run`` coordinate through a
    short lock. The DB write itself is atomic via UPDATE.
    """

    def __init__(
        self,
        task_repo: "TaskRepository",
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ):
        self._task_repo = task_repo
        self._interval = interval_seconds
        self._current_task_id: int | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._missed_heartbeats = 0  # diagnostic; resets on successful update

    def set_task(self, task_id: int | None) -> None:
        """Set the task whose heartbeat should be kept fresh.

        Pass ``None`` when the worker is idle (no task being processed).
        The heartbeat thread remains alive but performs no DB writes
        while ``current_task_id`` is None; this avoids restart cost on
        the hot path.
        """
        with self._lock:
            self._current_task_id = task_id
        # Eagerly refresh on claim so the recovery service sees a fresh
        # heartbeat even before the first interval tick.
        if task_id is not None:
            self._beat_now(task_id)

    def start(self) -> None:
        """Start the heartbeat thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"TaskHeartbeat-{id(self)}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the heartbeat thread. Safe to call multiple times."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        """Heartbeat loop: every interval, update current task's heartbeat."""
        while not self._stop_event.is_set():
            # Sleep first, then check current task. This avoids a redundant
            # DB write on the very first iteration (claim path already
            # calls _beat_now() eagerly via set_task()).
            interrupted = self._stop_event.wait(timeout=self._interval)
            if interrupted:
                return  # stop requested
            with self._lock:
                task_id = self._current_task_id
            if task_id is not None:
                self._beat_now(task_id)

    def _beat_now(self, task_id: int) -> None:
        """Single heartbeat update. Logs and swallows errors.

        Errors are non-fatal: a failed heartbeat just means the recovery
        service may flag the task as stale on the next pass, which is
        recoverable via the retry path.
        """
        try:
            ok = self._task_repo.update_heartbeat(task_id)
            if ok:
                self._missed_heartbeats = 0
            else:
                # Task no longer RUNNING (cancelled or completed by recovery).
                # The worker will discover this on its next read; do nothing.
                self._missed_heartbeats += 1
        except Exception as e:  # noqa: BLE001
            self._missed_heartbeats += 1
            logger.warning(
                f"Heartbeat update failed for task {task_id}: {type(e).__name__}: {e}"
            )


class Worker(threading.Thread):
    """Worker thread that processes tasks using notification-based coordination.

    Workers are completely stateless — no in-memory state, no persistent
    connections to other services. All state is in the database.

    Each worker:
    1. Attempts to claim a pending task from the database
    2. If no task available, waits for notification from the pool
    3. Runs the task asynchronously via the main event loop
    4. Updates task status in the database (complete or fail)
    5. Repeats

    The worker pool coordinates via threading.Condition to wake workers
    when new work arrives, avoiding continuous polling.

    Each worker also owns a TaskHeartbeat (started by the pool) that
    updates ``task.last_heartbeat_at`` periodically while a task is
    in flight. The recovery service uses this column to distinguish
    a live task (heartbeat fresh) from a crashed one (heartbeat stale).
    """

    def __init__(
        self,
        worker_id: str,
        task_processor,
        worker_pool,
        timeout_minutes: float = 45.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ):
        """Initialize a worker thread.

        Args:
            worker_id: Unique identifier for this worker.
            task_processor: TaskProcessor instance to delegate task processing.
            worker_pool: WorkerPool instance for notification coordination.
            timeout_minutes: Task timeout in minutes.
            max_retries: Maximum number of retry attempts.
            retry_backoff_base: Base for exponential backoff (seconds).
            retry_backoff_max: Maximum backoff delay (seconds).
            heartbeat_interval_seconds: How often the per-worker heartbeat
                thread updates ``task.last_heartbeat_at``. Sizing: keep
                this smaller than ``stale_task_recovery_threshold_minutes``
                so the recovery service sees a fresh heartbeat from
                every live task.
        """
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self._task_processor = task_processor
        self._worker_pool = worker_pool
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._stop_event = threading.Event()
        self._tasks_claimed = 0
        self._tasks_completed = 0
        self._tasks_failed = 0

        # Per-worker heartbeat. Lazily started in run() so the task
        # repository is fully wired before the thread begins.
        self._heartbeat = TaskHeartbeat(
            task_repo=self._task_processor._task_repo,
            interval_seconds=heartbeat_interval_seconds,
        )
    
    def run(self) -> None:
        """Main loop: claim tasks or wait for notification."""
        logger.info(f"Worker {self.worker_id} started")

        # Start heartbeat thread now that the worker is alive. It will
        # run for the lifetime of the worker process and stop in stop().
        self._heartbeat.start()
        try:
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

                        # Tell the heartbeat which task is in flight. The
                        # set_task() call also does an eager first beat so
                        # the recovery service sees a fresh heartbeat
                        # immediately, not after the first interval tick.
                        self._heartbeat.set_task(task.id)

                        try:
                            # Run the task asynchronously via the main event loop
                            # This is the FIX: C1 pattern - thread to async bridge
                            self._process_with_timeout(task)
                        finally:
                            # Clear the heartbeat so it stops writing to
                            # this task's row once the worker is done (or
                            # mid-retry). Critical: a stale ``current_task_id``
                            # would race the next claim and write to the
                            # wrong row.
                            self._heartbeat.set_task(None)

                        continue  # Check for more work immediately

                    # No task available → this is an empty claim attempt
                    self._worker_pool.incr_stat("empty_claim_attempts")

                    # Track whether the empty claim was due to the per-instance
                    # guard (pending tasks exist but all are blocked by a RUNNING
                    # task for the same instance). This surfaces "is Fix B causing
                    # excessive deferral?" in production.
                    if self._task_processor._task_repo.has_pending_tasks_blocked_by_busy_instance():
                        self._worker_pool.incr_stat("claims_skipped_due_to_busy_instance")

                    # Wait for notification OR safety timeout OR stop signal
                    self._worker_pool.wait_for_work(timeout=3.0, stop_event=self._stop_event)
                    # Loop back to try claiming again

                except Exception as e:
                    logger.error(f"Worker {self.worker_id} unexpected error: {e}", exc_info=True)
                    # Make sure heartbeat doesn't keep writing to a half-claimed task
                    self._heartbeat.set_task(None)
                    # Wait for work notification during error recovery
                    self._worker_pool.wait_for_work(timeout=1.0, stop_event=self._stop_event)
        finally:
            # Stop the heartbeat regardless of how we exit the loop
            self._heartbeat.stop()

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

        except concurrent.futures.CancelledError:
            # Pause cancelled the coroutine through run_coroutine_threadsafe.
            #
            # Phase 2 (pause/resume redesign, 2026-06-25) — B2 contract:
            #
            #   DO NOT call ``task_repo.complete_task`` here. The pause
            #   cascade (``pause_instance_cascade`` →
            #   ``_pause_cascade_db_sync``) is the SOLE writer of the
            #   task's PAUSED status; it has not yet run when this
            #   ``except`` block fires (the DB sync executes AFTER the
            #   graph task cancellation completes — see
            #   ``pause_instance_cascade`` lines 986-1018).
            #
            #   If we called ``complete_task`` here, two outcomes are
            #   possible:
            #
            #     1. DB sync runs AFTER ``complete_task`` → the task's
            #        ``WHERE status = running`` guard in UPDATE 3 of
            #        ``_pause_cascade_db_sync`` rowcount-drops (task
            #        already terminal). Functionally correct but wastes
            #        a DB write, generates an erroneous "task marked
            #        PAUSED while COMPLETED" log, and may violate
            #        invariant assertions downstream.
            #     2. DB sync runs BEFORE ``complete_task`` → the task
            #        is PAUSED, ``complete_task``'s ``WHERE status =
            #        running`` guard rowcount-drops (silent no-op). The
            #        task is correctly left in PAUSED for resume, but
            #        the path took a wasted DB roundtrip.
            #
            #   In both orderings the correct observable state is
            #   "task in PAUSED, ready for resume". Returning here
            #   without calling ``complete_task`` is the cleanest
            #   contract: the pause cascade owns the DB transition,
            #   the worker pool owns the concurrency-slot release
            #   (``finally`` block below), and resume owns the
            #   PAUSED → PENDING → CLAIMED re-claim path (Phase 3).
            #
            # DO NOT fail the task either (``fail_task``) — the work
            # was interrupted by user action, not by an error, and the
            # task must be re-claimable on resume, not dead-lettered.
            #
            # Task stays in RUNNING briefly until the DB sync flips it
            # to PAUSED — this is safe because the per-instance pause
            # gate in ``claim_pending_task`` already excludes PAUSED
            # instances, and the parallel UPDATE in
            # ``_pause_cascade_db_sync`` flips RUNNING → PAUSED in the
            # SAME WriteGuardSession as the instance status change.
            logger.info(
                f"Worker {self.worker_id}: task {task.id} paused "
                "(concurrent.futures.CancelledError — B2 contract: "
                "do NOT complete_task; pause cascade owns PAUSED write)"
            )
            # Task stays in RUNNING state — no failure, no retry
            return

        except Exception as e:
            # Other error — decide retry vs permanent fail
            error_msg = _truncate_error(str(e))
            logger.error(
                f"Worker {self.worker_id} failed task {task.id}: {error_msg}",
                exc_info=True
            )
            self._handle_task_failure(task, error_msg)
            
        finally:
            monitor.stop()
    
    def _notify_parent_of_failure(
        self,
        instance_id: str,
        error: str,
        error_type: str,
        message_id: str | None,
    ) -> None:
        """Notify parent instance of permanent failure via MainLoopBridge.
        
        Args:
            instance_id: The child instance ID that failed.
            error: Error message describing what went wrong.
            error_type: Category of error (e.g., "max_retries_exceeded", "cancelled").
            message_id: Optional message ID associated with the failure.
        """
        # Check if task_processor has _manager with _send_error_report method
        # Use try/except for hasattr to handle mocks that raise AttributeError
        try:
            manager = self._task_processor._manager
        except AttributeError:
            manager = None
        
        if manager is not None and hasattr(manager, '_send_error_report'):
            from daemon.services.main_loop_bridge import MainLoopBridge
            logger.info(
                f"Worker {self.worker_id}: notifying parent of failure "
                f"(instance={instance_id[:8]}..., type={error_type})"
            )
            MainLoopBridge.run_async_no_wait(
                manager._send_error_report(
                    instance_id=instance_id,
                    error=error,
                    error_type=error_type,
                    message_id=message_id,
                )
            )

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
                # Notify parent that child failed permanently
                self._notify_parent_of_failure(
                    instance_id=task.instance_id,
                    error=f"Task cancelled after {self._max_retries} retries",
                    error_type="max_retries_exceeded",
                    message_id=task.message_id,
                )
        else:
            # Non-timeout cancellation (shutdown, user request, etc.)
            self._task_processor._task_repo.cancel_task(
                task.id, reason=f"Cancelled: {reason.value}"
            )
            self._tasks_failed += 1
            # Notify parent that child was cancelled
            self._notify_parent_of_failure(
                instance_id=task.instance_id,
                error=f"Task cancelled: {reason.value}",
                error_type="cancelled",
                message_id=task.message_id,
            )
    
    def _handle_task_failure(self, task: "Task", error: str) -> None:
        """Handle task failure — schedule retry or permanent fail."""
        # For now: fail permanently. Retry-on-error is a separate feature.
        # Timeout cancellation already handles retry.
        self._task_processor._task_repo.fail_task(task.id, error)
        self._tasks_failed += 1


class WorkerPool:
    """Manages a pool of worker threads using notification-based coordination.
    
    The worker pool manages the lifecycle of multiple worker threads:
    - start(): Creates and starts N worker threads
    - stop(): Gracefully stops all workers
    - get_stats(): Returns statistics for monitoring
    
    Workers wait on a threading.Condition when idle, and are woken via
    notify_all() when new work arrives. A safety-net 3-second timeout
    ensures workers never permanently sleep.
    
    The pool tracks metrics:
    - notifications_sent: Total wakeups signaled
    - empty_claim_attempts: Claims that found no work
    - workers_woken_by_timeout: Workers that woke via timeout (not notification)
    - wakeup_efficiency: Ratio of useful notifications to total attempts
    """
    
    def __init__(
        self,
        task_processor,
        num_workers: int = 4,
        timeout_minutes: float = 45.0,
        max_retries: int = 3,
        retry_backoff_base: int = 60,
        retry_backoff_max: int = 3600,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ):
        """Initialize the worker pool.

        Args:
            task_processor: TaskProcessor instance for task processing.
            num_workers: Number of worker threads to spawn.
            timeout_minutes: Task timeout in minutes.
            max_retries: Maximum number of retry attempts.
            retry_backoff_base: Base for exponential backoff (seconds).
            retry_backoff_max: Maximum backoff delay (seconds).
            heartbeat_interval_seconds: How often each worker's heartbeat
                thread updates ``task.last_heartbeat_at``. Passed through
                to each Worker.
        """
        self._task_processor = task_processor
        self._num_workers = num_workers
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._workers: list[Worker] = []
        self._started = False
        self._stopped = False

        # Notification coordination
        self._condition = threading.Condition()
        self._notification_count = 0

        # Metrics. ``_stats`` is mutated from multiple worker threads
        # (and the API path for ``notifications_sent``); a plain
        # ``dict[key] += 1`` is a read-modify-write that the CPython
        # GIL does NOT make atomic across the bytecode boundary, so
        # concurrent writes can lose increments. Wrap writes in
        # ``_stats_lock`` to keep the counters accurate. Reads from
        # ``get_stats`` are best-effort: a snapshot under the lock is
        # consistent, but a snapshot without the lock can see a value
        # that just got incremented in another thread.
        self._stats_lock = threading.Lock()
        self._stats = {
            "notifications_sent": 0,
            "empty_claim_attempts": 0,
            "workers_woken_by_timeout": 0,
            "claims_skipped_due_to_busy_instance": 0,
        }

        # Event for test instrumentation - set when wait_for_work() is called
        # Tests can wait on this to synchronize with workers entering wait state
        self._wait_for_work_called = threading.Event()
    
    def notify_work(self) -> None:
        """Signal that new work is available. Safe to call from any thread."""
        with self._condition:
            self._notification_count += 1
            self._condition.notify()
        with self._stats_lock:
            self._stats["notifications_sent"] += 1

    def incr_stat(self, key: str, delta: int = 1) -> None:
        """Atomically increment a counter in ``_stats``.

        Single-call site for all worker-side metric writes. The lock
        here is the same one used by ``notify_work`` and
        ``wait_for_work``, so all writers serialize through one
        mutex. Contention is low (≤1 increment per claim attempt per
        worker, with 4 workers at idle the rate is ~80/min) — well
        below the cost of per-worker counters.
        """
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + delta
    
    def wait_for_work(self, timeout: float = 3.0, stop_event: threading.Event = None) -> bool:
        """Worker calls this when idle. Returns True if notified, False if timed out.
        
        Args:
            timeout: Maximum time to wait in seconds. timeout=0 performs a non-blocking
                check and does not count as a timeout wake.
            stop_event: Optional event to check for early exit. If set, returns False.
        
        Returns:
            True if woken by notify_work(), False if timed out or stopped.
        """
        # Signal for test instrumentation
        self._wait_for_work_called.set()
        
        with self._condition:
            start_time = time.monotonic()
            while self._notification_count == 0:
                # Check stop signal first to avoid waiting when shutting down
                if stop_event is not None and stop_event.is_set():
                    return False
                elapsed = time.monotonic() - start_time
                remaining = timeout - elapsed
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            with self._stats_lock:
                self._stats["workers_woken_by_timeout"] += 1
            return False
    
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
                worker_pool=self,
                timeout_minutes=self._timeout_minutes,
                max_retries=self._max_retries,
                retry_backoff_base=self._retry_backoff_base,
                retry_backoff_max=self._retry_backoff_max,
                heartbeat_interval_seconds=self._heartbeat_interval_seconds,
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
        
        # Wake all sleeping workers so they see the stop signal
        with self._condition:
            self._condition.notify_all()
        
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
        # Snapshot under the lock so the four counters are mutually
        # consistent. Outside the lock, two concurrent increments can
        # produce a snapshot that pairs e.g. the pre-increment value
        # of one counter with the post-increment value of another,
        # which would skew the ratio-based efficiency metric below.
        with self._stats_lock:
            notifications = self._stats["notifications_sent"]
            timeouts = self._stats["workers_woken_by_timeout"]
            empty_claims = self._stats["empty_claim_attempts"]
            skipped = self._stats["claims_skipped_due_to_busy_instance"]

        # Wakeup efficiency: useful notifications / total notifications
        # A useful notification = one that leads to claiming work
        # A timeout = notification was unnecessary (workers would have polled anyway)
        wakeup_efficiency = notifications / max(1, notifications + empty_claims)

        return {
            "num_workers": len(self._workers),
            "started": self._started,
            "stopped": self._stopped,
            "is_running": self.is_running(),
            "notifications_sent": notifications,
            "empty_claim_attempts": empty_claims,
            "workers_woken_by_timeout": timeouts,
            "claims_skipped_due_to_busy_instance": skipped,
            "wakeup_efficiency": round(wakeup_efficiency, 3),
            "workers": [w.get_stats() for w in self._workers],
            "pool_pending_tasks": self._task_processor.get_pending_count(),
        }
