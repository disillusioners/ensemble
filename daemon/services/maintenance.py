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

Size rationale (tidier fix pass 2026-09-27, ~1.8k lines): this module
predates the Maintenance Console and hosts TWO concerns by design —
the generic ``MaintenanceService`` scheduler and the
``CheckpointCleanupJob`` Op A–E engine whose three instance-sweep ops
(A/B/C) share ``_cleanup_instance`` + the pinned-subtree protection
set. Splitting the job out would fork those shared helpers or force
an import cycle; the Section-1 additions (run-lock gate, audit rows,
dual entry points) deliberately landed IN this file so the auto and
manual paths stay textually adjacent for the INV-1/INV-9 AST pins.
Revisit only if a third cleanup section arrives.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Coroutine

from daemon.checkpoint_adapter import CheckpointerAdapter
from daemon.config import PersistenceConfig
from daemon.constants import (
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
from daemon.services.checkpoint_prune import (
    BlobPruneSummary,
    prune_unreferenced_blobs,
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
        # Initial delay to let the system stabilize on startup. NOTE
        # (W2, v3 fix pass): this sleep is boot stabilization ONLY —
        # it is NOT the sweep-vs-first-tick ordering guarantee. That
        # ordering is structural: the maintenance boot sweep runs in
        # ``manager.initialize()`` immediately before ``start()`` (see
        # ``maintenance_boot_sweep.run_boot_sweep_with_retry``; pinned
        # in test_checkpoint_cleanup_job_wiring_pin.py).
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

    async def is_idle(self) -> bool:
        """Public idle probe — thin wrapper over :meth:`_is_idle`.

        Section 1 / AM-12: the ``MaintenanceApiService.execute()`` path
        needs the same system-wide idle signal the auto cycle uses to
        decide ``advisory: "system_busy"`` (the gate is advisory — a
        busy signal does NOT refuse an execute; see INV-12 in the
        plan). Wrapping the existing private method lets the API layer
        reuse the established ``_is_idle`` docblock (which documents
        the known blind-spots — ``list_all_pending`` sees only
        ``admission_state='queued'``; the predicate misses some
        in-flight paths).

        ``None`` returns from the inner probe (e.g. a missing
        ``_task_repository``) collapse to ``True`` (idle — no
        refusal). Same fail-soft semantics as the private method.
        """
        try:
            return await self._is_idle()
        except Exception:  # noqa: BLE001 — probe MUST be best-effort
            return True

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


# ── Section 1: CheckpointCleanup summary shape ──────────────────────────────────
#
# T1.2/1.3 — Section 1 of the Maintenance Console surfaces structured
# summaries from each checkpoint-cleanup cycle. Two dataclasses carry
# the per-cycle numbers, plus a ``to_summary_dict`` that emits the
# FROZEN wire shape (plan-overview §2 — ``/status`` ``last_run.summary``
# block + the dry-run response shape; see INV-5 / AM-11).


@dataclass
class CheckpointRowPruneSummary:
    """Op D (per-thread retention) per-cycle counters.

    The fields populated depend on the cycle kind:

    * **destructive** (``run_checkpoint_prunes(destructive=True)`` /
      auto ``_prune_per_thread_checkpoints``): ``deleted_checkpoints`` +
      ``deleted_writes`` accumulate the actual DELETE return values.
    * **dry-run** (``run_checkpoint_prunes(destructive=False)``): the
      auto cycle never calls this path; the manual dry-run pre-computes
      ``would_delete_checkpoints`` + ``would_delete_writes`` from the
      adapter's read-only ``count_writes_excluding`` (T2b) plus the
      existing ``get_checkpoint_ids``-driven keep-set arithmetic.

    ``scanned_pairs`` (always populated; one extra read-only
    ``GROUP BY`` per cycle — see T1.4 rationale) lets the wire shape
    express ``scanned_pairs=12, excess_pairs=0`` consistently with the
    contract example.

    v3.2 projection (R-1): ``would_free_bytes_after_row_prune`` is the
    sum across the excess pairs of "blobs currently referenced but
    whose ONLY referencers are excess rows D will delete" (per-pair
    computed by ``count_blobs_referenced_only_by_excess``; skipped pairs
    contribute 0 per R-4). Pairs whose ``ids_to_keep`` is empty
    (max_per_thread=0 edge) contribute 0 — the loop never queries the
    adapter for them, matching the destructive arm's skip; they do NOT
    collapse to the pair's referenced-set total. Field is DRY-RUN ONLY
    — the destructive arm is out of
    scope (auto never calls this method; manual destructive execute
    computes the real reclaim at execute time, not at the dry-run).
    """

    backend: str = "postgres"
    scanned_pairs: int = 0            # len(find_all_thread_ns_pairs()) — ALL groups
    excess_pairs: int = 0             # len(find_excess_checkpoint_groups(N))
    deleted_checkpoints: int = 0      # destructive: sum(delete_checkpoints_excluding)
    deleted_writes: int = 0           # destructive: sum(delete_writes_excluding)
    would_delete_checkpoints: int = 0 # dry-run: sum(cnt - N) per excess pair
    would_delete_writes: int = 0      # dry-run: sum(count_writes_excluding) (T2b)
    would_free_bytes_after_row_prune: int = 0  # [v3.2 R-1] dry-run projection only

    def to_summary_dict(self) -> dict[str, Any]:
        """The FROZEN ``checkpoint_rows`` + ``writes`` blocks of a cycle summary.

        Matches the contract v3 example in plan-overview §2:
        ``{"checkpoint_rows": {"scanned_pairs", "deleted", "excess_pairs"},
        "writes": {"deleted"}}`` — ONE shape. Flavor variation on the
        summary wire lives ONLY inside the ``blobs`` block (dual-flavor
        keys, AM-11): ``/status.last_run`` never contains
        ``manual_dry_run`` rows (AM-9), and the auto cycle's Op D is
        always destructive — so ``deleted`` is the actual delete count
        on every row that surfaces through this shape. The manual
        dry-run response (§3 ``would_delete`` block) is composed by
        ``MaintenanceApiService`` directly from the
        ``would_delete_*`` fields above — NOT through this method.

        EXCLUSION: ``would_free_bytes_after_row_prune`` lives on this
        dataclass but is NOT part of this frozen summary shape — it is
        consumed ONLY by the ``MaintenanceApiService`` manual dry-run
        wire composer (the §3 projection fields).
        """
        return {
            "checkpoint_rows": {
                "scanned_pairs": self.scanned_pairs,
                "deleted": self.deleted_checkpoints,
                "excess_pairs": self.excess_pairs,
            },
            "writes": {"deleted": self.deleted_writes},
        }


@dataclass
class CheckpointRunResult:
    """One checkpoint-cleanup cycle result — wires into the audit table.

    T1.3 — assembled by ``CheckpointCleanupJob.run_checkpoint_prunes``
    (manual entry point) and by ``CheckpointCleanupJob.execute``
    (auto-cycle summary assembly). ``to_summary_dict`` emits the FROZEN
    wire shape for ``maintenance_runs.summary_json`` and ``/status``
    ``last_run.summary`` (plan-overview §2): exactly
    ``{checkpoint_rows, writes, blobs, duration_ms}`` — flavor
    variation lives ONLY inside the ``blobs`` block (dual-flavor keys,
    AM-11); there is NO top-level ``skipped_truncated`` (the §2 example
    carries it inside ``blobs`` only).
    """

    rows: CheckpointRowPruneSummary = field(default_factory=CheckpointRowPruneSummary)
    blobs: BlobPruneSummary = field(default_factory=BlobPruneSummary)
    duration_ms: int = 0
    skipped_truncated: bool = False   # [AM-10/AM-15] 1000-entry cap fired

    def to_summary_dict(self) -> dict[str, Any]:
        """The FROZEN wire shape (plan-overview §2 — last_run.summary)."""
        rows_dict = self.rows.to_summary_dict()
        blobs_dict = self.blobs.to_summary_blobs_dict()
        # skipped_truncated inside the blobs block is the canonical flag;
        # the dataclass-level field is the same signal pre-serialization
        # (kept for the dry-run wire composer in MaintenanceApiService).
        return {
            **rows_dict,
            "blobs": blobs_dict,
            "duration_ms": self.duration_ms,
        }


class CheckpointCleanupJob:
    """Job that cleans up orphaned and expired checkpoint data.

    This job runs 5 cleanup operations in sequence:
    (A) Delete checkpoint threads with no matching instance (orphans)
    (B) Delete checkpoint data for expired terminal instances
    (C) Enforce max_instance_history cap on terminal instances
    (D) Prune per-thread checkpoints to ``config.checkpoint_max_per_thread``
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
        run_lock: "MaintenanceRunLock | None" = None,
        runs_repo: "MaintenanceRunsRepository | None" = None,
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
            run_lock: Optional :class:`MaintenanceRunLock` (T3 / AM-4).
                When wired, the auto ``execute()`` participates in the
                single-flight gate: if the lock is held at cycle start
                (by a manual dry-run or execute), the auto cycle
                non-raising-skips + re-arms ``job.last_run`` + DEBUGs
                the in-flight run_id (no ops, NO row — AM-6).
                ``None`` (default) preserves today's exact behavior
                (no gate, no audit row written).
            runs_repo: Optional :class:`MaintenanceRunsRepository` (T4 /
                AM-15). When wired, the auto ``execute()`` writes a
                ``maintenance_runs`` row (kind='auto', triggered_by='system')
                whose lifecycle is ``running → succeeded|failed|interrupted``
                and whose terminal summary carries the
                :class:`CheckpointRunResult.to_summary_dict` payload. ``None``
                (default) preserves today's exact behavior (no row written).
        """
        self._config = config
        self._checkpointer = checkpointer
        self._instance_repo = instance_repo
        self._on_instance_deleted = on_instance_deleted
        self._ui_prefs_repo = ui_prefs_repo
        self._message_metadata_repo = message_metadata_repo
        # T3 — optional gate / audit kwargs. ``None`` = legacy unwired
        # path; both kwargs default ``None`` so existing unit tests that
        # construct the job unwired (and the pre-T3 existing wiring-pin
        # test) stay green — the kwargs are OPTIONAL, behavior on
        # ``None`` is byte-identical to pre-T3.
        self._run_lock = run_lock
        self._runs_repo = runs_repo

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

        T1.8 / T3 — the auto cycle captures Op D + Op E into a
        :class:`CheckpointRunResult` (ops A–C remain log-only; not in
        the contract shape) and hands it to the auto run-row write.
        The RETURN TYPE stays ``-> None`` (callers at
        ``MaintenanceService._run_pending_jobs`` ignore returns; no
        signature ripple — plan T1.8).

        Single-flight (T3 / AM-4): when ``_run_lock`` is wired, the
        lock is acquired BEFORE the conditional INSERT and released in
        ``finally``. The auto call site passes no kwargs to the blob
        arm (``_prune_unreferenced_blobs(destructive=None)``) so the
        env dual-arm gate is the destructive arbiter for the auto cycle
        — INV-1 / INV-9. The lock-holder LOSES path (no ops, NO row,
        DEBUG with the in-flight run_id, ``job.last_run`` updated by
        the caller to re-arm the interval — the skip is non-raising)
        is the AM-4 / Focus Area 6 non-raising skip path. An INSERT
        CONFLICT on the partial unique index (the two-dev-daemons-
        on-shared-PG class, AM-5) takes the same skip path — the DB
        claim is the real gate; another daemon's run is in flight.
        """
        # Lazy imports (why: ``maintenance_run_lock`` and the
        # ``maintenance_runs`` models live on the same import graph as
        # the manager; keeping them function-local keeps THIS module
        # importable during early boot / conftest mock teardown without
        # pulling the repository layer at module import time. The
        # checkpoint_prune/timestamps imports are call-frequency
        # trivial but stay lazy for the same cold-import-cycle reason —
        # see daemon/config.py→services cold-import cycle precedent).
        from daemon.services.checkpoint_prune import blob_prune_env_state
        from daemon.services.maintenance_run_identity import new_maintenance_run_id
        from daemon.services.timestamps import now_utc_iso
        from daemon.services.maintenance_run_lock import MaintenanceRunContext

        logger.info("Starting checkpoint cleanup job")

        # T3 — auto single-flight gate, step 1 (the in-process lock).
        # ``acquire`` is FAIL-FAST (returns ``False`` immediately when
        # held — no await-queueing). The skip is NON-RAISING so the
        # caller (MaintenanceService._run_pending_jobs) stamps
        # ``job.last_run`` on this branch too, re-arming the interval.
        ctx: MaintenanceRunContext | None = None
        if self._run_lock is not None:
            ctx = MaintenanceRunContext(
                run_id=new_maintenance_run_id(),
                kind="auto",
                started_at=now_utc_iso(),
                triggered_by="system",
            )
            if not await self._run_lock.acquire(ctx):
                in_flight_id = (
                    self._run_lock.in_flight.run_id
                    if self._run_lock.in_flight is not None
                    else "unknown"
                )
                logger.debug(
                    "checkpoint_cleanup skipped: maintenance run %s in flight",
                    in_flight_id,
                )
                return

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

        # T3 — single-flight gate, step 2: the conditional INSERT (the
        # DB-side claim, AM-5). The lock is held, so an in-process
        # conflict is impossible; a CONFLICT here means another daemon
        # shares this PG and owns the lane → same non-raising skip path
        # (no ops, NO row, DEBUG names the in-flight run).
        if self._runs_repo is not None and ctx is not None:
            # Lazy import (why: SQLModel repository model — avoids a
            # module-level daemon.repositories import in the hot
            # services graph; same cold-import-cycle rationale as the
            # block above).
            from daemon.repositories.maintenance_runs.models import MaintenanceRun

            inserted = True
            try:
                inserted = self._runs_repo.insert(
                    MaintenanceRun(
                        run_id=ctx.run_id,
                        section="checkpoint-cleanup",
                        kind=ctx.kind,
                        started_at=ctx.started_at,
                        status="running",
                        triggered_by=ctx.triggered_by,
                        env_flags_json={
                            **blob_prune_env_state(),
                            "destructive_override": False,  # auto never passes the kwarg
                        },
                    )
                )
            except Exception as e:
                # Audit-table availability must not gate the auto cycle
                # (INV-1 spirit): log + continue WITHOUT the audit row.
                # [tidier fix pass] exception class + exc_info — the
                # fail-soft stance is unchanged, but the audit-write
                # failure class must be diagnosable from the log alone.
                logger.warning(
                    f"maintenance_runs insert failed (auto run_id="
                    f"{ctx.run_id}): {type(e).__name__}: {e}; "
                    f"continuing without audit row",
                    exc_info=True,
                )
            if not inserted:
                in_flight = self._runs_repo.get_running("checkpoint-cleanup")
                logger.debug(
                    "checkpoint_cleanup skipped: DB claim held by run %s "
                    "(started %s)",
                    in_flight.run_id if in_flight else "unknown",
                    in_flight.started_at if in_flight else "unknown",
                )
                if self._run_lock is not None:
                    self._run_lock.release()
                return

        try:
            # Operation A: Cleanup orphaned threads
            await self._cleanup_orphaned_threads()

            # Operation B: Cleanup expired terminal instances
            await self._cleanup_expired_terminal()

            # Operation C: Enforce history cap
            await self._enforce_history_cap()

            # Operation D: Prune per-thread checkpoints (T1.4 — now
            # returns a ``CheckpointRowPruneSummary``; existing logs
            # unchanged, INV-1).
            rows_summary = await self._prune_per_thread_checkpoints()

            # Operation E (Phase 1 C3): reference-aware checkpoint_blobs
            # prune. Isolated per the plan — a failure in the blob bucket
            # must NEVER break the retention prune above (which has
            # already completed) or any subsequent maintenance cycle.
            # ``prune_unreferenced_blobs`` itself never raises; this
            # belt-and-braces wrapper guarantees the isolation even if
            # that contract regresses. T1.6 — captures the summary for
            # the audit row; the destructive kwarg is NOT passed (auto
            # cycle uses the env gate — INV-1).
            blobs_summary = BlobPruneSummary()
            try:
                blobs_summary = await self._prune_unreferenced_blobs()
            except Exception as e:  # noqa: BLE001
                logger.error(f"Unreferenced blob prune operation failed: {e}")

            logger.info("Checkpoint cleanup job completed")

            result = CheckpointRunResult(
                rows=rows_summary,
                blobs=blobs_summary,
                skipped_truncated=len(blobs_summary.skipped) > 1000,
            )

            # T3 — terminal write on success.
            if self._runs_repo is not None and ctx is not None:
                try:
                    self._runs_repo.mark_terminal(
                        ctx.run_id,
                        "succeeded",
                        now_utc_iso(),
                        summary_json=result.to_summary_dict(),
                    )
                except Exception as e:
                    # [tidier fix pass] class + exc_info (fail-soft kept).
                    logger.warning(
                        f"maintenance_runs succeeded-mark failed for "
                        f"{ctx.run_id}: {type(e).__name__}: {e}",
                        exc_info=True,
                    )
        except Exception as outer_exc:
            # T3 — terminal write on failure (infra faults only — pair
            # failures live in ``summary.skipped``, not this row). The
            # ops already never raise (per-op isolation); this outer
            # guard is belt-and-braces per the plan (T3.3).
            logger.error(
                f"Checkpoint cleanup job failed: {outer_exc}", exc_info=True
            )
            if self._runs_repo is not None and ctx is not None:
                try:
                    self._runs_repo.mark_terminal(
                        ctx.run_id,
                        "failed",
                        now_utc_iso(),
                        error_json={
                            "code": "execution_error",
                            "message": f"{type(outer_exc).__name__}: {outer_exc}",
                        },
                    )
                except Exception as e:
                    # [tidier fix pass] class + exc_info (fail-soft kept).
                    logger.warning(
                        f"maintenance_runs failed-mark failed for "
                        f"{ctx.run_id}: {type(e).__name__}: {e}",
                        exc_info=True,
                    )
            raise
        finally:
            # T3 — release the in-process lock on every path that
            # acquired it (success, failure, exception). The two skip
            # paths above return BEFORE this block: the lock-loss path
            # never acquired; the DB-conflict path released explicitly.
            if self._run_lock is not None:
                self._run_lock.release()

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

            # Sweep-level accounting for the one-shot INFO summary emit
            # at the end of the loop. Per-thread observation is demoted
            # to DEBUG (was conditional INFO inside the loop).
            sweep_t0 = time.perf_counter()
            pruned_rows_total = 0

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
                        # Per-thread DEBUG emit (formerly conditional
                        # INFO inside the loop — one line per orphan,
                        # mostly with ``deleted=0`` during steady state,
                        # pure noise at INFO). Aggregate into the
                        # sweep-level INFO summary below.
                        logger.debug(
                            f"_cleanup_orphaned_threads: "
                            f"message_metadata prune deleted "
                            f"{deleted_rows} row(s) for thread "
                            f"{thread_id[:8]}..."
                        )
                        pruned_rows_total += deleted_rows
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

            # ONE summary line for the sweep (replaces per-thread INFO).
            elapsed_ms = int((time.perf_counter() - sweep_t0) * 1000)
            logger.info(
                f"message_metadata prune: summary threads={len(orphaned)} "
                f"deleted={pruned_rows_total} row(s) elapsed_ms={elapsed_ms} "
                f"(op=_cleanup_orphaned_threads)"
            )

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
            # Sweep-level accounting for the one-shot INFO summary line.
            # ``_cleanup_instance`` returns the per-thread message_metadata
            # prune count (``None`` when no prune was attempted); sum the
            # int returns and skip Nones.
            sweep_t0 = time.perf_counter()
            pruned_rows_total = 0
            for instance_id in candidates:
                try:
                    pruned_rows = await self._cleanup_instance(instance_id)
                    deleted += 1
                    if isinstance(pruned_rows, int):
                        pruned_rows_total += pruned_rows
                except Exception as e:
                    logger.error(
                        f"Failed to clean up instance {instance_id[:8]}...: {e}"
                    )

            logger.info(f"Cleaned up {deleted} expired terminal instances (checkpoints + records)")

            # ONE summary line for the sweep (replaces per-instance INFO
            # from ``_cleanup_instance`` — which now logs at DEBUG).
            elapsed_ms = int((time.perf_counter() - sweep_t0) * 1000)
            logger.info(
                f"message_metadata prune: summary threads={deleted} "
                f"deleted={pruned_rows_total} row(s) elapsed_ms={elapsed_ms} "
                f"(op=_cleanup_expired_terminal)"
            )

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
            # Sweep-level accounting for the one-shot INFO summary line.
            sweep_t0 = time.perf_counter()
            pruned_rows_total = 0
            for instance_id in to_delete:
                try:
                    pruned_rows = await self._cleanup_instance(instance_id)
                    deleted += 1
                    if isinstance(pruned_rows, int):
                        pruned_rows_total += pruned_rows
                except Exception as e:
                    logger.error(
                        f"Failed to clean up instance {instance_id[:8]}...: {e}"
                    )

            logger.info(f"Pruned {deleted} terminal instances from history cap (checkpoints + records)")

            # ONE summary line for the sweep (replaces per-instance INFO
            # from ``_cleanup_instance`` — which now logs at DEBUG).
            elapsed_ms = int((time.perf_counter() - sweep_t0) * 1000)
            logger.info(
                f"message_metadata prune: summary threads={deleted} "
                f"deleted={pruned_rows_total} row(s) elapsed_ms={elapsed_ms} "
                f"(op=_enforce_history_cap)"
            )

        except Exception as e:
            logger.error(f"History cap enforcement failed: {e}")

    async def _prune_per_thread_checkpoints(self) -> CheckpointRowPruneSummary:
        """(D) For each thread, keep only the latest ``config.checkpoint_max_per_thread`` checkpoints.

        Queries the checkpoint database to find threads with more than
        ``self._config.checkpoint_max_per_thread`` checkpoints, then deletes
        the oldest ones. The retention N is operator-tunable via the
        ``CHECKPOINT_MAX_PER_THREAD`` env var (default 3, floor 1 enforced
        by ``PersistenceConfig.checkpoint_max_per_thread`` ``ge=1`` at config
        load — the ge=1 guard makes a 0 / negative value unreachable in
        practice; if it were bypassed, the failure mode would be a silent
        no-op wedge: ``find_excess_checkpoint_groups(N=0)`` returns no
        rows (the adapter ``HAVING COUNT(*) > 0`` filters every thread
        out) AND ``get_checkpoint_ids(N=0)`` returns ``[]`` which makes
        ``_prune_thread_checkpoints`` early-return without deleting —
        retention drifts unbounded, never silent-mass-prune).

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

        T1.4 — the method now returns a :class:`CheckpointRowPruneSummary`
        (was ``-> None``). Existing logs and behavior unchanged (INV-1);
        the auto ``execute()`` captures the result into its own
        ``CheckpointRunResult`` for the audit row (T1.8). The summary
        carries ``scanned_pairs`` (one extra read-only ``GROUP BY`` per
        cycle — ``find_all_thread_ns_pairs`` — no delete-behavior
        change; see T1.4 rationale in the plan) so the wire shape can
        express ``scanned_pairs=12, excess_pairs=0`` consistently with
        the contract example.
        """
        from daemon.checkpoint_perf import log_prune

        t0 = time.perf_counter()
        # W7 — both counters are accumulated live: observed_total_deleted
        # increments INSIDE the pruning loop below, so a mid-walk
        # exception still reports the partial deletion count in the exit
        # line (a post-loop assignment would log 0 despite partial
        # deletes — the exact defect W7 fixes).
        observed_thread_count = 0
        observed_total_deleted = 0
        observed_total_deleted_writes = 0
        summary = CheckpointRowPruneSummary()
        try:
            try:
                max_per_thread = self._config.checkpoint_max_per_thread
                # DEBUG-level once-per-pass observability for the
                # env-tunable retention knob. Runs at most once per
                # ``checkpoint_cleanup_interval`` (default 24h,
                # ``CHECKPOINT_CLEANUP_INTERVAL_HOURS`` in
                # ``daemon/constants.py``) so it isn't noisy. Matches the
                # file's no-op-branch convention of logging at DEBUG.
                logger.debug(
                    f"Per-thread checkpoint retention: max_per_thread={max_per_thread} "
                    f"(CHECKPOINT_MAX_PER_THREAD env)"
                )

                # T1.4 — scanned_pairs (one extra read-only GROUP BY,
                # cycle cadence is 24h; no delete-behavior change).
                all_pairs = await self._checkpointer.find_all_thread_ns_pairs()
                summary.scanned_pairs = len(all_pairs)

                # Find threads with excessive checkpoints via the adapter.
                # The SQLite adapter wraps this in AsyncSqliteSaver's lock.
                excess_pairs = await self._checkpointer.find_excess_checkpoint_groups(
                    max_per_thread
                )

                if not excess_pairs:
                    log_prune("prune", 0, 0, 0, note="no excess threads")
                    logger.debug("No threads with excessive checkpoints found")
                    return summary

                observed_thread_count = len(excess_pairs)
                summary.excess_pairs = len(excess_pairs)
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
                    deleted_cps, deleted_wr = await self._prune_thread_checkpoints(
                        thread_id, checkpoint_ns, max_per_thread
                    )
                    observed_total_deleted += deleted_cps
                    observed_total_deleted_writes += deleted_wr

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
        summary.deleted_checkpoints = observed_total_deleted
        summary.deleted_writes = observed_total_deleted_writes
        return summary

    async def _prune_unreferenced_blobs(
        self, *, destructive: bool | None = None
    ) -> "BlobPruneSummary":
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
        ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`` (the auto cycle's
        env-arming path; auto call sites pass no kwarg → env gate) OR
        an explicit ``destructive=True`` kwarg (the manual Maintenance
        API execute path; INV-2 — see T2 / T1.6). PostgreSQL-only — no-ops
        with a WARNING on SQLite backends.

        Candidates are enumerated via ``find_all_thread_ns_pairs`` (D21) —
        ALL (thread_id, checkpoint_ns) pairs, NOT
        ``find_excess_checkpoint_groups`` whose HAVING clause would skip
        single-checkpoint threads.

        T1.6 — the previous shape discarded the returned ``BlobPruneSummary``
        (was ``await prune_unreferenced_blobs(self._checkpointer)`` — the
        summary object landed in a no-op). It is now captured and
        returned; the auto ``execute()`` and the manual
        ``run_checkpoint_prunes`` entry point both consume it for the
        audit row.
        """
        return await prune_unreferenced_blobs(
            self._checkpointer, destructive=destructive
        )

    async def _compute_row_prune_dry_run(
        self,
        *,
        blobs_skipped: list[tuple[str, str, str]] | None = None,
    ) -> CheckpointRowPruneSummary:
        """T1.5 — read-only mirror of Op D for the manual dry-run path.

        Iterates ``find_excess_checkpoint_groups(N)`` like the
        destructive arm, but instead of DELETE statements calls the
        adapter's read-only ``count_writes_excluding`` (T2b) for the
        writes accounting and a non-mutating ``get_checkpoint_ids``-derived
        keep-set arithmetic for the checkpoints accounting. Zero
        DELETE statements; the auto cycle never calls this method
        (INV-1).

        v3.2 projection (R-1): for each excess pair with non-empty
        ``ids_to_keep`` AND whose ``(thread_id, checkpoint_ns)`` is NOT
        in ``blobs_skipped`` (the blob prune's ZERO_REFS / MAX_REFS
        cap skip list — populated by the Op E arm that runs BEFORE
        this Op D dry-run per AM-2 manual-path ordering), the per-pair
        ``count_blobs_referenced_only_by_excess`` computes the
        ``after-row-prune`` bytes (blobs whose ONLY remaining
        referencers are the excess rows D of THIS pass deletes — the
        "referenced by excess only" reading of R-1). Skipped pairs
        contribute 0 per R-4; their excess rows still delete but their
        blobs' reclaimability is unknown (the FE flag covers the
        honesty gap). The ``would_free_bytes_after_row_prune`` field
        on the returned summary is the projection this dry-run hands
        to the wire composer for ``bytes_reclaimable_after_row_prune``.

        ``blobs_skipped`` is keyword-only (no positional drift) and
        defaults to ``None`` (= empty skip-set) so the destructive arm
        does not need to thread the list. Only the manual dry-run path
        passes it (the wire composer needs the projection; auto does
        not — INV-1).
        """
        max_per_thread = self._config.checkpoint_max_per_thread
        summary = CheckpointRowPruneSummary()
        # R-4: a skipped pair's contribution to the projection is 0.
        # Build a set for O(1) membership; the blob prune's skipped
        # list is bounded by total thread+ns pair count.
        skipped_pair_keys: set[tuple[str, str]] = {
            (tid, ns) for (tid, ns, _reason) in (blobs_skipped or [])
        }
        try:
            # T1.4 — scanned_pairs (one extra read-only GROUP BY; no
            # delete-behavior change; mirrors the destructive arm).
            all_pairs = await self._checkpointer.find_all_thread_ns_pairs()
            summary.scanned_pairs = len(all_pairs)
            excess_pairs = await self._checkpointer.find_excess_checkpoint_groups(
                max_per_thread
            )
            summary.excess_pairs = len(excess_pairs)
            for thread_id, checkpoint_ns, cnt in excess_pairs:
                # ``cnt`` is the pair's checkpoint count (from the HAVING
                # filter); keep the most recent ``max_per_thread`` and
                # would-delete ``cnt - N``.
                would_delete_cps = max(0, cnt - max_per_thread)
                ids_to_keep_list = await self._checkpointer.get_checkpoint_ids(
                    thread_id, checkpoint_ns, max_per_thread
                )
                ids_to_keep = set(ids_to_keep_list)
                if ids_to_keep:
                    would_delete_writes = await self._checkpointer.count_writes_excluding(
                        thread_id, checkpoint_ns, ids_to_keep
                    )
                else:
                    would_delete_writes = 0
                summary.would_delete_checkpoints += would_delete_cps
                summary.would_delete_writes += would_delete_writes
                # v3.2 projection (R-1): per-pair ``after`` bytes —
                # blobs referenced ONLY by excess rows D will delete.
                # R-4: skipped pairs contribute 0 (their blobs'
                # reclaimability is unknown; the FE flag covers the
                # honesty gap). Empty keep_ids (max_per_thread=0 edge)
                # short-circuits — see adapter docstring for the set
                # algebra; we simply do not query in that case.
                if (
                    ids_to_keep
                    and (thread_id, checkpoint_ns) not in skipped_pair_keys
                ):
                    _cnt_after, bytes_after = (
                        await self._checkpointer
                        .count_blobs_referenced_only_by_excess(
                            thread_id, checkpoint_ns, ids_to_keep
                        )
                    )
                    summary.would_free_bytes_after_row_prune += bytes_after
            logger.debug(
                f"Op D dry-run: scanned={summary.scanned_pairs} "
                f"excess={summary.excess_pairs} "
                f"would_delete_checkpoints={summary.would_delete_checkpoints} "
                f"would_delete_writes={summary.would_delete_writes} "
                f"would_free_bytes_after_row_prune={summary.would_free_bytes_after_row_prune} "
                f"skipped_excluded={len(skipped_pair_keys & {(t, n) for t, n, _c in excess_pairs})}"
            )
        except Exception as e:
            # Mirror the destructive arm's per-op isolation (INV-1):
            # the dry-run is an informational scan, a failure here
            # must not abort the whole entry point — we surface the
            # partial counts we managed to compute.
            logger.error(f"Op D dry-run scan failed: {e}")
        return summary

    async def run_checkpoint_prunes(
        self, *, destructive: bool
    ) -> CheckpointRunResult:
        """MANUAL-ONLY entry point — Op E → Op D (AM-2, BLOCKING).

        Used by :class:`daemon.services.maintenance_api_service.MaintenanceApiService`
        for ``POST /api/maintenance/checkpoint-cleanup/dry-run`` and
        ``POST /api/maintenance/checkpoint-cleanup/execute``. The auto
        ``execute()`` MUST NOT route through this method (INV-1 / INV-9);
        the AST pin in
        ``tests/integration/test_checkpoint_cleanup_job_wiring_pin.py``
        asserts ``execute()``'s body does not call this method.

        Order rationale (W-1 mechanism restated v3 / AM-2 BLOCKER):
        Op E runs BEFORE Op D in the manual path. The dry-run's
        anti-join counts blobs referenced by ANY REMAINING checkpoint
        row; under the old D-first order every server check PASSES (the
        byte-equality gate compares the echo against the STORED dry-run
        row, so it always matches) and the failure is SILENT
        over-deletion: Op D unreferences blobs the subsequent blob pass
        then deletes beyond the confirmed echo. E-first keeps
        ``actual == expected`` on the happy path; the auto-cycle keeps
        D→E (INV-1). No post-run actual-vs-expected completion gate
        ever lands (INV-13) — the AM-2 regression pin (test 57 + case
        44 freed-bytes) is the catcher.

        The manual dry-run's blob arm is FORCED ``destructive=False``
        even when the operator has the env dual-arm armed: the env
        default is dry-run, but an armed env must not leak
        destructivity into the manual preview (INV-2 + the dry-run
        fidelity rule). Dry-run E→D mirrors destructive E→D for
        preview fidelity (both dry-run arms are read-only; ordering is
        immaterial for correctness there, but the preview should not
        lie about the order it will run in). Residual cost of E-first
        (accepted per decision-log AM-2): blobs referenced only by
        excess rows survive one extra cycle — conservative under-delete,
        self-healing.
        """
        t0 = time.perf_counter()
        # MANUAL dry-run blob arm: forced dry-run (NOT the env gate —
        # operator's manual preview must NOT inherit an armed env).
        # Manual destructive execute: explicit ``destructive=True``;
        # INV-2 — no env pre-arming required.
        blob_destructive = bool(destructive)
        blobs = await self._prune_unreferenced_blobs(destructive=blob_destructive)
        if not isinstance(blobs, BlobPruneSummary):  # belt-and-braces typing guard
            blobs = BlobPruneSummary()
        if destructive:
            rows = await self._prune_per_thread_checkpoints()
        else:
            # v3.2 (R-4): pass the blob prune's ``skipped[]`` so the
            # projection subtracts skipped pairs (their excess rows
            # still delete; their blobs' reclaimability is unknown).
            rows = await self._compute_row_prune_dry_run(
                blobs_skipped=blobs.skipped,
            )
        skipped_truncated = len(blobs.skipped) > 1000
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return CheckpointRunResult(
            rows=rows,
            blobs=blobs,
            duration_ms=duration_ms,
            skipped_truncated=skipped_truncated,
        )

    # ── Helper Methods ─────────────────────────────────────────────────────────

    async def _cleanup_instance(self, instance_id: str) -> int | None:
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
            return None

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
        #
        # Returned to the caller so loop callers can aggregate a single
        # per-sweep INFO summary line (per-thread DEBUG; summary at INFO).
        pruned_rows: int | None = None
        if self._message_metadata_repo is not None:
            pruned_rows = 0
            try:
                deleted_rows = await asyncio.to_thread(
                    self._message_metadata_repo.delete_for_thread, instance_id
                )
                pruned_rows = deleted_rows
                # Per-thread emit demoted to DEBUG (formerly INFO). The
                # per-call INFO line fired on every maintenance tick,
                # typically with ``deleted=0`` (rows already gone), and
                # produced multi-thread/second noise during orphan /
                # TTL sweeps. Loop callers aggregate into ONE INFO
                # summary at the end of the sweep (search
                # ``message_metadata prune: summary``). One-shot
                # callers (e.g. ``hard_delete_instance``) keep exactly
                # one INFO line per invocation.
                logger.debug(
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

        return pruned_rows

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
            # Flat pagination — ``SQLModelInstanceRepository.list`` returns the
            # 3-tuple ``(instances, total, truncated)``; this call discards ``truncated``.
            instances, total, _ = self._instance_repo.list(limit=limit, offset=offset)
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

        # Fail-closed protection: a pinned-set lookup failure propagates
        # (never degrades to "no protection") so the per-op try/except in
        # the callers skips the cycle and the next cycle retries.
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
                # Flat pagination — ``SQLModelInstanceRepository.list`` returns the
                # 3-tuple ``(instances, total, truncated)``; this call discards ``truncated``.
                instances, total, _ = self._instance_repo.list(
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
                # Flat pagination — ``SQLModelInstanceRepository.list`` returns the
                # 3-tuple ``(instances, total, truncated)``; this call discards ``truncated``.
                instances, total, _ = self._instance_repo.list(
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
    ) -> tuple[int, int]:
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
            Tuple ``(deleted_checkpoints, deleted_writes)``. T1.4 — the
            previous shape returned only ``deleted_checkpoints``; the
            summary capture needs BOTH per-pair delete counts. Existing
            call sites that only consumed the int return (none — the
            ``_prune_per_thread_checkpoints`` caller is the only consumer)
            have been updated; the
            ``observed_total_deleted`` accumulation is joined with a new
            ``observed_total_deleted_writes`` accumulator (W7 live-count
            pattern, mirrors the existing checkpoint accumulator).
        """
        # Step 1: Get checkpoint_ids to KEEP (most recent N)
        ids_to_keep_list = await self._checkpointer.get_checkpoint_ids(
            thread_id, checkpoint_ns, max_per_thread
        )
        ids_to_keep = set(ids_to_keep_list)

        if not ids_to_keep:
            return (0, 0)

        # Step 2: Delete checkpoints NOT in keep list
        checkpoint_rows = await self._checkpointer.delete_checkpoints_excluding(
            thread_id, checkpoint_ns, ids_to_keep
        )

        # Step 3: Delete corresponding writes NOT in keep list
        write_rows = await self._checkpointer.delete_writes_excluding(
            thread_id, checkpoint_ns, ids_to_keep
        )

        return (checkpoint_rows, write_rows)
