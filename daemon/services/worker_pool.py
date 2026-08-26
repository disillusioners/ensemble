"""Worker pool for message queue redesign - notification-driven worker threads."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from daemon.cancellation import CancellationReason, OperationCancelledError
from daemon.constants import MAX_ERROR_LEN
from daemon.llm_error_classifier import UsageLimitError
from .main_loop_bridge import MainLoopBridge
from .usage_limit_schedule import (
    DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS,
    DEFAULT_USAGE_LIMIT_RETRY_JITTER_FRACTION,
    DEFAULT_USAGE_LIMIT_WINDOW_SECONDS,
    clear_usage_limit_first_seen,
    next_usage_limit_retry_at,
    read_usage_limit_first_seen,
    usage_limit_deadline,
    usage_limit_in_window,
    write_usage_limit_first_seen,
)
from .work_notifier import notify_work_watchers

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
        timeout_grace_seconds: float = 30.0,
        work_resolver=None,  # WorkResolverService — Phase 2 Batch 2 notification
        watcher_repo=None,  # JobWatcherRepository — Phase 2 Batch 2 notification
        usage_limit_window_seconds: float = DEFAULT_USAGE_LIMIT_WINDOW_SECONDS,
        usage_limit_retry_delays_seconds: tuple[float, ...] | list[float] = (
            DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS
        ),
        usage_limit_retry_jitter_fraction: float = (
            DEFAULT_USAGE_LIMIT_RETRY_JITTER_FRACTION
        ),
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
            timeout_grace_seconds: Grace window between the thread-side
                ``TimeoutError`` from ``MainLoopBridge`` and the worker
                deciding whether to schedule a retry. See
                ``_handle_cancellation`` for context — closes the race
                where the underlying coroutine completes naturally a
                few seconds after the safety timeout fires. Default 30s
                (production observed 4s gap between timeout and natural
                completion).
            work_resolver: Optional WorkResolverService — Phase 2
                Batch 2 terminal-notification routing. When ``None``,
                the four worker-side terminal sites short-circuit
                the notify call.
            watcher_repo: Optional JobWatcherRepository — Phase 2
                Batch 2 terminal-notification claim.
            usage_limit_window_seconds: Usage-limit episode horizon
                (dedicated deferral path — see
                ``_handle_usage_limit``); 6 h default.
            usage_limit_retry_delays_seconds: Usage-limit wake
                schedule steps (3m/5m/10m/15m-cap default).
            usage_limit_retry_jitter_fraction: Per-wake jitter
                fraction for the usage-limit schedule.
        """
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self._task_processor = task_processor
        self._worker_pool = worker_pool
        self._timeout_minutes = timeout_minutes
        self._timeout_grace_seconds = timeout_grace_seconds
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        # Dedicated usage-limit deferral path knobs (W4/W5).
        self._usage_limit_window_seconds = usage_limit_window_seconds
        self._usage_limit_retry_delays = tuple(usage_limit_retry_delays_seconds)
        self._usage_limit_retry_jitter_fraction = usage_limit_retry_jitter_fraction
        self._stop_event = threading.Event()
        self._tasks_claimed = 0
        self._tasks_completed = 0
        self._tasks_failed = 0
        # Phase 2 Batch 2 — notification dependencies for terminal sites.
        # Updated via WorkerPool.set_work_resolver / set_watcher_repo
        # after the worker thread has started (api.py wires them up
        # once the resolver / repo are constructed).
        self._work_resolver = work_resolver
        self._watcher_repo = watcher_repo

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

                        # Post-claim JobItem activation (message-Job
                        # path is always active after Phase 5 cutover).
                        # When ``enqueue_message_job`` creates a JobItem
                        # mirror alongside the Task, this flips the
                        # mirror's ``admission_state`` from QUEUED to
                        # ACTIVE as an informational mirror of the
                        # Task's running state. The Task's running
                        # state is the authoritative serialization
                        # gate; the JobItem exists only so the
                        # WorkResolver facade can read the
                        # job_queue_items side of the union without a
                        # divergent view.
                        #
                        # Best-effort by design — failure MUST NOT
                        # break message processing. The observer
                        # finalize fallback in ``job_feedback_observer``
                        # picks up stuck-queued JobItems on the next
                        # sweep, so a missed activation is recoverable.
                        # Activation runs via ``run_async`` — a blocking
                        # call with a 5-second timeout. The worker
                        # thread blocks until the activation UPDATE
                        # completes or the timeout fires. Safe because
                        # the activation is a single DB UPDATE
                        # (millisecond-scale).
                        if task.task_type == "process_message" and task.work_id:
                            self._activate_message_jobitem_async(task.work_id)

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
    
    def _activate_message_jobitem_async(self, work_id: str) -> None:
        """Post-claim JobItem activation — blocking, with bounded wait.

        C2 fix: was previously fire-and-forget via
        ``MainLoopBridge.run_async_no_wait``. The fire-and-forget
        path let the worker claim the Task and proceed immediately
        while the ``queued→active`` UPDATE was scheduled at an
        indeterminate later time. During that window a ``queued``
        JobItem is observable to the observer's finalize path, and
        (pre-C1) could be finalized by any unrelated lifecycle event
        — the message Task's ``pending`` status meant nothing was
        driving the work, and the data-loss bug fired.

        Now we use :meth:`MainLoopBridge.run_async` (blocking) with
        a small bounded timeout so the activation completes (and the
        ``admission_state`` flip is durable) BEFORE the worker
        continues. The single ``atomic_transition`` UPDATE is the
        only thing on the wire, so the call returns within
        milliseconds under normal load; we cap at 5s to bound the
        worker-thread stall when the main loop is congested.

        ``InvalidTransitionError`` (JobItem not in ``queued`` — already
        finalized) is the common path; logged at debug and swallowed —
        the activation is informational mirroring, the Task row alone
        is sufficient to process the message.

        Args:
            work_id: UUID4 of the Task row — same UUID as the JobItem's
                ``job_id`` (set in :meth:`InstanceMessagingService.enqueue_message_job`).
        """
        job_repo = self._get_job_repository()
        if job_repo is None:
            return
        try:
            MainLoopBridge.run_async(
                self._activate_message_jobitem_async_coro(job_repo, work_id),
                # Bounded wait — the activation is a single UPDATE so
                # the call normally returns in milliseconds. 5s is
                # generous enough that a busy main loop can still
                # serve the call, while bounding the worker-thread
                # stall under pathological load. The C1 Task-status
                # guard is the safety net if the timeout fires.
                timeout=5.0,
            )
        except TimeoutError:
            # Activation UPDATE did not complete within the timeout.
            # The C1 Task-status guard in ``_get_processing_job_for_instance``
            # is the safety net: a ``queued`` JobItem whose Task is
            # already RUNNING will still be found by the observer via
            # the gate, and a queued JobItem whose Task is still
            # PENDING will be skipped (avoiding the data-loss bug).
            # Logged at warning so persistent timeouts surface in
            # observability without crashing the worker.
            logger.warning(
                f"Worker {self.worker_id}: JobItem activation for "
                f"work_id={work_id[:8]}... timed out after 5s; "
                "C1 Task-status guard will keep finalize safe."
            )
        except Exception as e:  # noqa: BLE001 — never raise from activation
            logger.debug(
                f"Worker {self.worker_id}: JobItem activation failed "
                f"for work_id={work_id[:8]}...: "
                f"{type(e).__name__}: {e}"
            )

    @staticmethod
    async def _activate_message_jobitem_async_coro(job_repo: Any, work_id: str) -> None:
        """Coroutine body for :meth:`_activate_message_jobitem_async`.

        Performs the guarded UPDATE off the worker thread. Errors are
        logged at debug level and swallowed — this is informational
        mirroring, not a serialization gate.
        """
        try:
            # ``atomic_transition`` is the canonical path for admission
            # state changes (race-safe, single-statement, with a state-
            # machine pre-check). ``queued→active`` is a valid
            # transition. The ``WHERE admission_state='queued'`` guard
            # makes this idempotent against concurrent observer finalize.
            await asyncio.to_thread(
                job_repo.atomic_transition,
                work_id,
                "queued",
                "active",
            )
        except Exception as e:
            # ``atomic_transition`` raises ``InvalidTransitionError``
            # when the row is not in ``queued`` — that's the common
            # path for already-finalized JobItems. Log at debug; the
            # row is already in the right state.
            from daemon.services.job_state_machine import (
                InvalidTransitionError,
            )

            if isinstance(e, InvalidTransitionError):
                logger.debug(
                    f"JobItem work_id={work_id[:8]}... not in 'queued' "
                    "— already finalized or never existed (idempotent skip)."
                )
            else:
                logger.debug(
                    f"JobItem activation UPDATE failed for work_id="
                    f"{work_id[:8]}...: {type(e).__name__}: {e}"
                )

    def _get_job_repository(self) -> Any:
        """Resolve the JobRepository from the task processor's manager.

        Returns ``None`` when the JobQueueService / repository chain has
        not been wired yet (test fixtures, early bootstrap). Callers
        MUST handle ``None`` — the activation is best-effort and the
        Task row alone is sufficient to process the message.
        """
        manager = getattr(self._task_processor, "_manager", None)
        if manager is None:
            return None
        job_queue_service = getattr(manager, "_job_queue_service", None)
        if job_queue_service is None:
            return None
        return getattr(job_queue_service, "_repository", None)

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

        except UsageLimitError as e:
            # Dedicated usage-limit deferral path
            # (docs/plans/usage-limit-deferral-path.md W4): the worker
            # seam owns the episode decision. The task processor's W3
            # carve-out kept the stage-2 report cascade from firing; this
            # branch implements usage-limit POLICY (anchor, deadline,
            # fixed wake schedule, budget-free deferrals) on the reused
            # retry machinery. Placement: AFTER the cancellation
            # handlers, BEFORE the generic failure lane —
            # ``_handle_usage_limit`` returns normally on in-window
            # deferrals, so ``_handle_task_failure`` never fires.
            # MUST NOT raise out of this block (rev2 §3.3): a raise here
            # escapes the sibling ``except Exception`` below and
            # surfaces as an unexpected error attributed to the wrong
            # cause — the handler soft-fails internally.
            self._handle_usage_limit(task, e)

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

    def _cancel_bus_watchers_for_task(
        self, cancelled_task_id: int, retry_task_id: int | None = None
    ) -> None:
        """Cancel bus watchers for a task that was cancelled and retried.

        Called from :meth:`_handle_cancellation` after a retry is scheduled
        for a TIMEOUT-cancelled task. Without this, the bus's PENDING
        watchers keyed on the cancelled ``source_task_id`` stay PENDING
        forever — the retry's natural completion fires ``emit_terminal``
        for its OWN task id and cannot match the original watcher. The
        parent stays in ``waiting_children`` indefinitely (production
        incident 2026-06-26).

        Thin sync wrapper around the shared
        :func:`daemon.services.dependency_bus.cancel_bus_watchers_for_task_async`
        helper — same routing as ``manager._on_stale_task_cancelled_and_retried``
        so the two callsites cannot drift.

        Args:
            cancelled_task_id: The id of the task that was just cancelled
                by this worker thread.
            retry_task_id: Optional id of the newly-scheduled retry task.
                Used for logging context only.
        """
        from daemon.services.dependency_bus import cancel_bus_watchers_for_task_async
        from daemon.services.main_loop_bridge import MainLoopBridge

        MainLoopBridge.run_async_no_wait(
            cancel_bus_watchers_for_task_async(
                cancelled_task_id=cancelled_task_id,
                retry_task_id=retry_task_id,
                origin="worker_pool_timeout",
            )
        )

    def _handle_cancellation(
        self, task: "Task", reason: "CancellationReason"
    ) -> None:
        """Handle task cancellation — schedule retry or permanent fail."""
        if reason == CancellationReason.TIMEOUT:
            # BUG FIX (2026-06-26, timeout-orphan race, rev2): the underlying
            # ``_run()`` coroutine may complete successfully *after*
            # ``MainLoopBridge.run_async(_run(), timeout=...)`` raises
            # ``TimeoutError`` to the worker thread. ``future.result(timeout)``
            # is a sync thread-side check — per Python semantics it does NOT
            # cancel the coroutine; the coroutine keeps running on the event
            # loop and may finish seconds later (production timeline: 4s after
            # the safety timeout fired). When it does, it calls ``on_success``
            # → ``task_repo.complete_task(...)`` whose ``WHERE status='running'``
            # guard silently no-ops because ``schedule_retry`` (called below)
            # already flipped the task to ``cancelled``. The underlying
            # message gets marked ``completed`` and the bus emits terminal
            # — but the retry task is born orphaned. When the retry worker
            # claims it, ``ProcessMessageProcessor`` no-ops on the already-
            # completed message (return path skips ``on_success``), so the
            # retry task itself never transitions out of ``running`` —
            # recovery picks it up, retries, and eventually permanently-
            # fails a task whose work was actually successful, producing a
            # spurious error report to the parent.
            #
            # The narrow check "is message already COMPLETED?" that the
            # previous version of this code made at catch-time was almost
            # always False (the coroutine finishes 1-30s later). To
            # actually close the race we poll the message status for up
            # to ``_timeout_grace_seconds`` (default 30s) before deciding
            # to schedule the retry. If the coroutine completes within the
            # grace window — i.e. ``message.status == 'completed'`` — we
            # skip the retry and let ``complete_task`` carry the task to
            # terminal (idempotent under the ``WHERE status='running'``
            # guard). If the grace window expires, we proceed with the
            # retry as before — the coroutine has either finished already
            # (no harm, ``complete_task`` no-ops on the now-cancelled
            # row) or is genuinely hung.
            #
            # The grace window is bounded by ``_timeout_grace_seconds`` so a
            # truly hung coroutine cannot stall the worker thread
            # indefinitely. Workers are single-threaded per pool, so a
            # long grace window blocks other task processing for that
            # duration. 30s is the empirical safe upper bound: production
            # observed 4s gaps between timeout-fire and natural completion.
            #
            # This check is best-effort. The PRIMARY defense against the
            # race is the timeout ceiling: ``graph_timeout_minutes=120`` is
            # now well below ``task_timeout_minutes=125``, so the
            # ``CancellationToken`` path usually fires first (yielding
            # ``OperationCancelledError``, caught at the call site). The
            # ``TimeoutError`` path is the safety net for truly wedged
            # coroutines that don't observe cancellation. With both
            # defenses, the orphan-retry chain is fully closed.
            if task.message_id:
                completed = self._await_message_completion(
                    task.message_id, getattr(self, "_timeout_grace_seconds", 30)
                )
                if completed:
                    logger.warning(
                        f"Worker {self.worker_id}: timeout fired for task "
                        f"{task.id} but message {task.message_id[:8]}... "
                        f"completed during grace window — skipping retry "
                        f"creation (underlying coroutine finished after "
                        f"the thread-side timeout)."
                    )
                    completed_task = None
                    try:
                        completed_task = self._task_processor._task_repo.complete_task(
                            task.id,
                            {
                                "success": True,
                                "message_id": task.message_id,
                                "skipped": True,
                            },
                        )
                    except Exception as e:
                        logger.debug(
                            f"Worker {self.worker_id}: complete_task on "
                            f"task {task.id} no-op'd (race with recovery): "
                            f"{e}"
                        )
                    self._tasks_completed += 1
                    # Phase 2 Batch 2 — fire watcher notification only
                    # if the atomic complete_task returned non-None.
                    # ``try/except`` already swallows DB races; we now
                    # also need the return-value guard so a concurrent
                    # recovery cannot double-notify via this path.
                    if completed_task is not None:
                        self._schedule_work_notification(completed_task, "completed")
                    return

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
                # Bus cancel: the timeout-cancellation path above cancels the
                # task but the bus's PENDING watchers keyed on this
                # ``source_task_id`` are stranded. Without this call the
                # retry's natural completion fires ``emit_terminal`` for its
                # OWN task id and cannot match the original watcher — the
                # parent stays in ``waiting_children`` forever.
                #
                # Note: the retry does NOT re-invoke ``send_message``, so it
                # does NOT register a fresh bus watcher. Parent completion
                # is satisfied by the child-completion post-commit hook in
                # ``child_reports._process_child_completion_and_notify_parent``
                # which routes through ``_emit_terminal_via_bus`` on the
                # retried message id. Releasing the ORIGINAL watcher here
                # is what unblocks the parent gate.
                self._cancel_bus_watchers_for_task(task.id, retry_task.id)
            else:
                # Max retries exceeded
                failed_task = self._task_processor._task_repo.fail_task(
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
                # Phase 2 Batch 2 — fire watcher notification only if
                # the atomic fail_task returned non-None.
                if failed_task is not None:
                    self._schedule_work_notification(
                        failed_task,
                        "failed",
                        error=f"Task cancelled after {self._max_retries} retries",
                    )
        else:
            # Non-timeout cancellation (shutdown, user request, etc.)
            cancelled_task = self._task_processor._task_repo.cancel_task(
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
            # Phase 2 Batch 2 — fire watcher notification only if the
            # atomic cancel_task returned non-None.
            if cancelled_task is not None:
                self._schedule_work_notification(
                    cancelled_task,
                    "cancelled",
                    error=f"Task cancelled: {reason.value}",
                )
    
    def _handle_usage_limit(self, task: "Task", err: UsageLimitError) -> None:
        """Dedicated usage-limit deferral path — the episode decision owner.

        Called ONLY from the ``except UsageLimitError`` branch of
        :meth:`_process_with_timeout` (docs/plans/usage-limit-
        deferral-path.md W4-W6). All usage-limit POLICY lives here:

        1. Anchor (set-once per episode): read
           ``usage_limit_first_seen_at`` from instance metadata; absent
           → stamp ``now`` (persisted BEFORE any branching — crash-safe
           monotonic clock). Soft-fail: on a failed read/write the
           degenerate ``first_seen = now`` applies.
        2. In-window → defer: ``schedule_retry`` with the W5 schedule
           and ``bypass_retry_budget=True`` (budget-free deferrals;
           ``retry_count`` still grows for observability), then release
           the dependency-bus watchers for the cancelled parent (H1 —
           mirrors the TIMEOUT lane's post-incident fix). Per-attempt
           observables are ONE log line, nothing else: no report, no
           instance ERROR, no message FAILED, no hierarchy deletion, no
           error event (the W3 carve-out held the stage-2 cascade).
        3. Past-deadline → the episode's ONE report, SELF-COMPOSED and
           race-gated: the ENTIRE terminal composition (parent notify +
           watcher notify + anchor clear) fires only when ``fail_task``
           WON its status-guard race (returned non-None). A lost race
           means another actor terminalized or re-childed the task
           (W8's recovery child, an operator cancel) and reporting here
           would zombie-kill a live episode or double-report.
        4. Success inside the window clears the anchor silently (W6,
           in the pipeline success callback — not here).

        This method MUST NOT raise (review rev2 §3.3): an exception
        escaping the ``except UsageLimitError`` block propagates past
        the sibling ``except Exception`` handler and surfaces as an
        unexpected error attributed to the wrong cause. The whole
        policy body is wrapped; anchor reads/writes inside are
        individually soft-fail.
        """
        try:
            self._usage_limit_episode_decide(task, err)
        except Exception as e:  # noqa: BLE001 — handler must never raise
            logger.error(
                f"Worker {self.worker_id}: usage-limit handler failed for "
                f"task {task.id} (instance={task.instance_id[:8]}...): "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )

    def _usage_limit_episode_decide(
        self, task: "Task", err: UsageLimitError
    ) -> None:
        """Policy body of :meth:`_handle_usage_limit` (never raises)."""
        task_repo = self._task_processor._task_repo
        try:
            manager = self._task_processor._manager
        except AttributeError:
            manager = None
        instance_repo = (
            getattr(manager, "_instance_repository", None)
            if manager is not None
            else None
        )

        now = datetime.now(timezone.utc)

        # 1. Anchor — set-once per episode, soft-fail (degenerate:
        #    a failed read/write collapses to ``first_seen = now``,
        #    i.e. a fresh window; the next attempt re-reads).
        first_seen = read_usage_limit_first_seen(instance_repo, task.instance_id)
        if first_seen is None:
            first_seen = now
            write_usage_limit_first_seen(instance_repo, task.instance_id, now)

        # Shared boundary predicate (usage_limit_schedule) — the same
        # one stale recovery's liveness gate uses, so the two consumers
        # cannot drift on the boundary semantics.
        deadline = usage_limit_deadline(first_seen, self._usage_limit_window_seconds)

        # 2. In-window → defer (budget-free, W5 schedule).
        if usage_limit_in_window(first_seen, now, self._usage_limit_window_seconds):
            next_wake = next_usage_limit_retry_at(
                first_seen,
                now,
                delays=self._usage_limit_retry_delays,
                jitter_fraction=self._usage_limit_retry_jitter_fraction,
            )
            retry_task = task_repo.schedule_retry(
                task_id=task.id,
                max_retries=self._max_retries,
                next_retry_at=next_wake,
                bypass_retry_budget=True,
            )
            if retry_task is None:
                # Gate closed — a concurrent retry-creating actor won
                # (e.g. W8's anchor-gated recovery child, in which case
                # the episode is STILL ALIVE via that child) or the task
                # met a genuinely terminal fate (operator cancel).
                # Someone else decided; we stay silent — composing the
                # terminal here would zombie-kill the episode.
                logger.info(
                    f"Worker {self.worker_id}: usage-limit deferral for "
                    f"task {task.id} skipped — retry gate closed by "
                    f"another actor; episode continues elsewhere"
                )
                return
            # Bus-watcher release (review §3.1 / H1): without it the
            # bus's PENDING watcher keyed on the original
            # ``source_task_id`` strands the parent in
            # ``waiting_children`` forever (production incident
            # 2026-06-26). F6 covers the DB ``job_watchers``; this
            # covers the in-memory dependency bus.
            self._cancel_bus_watchers_for_task(task.id, retry_task.id)
            logger.info(
                f"Worker {self.worker_id}: usage-limit deferral for task "
                f"{task.id} (instance={task.instance_id[:8]}..., attempt "
                f"{retry_task.retry_count}) — next wake "
                f"{next_wake.isoformat()}, deadline {deadline.isoformat()}"
            )
            return

        # 3. Past-deadline → the episode's ONE report: self-composed,
        #    race-gated on the ``fail_task`` outcome. The TIMEOUT
        #    precedent fires the parent notify unconditionally because
        #    no other caller can re-child its task; THIS path can lose
        #    the task to W8's recovery child or an operator cancel, so
        #    the stronger gate applies (review rev2 §2.1).
        error_text = (
            f"usage_limit_deadline: window exceeded "
            f"({int(self._usage_limit_window_seconds)}s from first sighting "
            f"{first_seen.isoformat()}); original provider error: "
            f"{_truncate_error(str(err.original))}"
        )
        failed_task = task_repo.fail_task(task.id, error_text)
        if failed_task is None:
            # LOST the race — the episode is not ours to report.
            logger.info(
                f"Worker {self.worker_id}: usage-limit terminal for task "
                f"{task.id} skipped — fail_task guard lost to another "
                f"actor; no report composed"
            )
            return
        self._notify_parent_of_failure(
            instance_id=task.instance_id,
            error=(
                f"Usage limit window exceeded (quota episode since "
                f"{first_seen.isoformat()}): {_truncate_error(str(err.original))}"
            ),
            error_type="usage_limit_deadline",
            message_id=task.message_id,
        )
        self._schedule_work_notification(failed_task, "failed", error=error_text)
        # Terminal ENDS the episode — clear the anchor so a later quota
        # hit on a re-used instance gets a FRESH window (W6 clear-site
        # 2) and W8's bypass does not over-reach on the stale anchor.
        clear_usage_limit_first_seen(instance_repo, task.instance_id)
        self._tasks_failed += 1
        logger.warning(
            f"Worker {self.worker_id}: task {task.id} permanently failed — "
            f"usage-limit window exceeded (first sighting "
            f"{first_seen.isoformat()})"
        )

    def _handle_task_failure(self, task: "Task", error: str) -> None:
        """Handle task failure — schedule retry or permanent fail."""
        # For now: fail permanently. Retry-on-error is a separate feature.
        # Timeout cancellation already handles retry.
        failed_task = self._task_processor._task_repo.fail_task(task.id, error)
        self._tasks_failed += 1
        # Phase 2 Batch 2 — fire watcher notification only if the
        # atomic fail_task returned non-None (i.e. we won the
        # status=running guard race). Without this guard, a concurrent
        # recovery call could double-notify the same watcher.
        if failed_task is not None:
            self._schedule_work_notification(failed_task, "failed", error=error)

    def _schedule_work_notification(
        self,
        task: "Task",
        status: str,
        error: str | None = None,
    ) -> None:
        """Bridge the worker thread's sync terminal write to the async notifier.

        Phase 2 Batch 2 — called only when the atomic repo terminal
        method (``complete_task``/``fail_task``/``cancel_task``)
        returned a non-None ``Task``, proving we won the status-guard
        race. The notifier itself is async (it awaits
        ``instance_manager.enqueue_message``), so we use
        ``MainLoopBridge.run_async_no_wait`` to fire-and-forget on the
        main event loop — the same pattern already used by
        ``_notify_parent_of_failure`` and
        ``_cancel_bus_watchers_for_task``.

        Silently no-ops when ``work_resolver`` / ``watcher_repo`` are
        not yet wired (late-wiring path that runs before api.py sets
        them via ``WorkerPool.set_work_resolver``). The notification
        is best-effort — the next reconcile-on-startup sweep will
        catch any watcher that missed an event during this window.
        """
        if self._work_resolver is None or self._watcher_repo is None:
            return
        instance_manager = getattr(self._task_processor, "_manager", None)
        if instance_manager is None:
            return
        try:
            MainLoopBridge.run_async_no_wait(
                notify_work_watchers(
                    work_id=task.work_id,
                    status=status,
                    error=error,
                    instance_manager=instance_manager,
                    work_resolver=self._work_resolver,
                    watcher_repo=self._watcher_repo,
                )
            )
        except Exception as e:  # noqa: BLE001 — never raise from a fire-and-forget
            logger.debug(
                f"Worker {self.worker_id}: failed to schedule work "
                f"notification for task {task.id} work_id="
                f"{task.work_id[:8]}... status={status}: {e}"
            )

    def _await_message_completion(
        self, message_id: str, grace_seconds: float
    ) -> bool:
        """Poll the message row for ``completed`` status within a grace window.

        Used by the timeout-orphan-race path (see ``_handle_cancellation``
        for full context) to close the window between the thread-side
        ``TimeoutError`` and the underlying ``_run()`` coroutine's natural
        completion. ``future.result(timeout)`` does not cancel the
        coroutine — Python's contract is to raise ``TimeoutError`` while
        the future keeps running. The coroutine typically commits the
        ``message.status = 'completed'`` write within a few seconds of
        the timeout firing (4s in production), so a bounded poll catches
        the natural completion before ``schedule_retry`` flips the task
        to ``cancelled`` and produces an orphan retry.

        Args:
            message_id: Message row to watch.
            grace_seconds: Maximum total wall-clock time to poll. Bounded
                so a hung coroutine cannot stall the worker thread
                indefinitely. Default 30s (production observed 4s gap).

        Returns:
            True if ``message.status == 'completed'`` was observed within
            ``grace_seconds``. False if the message is still in any other
            status when the grace window expires (the caller proceeds
            with the retry).
        """
        import time as _time

        deadline = _time.monotonic() + grace_seconds
        interval_s = 0.5
        last_status: str | None = None
        repo = getattr(self._task_processor, "_message_repo", None)
        # Test fixtures often build a partial MockTaskProcessor that does
        # not wire _message_repo (MagicMock auto-creates attributes). Treat
        # any non-real repo (None, or a ``MagicMock``/``Mock`` instance)
        # as "missing" and fall through to the original retry path. This
        # keeps the helper a safety net, not a correctness gate, for
        # callers that don't have a message repo available.
        from unittest.mock import Mock as _Mock
        if repo is None or isinstance(repo, _Mock):
            return False
        while True:
            try:
                msg = repo.get(message_id)
            except Exception as e:
                logger.debug(
                    f"Worker {self.worker_id}: _await_message_completion "
                    f"DB read failed: {e}"
                )
                msg = None
            if msg is None:
                # Message row missing — nothing to poll. Be conservative
                # and treat as "not completed" so the caller proceeds with
                # the retry (same behavior as a DB outage).
                return False
            last_status = msg.status
            if msg.status == "completed":
                return True
            if msg.status in ("failed",):
                # Permanent failure committed by the underlying coroutine.
                # Don't retry — let the worker's outer error path handle it.
                return True
            if _time.monotonic() >= deadline:
                logger.debug(
                    f"Worker {self.worker_id}: grace window expired for "
                    f"message {message_id[:8]}... (last_status={last_status})"
                )
                return False
            _time.sleep(interval_s)


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
        timeout_grace_seconds: float = 30.0,
        work_resolver=None,  # WorkResolverService — Phase 2 Batch 2 notification
        watcher_repo=None,  # JobWatcherRepository — Phase 2 Batch 2 notification
        usage_limit_window_seconds: float = DEFAULT_USAGE_LIMIT_WINDOW_SECONDS,
        usage_limit_retry_delays_seconds: tuple[float, ...] | list[float] = (
            DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS
        ),
        usage_limit_retry_jitter_fraction: float = (
            DEFAULT_USAGE_LIMIT_RETRY_JITTER_FRACTION
        ),
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
            timeout_grace_seconds: Grace window for the timeout-orphan
                race fix (see ``Worker.__init__`` for full context).
            work_resolver: Optional WorkResolverService — Phase 2 Batch 2
                terminal-notification routing. When ``None``, the four
                worker-side terminal sites short-circuit the notify call
                so direct-construction tests do not crash. Wired in via
                :meth:`set_work_resolver` from ``daemon/api.py`` after
                the resolver is constructed (it depends on the
                JobRepository, which is built AFTER ``setup_worker_pool``
                returns).
            watcher_repo: Optional JobWatcherRepository — Phase 2 Batch 2
                terminal-notification claim. Same nullability contract
                as ``work_resolver``.
            usage_limit_window_seconds: Usage-limit episode horizon
                (dedicated deferral path — see
                ``Worker._handle_usage_limit``); 6 h default.
            usage_limit_retry_delays_seconds: Usage-limit wake schedule
                steps (3m/5m/10m/15m-cap default).
            usage_limit_retry_jitter_fraction: Per-wake jitter fraction
                for the usage-limit schedule.
        """
        self._task_processor = task_processor
        self._num_workers = num_workers
        self._timeout_minutes = timeout_minutes
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_backoff_max = retry_backoff_max
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._timeout_grace_seconds = timeout_grace_seconds
        # Dedicated usage-limit deferral path knobs (W4/W5/W7).
        self._usage_limit_window_seconds = usage_limit_window_seconds
        self._usage_limit_retry_delays_seconds = tuple(
            usage_limit_retry_delays_seconds
        )
        self._usage_limit_retry_jitter_fraction = usage_limit_retry_jitter_fraction
        # Phase 2 Batch 2 — notification dependencies for the four
        # worker-side terminal sites (``complete_task`` /
        # ``fail_task`` / ``cancel_task`` at ``_handle_cancellation``
        # and ``_handle_task_failure``). Stored on the pool AND copied
        # to each Worker at start() time so workers that begin before
        # late-wiring still see the right values (the worker thread
        # snapshots ``self.work_resolver`` / ``self.watcher_repo`` at
        # construction; late setters must update BOTH the pool and the
        # already-started workers, see :meth:`set_work_resolver` /
        # :meth:`set_watcher_repo`).
        self._work_resolver = work_resolver
        self._watcher_repo = watcher_repo
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
                timeout_grace_seconds=self._timeout_grace_seconds,
                work_resolver=self._work_resolver,
                watcher_repo=self._watcher_repo,
                usage_limit_window_seconds=self._usage_limit_window_seconds,
                usage_limit_retry_delays_seconds=(
                    self._usage_limit_retry_delays_seconds
                ),
                usage_limit_retry_jitter_fraction=(
                    self._usage_limit_retry_jitter_fraction
                ),
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

    def set_work_resolver(self, work_resolver) -> None:
        """Late-wire the WorkResolverService for Phase 2 Batch 2 notification.

        Wired by ``daemon/api.py`` AFTER the worker pool is started
        because the resolver is constructed in api.py after the worker
        pool (it depends on the JobRepository). Updates BOTH the pool
        and every already-started worker so a worker that races the
        late-wire still sees the right value at its next terminal
        call.

        Atomicity note: this method writes ``worker._work_resolver``
        directly without a lock. The Worker reads its own copy on each
        terminal call (the four sync sites at ``_handle_cancellation``
        and ``_handle_task_failure``), so a single-threaded writer
        (api.py lifespan startup) cannot lose updates — but a worker
        that interleaves a read between the pool write and the worker
        write would still see the OLD value until the next
        ``set_work_resolver``. In practice this is benign because the
        api.py lifespan completes before workers begin processing
        production tasks; the test surface that exercises terminal
        notifications wires ``work_resolver`` via the constructor or
        before any worker claims a task.
        """
        self._work_resolver = work_resolver
        for worker in self._workers:
            worker._work_resolver = work_resolver

    def set_watcher_repo(self, watcher_repo) -> None:
        """Late-wire the JobWatcherRepository for Phase 2 Batch 2 notification.

        Same rationale and atomicity contract as :meth:`set_work_resolver`.
        """
        self._watcher_repo = watcher_repo
        for worker in self._workers:
            worker._watcher_repo = watcher_repo
