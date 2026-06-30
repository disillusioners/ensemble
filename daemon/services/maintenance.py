"""Maintenance service for periodic cleanup tasks.

This module provides:
- MaintenanceService: Generic background service that runs registered jobs on intervals
- CheckpointCleanupJob: The first registered job that cleans up orphaned checkpoint data

Deletion Method Policy:
- All checkpoint database access goes through the CheckpointerAdapter interface
  (see daemon.checkpoint_adapter). The adapter abstracts away the underlying
  database technology (SQLite via AsyncSqliteSaver or PostgreSQL via
  AsyncPostgresSaver).
- For whole-thread deletion: use checkpointer.adelete_thread(thread_id).
- For partial checkpoint pruning (operation D): use the adapter's
  get_checkpoint_ids / delete_checkpoints_excluding / delete_writes_excluding.
- For listing threads or finding excess groups, use list_thread_ids and
  find_excess_checkpoint_groups respectively.

Why use the adapter instead of raw checkpointer.conn + checkpointer.lock?
- AsyncSqliteSaver exposes .conn and .lock, but AsyncPostgresSaver does not.
- Direct access binds the code to a single backend.
- The adapter provides a uniform interface that works with both backends
  while preserving the SQLite thread-safety contract (the SQLite adapter
  still wraps its calls in saver.lock and uses saver.conn internally).

Error Handling:
- Each cleanup operation runs independently with its own try/except.
- A failure in one operation does NOT prevent subsequent operations from running.
- job.last_run is only updated when the entire execute() completes successfully.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Coroutine

from daemon.checkpoint_adapter import CheckpointerAdapter
from daemon.config import PersistenceConfig
from daemon.constants import (
    CHECKPOINT_MAX_PER_THREAD,
    CHECKPOINT_TTL_HOURS,
    MAX_INSTANCE_HISTORY,
)
from daemon.services.job_queue_service import TERMINAL_STATUSES
from daemon.repositories.instance.repository import SQLModelInstanceRepository

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


@dataclass
class MaintenanceJob:
    """Represents a registered maintenance job."""

    name: str
    min_interval_hours: float
    last_run: datetime | None
    execute_fn: Callable[[], Coroutine[Any, Any, None]]


class MaintenanceService:
    """Generic background service for running periodic maintenance tasks.

    The service runs in a background loop, checking if registered jobs are due
    based on their minimum interval. Jobs only run when the system is idle
    (no active jobs AND no active LLM requests).

    Usage:
        service = MaintenanceService(check_interval_minutes=15)
        service.set_job_queue_service(job_queue_service)
        service.set_request_registry(manager._request_registry._requests)
        service.register("my_job", min_interval_hours=1.0, execute_fn=my_coro)
        await service.start()
    """

    def __init__(self, check_interval_minutes: int = 15):
        """Initialize the maintenance service.

        Args:
            check_interval_minutes: How often to check if jobs are due (default: 15).
        """
        self._jobs: list[MaintenanceJob] = []
        self._check_interval = check_interval_minutes * 60
        self._task: asyncio.Task | None = None
        self._running = False

        # References for idle check
        self._job_queue_service: Any = None
        self._request_registry: dict | None = None
        # Phase 1 of the defer-seam bugfix (2026-06-30): the shared
        # ``has_active_non_deferred_work`` predicate replaces the
        # ``list_all_pending`` blind-spot in ``_is_idle`` so the gate sees
        # ``task`` rows (the unified work path) and not only ``JobItem``
        # rows. Wired in by ``set_task_repository``; ``None`` means the
        # task-table probe is skipped — matches the existing "missing
        # dependency ⇒ skip the check" pattern used by the other two
        # references above.
        self._task_repository: Any = None

    def register(
        self,
        name: str,
        min_interval_hours: float,
        execute_fn: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Register a maintenance job.

        Args:
            name: Unique name for the job.
            min_interval_hours: Minimum hours between job executions.
            execute_fn: Async callable to execute when job is due.
        """
        job = MaintenanceJob(
            name=name,
            min_interval_hours=min_interval_hours,
            last_run=None,
            execute_fn=execute_fn,
        )
        self._jobs.append(job)
        logger.debug(f"Registered maintenance job: {name} (interval={min_interval_hours}h)")

    def set_job_queue_service(self, service: Any) -> None:
        """Set the JobQueueService reference for idle checking.

        Args:
            service: The JobQueueService instance.
        """
        self._job_queue_service = service

    def set_request_registry(self, registry: dict) -> None:
        """Set the request registry reference for idle checking.

        The registry should be a dict-like object where len() > 0 indicates
        active requests.

        Args:
            registry: The request registry dict from ActiveRequestRegistry._requests.
        """
        self._request_registry = registry

    def set_task_repository(self, task_repository: Any) -> None:
        """Set the TaskRepository reference for the shared idle probe.

        Phase 1 of the defer-seam bugfix (2026-06-30, Category B):
        ``_is_idle`` used to call ``list_all_pending`` on the job-queue
        repository only, which never sees ``task`` rows. This setter wires
        the same ``TaskRepository.has_active_non_deferred_work`` predicate
        used by ``claim_pending_task`` so the maintenance idle gate and the
        worker pool's claim path share one definition of "non-deferred
        in-flight work" and can never disagree.

        Args:
            task_repository: The ``TaskRepository`` instance used to call
                ``has_active_non_deferred_work(None)`` for the system-wide
                task probe. Pass ``None`` to disable the task probe (the
                gate will then only see JobItems + active LLM requests).
        """
        self._task_repository = task_repository

    async def start(self) -> None:
        """Start the maintenance service background loop."""
        if self._running:
            logger.warning("Maintenance service already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Maintenance service started")

    async def stop(self) -> None:
        """Stop the maintenance service and wait for the loop to exit."""
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Maintenance service stopped")

    async def _loop(self) -> None:
        """Main background loop that checks and runs pending jobs."""
        # Initial delay to let the system stabilize on startup
        await asyncio.sleep(60)

        while self._running:
            try:
                await self._run_pending_jobs()
            except Exception as e:
                logger.error(f"Maintenance loop error: {e}")

            await asyncio.sleep(self._check_interval)

    async def _run_pending_jobs(self) -> None:
        """Check each job and run those that are due and system is idle."""
        for job in self._jobs:
            if not self._is_due(job):
                continue

            # ``_is_idle`` is async because it wraps a sync ``list_all_pending``
            # DB call in ``asyncio.to_thread`` to keep the event loop responsive
            # under SQLite WAL write contention.
            if not await self._is_idle():
                logger.debug(f"Skipping {job.name}: system not idle")
                continue

            try:
                await job.execute_fn()
                job.last_run = utcnow()
                logger.info(f"Maintenance job '{job.name}' completed successfully")
            except Exception as e:
                logger.error(f"Maintenance job '{job.name}' failed: {e}")
                # Don't update last_run — will retry next cycle

    def _is_due(self, job: MaintenanceJob) -> bool:
        """Check if a job is due to run.

        Args:
            job: The maintenance job to check.

        Returns:
            True if job has never run or enough time has elapsed since last run.
        """
        if job.last_run is None:
            return True

        elapsed_hours = (utcnow() - job.last_run).total_seconds() / 3600
        return elapsed_hours >= job.min_interval_hours

    async def _is_idle(self) -> bool:
        """Check if the system is idle (no active work).

        System is considered idle when ALL of the following hold:
        - No non-deferred tasks in PENDING or RUNNING status, system-wide
          (shared ``TaskRepository.has_active_non_deferred_work`` predicate;
          also folded into ``claim_pending_task`` so the gate and the
          claim path never disagree — Phase 1 of the defer-seam bugfix,
          2026-06-30).
        - No JobItems with ``admission_state`` in ('queued', 'active')
          (covers the full admission lifecycle: work waiting to start AND
          work in-flight that holds the queue lock — ``list_all_pending``
          alone missed the 'active' bucket).
        - No active LLM requests in the request registry.

        Returns:
            True if system is idle and can run maintenance tasks.
        """
        # 1. Check for active non-deferred tasks (system-wide).
        # This is the shared ``has_active_non_deferred_work`` predicate
        # used by ``claim_pending_task`` and other defer-queue call sites
        # — keeping the gate in sync with the claim path is the whole
        # point of the Phase 1 refactor (Category B in the bugfix plan).
        # Wrapped in ``asyncio.to_thread`` because the predicate is a sync
        # SQLModel/SQLAlchemy call that takes a connection from the engine
        # pool under SQLite WAL — same rationale as ``list_all_pending``
        # below. If ``_task_repository`` was never wired (e.g. partial
        # init or test setup that doesn't care about tasks), the probe is
        # skipped, matching the existing "missing dependency ⇒ skip the
        # check" pattern.
        if self._task_repository is not None:
            try:
                has_work = await asyncio.to_thread(
                    self._task_repository.has_active_non_deferred_work, None
                )
                if has_work:
                    return False
            except Exception as e:
                logger.warning(f"Failed to check task repository: {e}")

        # 2. Check for queued or active JobItems (queue-policy state).
        # ``list_all_pending`` only sees ``admission_state='queued'``;
        # ``find_processing_jobs`` only sees ``admission_state='active'``.
        # Both must be checked to cover the full admission lifecycle — a
        # job that has been claimed and is executing still holds the queue
        # lock and must keep the maintenance job from running. Each call
        # is wrapped in ``asyncio.to_thread`` so SQLite WAL write
        # contention cannot block the event loop. This loop runs every
        # ``check_interval_minutes`` (default 15); if a pending query
        # deadlocks the event loop, the entire daemon freezes — same
        # root cause as the ``notify_watchers`` / ``enqueue_message``
        # deadlock chain.
        #
        # ``len(...)`` is used for the truthiness check (not ``if x:``)
        # so the gate remains correct under ``MagicMock`` test doubles
        # that don't explicitly stub ``find_processing_jobs`` — a
        # bare ``MagicMock()`` is truthy via ``__bool__`` but reports
        # ``len() == 0`` via ``__len__``, matching the production
        # "empty list ⇒ idle" semantics. ``list_all_pending`` is already
        # explicitly stubbed by every test (``MagicMock(return_value=[])``)
        # so plain truthiness is safe for it.
        if self._job_queue_service is not None:
            try:
                repo = self._job_queue_service._repository
                pending = await asyncio.to_thread(repo.list_all_pending)
                if pending:
                    return False
                processing = await asyncio.to_thread(repo.find_processing_jobs)
                if len(processing) > 0:
                    return False
            except Exception as e:
                logger.warning(f"Failed to check job queue: {e}")

        # 3. Check for active LLM requests
        if self._request_registry is not None:
            if len(self._request_registry) > 0:
                return False

        return True


class CheckpointCleanupJob:
    """Job that cleans up orphaned and expired checkpoint data.

    This job runs 4 cleanup operations in sequence:
    (A) Delete checkpoint threads with no matching instance (orphans)
    (B) Delete checkpoint data for expired terminal instances
    (C) Enforce max_instance_history cap on terminal instances
    (D) Prune per-thread checkpoints to CHECKPOINT_MAX_PER_THREAD

    Error Handling:
    - Each operation is wrapped in its own try/except.
    - A failure in one operation does NOT prevent subsequent operations.
    - The job's execute() method catches all operation failures internally.

    Thread Safety:
    - All database access is delegated to the CheckpointerAdapter. The SQLite
      implementation of the adapter wraps its operations in
      AsyncSqliteSaver's lock and uses its existing connection, preserving
      the same thread-safety contract as direct access.
    """

    def __init__(
        self,
        config: PersistenceConfig,
        checkpointer: CheckpointerAdapter,
        instance_repo: SQLModelInstanceRepository,
        on_instance_deleted: Callable[[str], None] | None = None,
    ):
        """Initialize the checkpoint cleanup job.

        Args:
            config: PersistenceConfig with checkpoint_ttl_hours, max_instance_history.
            checkpointer: A CheckpointerAdapter wrapping the underlying saver.
                Use checkpointer.adelete_thread() for whole-thread deletions
                (Ops A-C) and the partial-pruning methods for Op D.
            instance_repo: Instance repository for querying instance data.
            on_instance_deleted: Optional callback invoked after BOTH checkpoint
                cleanup AND instance record deletion succeed for an instance.
                Used to release in-memory state (graph, tasks, request registry)
                in InstanceManager without creating a circular dependency.
                Signature: takes instance_id, returns None.
        """
        self._config = config
        self._checkpointer = checkpointer
        self._instance_repo = instance_repo
        self._on_instance_deleted = on_instance_deleted

    async def execute(self) -> None:
        """Run all 4 checkpoint cleanup operations.

        Each operation runs independently with its own error handling.
        Failures are logged but do not prevent subsequent operations.
        """
        logger.info("Starting checkpoint cleanup job")

        # Operation A: Cleanup orphaned threads
        await self._cleanup_orphaned_threads()

        # Operation B: Cleanup expired terminal instances
        await self._cleanup_expired_terminal()

        # Operation C: Enforce history cap
        await self._enforce_history_cap()

        # Operation D: Prune per-thread checkpoints
        await self._prune_per_thread_checkpoints()

        logger.info("Checkpoint cleanup job completed")

    async def _cleanup_orphaned_threads(self) -> None:
        """(A) Delete checkpoint threads with no matching instance.

        Finds checkpoint thread IDs in the checkpoint DB that don't have
        corresponding instance records in the instances DB.

        Deletion method: checkpointer.adelete_thread(thread_id)
        Thread list is obtained via checkpointer.list_thread_ids().
        """
        try:
            # Get all thread IDs from checkpoint database via the adapter.
            # The SQLite adapter implementation wraps this call in
            # AsyncSqliteSaver's lock and uses its existing connection,
            # preserving thread-safe access to the same DB.
            checkpoint_threads = await self._checkpointer.list_thread_ids()

            if not checkpoint_threads:
                return

            # Get all instance IDs from instance repository
            instance_ids = self._get_all_instance_ids()

            # Find orphaned threads (exist in checkpoints but not in instances)
            orphaned = [t for t in checkpoint_threads if t not in instance_ids]

            if not orphaned:
                logger.debug("No orphaned checkpoint threads found")
                return

            logger.info(f"Found {len(orphaned)} orphaned checkpoint threads")

            # Delete each orphaned thread using adelete_thread
            for thread_id in orphaned:
                await self._checkpointer.adelete_thread(thread_id)

            logger.info(f"Deleted {len(orphaned)} orphaned checkpoint threads")

        except Exception as e:
            logger.error(f"Orphaned threads cleanup failed: {e}")

    async def _cleanup_expired_terminal(self) -> None:
        """(B) Delete checkpoint data, instance records, and in-memory state
        for terminal instances older than TTL.

        Finds instances in terminal states (TERMINATED, COMPLETED, ERROR, FAILED)
        where updated_at is older than checkpoint_ttl_hours, and performs full
        cleanup via _cleanup_instance (checkpoint data, DB record, in-memory state).

        Deletion method: _cleanup_instance() → adelete_thread() + instance_repo.delete() + callback.
        """
        try:
            ttl_hours = self._config.checkpoint_ttl_hours
            ttl_hours = ttl_hours if ttl_hours > 0 else CHECKPOINT_TTL_HOURS

            # Find terminal instances older than TTL
            cutoff = utcnow() - timedelta(hours=ttl_hours)
            expired_instances = self._find_expired_terminal_instances(cutoff)

            if not expired_instances:
                logger.debug(f"No expired terminal instances found")
                return

            logger.info(f"Found {len(expired_instances)} expired terminal instances")

            # Full cleanup for each expired instance (checkpoint + record + in-memory)
            # Per-instance try/except ensures one failure doesn't abort the batch
            deleted = 0
            for instance_id in expired_instances:
                try:
                    await self._cleanup_instance(instance_id)
                    deleted += 1
                except Exception as e:
                    logger.error(
                        f"Failed to clean up instance {instance_id[:8]}...: {e}"
                    )

            logger.info(f"Cleaned up {deleted} expired terminal instances (checkpoints + records)")

        except Exception as e:
            logger.error(f"Expired terminal cleanup failed: {e}")

    async def _enforce_history_cap(self) -> None:
        """(C) Keep only max_instance_history terminal instances.

        Counts terminal instances with checkpoint data. If count exceeds
        max_instance_history (default 300), prunes oldest instances by updated_at.

        Each pruned instance is fully cleaned up via _cleanup_instance
        (checkpoint data, DB record, in-memory state).

        Deletion method: _cleanup_instance() → adelete_thread() + instance_repo.delete() + callback.
        """
        try:
            max_history = self._config.max_instance_history
            max_history = max_history if max_history > 0 else MAX_INSTANCE_HISTORY

            # Get terminal instances ordered by updated_at (oldest first)
            terminal_instances = self._get_terminal_instances_ordered_by_age()
            total_count = len(terminal_instances)

            if total_count <= max_history:
                logger.debug(
                    f"Terminal instance history within cap: {total_count}/{max_history}"
                )
                return

            excess = total_count - max_history
            logger.info(
                f"Terminal instance history exceeds cap: {total_count} > {max_history}, "
                f"pruning {excess} oldest"
            )

            # Prune the oldest instances (first 'excess' items in the list)
            # Per-instance try/except ensures one failure doesn't abort the batch
            to_delete = terminal_instances[:excess]
            deleted = 0
            for instance_id in to_delete:
                try:
                    await self._cleanup_instance(instance_id)
                    deleted += 1
                except Exception as e:
                    logger.error(
                        f"Failed to clean up instance {instance_id[:8]}...: {e}"
                    )

            logger.info(f"Pruned {deleted} terminal instances from history cap (checkpoints + records)")

        except Exception as e:
            logger.error(f"History cap enforcement failed: {e}")

    async def _prune_per_thread_checkpoints(self) -> None:
        """(D) For each thread, keep only the latest CHECKPOINT_MAX_PER_THREAD checkpoints.

        Queries the checkpoint database to find threads with more than
        CHECKPOINT_MAX_PER_THREAD checkpoints, then deletes the oldest ones.

        Uses checkpointer.find_excess_checkpoint_groups() to identify threads
        that exceed the cap, and the partial-pruning helpers
        (get_checkpoint_ids / delete_checkpoints_excluding /
        delete_writes_excluding) to remove the oldest checkpoints while
        keeping the most recent N.

        Schema:
        - checkpoints: (thread_id, checkpoint_ns, checkpoint_id, ...) PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        - writes: (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, ...) PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        - checkpoint_id is a UUID string where lexicographic ordering = chronological ordering

        For each (thread_id, checkpoint_ns) pair with excess checkpoints:
        1. Find checkpoint_ids to KEEP (most recent N by lexicographic DESC order)
        2. Delete from checkpoints where checkpoint_id NOT IN keep list
        3. Delete from writes where checkpoint_id NOT IN keep list
        """
        try:
            max_per_thread = CHECKPOINT_MAX_PER_THREAD

            # Find threads with excessive checkpoints via the adapter.
            # The SQLite adapter wraps this in AsyncSqliteSaver's lock.
            excess_pairs = await self._checkpointer.find_excess_checkpoint_groups(
                max_per_thread
            )

            if not excess_pairs:
                logger.debug("No threads with excessive checkpoints found")
                return

            logger.info(
                f"Found {len(excess_pairs)} thread/namespace pairs with > {max_per_thread} checkpoints"
            )

            # Prune each thread's checkpoints
            total_deleted = 0
            for thread_id, checkpoint_ns, cnt in excess_pairs:
                deleted = await self._prune_thread_checkpoints(
                    thread_id, checkpoint_ns, max_per_thread
                )
                total_deleted += deleted

            logger.info(
                f"Pruned {total_deleted} checkpoints from {len(excess_pairs)} thread/namespace pairs"
            )

        except Exception as e:
            logger.error(f"Per-thread checkpoint pruning failed: {e}")

    # ── Helper Methods ─────────────────────────────────────────────────────────

    async def _cleanup_instance(self, instance_id: str) -> None:
        """Delete instance record, checkpoint data, and in-memory state for an instance.

        Performs the full cleanup sequence in order:
        0. Re-verify the instance is still in a terminal status (TOCTOU guard).
           Between the time the maintenance job listed terminal instances and
           now, the instance could have been resumed by a new job. Re-fetching
           and re-checking prevents deleting a record that is no longer
           eligible for cleanup.
        1. Delete instance record from instances.db (cascades to hierarchy,
           tasks, events, message_queue tables).
        2. Delete checkpoint data from checkpoints.db (via adelete_thread).
        3. Invoke the on_instance_deleted callback to release in-memory state
           (graph cache, graph tasks, request registry) — only if the
           instance record was actually deleted.

        Rationale for this order:
        - If instance delete fails → checkpoint data is preserved, and the
          next maintenance cycle will retry cleanly.
        - If instance delete succeeds but checkpoint deletion fails → the
          orphan checkpoint thread is naturally swept by Operation A
          (_cleanup_orphaned_threads) on the next cycle.

        If the instance record is not found in the DB (deleted by another
        process between query and delete), a warning is logged and both the
        checkpoint deletion and the in-memory callback are skipped. The
        orphan checkpoint data, if any, is left to Operation A to sweep.

        Args:
            instance_id: The instance ID to clean up.
        """
        # 0. TOCTOU guard — re-verify the instance is still terminal.
        # Between listing terminal instances and acting on them, the instance
        # could have been resumed by a new job. Skip in that case.
        # If the instance is already gone (get returns None), fall through
        # so the delete + checkpoint sweep still runs as a self-heal.
        instance = self._instance_repo.get(instance_id)
        if instance and instance.status not in TERMINAL_STATUSES:
            logger.debug(
                f"Instance {instance_id[:8]}... no longer terminal, skipping"
            )
            return

        # 1. Delete instance record from instances.db (with cascade)
        result = self._instance_repo.delete(instance_id)
        if not result.get("deleted", False):
            logger.warning(
                f"Instance record not found during cleanup: {instance_id[:8]}... "
                f"(skipping checkpoint and in-memory cleanup)"
            )
            return

        # 2. Delete checkpoint data from checkpoints.db
        await self._checkpointer.adelete_thread(instance_id)

        # 3. Clean up in-memory state via callback (if provided).
        # The callback is best-effort in-memory cleanup, so we isolate it
        # from the surrounding flow: a failure here must not undo the
        # already-completed DB cleanup.
        if self._on_instance_deleted is not None:
            try:
                self._on_instance_deleted(instance_id)
            except Exception as e:
                logger.warning(
                    f"In-memory cleanup callback failed for {instance_id[:8]}...: {e}"
                )

    def _get_all_instance_ids(self) -> set[str]:
        """Get all instance IDs from the instance repository.

        Returns:
            Set of all instance_id strings.
        """
        instance_ids: set[str] = set()

        # List instances in batches to avoid memory issues
        offset = 0
        limit = 100

        while True:
            instances, total = self._instance_repo.list(limit=limit, offset=offset)
            for inst in instances:
                instance_ids.add(inst.instance_id)

            offset += limit
            if offset >= total:
                break

        return instance_ids

    def _find_expired_terminal_instances(self, cutoff: datetime) -> list[str]:
        """Find terminal instances older than the cutoff time.

        Args:
            cutoff: Datetime threshold. Instances with updated_at before this
                   are considered expired.

        Returns:
            List of instance_id strings for expired terminal instances.
        """
        expired: list[str] = []
        cutoff_str = cutoff.isoformat()

        # List instances by each terminal status
        for status in TERMINAL_STATUSES:
            offset = 0
            limit = 100

            while True:
                instances, total = self._instance_repo.list(
                    status=status, limit=limit, offset=offset
                )

                for inst in instances:
                    # Check if instance is older than cutoff
                    if inst.updated_at and inst.updated_at < cutoff_str:
                        expired.append(inst.instance_id)

                offset += limit
                if offset >= total:
                    break

        return expired

    def _get_terminal_instances_ordered_by_age(self) -> list[str]:
        """Get all terminal instances ordered by age (oldest first).

        Iterates every status in TERMINAL_STATUSES, so the result includes all
        terminal instances regardless of whether they have checkpoint data.

        Returns:
            List of instance_id strings for terminal instances, oldest first.
        """
        terminal_instances: list[tuple[str, str]] = []  # (instance_id, updated_at)

        for status in TERMINAL_STATUSES:
            offset = 0
            limit = 100

            while True:
                instances, total = self._instance_repo.list(
                    status=status, limit=limit, offset=offset
                )

                for inst in instances:
                    terminal_instances.append((inst.instance_id, inst.updated_at or ""))

                offset += limit
                if offset >= total:
                    break

        # Sort by updated_at (oldest first)
        terminal_instances.sort(key=lambda x: x[1])

        return [inst_id for inst_id, _ in terminal_instances]

    async def _prune_thread_checkpoints(
        self,
        thread_id: str,
        checkpoint_ns: str,
        max_per_thread: int,
    ) -> int:
        """Prune checkpoints for a specific (thread_id, checkpoint_ns), keeping only the latest N.

        Uses the CheckpointerAdapter to:
        1. Find checkpoint_ids to KEEP (most recent N by lexicographic DESC).
        2. Delete checkpoints NOT in keep list.
        3. Delete corresponding writes NOT in keep list.

        checkpoint_id is a UUID string where lexicographic ordering equals
        chronological ordering, so the adapter's "newest first" ordering gives
        the most recent checkpoints.

        Args:
            thread_id: The thread ID to prune.
            checkpoint_ns: The checkpoint namespace to prune.
            max_per_thread: Number of checkpoints to keep.

        Returns:
            Number of checkpoints deleted.
        """
        # Step 1: Get checkpoint_ids to KEEP (most recent N)
        ids_to_keep_list = await self._checkpointer.get_checkpoint_ids(
            thread_id, checkpoint_ns, max_per_thread
        )
        ids_to_keep = set(ids_to_keep_list)

        if not ids_to_keep:
            return 0

        # Step 2: Delete checkpoints NOT in keep list
        checkpoint_rows = await self._checkpointer.delete_checkpoints_excluding(
            thread_id, checkpoint_ns, ids_to_keep
        )

        # Step 3: Delete corresponding writes NOT in keep list
        write_rows = await self._checkpointer.delete_writes_excluding(
            thread_id, checkpoint_ns, ids_to_keep
        )

        return checkpoint_rows
