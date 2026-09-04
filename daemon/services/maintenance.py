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
- The one deliberate non-adapter deletion is the T5.19 ``message_metadata``
  side-table prune in ``_cleanup_instance``: the side table lives on the
  manager's shared engine (not the checkpoint store), so it goes through
  the injected ``MessageMetadataRepository`` directly, wrapped in a
  never-raise guard (orphans tolerated on failure).

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
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)
from daemon.repositories.message_metadata.repository import (
    MessageMetadataRepository,
)
from daemon.services.job_queue_service import TERMINAL_STATUSES

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
        last_run: datetime | None = None,
    ) -> None:
        """Register a maintenance job.

        Args:
            name: Unique name for the job.
            min_interval_hours: Minimum hours between job executions.
            execute_fn: Async callable to execute when job is due.
            last_run: Optional initial ``last_run`` timestamp. Pass this
                when the job's prior run time has been persisted
                elsewhere (e.g. project metadata KV) so the next
                execution does not fire immediately on restart. Defaults
                to ``None`` (job is due on first check, which matches
                the original "fresh-process" semantics).
        """
        job = MaintenanceJob(
            name=name,
            min_interval_hours=min_interval_hours,
            last_run=last_run,
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
        #
        # Phase 2 (defer-queue idle gate, 2026-07-23): check both
        # work-tracking tables. The job predicate catches active admission
        # lifecycle rows, while the task predicate below catches active Tasks
        # that have no backing JobItem. A non-bool job result is ignored so a
        # loosely configured Mock cannot make the system look busy.
        if self._job_queue_service is not None:
            repo = getattr(self._job_queue_service, "_repository", None)
            if repo is not None and hasattr(repo, "has_active_non_deferred_work"):
                try:
                    has_work = await asyncio.to_thread(
                        repo.has_active_non_deferred_work, None
                    )
                    if isinstance(has_work, bool) and has_work:
                        return False
                except Exception as e:
                    logger.warning(
                        f"Failed to check job repository: has_active_non_deferred_work: {e}"
                    )
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

    This job runs 5 cleanup operations in sequence:
    (A) Delete checkpoint threads with no matching instance (orphans)
    (B) Delete checkpoint data for expired terminal instances
    (C) Enforce max_instance_history cap on terminal instances
    (D) Prune per-thread checkpoints to CHECKPOINT_MAX_PER_THREAD
    (E) Reference-aware checkpoint_blobs prune (Phase 1 C3 — dry-run by
        default, PostgreSQL-only, isolated so blob-bucket failures can
        never affect A-D)

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
        ui_prefs_repo: InstanceUiPrefsRepository | None = None,
        message_metadata_repo: MessageMetadataRepository | None = None,
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
            ui_prefs_repo: Optional UI-prefs repository. When provided, the
                cleanup job excludes any terminal instance whose tree root
                (or any descendant of it) is currently pinned from TTL-based
                and history-cap cleanup (Operations B and C). The set of
                protected IDs is the union of every pinned instance's tree
                root's full subtree — a pinned child resolves up to its root
                and the entire sibling + descendant tree becomes protected.
                ``None`` (the default) disables protection so the job runs in
                backward-compatible mode for callers that have not wired the
                UI-prefs repo. New code should pass this as a keyword argument.
            message_metadata_repo: Optional SYNC ``message_metadata`` side-table
                repository (T5.19 — merge precondition, architect §3). When
                provided, ``_cleanup_instance`` prunes the cleaned instance's
                side-table rows (AFTER ``adelete_thread``, BEFORE the
                in-memory callback) so the table does not grow without bound;
                the prune is never-raise — a prune failure is logged as a
                WARNING and tolerated (orphaned rows never join the read
                path). ``None`` (the default) skips the prune, preserving the
                backward-compatible behavior for existing constructors.
        """
        self._config = config
        self._checkpointer = checkpointer
        self._instance_repo = instance_repo
        self._on_instance_deleted = on_instance_deleted
        self._ui_prefs_repo = ui_prefs_repo
        self._message_metadata_repo = message_metadata_repo

    async def execute(self) -> None:
        """Run all 5 checkpoint cleanup operations.

        Each operation runs independently with its own error handling.
        Failures are logged but do not prevent subsequent operations.

        P1 (phase1-plan.md T6, C11): emits the
        ``pinned_subtree_terminal_count`` metric once per maintenance
        tick (sum of terminal descendant counts across every pinned
        root). Makes the polarity change from transient
        ``get_tree_ids`` to permanent ``get_cascade_tree_ids`` observable
        — terminal descendants under pinned roots are now protected from
        TTL purge, and the operator can verify the new behavior is in
        effect without diffing the DB.
        """
        logger.info("Starting checkpoint cleanup job")

        # P1 metric emit (C11). Computed once per tick; summed across
        # pinned roots. Stays a single INFO line so log-asserting tests
        # can pin the count without scraping the protected-set structure.
        pinned_terminal_count = self._compute_pinned_subtree_terminal_count()
        if pinned_terminal_count > 0 or self._ui_prefs_repo is not None:
            logger.info(
                "pinned_subtree_terminal_count=%d (sum of terminal "
                "descendants under pinned roots; P1 polarity change "
                "now visible)",
                pinned_terminal_count,
            )

        # Operation A: Cleanup orphaned threads
        await self._cleanup_orphaned_threads()

        # Operation B: Cleanup expired terminal instances
        await self._cleanup_expired_terminal()

        # Operation C: Enforce history cap
        await self._enforce_history_cap()

        # Operation D: Prune per-thread checkpoints
        await self._prune_per_thread_checkpoints()

        # Operation E (Phase 1 C3): reference-aware checkpoint_blobs prune.
        # Isolated per the plan — a failure in the blob bucket must NEVER
        # break the retention prune above (which has already completed)
        # or any subsequent maintenance cycle. prune_unreferenced_blobs
        # itself never raises; this belt-and-braces wrapper guarantees
        # the isolation even if that contract regresses.
        try:
            await self._prune_unreferenced_blobs()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Unreferenced blob prune operation failed: {e}")

        logger.info("Checkpoint cleanup job completed")

    def _compute_pinned_subtree_terminal_count(self) -> int:
        """Sum of terminal descendants across every pinned root.

        P1 (phase1-plan.md T6, C11): the metric operator observes to
        verify the polarity change is live. Walks the same protected
        subtree as :meth:`_get_protected_instance_ids` (so the count is
        consistent with the protection set the cleanup actually applies),
        then queries each member's status via the instance repo and
        counts the ones in :data:`TERMINAL_STATUSES`. Returns 0 when
        ``_ui_prefs_repo`` is not wired (backward-compatible mode).

        One DB read per protected node — bounded by total pinned
        subtree size (typically small; production trees well under 100
        nodes). Not a hot path; runs once per maintenance tick.
        """
        if self._ui_prefs_repo is None:
            return 0
        try:
            protected = self._get_protected_instance_ids()
        except Exception:
            # Mirror ``_get_protected_instance_ids``'s fail-safe: the
            # exception propagates from cleanup callers, but for the
            # metric we degrade to 0 so a transient DB hiccup doesn't
            # mask the polarity-change observation.
            logger.warning(
                "_compute_pinned_subtree_terminal_count: "
                "_get_protected_instance_ids raised; emitting 0",
                exc_info=True,
            )
            return 0
        terminal_count = 0
        for iid in protected:
            try:
                inst = self._instance_repo.get(iid)
            except Exception:
                # Best-effort — a single missing/corrupt row should
                # not poison the count. Skip and continue.
                continue
            if inst is not None and inst.status in TERMINAL_STATUSES:
                terminal_count += 1
        return terminal_count

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

            # Delete each orphaned thread using adelete_thread, then
            # prune the ``message_metadata`` side-table rows for the
            # same thread. The prune is best-effort and never-raises
            # per-thread (cpv2 final-gate finding 🟡1 — mirrors the
            # canonical pattern in ``_cleanup_instance`` step-2.5 at
            # ``maintenance.py:913-927``; the SYNC repo bridges via
            # ``asyncio.to_thread`` per decisions.md D14).
            for thread_id in orphaned:
                await self._checkpointer.adelete_thread(thread_id)

                if self._message_metadata_repo is not None:
                    try:
                        deleted_rows = await asyncio.to_thread(
                            self._message_metadata_repo.delete_for_thread,
                            thread_id,
                        )
                        if deleted_rows:
                            logger.info(
                                f"_cleanup_orphaned_threads: "
                                f"message_metadata prune deleted "
                                f"{deleted_rows} row(s) for thread "
                                f"{thread_id[:8]}..."
                            )
                    except Exception:
                        # Never-raise guard (W3): orphan side-table
                        # rows are over-record-only and never join the
                        # read path; a broken sweep is not. Continue
                        # with the next orphaned thread.
                        logger.warning(
                            f"_cleanup_orphaned_threads: "
                            f"message_metadata prune failed for "
                            f"{thread_id[:8]}... — orphans tolerated "
                            f"(never-raise guard)",
                            exc_info=True,
                        )

            logger.info(f"Deleted {len(orphaned)} orphaned checkpoint threads")

        except Exception as e:
            logger.error(f"Orphaned threads cleanup failed: {e}")

    async def _cleanup_expired_terminal(self) -> None:
        """(B) Delete checkpoint data, instance records, and in-memory state
        for terminal instances older than TTL.

        Finds instances in terminal states (TERMINATED, COMPLETED, ERROR, FAILED)
        where updated_at is older than checkpoint_ttl_hours, and performs full
        cleanup via _cleanup_instance (checkpoint data, DB record, in-memory state).

        Instances that belong to a pinned subtree (see
        :meth:`_get_protected_instance_ids`) are excluded from the candidate
        set so user-pinned work is preserved indefinitely.

        Deletion method: _cleanup_instance() → adelete_thread() + instance_repo.delete() + callback.
        """
        try:
            ttl_hours = self._config.checkpoint_ttl_hours
            ttl_hours = ttl_hours if ttl_hours > 0 else CHECKPOINT_TTL_HOURS

            # Compute the set of IDs to NEVER delete (pinned subtrees).
            protected = self._get_protected_instance_ids()

            # Find terminal instances older than TTL
            cutoff = utcnow() - timedelta(hours=ttl_hours)
            expired_instances = self._find_expired_terminal_instances(cutoff)

            if not expired_instances:
                logger.debug(f"No expired terminal instances found")
                return

            # Exclude protected IDs before doing any work.
            excluded = [iid for iid in expired_instances if iid in protected]
            candidates = [iid for iid in expired_instances if iid not in protected]
            if excluded:
                logger.info(
                    f"Excluded {len(excluded)} terminal instances from TTL "
                    f"cleanup (pinned)"
                )

            if not candidates:
                logger.info(
                    f"Found {len(expired_instances)} expired terminal instances, "
                    f"all pinned — skipping TTL cleanup"
                )
                return

            logger.info(
                f"Found {len(expired_instances)} expired terminal instances, "
                f"{len(candidates)} after pin exclusion"
            )

            # Full cleanup for each expired instance (checkpoint + record + in-memory)
            # Per-instance try/except ensures one failure doesn't abort the batch
            deleted = 0
            for instance_id in candidates:
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

        Instances that belong to a pinned subtree (see
        :meth:`_get_protected_instance_ids`) do NOT count toward the cap and
        are never pruned — the cap is computed on the protected-excluded set
        so user-pinned work never pushes non-pinned history off the back of
        the queue.

        Deletion method: _cleanup_instance() → adelete_thread() + instance_repo.delete() + callback.
        """
        try:
            max_history = self._config.max_instance_history
            max_history = max_history if max_history > 0 else MAX_INSTANCE_HISTORY

            # Compute the set of IDs to NEVER delete (pinned subtrees).
            protected = self._get_protected_instance_ids()

            # Get terminal instances ordered by updated_at (oldest first)
            terminal_instances = self._get_terminal_instances_ordered_by_age()

            # Exclude protected IDs before counting and before pruning.
            # Pinned subtrees do not count toward the history cap.
            candidates = [iid for iid in terminal_instances if iid not in protected]
            excluded_count = len(terminal_instances) - len(candidates)
            total_count = len(candidates)

            if total_count <= max_history:
                if excluded_count:
                    logger.debug(
                        f"Terminal instance history within cap: "
                        f"{total_count}/{max_history} "
                        f"({excluded_count} pinned, excluded)"
                    )
                else:
                    logger.debug(
                        f"Terminal instance history within cap: {total_count}/{max_history}"
                    )
                return

            excess = total_count - max_history
            if excluded_count:
                logger.info(
                    f"Terminal instance history exceeds cap: {total_count} > "
                    f"{max_history} (after excluding {excluded_count} pinned), "
                    f"pruning {excess} oldest"
                )
            else:
                logger.info(
                    f"Terminal instance history exceeds cap: {total_count} > {max_history}, "
                    f"pruning {excess} oldest"
                )

            # Prune the oldest instances (first 'excess' items in the list)
            # Per-instance try/except ensures one failure doesn't abort the batch
            to_delete = candidates[:excess]
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

        PR1 (C4) — observation-only timing wrapper. We bracket the whole
        method with ``time.perf_counter`` and emit one gated INFO line
        per branch via ``daemon.checkpoint_perf.log_prune`` (entry-with-
        threads / no-threads / exit) carrying the thread count + deleted
        count + duration_ms. The exit line always emits from the finally
        block; on the error branch it carries the PARTIAL deleted count
        accumulated before the failure (alongside the existing
        ``logger.error`` from the inner except). Every ``log_prune`` line
        honors ``CHECKPOINT_PERF_LOGS=0`` (W4). The try/finally sits
        OUTSIDE the existing try/except so error semantics stay identical
        (the existing ``except Exception as e`` still swallows and logs
        the error; the finally only adds timing observation).
        """
        import time
        from daemon.checkpoint_perf import log_prune

        t0 = time.perf_counter()
        # W7 — both counters are accumulated live: observed_total_deleted
        # increments INSIDE the pruning loop below, so a mid-walk
        # exception still reports the partial deletion count in the exit
        # line (a post-loop assignment would log 0 despite partial
        # deletes — the exact defect W7 fixes).
        observed_thread_count = 0
        observed_total_deleted = 0
        try:
            try:
                max_per_thread = CHECKPOINT_MAX_PER_THREAD

                # Find threads with excessive checkpoints via the adapter.
                # The SQLite adapter wraps this in AsyncSqliteSaver's lock.
                excess_pairs = await self._checkpointer.find_excess_checkpoint_groups(
                    max_per_thread
                )

                if not excess_pairs:
                    log_prune("prune", 0, 0, 0, note="no excess threads")
                    logger.debug("No threads with excessive checkpoints found")
                    return

                observed_thread_count = len(excess_pairs)
                log_prune(
                    "prune-entry",
                    threads=observed_thread_count,
                    deleted=0,
                    duration_ms=0,
                    max_per_thread=max_per_thread,
                )

                logger.info(
                    f"Found {len(excess_pairs)} thread/namespace pairs with > {max_per_thread} checkpoints"
                )

                # Prune each thread's checkpoints. W7: the running total
                # increments as deletions happen so the exit line reports
                # partial progress even if a later iteration raises.
                for thread_id, checkpoint_ns, cnt in excess_pairs:
                    observed_total_deleted += await self._prune_thread_checkpoints(
                        thread_id, checkpoint_ns, max_per_thread
                    )

                logger.info(
                    f"Pruned {observed_total_deleted} checkpoints from {len(excess_pairs)} thread/namespace pairs"
                )

            except Exception as e:
                logger.error(f"Per-thread checkpoint pruning failed: {e}")
        finally:
            log_prune(
                "prune-exit",
                threads=observed_thread_count,
                deleted=observed_total_deleted,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def _prune_unreferenced_blobs(self) -> None:
        """(E) Phase 1 C3 — reference-aware checkpoint_blobs prune (dry-run default).

        Deletes blobs whose (channel, version) is not referenced by
        ``checkpoint->'channel_versions'`` of any REMAINING checkpoint row
        in the same (thread_id, checkpoint_ns) — the direct anti-join
        (decision D1: no reference-table machinery). The algorithm, the
        zero-refs fail-safe, and the destructive env-flag gate all live in
        ``daemon/services/checkpoint_prune.py`` (single owner per the C3
        file table); this wrapper only delegates to it.

        Conservative ladder: DRY-RUN ONLY by default (reports would-delete
        counts + bytes, deletes nothing). The destructive arm requires
        BOTH ``CHECKPOINT_BLOB_PRUNE_DRY_RUN=0`` AND
        ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`` and is structurally
        unreachable otherwise (see checkpoint_prune module docstring).
        PostgreSQL-only — no-ops with a WARNING on SQLite backends.

        Candidates are enumerated via ``find_all_thread_ns_pairs`` (D21) —
        ALL (thread_id, checkpoint_ns) pairs, NOT
        ``find_excess_checkpoint_groups`` whose HAVING clause would skip
        single-checkpoint threads.
        """
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        await prune_unreferenced_blobs(self._checkpointer)

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
        2.5. Prune the ``message_metadata`` side-table rows for the thread
           (T5.19 — merge precondition, architect §3). The side table has no
           FK on either backend, so without this step a deleted instance's
           rows would accumulate forever. Never-raise: a prune failure is
           logged as a WARNING and tolerated — orphaned rows are
           over-record-only and never join the read path.
        3. Invoke the on_instance_deleted callback to release in-memory state
           (graph cache, graph tasks, request registry) — only if the
           instance record was actually deleted.

        Rationale for this order:
        - If instance delete fails → checkpoint data is preserved, and the
          next maintenance cycle will retry cleanly.
        - If instance delete succeeds but checkpoint deletion fails → the
          orphan checkpoint thread is naturally swept by Operation A
          (_cleanup_orphaned_threads) on the next cycle.
        - The side-table prune (2.5) runs only after checkpoint deletion
          succeeded and before in-memory state is released, mirroring the
          architect §3 anchor ordering.

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

        # 2.5. Prune the message_metadata side-table rows for this thread
        # (T5.19 — merge precondition, architect §3). The side table has
        # no FK on either backend, so a deleted instance's rows would
        # otherwise persist forever (growth ≈ 2–4 rows/turn × turns ×
        # instances). Positioned AFTER adelete_thread / BEFORE the
        # in-memory callback per the architect §3 anchor.
        #
        # NEVER-RAISE GUARD (W3): adelete_thread already succeeded above;
        # a prune failure MUST NOT raise out of _cleanup_instance —
        # orphaned side-table rows are tolerated (over-record-only, they
        # never join the read path), a broken instance teardown is not.
        # The repo is SYNC (decisions.md D14) — bridged via
        # asyncio.to_thread like every other consumer of this repo.
        if self._message_metadata_repo is not None:
            try:
                deleted_rows = await asyncio.to_thread(
                    self._message_metadata_repo.delete_for_thread, instance_id
                )
                logger.info(
                    f"message_metadata prune: deleted {deleted_rows} row(s) "
                    f"for thread {instance_id[:8]}..."
                )
            except Exception:
                logger.warning(
                    f"message_metadata prune failed for {instance_id[:8]}... "
                    "— orphans tolerated (never-raise guard)",
                    exc_info=True,
                )

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

    def _get_protected_instance_ids(self) -> set[str]:
        """Return the set of instance IDs that must NEVER be deleted by cleanup.

        The protected set is the union of every pinned instance's tree
        root's full subtree: a pinned instance always resolves up to
        its tree root via :meth:`SQLModelInstanceRepository.get_tree_root_id`,
        and the entire subtree under that root (the root itself plus
        every descendant) is collected via
        :meth:`SQLModelInstanceRepository.get_cascade_tree_ids`. This means a
        pinned child protects all of its siblings + cousins + their
        descendants under the same root.

        P1 (phase1-plan.md T6, R6, C11): enumeration switched from the
        transient ``get_tree_ids`` to the kill-switch wrapper
        ``get_cascade_tree_ids``. Polarity change: terminal descendants of
        a pinned root are now protected (previously the transient set
        could miss them when their hierarchy rows were deleted, allowing
        premature TTL purge). Matches user pin intent. Observable via the
        ``pinned_subtree_terminal_count`` metric emitted from
        :meth:`execute` so the polarity change is not silent.

        Returns an empty ``set`` when ``self._ui_prefs_repo is None``
        (the UI-prefs repo has not been wired) — that is the
        backward-compatible mode the existing tests rely on.

        Fail-safe contract: a ``get_pinned_instance_ids()`` failure is
        NOT swallowed into an empty set. Pinned instances are
        user-visible protection with a guarantee that they (and their
        subtree) are never deletable; degrading to "no protection" on a
        transient prefs-DB error would silently violate that guarantee.
        The exception propagates so the per-operation ``try/except`` in
        the callers (``_cleanup_expired_terminal``, ``_enforce_history_cap``)
        skips the entire cleanup cycle and the next cycle retries.

        Returns:
            ``set`` of protected ``instance_id`` strings. Empty when
            no instances are pinned OR when ``ui_prefs_repo`` was
            not provided. Raises whatever ``get_pinned_instance_ids``
            raises when the lookup fails.
        """
        if self._ui_prefs_repo is None:
            # UI-prefs repo not wired — backward-compatible mode the
            # existing tests rely on. No protection is possible without
            # the repo, so an empty set is the only safe answer here.
            return set()

        # NOTE: this call may raise (e.g. DB connectivity failure). We
        # intentionally do NOT catch it — a pinned instance is a
        # user-visible guarantee that the instance (and its tree) is
        # NEVER deletable. If we cannot determine the protected set, the
        # safe answer is to skip this cleanup cycle and let the next
        # cycle retry. The two callers (``_cleanup_expired_terminal`` Op
        # B and ``_enforce_history_cap`` Op C) each wrap their full
        # operation body in ``try/except Exception``, so the propagated
        # exception lands there, logs as
        # "Expired terminal cleanup failed" / "History cap enforcement
        # failed", and the cycle is skipped without any deletions.
        pinned_ids = self._ui_prefs_repo.get_pinned_instance_ids()

        if not pinned_ids:
            return set()

        # Resolve each pinned ID up to its tree root, dedupe, then collect
        # each root's full subtree. This bounds the round-trip count to
        # O(unique_roots + total_pinned) — typically small.
        protected: set[str] = set()
        roots: set[str] = set()
        for pinned_id in pinned_ids:
            root_id = self._instance_repo.get_tree_root_id(pinned_id)
            if root_id is None:
                # A missing ancestor or traversal depth cap can leave a live
                # pinned instance unreachable. Fail-protect its subtree.
                existing = self._instance_repo.get(pinned_id)
                if existing is not None:
                    logger.warning(
                        "Pinned instance %s has a broken parent chain or depth limit "
                        "was reached; protecting it as its own root",
                        pinned_id,
                    )
                    protected.update(
                        self._instance_repo.get_cascade_tree_ids(pinned_id)
                    )
                continue
            roots.add(root_id)

        for root_id in roots:
            subtree = self._instance_repo.get_cascade_tree_ids(root_id)
            protected.update(subtree)

        return protected

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
