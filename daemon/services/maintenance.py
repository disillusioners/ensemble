"""Maintenance service for periodic cleanup tasks.

This module provides:
- MaintenanceService: Generic background service that runs registered jobs on intervals
- CheckpointCleanupJob: The first registered job that cleans up orphaned checkpoint data

Deletion Method Policy:
- For whole-thread deletion: Use checkpointer.adelete_thread(thread_id).
  This deletes ALL checkpoints + writes for the thread, protected by AsyncSqliteSaver's lock.
- For partial checkpoint pruning (operation D): Use checkpointer.conn + checkpointer.lock
  for direct SQL queries, then adelete_thread() is NOT suitable because we need to keep
  some checkpoints (the latest N) and only delete the oldest ones.

Why checkpointer.conn + checkpointer.lock instead of aiosqlite.connect()?
AsyncSqliteSaver already holds an open connection to the checkpoint DB. Opening a second
connection bypasses its internal lock mechanism, risking corruption if operations interleave.
Using checkpointer.conn (wrapped in checkpointer.lock) ensures thread-safe access.

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

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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

            if not self._is_idle():
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

    def _is_idle(self) -> bool:
        """Check if the system is idle (no active work).

        System is considered idle when:
        - No active jobs in the job queue service
        - No active LLM requests in the request registry

        Returns:
            True if system is idle and can run maintenance tasks.
        """
        # Check for active jobs in job queue service
        if self._job_queue_service is not None:
            try:
                repo = self._job_queue_service._repository
                pending = repo.list_all_pending()
                if pending:
                    return False
            except Exception as e:
                logger.warning(f"Failed to check job queue: {e}")

        # Check for active LLM requests
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
    - All database queries use checkpointer.conn wrapped in checkpointer.lock.
    - This ensures thread-safe access to the same connection that AsyncSqliteSaver uses.
    """

    def __init__(
        self,
        config: PersistenceConfig,
        checkpointer: AsyncSqliteSaver,
        instance_repo: SQLModelInstanceRepository,
    ):
        """Initialize the checkpoint cleanup job.

        Args:
            config: PersistenceConfig with checkpoint_ttl_hours, max_instance_history.
            checkpointer: AsyncSqliteSaver instance.
                - Use checkpointer.adelete_thread() for whole-thread deletions (Ops A-C).
                - Use checkpointer.conn + checkpointer.lock for queries (all ops).
            instance_repo: Instance repository for querying instance data.
        """
        self._config = config
        self._checkpointer = checkpointer
        self._instance_repo = instance_repo

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
        Uses checkpointer.conn + lock for thread-safe query access.
        """
        try:
            # Get all thread IDs from checkpoint database
            # Use checkpointer's existing connection wrapped in its lock
            # to ensure thread-safe access (avoiding a second connection)
            async with self._checkpointer.lock:
                cursor = await self._checkpointer.conn.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints"
                )
                rows = await cursor.fetchall()
                checkpoint_threads = [row[0] for row in rows]

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
        """(B) Delete checkpoint data for terminal instances older than TTL.

        Finds instances in terminal states (TERMINATED, COMPLETED, ERROR, FAILED)
        where updated_at is older than checkpoint_ttl_hours, and deletes their
        checkpoint data.

        Deletion method: checkpointer.adelete_thread(instance_id)
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

            # Delete checkpoint data for each expired instance
            deleted = 0
            for instance_id in expired_instances:
                await self._checkpointer.adelete_thread(instance_id)
                deleted += 1

            logger.info(f"Deleted checkpoint data for {deleted} expired terminal instances")

        except Exception as e:
            logger.error(f"Expired terminal cleanup failed: {e}")

    async def _enforce_history_cap(self) -> None:
        """(C) Keep only max_instance_history terminal instances' checkpoint data.

        Counts terminal instances with checkpoint data. If count exceeds
        max_instance_history (default 300), prunes oldest instances by updated_at.

        Deletion method: checkpointer.adelete_thread(instance_id)
        """
        try:
            max_history = self._config.max_instance_history
            max_history = max_history if max_history > 0 else MAX_INSTANCE_HISTORY

            # Get terminal instances ordered by updated_at (oldest first)
            terminal_instances = self._get_terminal_instances_with_checkpoints()
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
            to_delete = terminal_instances[:excess]
            deleted = 0
            for instance_id in to_delete:
                await self._checkpointer.adelete_thread(instance_id)
                deleted += 1

            logger.info(f"Pruned {deleted} terminal instances from history cap")

        except Exception as e:
            logger.error(f"History cap enforcement failed: {e}")

    async def _prune_per_thread_checkpoints(self) -> None:
        """(D) For each thread, keep only the latest CHECKPOINT_MAX_PER_THREAD checkpoints.

        Queries the checkpoint database to find threads with more than
        CHECKPOINT_MAX_PER_THREAD checkpoints, then deletes the oldest ones.

        Uses checkpointer.conn + checkpointer.lock for thread-safe queries.

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

            # Find threads with excessive checkpoints
            # Use checkpointer's connection wrapped in its lock for thread safety
            async with self._checkpointer.lock:
                # Step 1: Find (thread_id, checkpoint_ns) pairs with excess
                cursor = await self._checkpointer.conn.execute(
                    """
                    SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                    FROM checkpoints
                    GROUP BY thread_id, checkpoint_ns
                    HAVING cnt > ?
                    """,
                    (max_per_thread,),
                )
                excess_pairs = await cursor.fetchall()

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

    def _get_terminal_instances_with_checkpoints(self) -> list[str]:
        """Get terminal instances that have checkpoint data, ordered by updated_at.

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

        Uses direct SQL to delete the oldest checkpoints, preserving the most recent
        max_per_thread checkpoints. checkpoint_id is a UUID string where lexicographic
        ordering = chronological ordering, so ORDER BY checkpoint_id DESC gives newest first.

        Uses checkpointer.conn + checkpointer.lock for thread-safe access.

        Args:
            thread_id: The thread ID to prune.
            checkpoint_ns: The checkpoint namespace to prune.
            max_per_thread: Number of checkpoints to keep.

        Returns:
            Number of checkpoints deleted.
        """
        async with self._checkpointer.lock:
            # Step 1: Get checkpoint_ids to KEEP (most recent N by lexicographic DESC)
            cursor = await self._checkpointer.conn.execute(
                """
                SELECT checkpoint_id FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                ORDER BY checkpoint_id DESC
                LIMIT ?
                """,
                (thread_id, checkpoint_ns, max_per_thread),
            )
            rows = await cursor.fetchall()
            ids_to_keep = {row[0] for row in rows}

            if not ids_to_keep:
                return 0

            # Step 2: Delete checkpoints NOT in keep list
            placeholders = ",".join("?" * len(ids_to_keep))
            cursor = await self._checkpointer.conn.execute(
                f"""
                DELETE FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *ids_to_keep),
            )
            await self._checkpointer.conn.commit()
            checkpoint_rows = cursor.rowcount

            # Step 3: Delete corresponding writes NOT in keep list
            cursor = await self._checkpointer.conn.execute(
                f"""
                DELETE FROM writes
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *ids_to_keep),
            )
            await self._checkpointer.conn.commit()
            write_rows = cursor.rowcount

            return checkpoint_rows
