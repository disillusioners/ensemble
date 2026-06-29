"""Job Queue Service - Manages job queuing with per-queue locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.dispatch_event_bus import DispatchEventBus
    from daemon.services.work_resolver import WorkRecord

from daemon.repositories.job_queue import (
    AdmissionState,
    Decision,
    JobRepository,
    JobQueueRepository,
    JobItem,
    JobStatus,
)
from daemon.repositories.job_queue.models import ADMISSION_STATE_TO_STATUS
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError
from daemon.services.project_normalizer import normalize_project_id
from daemon.services.work_notifier import _format_status_display, notify_work_watchers
from daemon.services.work_status import is_terminal as _work_status_is_terminal
from daemon.registry import get_registry

logger = logging.getLogger(__name__)


# Shared terminal instance statuses — used across job_queue_service,
# instance_lifecycle, and job_processor for consistent cleanup behavior.
TERMINAL_STATUSES = frozenset([
    InstanceStatus.TERMINATED.value,
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.FAILED.value,
])

# TERMINAL_CANCEL_STATUSES contains instance statuses that represent abnormal termination
# (not a normal completion). COMPLETED is NOT here because it's a normal completion.
TERMINAL_CANCEL_STATUSES = frozenset([
    InstanceStatus.TERMINATED.value,
])


# Natural-language aliases → canonical job status.
# Applied in normalize_statuses() so that agents/LLMs that pass
# "running" (meaning "processing") get correct results instead of empty lists.
# ``paused`` is an identity mapping so natural-language queries like
# ``status="paused"`` resolve to the canonical enum value; pause is
# non-terminal (see JobStatus.PAUSED docs at models.py:25-28).
STATUS_ALIASES: dict[str, str] = {
    "running": "processing",
    "active": "processing",
    "in_progress": "processing",
    "queued": "pending",
    "waiting": "pending",
    "done": "completed",
    "success": "completed",
    "finished": "completed",
    "error": "failed",
    "failed": "failed",  # identity, for safety
    "killed": "cancelled",
    "canceled": "cancelled",  # common misspelling
    "dlq": "dead_letter",
    "dead": "dead_letter",
    "paused": "paused",  # identity — see comment above
}


def normalize_statuses(statuses: list[str] | None) -> list[str] | None:
    """Resolve natural-language status aliases to canonical job status values.

    - Case-insensitive (lowercases before lookup)
    - If a status is already a canonical value, keeps it as-is (backward compatible)
    - If a status is not a known alias, passes it through unchanged (let SQL return empty)
    """
    if not statuses:
        return statuses
    out: list[str] = []
    for s in statuses:
        canonical = STATUS_ALIASES.get(s.lower(), s.lower())
        out.append(canonical)
    return out


class DemandState(enum.Enum):
    """Job demand state for completion.
    
    Used by complete_job/complete_job_sync to specify the terminal state.
    CANCELLED does not trigger retry (unlike FAILED).
    """
    COMPLETED = "completed"   # Successful completion, no retry
    FAILED = "failed"        # Failed with error, may trigger retry
    CANCELLED = "cancelled"  # Cancelled, no retry


class JobQueueService:
    """Manages job queuing with per-queue locking.
    
    Provides the main interface for submitting, tracking, and managing
    jobs in a queue with per-queue serialization via locks.
    
    Attributes:
        _repository: Database repository for job persistence.
        _lock_manager: Lock manager for per-queue job serialization.
        _queue_repo: Queue repository for queue metadata and concurrency limits.
    """
    
    def __init__(
        self,
        repository: JobRepository,
        lock_manager: JobLockManager,
        queue_repo: JobQueueRepository,
        instance_manager: "InstanceManager" | None = None,
    ):
        """Initialize the JobQueueService.
        
        Args:
            repository: Job repository for database operations.
            lock_manager: Lock manager for per-queue job serialization.
            queue_repo: Queue repository for queue metadata and concurrency limits.
            instance_manager: Optional instance manager for terminating PROCESSING jobs.
        """
        self._repository = repository
        self._lock_manager = lock_manager
        self._queue_repo = queue_repo
        self._instance_manager = instance_manager
        self._retry_engine = None
        # Phase 4 (Job as Queue Proxy): the DeadLetterService is wired
        # at startup via ``set_dlq_service``. ``_finalize_terminal``
        # uses it for the ``Decision.DEAD_LETTER`` path. Optional
        # because tests construct ``JobQueueService`` without one and
        # the helper falls back to a direct ``atomic_transition``
        # write in that case.
        self._dlq_service: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_bus: "DispatchEventBus" | None = None  # Dispatch event bus for job notifications
        self._idempotency_key_ttl_hours: int = 24  # Default TTL for idempotency key deduplication
        self._project_repo: Any | None = None  # Project repository for pause state checks
        self._watcher_repo: Any | None = None  # Repository for job watchers
        self._work_resolver: Any | None = None  # WorkResolverService for work_id → WorkRecord
        # Virtual job resolver flag (Phase 2 Batch 4a, 2026-06-27,
        # feature/virtual-job-management-surface). Tools branch on this to
        # route ``job_get`` / ``job_list`` / ``job_cancel`` / ``watch_job``
        # through the WorkResolverService (``get_work`` / ``list_work`` /
        # kind-aware cancel) or fall back to the legacy JobItem-only
        # primitives (``get_job`` / ``list_jobs`` / ``cancel_job``).
        # Default ``True`` so the new surface is exercised in dev/test from
        # day one; ``set_config`` writes the real value at startup.
        self._use_virtual_job_resolver: bool = True
    
    def set_retry_engine(self, retry_engine) -> None:
        """Set the retry engine used for auto-retries.

        Args:
            retry_engine: The JobRetryEngine instance to use for auto-retries.
        """
        self._retry_engine = retry_engine

    def set_dlq_service(self, dlq_service) -> None:
        """Set the DLQ service used by :meth:`_finalize_terminal`.

        Args:
            dlq_service: The DeadLetterService instance for the
                ``Decision.DEAD_LETTER`` path.
        """
        self._dlq_service = dlq_service
    
    def set_project_repo(self, project_repo: Any) -> None:
        """Set the project repository for pause state checks.
        
        Args:
            project_repo: The SQLModelProjectRepository instance.
        """
        self._project_repo = project_repo
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop for sync→async operations.
        
        Args:
            loop: The running event loop to use for async operations.
        """
        self._loop = loop
    
    def set_instance_manager(self, instance_manager) -> None:
        """Set the InstanceManager reference for cancellation cascade.
        
        Args:
            instance_manager: InstanceManager instance.
        """
        self._instance_manager = instance_manager
    
    def set_dispatch_bus(self, dispatch_bus: "DispatchEventBus") -> None:
        """Set the dispatch event bus for notifying new jobs.
        
        Args:
            dispatch_bus: DispatchEventBus instance.
        """
        self._dispatch_bus = dispatch_bus
    
    def set_config(self, config: Any) -> None:
        """Set job system config for TTL and other settings.

        Args:
            config: JobSystemConfig instance with idempotency_key_ttl_hours and other settings.
        """
        if hasattr(config, 'idempotency_key_ttl_hours'):
            self._idempotency_key_ttl_hours = config.idempotency_key_ttl_hours
        # Phase 2 (Batch 4a) — capture the ``use_virtual_job_resolver`` kill
        # switch so tools can branch cleanly. Default-on (matching the
        # JobSystemConfig default) but defensively checked via ``hasattr``
        # so older test doubles that pass a partial config object don't
        # blow up on the attribute lookup.
        if hasattr(config, 'use_virtual_job_resolver'):
            self._use_virtual_job_resolver = config.use_virtual_job_resolver
    
    def set_watcher_repo(self, watcher_repo: Any) -> None:
        """Set the watcher repository for job event notifications.

        Args:
            watcher_repo: The JobWatcherRepository instance.
        """
        self._watcher_repo = watcher_repo

    def set_work_resolver(self, work_resolver: Any) -> None:
        """Set the WorkResolverService for kind-agnostic notification routing.

        Phase 2 (Batch 2) of feature/virtual-job-management-surface:
        ``notify_watchers`` delegates to ``daemon.services.work_notifier.
        notify_work_watchers`` which needs the resolver to look up
        ``agent_id`` / ``result_summary`` / ``error`` for both Task and
        JobItem work from a single ``work_id``.

        Args:
            work_resolver: The WorkResolverService instance.
        """
        self._work_resolver = work_resolver

    @property
    def use_virtual_job_resolver(self) -> bool:
        """Return the kill-switch for the virtual-job read API.

        Phase 2 (Batch 4a, 2026-06-27) of
        ``feature/virtual-job-management-surface``. Tools in
        ``daemon/tools/job_queue.py`` branch on this to decide whether to
        route through ``self.get_work`` / ``self._work_resolver.list_work``
        (kind-agnostic) or fall back to the legacy JobItem-only
        primitives (``get_job`` / ``list_jobs`` / ``cancel_job``).

        Mirrors the ``JobSystemConfig.use_virtual_job_resolver`` flag
        (env ``ENSEMBLE_JOB_SYSTEM_USE_VIRTUAL_JOB_RESOLVER``), which
        ``daemon/api.py`` wires through :meth:`set_config` at startup.
        Defaults to ``True`` so the new surface is exercised in dev/test
        from day one.

        Returns:
            The flag value as captured by the last :meth:`set_config`
            call. ``True`` if no config has been wired yet (matches the
            ``JobSystemConfig`` default and the constructor default).
        """
        return getattr(self, "_use_virtual_job_resolver", True)

    async def notify_watchers(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        progress: str | None = None,
    ) -> int:
        """Notify ALL watchers for a job. Called from EVERY terminal path.

        Returns number of watchers notified.
        Safe to call even if no watchers exist (returns 0).
        If watching instance is not running, message queues in DB for later delivery.

        Phase 2 (Batch 2) of feature/virtual-job-management-surface:
        delegates to :func:`daemon.services.work_notifier.notify_work_watchers`
        so terminal notifications share one code path with the new
        task-terminal sites (``worker_pool``, ``stale_task_recovery``,
        ``task_processor.on_success``, ``manager._resume_processing_background``
        failure path). The ``job_id`` parameter is the ``work_id`` —
        for job-spawned work (this method's call sites in
        ``job_feedback_observer``) the JobItem PK is itself the work_id,
        so the two are interchangeable here.

        The notification format (the ``[JOB_EVENT]`` block) and the
        ``source`` prefix are byte-for-byte identical to the prior
        implementation — the orchestrator's parser contract depends on
        both.
        """
        if self._watcher_repo is None or self._instance_manager is None:
            return 0
        # ``_work_resolver`` is set via the ``set_work_resolver``
        # setter by ``daemon/api.py`` after construction. Use
        # ``getattr`` with a default so partial-wiring test doubles
        # that build via ``JobQueueService.__new__`` (skipping
        # ``__init__``) do not crash on the attribute lookup.
        work_resolver = getattr(self, "_work_resolver", None)
        if work_resolver is None:
            # Backwards-compat: if no resolver is wired (older test
            # doubles, or a partial init), fall back to the legacy
            # JobItem-only path so the notification still fires with
            # whatever the JobItem carries. Phase 3 will tighten this
            # to a hard error once every wiring site sets the resolver.
            return await self._notify_watchers_legacy(
                job_id, status, error, progress
            )

        # Delegate to the centralized helper. The helper atomically
        # claims watchers via DELETE...RETURNING, resolves the work
        # record through ``self._work_resolver``, builds the
        # ``[JOB_EVENT]`` payload, and enqueues per-watcher. The
        # ``progress=`` kwarg is forwarded so the legacy
        # ``in_progress`` callers (job_feedback_observer) keep working.
        return await notify_work_watchers(
            job_id,
            status,
            error=error,
            instance_manager=self._instance_manager,
            work_resolver=work_resolver,
            watcher_repo=self._watcher_repo,
            progress=progress,
        )

    async def _notify_watchers_legacy(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        progress: str | None = None,
    ) -> int:
        """Legacy JobItem-only notification path used when no resolver is wired.

        Phase 2 (Batch 2): retained for tests / partial-wiring
        scenarios. Production code paths go through
        :func:`notify_work_watchers` via ``self.notify_watchers``.
        Kept as a private helper to make the fallback explicit.
        """
        try:
            watchers = await asyncio.to_thread(
                self._watcher_repo.get_watchers_for_job, job_id
            )
            if not watchers:
                return 0

            job = await asyncio.to_thread(self._repository.get, job_id)
            if job is None:
                return 0

            notified = 0
            for watcher in watchers:
                if status not in watcher.watch_events:
                    continue

                # Status display mapping lives in
                # ``work_notifier._format_status_display`` so the
                # orchestrator's [JOB_EVENT] parser contract is
                # defined exactly once (see ``work_notifier.py``
                # module docstring — DO NOT CHANGE the byte output).
                status_display = _format_status_display(status)

                notification_parts = [f"[JOB_EVENT] Job {job_id[:8]}... {status_display}"]
                notification_parts.append(f"  Agent: {job.agent_id}")

                if status == "in_progress":
                    if progress:
                        notification_parts.append(f"  Progress:\n{progress}")
                else:
                    # Phase 5: ``job.result_summary`` was dropped from
                    # the JobItem model in Phase B. Guard the read
                    # with ``getattr`` so the watcher notification
                    # never crashes on legacy rows that may still
                    # carry the attribute in fixture data; the live
                    # path here uses ``error`` (from the resolver) as
                    # the authoritative failure detail.
                    result_summary = getattr(job, 'result_summary', None)
                    if result_summary:
                        notification_parts.append(f"  Result:\n{result_summary}")
                    if error:
                        notification_parts.append(f"  Error: {error}")

                notification = "\n".join(notification_parts)

                await self._instance_manager.enqueue_message(
                    instance_id=watcher.instance_id,
                    message=notification,
                    source=f"internal_agent:job_event:{job_id}:{status}",
                )
                notified += 1

            if status in ALL_TERMINAL_STATES:
                await asyncio.to_thread(
                    self._watcher_repo.remove_all_watches_for_job, job_id
                )
            return notified

        except Exception as e:
            logger.warning(f"Failed to notify watchers for job {job_id[:8]}...: {e}")
            return 0
    
    async def reconcile_terminal_watches(self) -> int:
        """Scan for watches where work is already terminal. Notify and cleanup.

        Phase 2 (Batch 3) of ``feature/virtual-job-management-surface``:
        routes through ``self._work_resolver`` so the same reconciliation
        sweep catches watched **Task** rows (the worker-pool side) as well
        as watched **JobItem** rows (the dispatch-queue side). When no
        resolver is wired (older test doubles, partial init), falls back
        to the legacy JobItem-only path so the sweep still runs.

        The terminal check uses ``daemon.services.work_status.is_terminal``
        against the **canonical** status produced by ``resolve_work`` —
        Task ``"running"`` is canonicalised to ``"processing"`` so
        ``is_terminal`` returns ``False`` (correctly treating it as
        non-terminal) and JobItem ``"completed"`` / ``"failed"`` /
        ``"cancelled"`` / ``"dead_letter"`` all return ``True``.

        Notification is delegated to :meth:`notify_watchers` (which
        itself delegates to :func:`notify_work_watchers` when the
        resolver is wired) so the ``[JOB_EVENT]`` format and the
        ``source`` prefix stay byte-for-byte identical to the
        orchestrator's parser contract.

        Returns:
            Number of watched work units that were already terminal and
            for which watchers were notified.
        """
        if self._watcher_repo is None:
            return 0

        work_resolver = getattr(self, "_work_resolver", None)
        if work_resolver is None:
            # Backwards-compat: no resolver wired. Use the legacy
            # JobItem-only path so reconcile still runs in partial-wiring
            # scenarios (older tests, ad-hoc service construction).
            return await self._reconcile_terminal_watches_legacy()

        # New resolver-based path: walk every active watch, resolve
        # through the resolver (Task OR JobItem), notify if the resolved
        # record is terminal.
        all_watches = self._watcher_repo.get_all_active_watches()
        reconciled = 0

        for watch in all_watches:
            record = await asyncio.to_thread(
                work_resolver.resolve_work, watch.job_id
            )
            if record is None:
                # Work was deleted between the watcher registration and
                # the reconcile sweep — nothing to notify, but the
                # watcher row will be cleaned up by ``notify_watchers``
                # below when status is terminal (and skipped otherwise).
                # We don't increment ``reconciled`` because no
                # notification fired.
                continue
            if not _work_status_is_terminal(record.status):
                continue
            await self.notify_watchers(
                watch.job_id, record.status, record.error
            )
            reconciled += 1

        return reconciled

    async def _reconcile_terminal_watches_legacy(self) -> int:
        """Legacy JobItem-only reconcile path used when no resolver is wired.

        Mirrors the pre-Phase-2-Batch-3 implementation of
        :meth:`reconcile_terminal_watches` exactly — looks up each
        watched ``job_id`` via ``self._repository.get`` (the
        ``JobItem`` repository, not the resolver) and notifies watchers
        when the row is in a terminal status. Kept so partial-wiring
        scenarios (older tests that don't call ``set_work_resolver``)
        continue to exercise the JobItem-only reconcile semantics.
        """
        terminal_admission = {AdmissionState.DONE.value, AdmissionState.DEAD.value}

        all_watches = self._watcher_repo.get_all_active_watches()
        reconciled = 0

        for watch in all_watches:
            job = await asyncio.to_thread(self._repository.get, watch.job_id)
            if job and job.admission_state in terminal_admission:
                # Phase 5: ``job.error_message`` was dropped from the
                # JobItem model in Phase B. Guard with ``getattr`` so
                # the legacy reconcile path can't crash on jobs that
                # lack the attribute; the canonical
                # ``reconcile_terminal_watches`` path resolves the
                # error via the WorkResolver instead.
                await self.notify_watchers(
                    watch.job_id, job.admission_state, getattr(job, 'error_message', None)
                )
                reconciled += 1

        return reconciled
    
    # ========== Public API ==========
    
    def find_active_jobs_by_instance(self, instance_id: str, job_type: str | None = None) -> list[JobItem]:
        return self._repository.find_jobs_by_instance(instance_id, job_type)
    
    async def enqueue(
        self,
        agent_id: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5,
        metadata: dict[str, Any] | None = None,
        queue_id: str | None = None,
        idempotency_key: str | None = None,
        job_type: str = "task",
        instance_id: str | None = None,
    ) -> JobItem:
        """Submit a job for processing.

        Jobs are always created as PENDING. The JobProcessor picks up pending
        jobs, transitions them to PROCESSING, spawns instances, and enqueues
        messages for processing.

        If project_id is set but queue_id is None, the job is assigned to the
        project's "system_fifo_queue" (for TASK jobs) automatically.

        With idempotency_key: if a job with the same key exists and is non-terminal,
        returns the existing job instead of creating a duplicate.

        D13 (Phase 2): ``job_type="message"`` is REJECTED with ``ValueError``.
        Messages no longer create ``JobItem`` rows — they create ``Task`` rows
        in the WorkerPool path (see :meth:`InstanceMessagingService.enqueue_message`).
        This guard is defense-in-depth against any leftover caller that might
        attempt the legacy API.

        Args:
            agent_id: Agent ID (e.g., 'developer').
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing. If None and project_id
                     is set, defaults to the project's system FIFO queue.
            idempotency_key: Optional idempotency key for deduplication.
                           If a non-terminal job with this key exists, returns it.
            job_type: Job type — must be ``"task"`` (D13 rejects ``"message"``).
            instance_id: Optional pre-set instance ID.

        Returns:
            JobItem with PENDING status (or existing non-terminal job if idempotent).

        Raises:
            ValueError: If ``job_type == "message"`` (D13 — use
                :meth:`InstanceMessagingService.enqueue_message` instead).
        """
        # D13 defense-in-depth: messages must use enqueue_message (WorkerPool
        # Task row), not this JobItem-creating path. Raising here ensures
        # any leftover caller fails loudly rather than silently creating
        # a JobItem that no processor can handle.
        if job_type == "message":
            raise ValueError(
                "enqueue_job no longer accepts job_type='message' — "
                "use enqueue_message instead (D13 architecture migration)"
            )

        # Canonical normalization: ensures ALL callers get system_default_project for None/empty
        project_id = normalize_project_id(project_id)
        if project_id is None:
            raise ValueError("project_id must be normalized before enqueue. This indicates a normalization gap.")

        # M6 fix: use atomic ``INSERT ... ON CONFLICT DO NOTHING`` instead
        # of the previous read-then-insert pattern. Two concurrent
        # ``enqueue`` calls with the same key would BOTH pass the
        # ``find_by_idempotency_key`` check, and the loser's INSERT
        # would raise an unhandled ``IntegrityError`` (surfacing as a
        # 500 to the caller). The atomic insert claims the partial
        # unique index ``idx_job_idempotency`` in one round trip.
        if idempotency_key:
            # Derive agent_dir from agent_id using registry before the
            # atomic insert — we still need it for both the insert path
            # and the registry validation below.
            # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
            # since agent_id may come from a DB row that still has the old value.
            registry = get_registry()
            agent_meta = registry.get_resolved(agent_id)
            resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
            if agent_meta is None:
                raise ValueError(f"Agent not found: {agent_id}")
            agent_dir = str(agent_meta.path)
            agent_id = resolved_agent_id

            # Resolve queue_id for projects (needed for the INSERT row)
            # D13: only TASK jobs reach this point (message jobs are
            # rejected by the guard above). Always route to the FIFO
            # system queue.
            resolved_queue_id = queue_id
            if resolved_queue_id is None:
                queue = await asyncio.to_thread(
                    self._queue_repo.get_by_name, project_id, "system_fifo_queue"
                )
                queue_kind = "fifo"
                if queue is not None:
                    resolved_queue_id = queue.queue_id
                else:
                    raise ValueError(
                        f"No system {queue_kind} queue found for project {project_id}. "
                        f"Ensure system queues are provisioned."
                    )
            elif queue_id and project_id:
                # Validate queue exists and belongs to project
                queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
                if queue is None:
                    logger.warning(
                        f"Queue '{queue_id}' not found, job will be created without queue assignment"
                    )
                    resolved_queue_id = None
                elif queue.project_id != project_id:
                    # C4: Queue belongs to different project - reject the request
                    raise ValueError(
                        f"Queue {queue_id} does not belong to project {project_id}"
                    )

            # Atomically claim the key. ``created`` is True iff we
            # inserted a fresh row.
            job, created = await asyncio.to_thread(
                self._repository.create_or_get_by_idempotency_key,
                agent_id=agent_id,
                agent_dir=agent_dir,
                message=message,
                source=source,
                project_id=project_id,
                priority=priority,
                job_metadata=metadata,
                queue_id=resolved_queue_id,
                idempotency_key=idempotency_key,
                job_type=job_type,
                instance_id=instance_id,
            )

            if not created and job is not None:
                # Another writer beat us. Apply the same terminal-vs-TTL
                # policy as the previous read-then-insert code: if the
                # existing job is non-terminal AND within TTL, return it
                # (idempotent); otherwise fall through to a fresh insert.
                try:
                    created_time = datetime.fromisoformat(job.created_at)
                    ttl_cutoff = datetime.now(timezone.utc) - timedelta(
                        hours=self._idempotency_key_ttl_hours
                    )
                    if created_time < ttl_cutoff:
                        logger.info(
                            f"Idempotency key '{idempotency_key}' matched job {job.job_id} "
                            f"but exceeded TTL ({self._idempotency_key_ttl_hours}h), retrying insert"
                        )
                        job = None  # fall through to a fresh insert below
                except (ValueError, TypeError):
                    # If timestamp parsing fails, keep the existing job.
                    pass

                if job is not None:
                    terminal_admission = {
                        AdmissionState.DONE.value,
                        AdmissionState.DEAD.value,
                    }
                    if job.admission_state not in terminal_admission:
                        logger.debug(
                            f"Idempotency key '{idempotency_key}' matched existing job {job.job_id} "
                            f"(admission_state={job.admission_state})"
                        )
                        return job
                    logger.info(
                        f"Idempotency key '{idempotency_key}' matched terminal job {job.job_id}, "
                        "creating new job"
                    )

            if created and job is not None:
                # Fresh insert succeeded — notify and return.
                if self._dispatch_bus is not None:
                    self._dispatch_bus.notify_new_job(project_id)
                return job

            # Fallthrough: existing job was terminal / TTL-expired and
            # we need a brand-new insert. Drop into the regular create
            # path below by clearing idempotency_key (otherwise the
            # legacy code below would loop on the same key).
            if job is not None:
                # Terminal existing job — bypass the unique index by
                # using a synthetic suffix so the new row has a unique
                # non-null key. This preserves the previous behavior
                # where a terminal job does NOT block a fresh submit.
                idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"
            else:
                # TTL-expired job — same treatment.
                idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"

        # Non-idempotency path (or terminal-fallback path above).
        # Derive agent_dir from agent_id using registry.
        # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
        # since agent_id may come from a DB row that still has the old value.
        registry = get_registry()
        agent_meta = registry.get_resolved(agent_id)
        resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
        agent_id = resolved_agent_id

        # Resolve queue_id for projects
        # D13: only TASK jobs reach this point (message jobs are
        # rejected by the guard above). Always route to the FIFO
        # system queue.
        resolved_queue_id = queue_id
        if queue_id is None:
            # project_id is always valid after normalize_project_id()
            queue = await asyncio.to_thread(
                self._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            queue_kind = "fifo"
            if queue is not None:
                resolved_queue_id = queue.queue_id
            else:
                raise ValueError(
                    f"No system {queue_kind} queue found for project {project_id}. "
                    f"Ensure system queues are provisioned."
                )
        elif queue_id and project_id:
            # Validate queue exists and belongs to project
            queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
            if queue is None:
                logger.warning(
                    f"Queue '{queue_id}' not found, job will be created without queue assignment"
                )
                resolved_queue_id = None
            elif queue.project_id != project_id:
                # C4: Queue belongs to different project - reject the request
                raise ValueError(
                    f"Queue {queue_id} does not belong to project {project_id}"
                )
        
        # Create job with PENDING status - JobProcessor will handle the rest
        job = await asyncio.to_thread(
            self._repository.create,
            agent_id=agent_id,
            agent_dir=agent_dir,
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            job_metadata=metadata,
            queue_id=resolved_queue_id,
            idempotency_key=idempotency_key,
            job_type=job_type,
            instance_id=instance_id,
        )
        
        # Notify dispatch bus of new job (for event-driven processing)
        if self._dispatch_bus is not None:
            self._dispatch_bus.notify_new_job(project_id)
        
        return job
    
    async def get_job(self, job_id: str) -> JobItem | None:
        """Get job by ID.

        Args:
            job_id: Unique job identifier.

        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get, job_id)

    async def get_work(self, work_id: str) -> WorkRecord | None:
        """Resolve a ``work_id`` to its unified :class:`WorkRecord`, or ``None``.

        Phase 2 (Batch 3) of ``feature/virtual-job-management-surface``:
        kind-agnostic read API that looks up the ``work_id`` against BOTH
        the worker-pool ``task`` table AND the dispatch-queue
        ``job_queue_items`` table through the ``WorkResolverService``.

        The HTTP API path (:meth:`get_job`) stays JobItem-only because
        the ``JobResponse`` schema (``daemon/routers/schemas.py``) carries
        JobItem-specific fields (``agent_dir``, ``job_metadata``,
        ``cancelled_at``, ``idempotency_key``, ``started_at``,
        ``completed_at``, ``message``, ``priority``, ``source``,
        ``retry_count``, ``max_retries``, ``failed_at``, ``next_retry_at``)
        that the WorkRecord view-model does not yet expose. Batch 4 will
        migrate the ``job_get`` / ``job_list`` MCP tools onto this
        resolver path once a unified response shape lands.

        Args:
            work_id: The cross-system UUID4 work identifier (Task.work_id
                or JobItem.job_id — both share the same column).

        Returns:
            A populated :class:`WorkRecord` if the work_id resolves to
            either a Task row or a JobItem row, or ``None`` if neither
            table has a match OR if no resolver has been wired yet
            (matches the deferred-wiring pattern used elsewhere — e.g.
            :meth:`notify_watchers` falls back to a legacy path when
            the resolver is missing).
        """
        work_resolver = getattr(self, "_work_resolver", None)
        if work_resolver is None:
            # No resolver wired — partial-wiring / older test doubles.
            # Returning ``None`` lets callers (Batch 4 tool code) branch
            # cleanly on the "resolver not available" case rather than
            # crashing on the attribute lookup.
            return None
        return await asyncio.to_thread(work_resolver.resolve_work, work_id)

    async def list_work(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        kind: str = "job",
        root_only: bool = True,
    ) -> list[WorkRecord]:
        """Batched equivalent of :meth:`get_work` for the ``list_jobs`` path.

        Phase 1 (Job as Queue Proxy): wraps
        :meth:`WorkResolverService.list_work` so the HTTP ``GET /api/jobs``
        endpoint can resolve an entire page of JobItems through the
        resolver in a SINGLE database round-trip rather than issuing one
        resolver call per job (the previous implementation was a classic
        N+1 — 50 jobs → 50 ``get_work`` calls → 50 separate
        ``SELECT … FROM instances`` queries).

        Defaults are tuned for the ``list_jobs`` router call:

        * ``kind="job"`` — restrict to the JobItem side of the union
          (the HTTP API lists jobs, not worker-pool tasks).
        * ``root_only=True`` — match the pre-Phase-1 ``list_jobs``
          behaviour, which did not exclude child-instance jobs.

        The resolver does not currently expose ``queue_id`` or
        pagination (``limit``/``offset``); those filters remain on the
        repository side (the JobItem list is the page-defining source of
        truth, and the WorkRecord list is a per-row enrichment). The
        caller matches the two lists by ``job_id == work_id``.

        Args:
            project_id: Optional project filter. Matches the
                ``JobRepository.list`` filter so the batched result
                covers the same rows.
            status: Optional canonical-status filter, comma-separated
                (e.g. ``"completed,failed"``). Passed straight through
                to :meth:`WorkResolverService.list_work` which already
                understands the canonical vocabulary.
            kind: ``"job"`` by default — narrows to JobItem-backed
                rows. Callers that need the union can pass ``None``.
            root_only: When ``True`` (default), drop JobItems bound to
                child instances. Matches the legacy ``list_jobs``
                semantics which did not surface child-instance jobs.

        Returns:
            A list of :class:`WorkRecord` ordered newest-first. Empty
            list when the resolver is not wired OR no rows match.
        """
        work_resolver = getattr(self, "_work_resolver", None)
        if work_resolver is None:
            # No resolver wired — partial-wiring / older test doubles.
            # Returning ``[]`` keeps the call site simple: an empty
            # WorkRecord map degrades to the legacy JobItem-mirror path
            # in ``_job_to_response``, which is the documented
            # behaviour for "resolver not available".
            return []
        return await asyncio.to_thread(
            work_resolver.list_work,
            project_id=project_id,
            status=status,
            kind=kind,
            root_only=root_only,
        )

    async def get_job_by_instance(self, instance_id: str) -> JobItem | None:
        """Get job by instance ID.
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get_by_instance, instance_id)
    
    def get_job_by_instance_sync(self, instance_id: str) -> JobItem | None:
        """Get job by instance ID (synchronous version).
        
        For use from synchronous callers like terminate_instance().
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return self._repository.get_by_instance(instance_id)
    
    async def update_job(self, job_id: str, **updates) -> JobItem | None:
        """Update job fields.
        
        Args:
            job_id: Unique job identifier.
            **updates: Fields to update (e.g., status, result_summary).
            
        Returns:
            Updated JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.update, job_id, **updates)
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Works for PENDING, PROCESSING, and FAILED states.

        For PROCESSING jobs with an alive instance, this cascades termination
        to the instance (cancelling active requests, terminating children,
        releasing locks) before marking the job as CANCELLED.

        For PROCESSING jobs with a dead/terminal instance and for PENDING /
        FAILED jobs, this delegates to the atomic repository ``cancel_job``,
        which handles all cancellable states in a single UPDATE-WHERE-IN.
        The atomic repository method closes the TOCTOU window where a
        concurrent ``start_job`` would transition PENDING -> PROCESSING
        between this method's read and its dispatch, causing the cancel
        to be silently lost.

        For FAILED jobs, this stops any pending retries.

        Phase 4 (Job as Queue Proxy): the PENDING / PROCESSING-dead /
        FAILED branch routes through the single terminal-write boundary
        ``_finalize_terminal(Decision.NO_RETRY)`` — the same path
        ``complete_job(CANCELLED)`` uses. The PROCESSING-with-alive-
        instance branch keeps its cascade to ``terminate_instance``,
        which is an Instance concern (not a queue concern) and is
        outside the boundary's scope.

        Args:
            job_id: Job identifier.

        Returns:
            True if cancelled successfully, False if job not found or
            not in a cancellable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return False

        # Pre-validate with state machine for better error messages. This
        # is a best-effort check; the atomic repo.cancel_job is the source
        # of truth and will raise ValueError for non-cancellable states.
        # Phase 5: validate directly on admission_state vocabulary —
        # no JobStatus translation. A non-cancellable admission state
        # is ``done`` (terminal) or ``dead`` (terminal); ``queued`` and
        # ``active`` both have explicit transitions to ``done``.
        if not job_state_machine.can_transition(
            job.admission_state, AdmissionState.DONE.value
        ):
            return False

        # Special case: ACTIVE with an alive instance requires a
        # cascade — ``terminate_instance`` will mark the job CANCELLED
        # itself. Lock release happens first regardless of instance
        # liveness (matches pre-fix semantics).
        if job.admission_state == AdmissionState.ACTIVE.value:
            instance_id = job.instance_id

            # Release any locks held by this job first
            if job.queue_id and job.project_id:
                await self._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job_id
                )
            elif job.project_id:
                await self._lock_manager.release(job.project_id, job_id)

            # Check if instance is still alive
            instance_alive = (
                instance_id is not None
                and self._instance_manager is not None
                and self._is_instance_alive(instance_id)
            )

            if instance_alive:
                # Terminate the instance (cascades to children, cancels
                # requests, releases locks, marks job as CANCELLED via
                # DemandState.CANCELLED).
                await self._instance_manager.terminate_instance(instance_id)
                # Job is now CANCELLED by instance_lifecycle, notify watchers
                await self.notify_watchers(job.job_id, "cancelled")
                return True
            # else: instance already dead/terminal — fall through to atomic
            # repo.cancel_job which will handle PROCESSING -> CANCELLED.

        # Phase 4: route through the single terminal-write boundary.
        # The cancel decision is always NO_RETRY (cancel never retries).
        # ``_finalize_terminal`` does the atomic active → done write
        # (admission_state='done', status='cancelled') and releases
        # the lock in its finally block.
        canonical_job_id, final_status = await self._finalize_terminal(
            instance_id=job.instance_id or "",
            decision=Decision.NO_RETRY,
            job_id=job_id,
            error_message="Cancelled",
            # Phase 4: cancel always writes ``status='cancelled'`` —
            # don't let the instance-derived status (which falls back
            # to 'failed' for missing/unexpected instance states)
            # mask the cancel intent.
            target_status=JobStatus.CANCELLED.value,
        )

        # The terminal-write boundary only handles ``admission_state=
        # 'active'`` rows. A queued (pending) job's status flip
        # ``pending → cancelled`` lives outside the boundary — the
        # atomic repo ``cancel_job`` closes the TOCTOU window where a
        # concurrent ``start_job`` would transition PENDING → PROCESSING
        # between our read and the dispatch, causing the cancel to be
        # silently lost. We detect that case via an empty
        # ``final_status`` (the boundary returned the job_id but no
        # status, meaning no UPDATE actually ran).
        if not final_status:
            try:
                await asyncio.to_thread(self._repository.cancel_job, job.job_id)
            except ValueError:
                return False
        try:
            await self.notify_watchers(job.job_id, "cancelled")
        except Exception as e:
            logger.warning(
                "cancel_job: notify_watchers failed for %s: %s",
                job.job_id[:8], e,
            )
        return True
    
    def _is_instance_alive(self, instance_id: str) -> bool:
        """Check if an instance exists and is not in a terminal state.
        
        Args:
            instance_id: The instance ID to check.
            
        Returns:
            True if instance is alive (exists with non-terminal status).
        """
        if not self._instance_manager or not hasattr(self._instance_manager, '_instance_repository'):
            return False
        
        meta = self._instance_manager._instance_repository.get(instance_id)
        if meta is None:
            return False
        
        return meta.status not in TERMINAL_STATUSES
    
    async def retry_job(self, job_id: str) -> JobItem | None:
        """Retry a failed job by creating a new job with the same parameters.
        
        Creates a new job with the same parameters and starts it immediately
        if possible (no lock contention), otherwise queues it.
        
        Args:
            job_id: Job identifier of the failed job to retry.
            
        Returns:
            New JobItem if retry successful, None if job not found or
            not in a retryable state (not FAILED).
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None
        
        # Can only retry FAILED jobs (admission_state='done' with
        # failed_at set — only failed jobs carry the timestamp).
        if job.admission_state != AdmissionState.DONE.value or job.failed_at is None:
            return None
        
        # Create a new job and use enqueue logic to determine if it should
        # start immediately or be queued
        # W13: Carry over the original queue_id to preserve queue routing
        new_job = await self.enqueue(
            agent_id=job.agent_id,
            message=job.message,
            source=job.source,
            project_id=job.project_id,
            queue_id=job.queue_id,  # Carry over the original queue
            priority=job.priority,
            metadata=job.job_metadata,
        )
        
        return new_job
    
    async def soft_delete_job(self, job_id: str) -> JobItem | None:
        """Soft-delete a job by setting deleted_at timestamp.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Updated JobItem if successful, None if job not found.
        """
        return await asyncio.to_thread(self._repository.soft_delete, job_id)
    
    async def restore_job(self, job_id: str) -> JobItem | None:
        """Restore a soft-deleted job.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Updated JobItem if successful, None if job not found.
        """
        return await asyncio.to_thread(self._repository.restore, job_id)
    
    async def list_jobs(
        self,
        statuses: list[str] | None = None,
        project_id: str | None = None,
        queue_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[JobItem]:
        """List jobs with optional filters.
        
        Args:
            statuses: Optional list of status filters.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            offset: Number of jobs to skip.
            limit: Maximum number of jobs to return.
            include_deleted: Whether to include soft-deleted jobs.
            
        Returns:
            List of JobItem objects.
        """
        statuses = normalize_statuses(statuses)
        jobs, _ = await asyncio.to_thread(
            self._repository.list,
            statuses=statuses,
            project_id=project_id,
            queue_id=queue_id,
            offset=offset,
            limit=limit,
            include_deleted=include_deleted,
        )
        return jobs
    
    # ========== Helper Methods ==========
    
    async def _release_job_lock(
        self,
        *,
        project_id: str | None,
        queue_id: str | None,
        job_id: str,
        instance_id: str | None = None,
        release_by_instance: bool = True,
    ) -> None:
        """Safely release a job's queue lock with backward-compatible fallback.
        
        Args:
            project_id: The project owning the lock.
            queue_id: The queue ID (if any).
            job_id: The job ID to release.
            instance_id: The instance ID (for release_by_instance mode).
            release_by_instance: If True, uses release_by_instance (from _complete_job);
                                 if False, uses release (from complete_job).
        """
        if queue_id and project_id:
            await self._lock_manager.release_queue_lock(
                project_id, queue_id, job_id
            )
        elif project_id:
            if release_by_instance:
                if instance_id:
                    await self._lock_manager.release_by_instance(instance_id)
                # else: do nothing (matches original Pattern A)
            else:
                await self._lock_manager.release(project_id, job_id)

    async def _finalize_terminal(
        self,
        instance_id: str,
        decision: Decision,
        *,
        job_id: str | None = None,
        result_summary: str | None = None,
        error_message: str | None = None,
        target_status: str | None = None,
    ) -> tuple[str | None, str]:
        """Single terminal-write boundary for job admission transitions (Phase 4).

        Required :class:`Decision` enum so a new finalize path that forgets
        retry/DLQ handling fails at instantiation, not in production. The
        admission transition is computed internally:

          - ``NO_RETRY``     → ``admission_state='done'`` (direct write;
            ``status`` mirror derived from Instance terminal status, or
            from ``target_status`` override when supplied).
          - ``RETRY``        → ``admission_state='queued'`` via the
            retry engine (``active → queued`` direct, no intermediate
            FAILED — Plan §3.2 retry-without-instance guarantee).
          - ``DEAD_LETTER``  → ``admission_state='dead'`` via the DLQ
            service (``active → dead`` direct).

        Lock release happens in a ``finally`` block so any path
        (success, validation error, transition no-op) returns the lock
        to the queue — matching the prior ``complete_job`` / ``cancel_job``
        contract.

        Args:
            instance_id: The instance whose job is being finalized.
                Used to (a) derive the backward-compat ``status``
                mirror from the Instance terminal status for NO_RETRY,
                and (b) release the per-queue lock.
            decision: Required terminal decision — see :class:`Decision`.
                The enum is closed and non-defaulted; a missing value
                is a type error.
            job_id: Optional explicit job_id. When ``None``, the
                method looks up the active job via
                ``JobRepository.find_jobs_by_instance`` (single-active
                invariant is the common case).
            result_summary: COMPLETED-path result text (optional).
            error_message: ERROR/TERMINATED-path error text (optional).
            target_status: Override the instance-derived status mirror.
                When ``None`` (default), the status is derived from
                the Instance terminal status. When supplied, it
                overrides that derivation — used by ``cancel_job``
                to write ``status='cancelled'`` regardless of the
                Instance's reported terminal state (a manual
                ``start_job`` followed by ``cancel_job`` should land
                in ``cancelled``, not ``failed``).

        Returns:
            ``(job_id, final_status_for_backward_compat)`` tuple.
            ``job_id`` is ``None`` if no active job was found.
            ``final_status_for_backward_compat`` is the legacy
            ``JobStatus`` enum value written as the ``status`` column
            mirror (``completed`` / ``failed`` / ``cancelled`` /
            ``pending`` for queued retry / ``dead_letter`` for DLQ).
            An empty string indicates no UPDATE was applied (caller
            should fall back to legacy atomic cancel).
        """
        # ── Step 1: locate the job (by job_id or by instance) ──────
        canonical_job_id: str | None = None
        canonical_project_id: str | None = None
        canonical_queue_id: str | None = None
        canonical_instance_id: str | None = instance_id

        if job_id is not None:
            job = await asyncio.to_thread(self._repository.get, job_id)
            if job is not None:
                canonical_job_id = job.job_id
                canonical_project_id = job.project_id
                canonical_queue_id = job.queue_id
                if job.instance_id:
                    canonical_instance_id = job.instance_id
        else:
            # Look up the active job for this instance. The
            # single-active-per-instance invariant holds for all
            # post-D13 callers (D13 collapsed MESSAGE-type jobs).
            jobs = await asyncio.to_thread(
                self._repository.find_jobs_by_instance, instance_id
            )
            # Find the active one (not queued without a lock, not done).
            for candidate in jobs:
                if candidate.admission_state == AdmissionState.ACTIVE.value:
                    job = candidate
                    canonical_job_id = job.job_id
                    canonical_project_id = job.project_id
                    canonical_queue_id = job.queue_id
                    break
            else:
                job = None

        if canonical_job_id is None:
            logger.warning(
                f"_finalize_terminal: no active job for instance "
                f"{instance_id[:8]}..., decision={decision.value}"
            )
            return None, ""

        # Phase 4: explicit admission_state pre-check. The terminal
        # boundary only handles ``admission_state='active'`` rows —
        # a queued (pending) job's status flip ``pending →
        # cancelled`` lives outside the boundary (the atomic repo
        # ``cancel_job`` closes the TOCTOU window where a concurrent
        # ``start_job`` would transition PENDING → PROCESSING
        # between the caller's read and this UPDATE). Returning
        # ``(canonical_job_id, "")`` here signals the no-op to the
        # caller via the empty final_status string — callers fall
        # back to their legacy atomic cancel path. This explicit
        # check also makes the boundary robust to mocked
        # repositories (where ``finalize_active_to_done`` returns
        # a non-None ``MagicMock`` even when no UPDATE actually
        # ran).
        #
        # ``getattr`` with the QUEUED default keeps the boundary
        # backward-compatible with tests/mocks that don't model
        # ``admission_state`` — those callers fall through to the
        # legacy atomic transition path (the pre-Phase 4 default
        # for pending jobs).
        job_admission_state = getattr(
            job, "admission_state", AdmissionState.QUEUED.value
        )
        if job_admission_state != AdmissionState.ACTIVE.value:
            logger.debug(
                f"_finalize_terminal: job {canonical_job_id[:8]}... is "
                f"in admission_state={job_admission_state} (not "
                f"'active'); no-op — caller falls back to legacy "
                f"atomic transition"
            )
            # Release the lock in finally, then return the no-op
            # signal. We cannot `return` directly because the
            # finally block (lock release) must run — restructure
            # as a flag that skips the dispatch.
            _dispatch_skipped = True
        else:
            _dispatch_skipped = False

        # Snapshot the legacy status mirror returned to callers.
        final_status = ""

        # ── Step 2: dispatch on decision ────────────────────────────
        try:
            if _dispatch_skipped:
                # Already returned (canonical_job_id, "") after
                # finally. The flag is just a marker so we don't
                # double-run the dispatch below.
                pass
            elif decision == Decision.NO_RETRY:
                # Look up the instance to derive the backward-compat
                # status. COMPLETED → 'completed', ERROR/FAILED →
                # 'failed', TERMINATED → 'cancelled'. Falls back to
                # 'failed' if the instance is missing or in an
                # unexpected state (the caller should have supplied
                # a final instance status already).
                #
                # Phase 4: ``target_status`` overrides the instance
                # derivation when supplied — used by ``cancel_job``
                # which always wants ``status='cancelled'`` regardless
                # of the instance's reported terminal state.
                if target_status is not None:
                    derived_status = target_status
                else:
                    derived_status = self._derive_terminal_status_from_instance(
                        canonical_instance_id
                    )

                update_kwargs: dict[str, Any] = {}
                if derived_status == JobStatus.COMPLETED.value:
                    update_kwargs["result_summary"] = (
                        result_summary or "Job completed successfully"
                    )
                else:
                    update_kwargs["error_message"] = (
                        error_message or "Unknown error"
                    )

                finalized = await asyncio.to_thread(
                    self._repository.finalize_active_to_done,
                    canonical_job_id,
                    derived_status,
                    **update_kwargs,
                )
                if finalized is None:
                    # Race: another writer transitioned the job out of
                    # ACTIVE between our lookup and the UPDATE. Surface
                    # the no-op to the caller via empty status; lock
                    # release in finally still runs.
                    logger.debug(
                        f"_finalize_terminal NO_RETRY no-op for job "
                        f"{canonical_job_id[:8]}... (concurrent "
                        f"transition)"
                    )
                    return canonical_job_id, ""
                final_status = derived_status

            elif decision == Decision.RETRY:
                if self._retry_engine is None:
                    logger.error(
                        f"_finalize_terminal RETRY: no retry_engine "
                        f"configured, falling back to DEAD_LETTER for "
                        f"job {canonical_job_id[:8]}..."
                    )
                    decision = Decision.DEAD_LETTER
                else:
                    # maybe_retry does the active → queued or
                    # active → dead transition internally based on
                    # ``should_retry()``. Updated in Phase 4 to use
                    # admission_state guards.
                    retried = await asyncio.to_thread(
                        self._retry_engine.maybe_retry, canonical_job_id
                    )
                    if retried is not None:
                        final_status = JobStatus.PENDING.value
                    else:
                        # maybe_retry moved it to DLQ (retries
                        # exhausted); surface that to the caller.
                        final_status = JobStatus.DEAD_LETTER.value

            if decision == Decision.DEAD_LETTER:
                # Direct active → dead transition via the DLQ service.
                # The standalone variant opens its own session so we
                # don't need a parent transaction here.
                if self._dlq_service is None:
                    # No DLQ service wired — fall back to writing
                    # admission_state='dead' directly via the
                    # repository. This branch is hit only in tests
                    # that construct JobQueueService without a DLQ
                    # service; production always has one.
                    logger.warning(
                        f"_finalize_terminal DEAD_LETTER: no "
                        f"dlq_service wired, using repository direct "
                        f"write for job {canonical_job_id[:8]}..."
                    )
                    await asyncio.to_thread(
                        self._repository.atomic_transition,
                        canonical_job_id,
                        from_status=JobStatus.PROCESSING.value,
                        to_status=JobStatus.DEAD_LETTER.value,
                    )
                    final_status = JobStatus.DEAD_LETTER.value
                else:
                    await asyncio.to_thread(
                        self._dlq_service.move_to_dlq_standalone,
                        canonical_job_id,
                        reason="MANUAL",
                        from_admission_state=AdmissionState.ACTIVE.value,
                    )
                    final_status = JobStatus.DEAD_LETTER.value
        finally:
            # ── Step 3: release lock (always, even on error) ────────
            # Use release_by_instance to clean up regardless of whether
            # the lock was acquired via the queue_id path or the
            # synthesized project:{project_id} path — both write
            # ``job_locks`` rows keyed by instance_id.
            if canonical_instance_id:
                try:
                    await self._lock_manager.release_by_instance(
                        canonical_instance_id
                    )
                except Exception as e:
                    logger.warning(
                        f"_finalize_terminal: failed to release lock "
                        f"for instance {canonical_instance_id[:8]}...: {e}"
                    )

        if _dispatch_skipped:
            # Return the no-op signal so the caller falls back to
            # its legacy atomic transition (cancel_job for queued
            # jobs, etc.). The lock has already been released above.
            return canonical_job_id, ""

        return canonical_job_id, final_status

    def _derive_terminal_status_from_instance(
        self, instance_id: str | None
    ) -> str:
        """Map an Instance terminal status to the legacy ``status`` mirror.

        Phase 4 backward-compat helper: the ``status`` column is still
        written (Phase 5 drops it) but is no longer the authority.
        Callers that need the legacy value (e.g. SSE status_change
        events) can use this to derive it from the Instance. Falls
        back to ``'failed'`` when the Instance is missing or in an
        unexpected state.
        """
        if (
            instance_id is None
            or self._instance_manager is None
            or not hasattr(self._instance_manager, "_instance_repository")
            or self._instance_manager._instance_repository is None
        ):
            return JobStatus.FAILED.value
        try:
            instance = self._instance_manager._instance_repository.get(
                instance_id
            )
        except Exception:
            instance = None
        if instance is None:
            return JobStatus.FAILED.value
        if instance.status == InstanceStatus.COMPLETED.value:
            return JobStatus.COMPLETED.value
        if instance.status in (
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ):
            return JobStatus.FAILED.value
        if instance.status == InstanceStatus.TERMINATED.value:
            return JobStatus.CANCELLED.value
        return JobStatus.FAILED.value

    async def _get_concurrency_limit(self, queue_id: str) -> int:
        """Get the concurrency limit for a queue.
        
        Args:
            queue_id: The queue ID to get concurrency limit for.
            
        Returns:
            The concurrency limit, defaulting to 1 if queue not found.
        """
        queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
        if queue is None:
            logger.warning(f"Queue '{queue_id}' not found, using default concurrency_limit=1")
            return 1
        return queue.concurrency_limit
    
    async def _try_start_job(self, job: JobItem) -> bool:
        """Try to start a pending job.

        Attempts to acquire the lock for the job's queue and start
        processing the job atomically.

        B1 Fix (Phase 2 of "Job as Queue Proxy"): the lock INSERT and
        the status UPDATE happen in a SINGLE transaction via
        ``JobRepository.start_job_atomic_with_lock``. This keeps the
        PostgreSQL constraint trigger ``trg_job_locks_active_guard``
        (installed in ``daemon/manager.py::_ensure_postgres_columns``)
        happy at COMMIT — the trigger requires both the ``job_locks``
        row AND the matching ``admission_state='active'`` row to be
        visible together. The pre-fix two-transaction flow (lock acquire
        → status UPDATE in separate commits) caused the trigger to
        false-fire on every job start.

        Because the two writes now share a transaction, a status
        mismatch rolls back BOTH the lock INSERT and the failed UPDATE
        — the caller no longer needs a try/finally to release the lock
        on failure.

        Args:
            job: The pending job to start.

        Returns:
            True if job was started, False otherwise.
        """
        instance_id = str(uuid.uuid4())

        # If job has queue_id, use per-queue locking with concurrency limit
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)

            # B1 Fix: single-transaction lock + status UPDATE. The trigger
            # fires at COMMIT; both writes are staged together so the
            # trigger sees matching state. On status mismatch the
            # transaction rolls back atomically — no manual lock release
            # required.
            try:
                job_obj, lock_acquired = await asyncio.to_thread(
                    self._repository.start_job_atomic_with_lock,
                    job.job_id,
                    instance_id,
                    job.project_id,
                    job.queue_id,
                    concurrency_limit,
                )
            except ValueError:
                # Job state changed (already started/cancelled) — the
                # transaction (including the lock INSERT) rolled back
                # automatically. Preserve the original "return False"
                # behaviour for callers.
                return False

            if not lock_acquired:
                return False
            return job_obj is not None

        # If job has project_id but no queue_id, use backward-compatible
        # project-based locking. Same B1 fix applies: the synthesized
        # ``"project:{project_id}"`` queue_id also writes to ``job_locks``
        # via the same INSERT OR IGNORE pattern, so the trigger fires at
        # commit and the lock+status writes must be in one transaction.
        if job.project_id:
            try:
                job_obj, lock_acquired = await asyncio.to_thread(
                    self._repository.start_job_atomic_with_lock,
                    job.job_id,
                    instance_id,
                    job.project_id,
                    f"project:{job.project_id}",
                    1,  # default concurrency for project-based locks
                )
            except ValueError:
                return False

            if not lock_acquired:
                return False
            return job_obj is not None

        # No project_id - start immediately without locking.
        # No lock INSERT means no trigger to worry about, so the legacy
        # two-transaction flow is fine. Keep the try/except ValueError
        # semantics so we still return False (not None) on a state
        # mismatch.
        try:
            await asyncio.to_thread(
                self._repository.start_job_atomic, job.job_id, instance_id
            )
            return True
        except ValueError:
            return False
    
    async def _complete_job(self, job: JobItem, result_summary: str | None) -> None:
        """Mark a job as completed and release its lock.

        H9 Fix: Status transition FIRST, lock release in finally.
        The previous order (release → transition) created a race window where
        a failed transition would leave the job in PROCESSING with no lock,
        allowing a second worker to double-claim. By holding the lock until
        the status is committed, the recovery sweep (``recover_stale_locks``)
        remains the only path that can re-claim a stuck PROCESSING job.

        Args:
            job: The processing job to complete.
            result_summary: Optional summary of the job result.
        """
        try:
            # 1. Transition status FIRST (commit before releasing the lock)
            await asyncio.to_thread(
                self._repository.complete_job, job.job_id, result_summary
            )
        finally:
            # 2. Release lock AFTER transition attempt (success OR failure).
            # On failure, the job stays PROCESSING and the recovery sweep will
            # pick it up — which is the correct, race-free path.
            try:
                await self._release_job_lock(
                    project_id=job.project_id,
                    queue_id=job.queue_id,
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    release_by_instance=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to release lock for job {job.job_id[:8]}...: {e}"
                )

    async def _fail_job(self, job: JobItem, error_message: str) -> None:
        """Mark a job as failed and release its lock.

        H9 Fix: Status transition FIRST, lock release in finally.
        Mirrors the ordering in ``_complete_job`` and the public
        ``complete_job`` (status-first, lock-second in finally). Holding the
        lock until the transition is committed prevents a failed transition
        from leaving a job in PROCESSING with no lock — which would let a
        second worker double-claim during the recovery window.

        Args:
            job: The processing job that failed.
            error_message: Error message describing the failure.
        """
        try:
            # 1. Transition status FIRST (commit before releasing the lock)
            await asyncio.to_thread(
                self._repository.fail_job, job.job_id, error_message
            )
        finally:
            # 2. Release lock AFTER transition attempt (success OR failure).
            try:
                await self._release_job_lock(
                    project_id=job.project_id,
                    queue_id=job.queue_id,
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    release_by_instance=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to release lock for job {job.job_id[:8]}...: {e}"
                )
    
    async def _get_next_job(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
    ) -> JobItem | None:
        """Get the next pending job for a queue or project.
        
        Args:
            project_id: Optional project ID to get next job for.
                       If None, gets next pending job regardless of project.
            queue_id: Optional queue ID to get next job for.
                      Takes precedence over project_id if specified.
            
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        if queue_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_queue, queue_id
            )
            return pending[0] if pending else None
        elif project_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_project, project_id
            )
            return await self._select_next_eligible_job(pending, project_id)
        else:
            pending = await asyncio.to_thread(self._repository.list_all_pending)
            return pending[0] if pending else None

    async def _select_next_eligible_job(
        self,
        pending: list[JobItem],
        project_id: str,
    ) -> JobItem | None:
        """Select the next eligible job from pending list, respecting defer semantics.
        
        Defer jobs are only returned when no non-defer work (active or pending) exists.
        This ensures defer queues don't start processing while non-defer work is pending.
        
        Args:
            pending: List of pending jobs (ordered by priority desc, created_at asc).
            project_id: Project ID for idle check.
            
        Returns:
            Next eligible JobItem, or None if no eligible jobs.
        """
        if not pending:
            return None
        
        # Batch-fetch queue types to avoid N+1 queries
        unique_queue_ids = {job.queue_id for job in pending if job.queue_id}
        queue_type_map: dict[str, bool] = {}  # queue_id -> is_defer
        for qid in unique_queue_ids:
            queue = await asyncio.to_thread(self._queue_repo.get, qid)
            queue_type_map[qid] = queue.queue_type == "defer" if queue else False
        
        # Check once if non-defer work is active (used for defer jobs only)
        non_defer_active = 0
        for job in pending:
            is_defer = queue_type_map.get(job.queue_id, False)
            if is_defer:
                # Defer job found - check if non-defer work is active
                non_defer_active = await asyncio.to_thread(
                    self._repository.count_active_jobs_in_non_defer_queues, project_id
                )
                break
        
        # Iterate through pending jobs and select first eligible
        for job in pending:
            is_defer = queue_type_map.get(job.queue_id, False)
            if not is_defer:
                # Non-defer job - always safe to return
                return job
            else:
                # Defer job - only return if non-defer work is idle
                if non_defer_active == 0:
                    return job
                # Otherwise skip this defer job and continue checking
        return None
    
    async def _get_queue_position(
        self,
        job_id: str | None,
        project_id: str,
        queue_id: str | None = None,
    ) -> int:
        """Get the queue position for a job in its queue.
        
        Returns the 1-based position of the job in the pending queue,
        ordered by priority (desc) then created_at (asc).
        
        Args:
            job_id: Optional job ID to find position for.
                    If None, returns count of pending jobs + 1.
            project_id: The project to get queue position for.
            queue_id: Optional specific queue to check position in.
            
        Returns:
            1-based queue position, or position after all pending jobs if job not found.
        """
        if queue_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_queue, queue_id
            )
        else:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_project, project_id
            )
        
        if job_id is None:
            # Return position as if this job was added to end
            return len(pending) + 1
        
        for i, job in enumerate(pending, start=1):
            if job.job_id == job_id:
                return i
        
        return len(pending) + 1

    # ========== JobProcessor Helper Methods ==========
    
    async def get_next_pending_job(self) -> JobItem | None:
        """Get the next pending job (highest priority, oldest first).
        
        Returns the first pending job from all projects, ordered by
        priority (descending) then created_at (ascending).
        
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        pending = await asyncio.to_thread(self._repository.list_all_pending)
        return pending[0] if pending else None
    
    async def start_job(self, job_id: str) -> JobItem | None:
        """Mark job as processing and acquire lock.
        
        C1 Fix: Acquires the lock FIRST, then transitions the job atomically.
        This prevents the race condition where multiple workers transition the
        same job to PROCESSING but only one can acquire the lock, causing
        repeated PENDING→PROCESSING→rollback cycles.
        
        The flow is:
        1. Get job → check PENDING
        2. Acquire queue/project lock FIRST (if at capacity, don't transition)
        3. If lock acquired → THEN call start_job_atomic()
        4. If start fails → release the lock we acquired
        
        Args:
            job_id: The job ID to start.
            
        Returns:
            Updated JobItem if started successfully, None if
            job not found, cancelled, or start/lock acquisition failed.
        """
        # [TRACE] Log entry
        logger.debug(f"[TRACE] start_job: called for job_id={job_id[:8]}...")
        
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            logger.debug(f"[TRACE] start_job: job {job_id[:8]}... not found")
            return None
        
        # [TRACE] Log job details
        logger.debug(
            f"[TRACE] start_job: job={job_id[:8]}... admission_state={job.admission_state} "
            f"instance={job.instance_id[:8] if job.instance_id else 'N/A'}... job_type={getattr(job, 'job_type', 'task')}"
        )
        
        # Check if job is still queued (could have been cancelled)
        if job.admission_state != AdmissionState.QUEUED.value:
            logger.debug(f"[TRACE] start_job: job {job_id[:8]}... SKIP — not QUEUED (admission_state={job.admission_state})")
            return None
        
        # CENTRALIZED PAUSE CHECK - protects ALL callers
        if self._project_repo is not None and job.project_id:
            project = await asyncio.to_thread(self._project_repo.get, job.project_id)
            if project and project.job_queue_paused:
                logger.info(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — project {job.project_id[:8]}... PAUSED"
                )
                return None
        
        # Warn if project_repo is not set (can't check pause state)
        if self._project_repo is None:
            logger.warning("start_job: project_repo not set, cannot check pause state")

        # INSTANCE STATUS CHECK - prevent starting jobs for terminated instances or paused instances
        # Check for all job types that have a target instance_id (TASK jobs may not have instance_id while PENDING)
        if job.instance_id:
            if (
                self._instance_manager is not None
                and hasattr(self._instance_manager, '_instance_repository')
                and self._instance_manager._instance_repository is not None
            ):
                try:
                    instance = await asyncio.to_thread(
                        self._instance_manager._instance_repository.get, job.instance_id
                    )
                except Exception as e:
                    logger.warning(
                        f"start_job: failed to fetch instance {job.instance_id[:8]}...: {e}"
                    )
                    instance = None

                if instance is None:
                    logger.info(
                        f"[TRACE] start_job: job {job_id[:8]}... SKIP — instance {job.instance_id[:8]}... NOT FOUND"
                    )
                    return None

                if instance.status in TERMINAL_STATUSES:
                    # D13: all jobs are TASK-type now (message-type jobs
                    # are rejected at enqueue). TASK jobs get fresh
                    # instances — clear stale ref and allow normal start.
                    logger.info(
                        f"[TRACE] start_job: clearing stale instance_id for TASK job {job_id[:8]}... "
                        f"(instance {job.instance_id[:8]}... is {instance.status})"
                    )
                    await asyncio.to_thread(self._repository.update, job.job_id, instance_id=None)
                    # Fall through to normal start logic below (don't return None)

                if instance.status == InstanceStatus.PAUSED.value:
                    logger.info(
                        f"[TRACE] start_job: job {job_id[:8]}... SKIP — instance {job.instance_id[:8]}... PAUSED"
                    )
                    return None

        # Generate instance_id: TASK jobs always get a new UUID.
        # D13: removed MESSAGE-specific ``if job.job_type == "message"
        # and job.instance_id`` branch — no MESSAGE jobs exist anymore,
        # so all jobs uniformly get a fresh UUID.
        instance_id = str(uuid.uuid4())
        
        # [TRACE] Log instance_id being used
        logger.info(
            f"[TRACE] start_job: using instance_id={instance_id[:8]}... "
            f"for job={job_id[:8]}... (job_type={job.job_type})"
        )

        # B1 Fix (Phase 2 of "Job as Queue Proxy"): the lock INSERT and
        # the status UPDATE happen in a SINGLE transaction via
        # ``JobRepository.start_job_atomic_with_lock``. The PostgreSQL
        # constraint trigger ``trg_job_locks_active_guard`` (installed in
        # ``daemon/manager.py::_ensure_postgres_columns``) fires at COMMIT
        # and requires the matching ``job_queue_items.admission_state =
        # 'active'`` row to be visible together with the ``job_locks``
        # row. Pre-fix flow ran two separate commits (lock first, status
        # second) — the trigger false-fired at the lock commit and every
        # job start in production aborted.
        #
        # On status mismatch the transaction rolls back BOTH the lock
        # INSERT and the failed UPDATE atomically, so no try/finally is
        # needed to release the lock on failure.
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            logger.info(
                f"[TRACE] start_job: acquiring queue lock for job {job_id[:8]}... "
                f"queue={job.queue_id[:8]}... concurrency_limit={concurrency_limit}"
            )
            try:
                started_job, lock_acquired = await asyncio.to_thread(
                    self._repository.start_job_atomic_with_lock,
                    job_id,
                    instance_id,
                    job.project_id,
                    job.queue_id,
                    concurrency_limit,
                )
            except ValueError:
                # Job state changed (already started/cancelled) — the
                # transaction (including the lock INSERT) rolled back
                # automatically. Preserve the original "return None"
                # behaviour so callers can detect "lock held by another
                # worker" without having to catch an exception.
                return None
            if not lock_acquired:
                logger.info(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — lock NOT acquired (concurrency limit)"
                )
                return None
            logger.debug(
                f"[TRACE] start_job: SUCCESS job {job_id[:8]}... started with instance={instance_id[:8]}..."
            )
            return started_job
        elif job.project_id:
            logger.debug(f"[TRACE] start_job: acquiring project lock for job {job_id[:8]}...")
            # Project-only path uses the synthesized ``"project:{project_id}"``
            # queue with concurrency_limit=1 — same ``job_locks`` table,
            # same trigger, same single-transaction fix.
            try:
                started_job, lock_acquired = await asyncio.to_thread(
                    self._repository.start_job_atomic_with_lock,
                    job_id,
                    instance_id,
                    job.project_id,
                    f"project:{job.project_id}",
                    1,
                )
            except ValueError:
                return None
            if not lock_acquired:
                logger.debug(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — project lock NOT acquired"
                )
                return None
            logger.debug(
                f"[TRACE] start_job: SUCCESS job {job_id[:8]}... started with instance={instance_id[:8]}..."
            )
            return started_job
        else:
            # No project_id, no queue_id — no lock to acquire, no
            # trigger to worry about. Legacy flow is fine here.
            logger.debug(f"[TRACE] start_job: attempting atomic transition PENDING→PROCESSING for job {job_id[:8]}...")
            try:
                started_job = await asyncio.to_thread(
                    self._repository.start_job_atomic, job_id, instance_id
                )
            except (ValueError, InvalidTransitionError):
                # Job state changed (already started/cancelled) — preserve
                # the original "return None" behaviour for callers.
                return None
            logger.debug(
                f"[TRACE] start_job: SUCCESS job {job_id[:8]}... started with instance={instance_id[:8]}..."
            )
            return started_job
    
    async def complete_job(
        self,
        job_id: str,
        demand_state: DemandState = DemandState.COMPLETED,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Mark job as completed/failed/cancelled and release lock.

        Phase 4 (Job as Queue Proxy): routes through the single
        terminal-write boundary ``_finalize_terminal`` (Plan §3.2).
        Every terminal admission transition is funneled through this
        path with a required ``Decision`` enum, so a future caller
        that forgets to state retry/DLQ semantics fails at
        instantiation rather than in production.

        Args:
            job_id: The job ID to complete.
            demand_state: Terminal state (COMPLETED, FAILED, or CANCELLED).
            error: Error message if demand_state is FAILED or CANCELLED.
            result_summary: Optional summary text for completed jobs.

        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None

        # Phase 4: derive the canonical Decision from the
        # ``DemandState`` BEFORE calling ``_finalize_terminal``. The
        # FAILED path consults ``should_retry`` to choose between
        # RETRY / DEAD_LETTER / NO_RETRY so the retry decision is
        # made at the boundary (Plan §8.2 structural guarantee).
        decision = self._decide_terminal_decision(
            job=job,
            demand_state=demand_state,
        )
        if decision is None:
            # Caller passed an unsupported demand_state (defensive —
            # DemandState is a closed enum). Fall back to NO_RETRY.
            decision = Decision.NO_RETRY

        # Lock release happens in ``_finalize_terminal``'s finally
        # block so all paths (success, exception, transition no-op)
        # release the per-queue lock. We do NOT call the legacy
        # ``_release_job_lock`` here.
        #
        # Phase 4: pass ``target_status`` derived from the caller's
        # ``DemandState`` so the status mirror reflects the call
        # intent (``completed`` / ``failed`` / ``cancelled``) instead
        # of the instance-derived fallback (``'failed'`` for missing
        # instances). Tests construct a job + start it without
        # an instance — they expect COMPLETED to land as
        # ``status='completed'``, not the instance-derivation
        # fallback.
        target_status: str | None
        if demand_state == DemandState.COMPLETED:
            target_status = JobStatus.COMPLETED.value
        elif demand_state == DemandState.CANCELLED:
            target_status = JobStatus.CANCELLED.value
        else:
            # FAILED — let ``_finalize_terminal`` derive from the
            # instance for NO_RETRY (matches the legacy
            # ``complete_job`` behaviour of writing ``status=
            # 'failed'`` when no instance is attached).
            target_status = None

        canonical_job_id, final_status = await self._finalize_terminal(
            instance_id=job.instance_id or "",
            decision=decision,
            job_id=job_id,
            result_summary=result_summary,
            error_message=error,
            target_status=target_status,
        )

        if canonical_job_id is None or not final_status:
            # Either the job wasn't found, OR the boundary was a
            # no-op (e.g. job is already in a terminal admission
            # state — ``admission_state='done'`` — and the finalize
            # UPDATE matched zero rows). The legacy contract was to
            # return ``None`` for "wrong state" callers (see
            # ``test_complete_job_wrong_state`` in
            # ``test_task_queue_service.py``), so preserve that.
            return None

        # Notify watchers after successful transition. The terminal
        # state for the watcher event is the legacy status mirror
        # (Phase 5 drops the ``status`` column; Phase 4 keeps both
        # in sync via the dual-write contract).
        try:
            if demand_state == DemandState.COMPLETED:
                await self.notify_watchers(job_id, "completed")
            elif demand_state == DemandState.FAILED:
                # Watchers care about the outcome AFTER the retry
                # decision — DEAD_LETTER is the terminal failure
                # signal even if ``maybe_retry`` was attempted.
                if decision == Decision.RETRY and final_status != JobStatus.DEAD_LETTER.value:
                    # Retry scheduled — don't notify FAILED yet; the
                    # retry engine will emit the appropriate signal
                    # when the next attempt terminates.
                    pass
                else:
                    await self.notify_watchers(job_id, "failed", error)
            elif demand_state == DemandState.CANCELLED:
                await self.notify_watchers(job_id, "cancelled", error)
        except Exception as e:
            logger.warning(
                "complete_job: notify_watchers failed for %s: %s",
                job_id[:8], e,
            )

        # Re-read the job to return its current state (which may
        # have been mutated by ``_finalize_terminal`` to the
        # appropriate terminal/queued admission state).
        return await asyncio.to_thread(self._repository.get, canonical_job_id)

    def _decide_terminal_decision(
        self,
        *,
        job: JobItem,
        demand_state: DemandState,
    ) -> Decision:
        """Phase 4: map a ``DemandState`` to a canonical ``Decision``.

        Returns:
            - ``NO_RETRY`` for COMPLETED and CANCELLED (no retry).
            - For FAILED: consults the retry engine's
              ``should_retry()`` to choose between RETRY /
              DEAD_LETTER / NO_RETRY (when no retry engine is wired
              we fall back to NO_RETRY).
        """
        if demand_state == DemandState.COMPLETED:
            return Decision.NO_RETRY
        if demand_state == DemandState.CANCELLED:
            return Decision.NO_RETRY
        # FAILED — pick retry vs DLQ vs no_retry.
        if self._retry_engine is None:
            return Decision.NO_RETRY
        try:
            from daemon.services.job_retry_engine import JobSystemConfig
            config = (
                self._retry_engine._config
                if hasattr(self._retry_engine, "_config")
                else None
            )
            queue = (
                self._queue_repo.get(job.queue_id)
                if job.queue_id
                else None
            )
            if self._retry_engine.should_retry(job, queue, config):
                return Decision.RETRY
            # Retries exhausted → DLQ.
            return Decision.DEAD_LETTER
        except Exception as e:
            logger.warning(
                "_decide_terminal_decision: should_retry raised for %s: %s — "
                "falling back to NO_RETRY",
                job.job_id[:8], e,
            )
            return Decision.NO_RETRY
    
    def complete_job_sync(
        self,
        job_id: str,
        demand_state: DemandState,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Mark job as completed/failed/cancelled and release lock (synchronous version).

        W6 Fix: Uses asyncio.run_coroutine_threadsafe() to properly release
        per-queue locks from synchronous context by scheduling the async
        release on the stored event loop.

        Phase 4 (Job as Queue Proxy): routes through the
        ``_finalize_terminal`` boundary exactly like ``complete_job``,
        but executes the underlying repository writes synchronously
        (no ``asyncio.to_thread`` for those — the repository methods
        are sync). The ``Decision`` enum is the same.

        Args:
            job_id: The job ID to complete.
            demand_state: Terminal state (COMPLETED, FAILED, or CANCELLED).
            error: Error message if demand_state is FAILED or CANCELLED.
            result_summary: Optional summary of the job result (for COMPLETED).

        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None

        # Phase 4: derive Decision from DemandState.
        decision = self._decide_terminal_decision(
            job=job,
            demand_state=demand_state,
        )
        if decision is None:
            decision = Decision.NO_RETRY

        # ``_finalize_terminal`` requires an event loop for the lock
        # release via the async lock manager. From a sync context we
        # use ``asyncio.run_coroutine_threadsafe`` to dispatch onto
        # the stored loop. The DB writes themselves run on the
        # calling thread (faster than threading off).
        #
        # Phase 4: pass ``target_status`` derived from the caller's
        # ``DemandState`` so the status mirror reflects call intent
        # (COMPLETED → 'completed', CANCELLED → 'cancelled') instead
        # of the instance-derivation fallback.
        target_status: str | None
        if demand_state == DemandState.COMPLETED:
            target_status = JobStatus.COMPLETED.value
        elif demand_state == DemandState.CANCELLED:
            target_status = JobStatus.CANCELLED.value
        else:
            target_status = None

        try:
            canonical_job_id, final_status = self._finalize_terminal_sync(
                instance_id=job.instance_id or "",
                decision=decision,
                job_id=job_id,
                result_summary=result_summary,
                error_message=error,
                target_status=target_status,
            )
        except Exception as e:
            logger.warning(
                "complete_job_sync: _finalize_terminal_sync raised for %s: %s",
                job_id[:8], e,
            )
            return None

        if canonical_job_id is None or not final_status:
            # Either the job wasn't found, OR the boundary was a
            # no-op (job already terminal). The legacy contract is
            # to return ``None`` for "wrong state" callers — see
            # ``test_complete_job_sync_handles_valueerror``.
            return None

        # Notify watchers (async dispatch from sync context).
        try:
            if demand_state == DemandState.COMPLETED:
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.notify_watchers(job_id, "completed"),
                        self._loop,
                    )
            elif demand_state == DemandState.FAILED:
                if decision == Decision.RETRY and final_status != JobStatus.DEAD_LETTER.value:
                    pass  # retry in flight; engine will notify
                else:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self.notify_watchers(job_id, "failed", error),
                            self._loop,
                        )
            elif demand_state == DemandState.CANCELLED:
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.notify_watchers(job_id, "cancelled", error),
                        self._loop,
                    )
        except Exception as e:
            logger.warning(
                "complete_job_sync: notify_watchers dispatch failed for %s: %s",
                job_id[:8], e,
            )

        return self._repository.get(canonical_job_id)

    def _finalize_terminal_sync(
        self,
        instance_id: str,
        decision: Decision,
        *,
        job_id: str | None = None,
        result_summary: str | None = None,
        error_message: str | None = None,
        target_status: str | None = None,
    ) -> tuple[str | None, str]:
        """Synchronous variant of ``_finalize_terminal``.

        Mirrors the async version's decision dispatch and lock-release
        contract but uses ``asyncio.run_coroutine_threadsafe`` for the
        lock release (so it works from sync callers like
        ``trigger_next_job_sync``).

        Returns ``(job_id, final_status_for_backward_compat)``.
        """
        # Locate the job (same logic as the async version).
        canonical_job_id: str | None = None
        canonical_instance_id: str | None = instance_id

        if job_id is not None:
            job = self._repository.get(job_id)
            if job is not None:
                canonical_job_id = job.job_id
                if job.instance_id:
                    canonical_instance_id = job.instance_id
        else:
            jobs = self._repository.find_jobs_by_instance(instance_id)
            for candidate in jobs:
                if candidate.admission_state == AdmissionState.ACTIVE.value:
                    canonical_job_id = candidate.job_id
                    if candidate.instance_id:
                        canonical_instance_id = candidate.instance_id
                    break

        if canonical_job_id is None:
            return None, ""

        # Phase 4: explicit admission_state pre-check (mirrors the
        # async variant). See ``_finalize_terminal`` for the
        # rationale — non-ACTIVE rows fall back to the caller's
        # legacy atomic transition path. ``getattr`` defaults to
        # QUEUED for tests/mocks that don't model the attribute.
        job_admission_state = getattr(
            job, "admission_state", AdmissionState.QUEUED.value
        )
        if job_admission_state != AdmissionState.ACTIVE.value:
            _dispatch_skipped = True
        else:
            _dispatch_skipped = False

        final_status = ""

        try:
            if _dispatch_skipped:
                pass
            elif decision == Decision.NO_RETRY:
                # Phase 4: ``target_status`` overrides the instance-
                # derived status when supplied (used by ``cancel_job``
                # which always wants ``status='cancelled'`` and by
                # ``complete_job(COMPLETED)`` which wants
                # ``status='completed'`` regardless of whether the
                # Instance is attached).
                if target_status is not None:
                    derived_status = target_status
                else:
                    derived_status = self._derive_terminal_status_from_instance(
                        canonical_instance_id
                    )
                update_kwargs: dict[str, Any] = {}
                if derived_status == JobStatus.COMPLETED.value:
                    update_kwargs["result_summary"] = (
                        result_summary or "Job completed successfully"
                    )
                else:
                    update_kwargs["error_message"] = (
                        error_message or "Unknown error"
                    )
                finalized = self._repository.finalize_active_to_done(
                    canonical_job_id,
                    derived_status,
                    **update_kwargs,
                )
                if finalized is None:
                    return canonical_job_id, ""
                final_status = derived_status

            elif decision == Decision.RETRY:
                if self._retry_engine is None:
                    decision = Decision.DEAD_LETTER
                else:
                    retried = self._retry_engine.maybe_retry(canonical_job_id)
                    if retried is not None:
                        final_status = JobStatus.PENDING.value
                    else:
                        final_status = JobStatus.DEAD_LETTER.value

            if decision == Decision.DEAD_LETTER:
                if self._dlq_service is None:
                    self._repository.atomic_transition(
                        canonical_job_id,
                        from_status=JobStatus.PROCESSING.value,
                        to_status=JobStatus.DEAD_LETTER.value,
                    )
                    final_status = JobStatus.DEAD_LETTER.value
                else:
                    self._dlq_service.move_to_dlq_standalone(
                        canonical_job_id,
                        reason="MANUAL",
                        from_admission_state=AdmissionState.ACTIVE.value,
                    )
                    final_status = JobStatus.DEAD_LETTER.value
        finally:
            if canonical_instance_id and self._loop and self._loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._lock_manager.release_by_instance(
                            canonical_instance_id,
                        ),
                        self._loop,
                    )
                    future.result(timeout=5)
                except Exception as e:
                    logger.warning(
                        "_finalize_terminal_sync: lock release failed for "
                        "%s: %s",
                        canonical_instance_id[:8],
                        e,
                    )

        if _dispatch_skipped:
            return canonical_job_id, ""

        return canonical_job_id, final_status
    
    async def trigger_next_job(
        self,
        project_id: str,
        queue_id: str | None = None,
    ) -> JobItem | None:
        """Trigger the next pending job for a queue or project.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Emits a dispatch event so JobProcessor wakes up to handle spawning
        the instance and sending the message.
        
        Args:
            project_id: The project to trigger next job for.
            queue_id: Optional specific queue to trigger next job for.
                     Takes precedence over project_id.
            
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        next_job = await self._get_next_job(project_id, queue_id)
        if next_job is None:
            return None
        
        # Pause check is centralized in start_job()
        result = await self.start_job(next_job.job_id)
        
        # Emit dispatch event so JobProcessor wakes up immediately to
        # spawn instance and send the job message
        if result and self._dispatch_bus:
            self._dispatch_bus.notify_new_job(project_id)
        
        return result
    
    def trigger_next_job_sync(
        self,
        project_id: str,
        queue_id: str | None = None,
    ) -> JobItem | None:
        """Trigger the next pending job for a queue or project (synchronous version).
        
        NOTE: This method has limitations with the new async-only lock manager.
        For new code, prefer the async trigger_next_job() method.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Args:
            project_id: The project to trigger next job for.
            queue_id: Optional specific queue to trigger next job for.
            
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        # TODO: This sync method cannot properly use the async-only lock manager.
        # Migrate all callers to async trigger_next_job()
        
        # Get next pending job
        if queue_id:
            pending = self._repository.list_pending_by_queue(queue_id)
        else:
            pending = self._repository.list_pending_by_project(project_id)
        
        next_job = pending[0] if pending else None
        if next_job is None:
            return None
        
        # PAUSE CHECK: Skip if project is paused (sync call - no wrapper needed)
        if self._project_repo is not None:
            project = self._project_repo.get(project_id)
            if project and project.job_queue_paused:
                logger.debug(
                    f"trigger_next_job_sync: project {project_id[:8]}... is paused, skipping"
                )
                return None
        
        # Get the job
        job = self._repository.get(next_job.job_id)
        if job is None:
            return None
        
        # Check if job is still queued
        if job.admission_state != AdmissionState.QUEUED.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # If job has queue_id, we can't properly acquire async lock in sync context
        if job.queue_id and job.project_id:
            logger.warning(
                f"trigger_next_job_sync called with queue_id for job {job.job_id}. "
                "Lock acquisition will not work properly. Use async trigger_next_job() instead."
            )
            # B1 note: this path does NOT acquire a lock (no INSERT into
            # ``job_locks``), so the trigger ``trg_job_locks_active_guard``
            # is not fired. ``start_job_atomic`` alone is safe here.
            try:
                return self._repository.start_job_atomic(next_job.job_id, instance_id)
            except ValueError:
                return None

        # If job has project_id but no queue_id, use the synthesized
        # ``project:{project_id}`` queue with concurrency_limit=1.
        #
        # B1 Fix: the project-lock path used to acquire via the async
        # lock manager (``asyncio.run_coroutine_threadsafe``) and then
        # call ``start_job`` separately — two commits, trigger false-fires
        # at the lock INSERT. ``start_job_atomic_with_lock`` collapses
        # the lock INSERT and the status UPDATE into ONE ``engine.begin()``
        # transaction so the trigger sees both rows at COMMIT. The method
        # is synchronous and thread-safe, so it can be called from this
        # sync entry point directly.
        if job.project_id:
            try:
                started_job, lock_acquired = (
                    self._repository.start_job_atomic_with_lock(
                        next_job.job_id,
                        instance_id,
                        job.project_id,
                        f"project:{job.project_id}",
                        1,  # default concurrency for project-based locks
                    )
                )
                if not lock_acquired:
                    return None
                return started_job
            except ValueError:
                # Status mismatch — transaction (including the lock
                # INSERT) rolled back automatically.
                return None

        # No project_id - start immediately without locking. No lock
        # INSERT means no trigger to worry about; legacy flow is fine.
        try:
            return self._repository.start_job_atomic(next_job.job_id, instance_id)
        except ValueError:
            return None
    
    async def release_lock_by_instance(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance.
        
        This method is called during instance termination to clean up
        any queue locks that the instance's jobs were holding.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            List of project_ids that were released (deduplicated).
        """
        released = await self._lock_manager.release_by_instance(instance_id)
        # Return unique project_ids for backward compatibility
        return list(set(project_id for project_id, _ in released))
    
    def release_locks_by_instance_sync(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance (synchronous version).
        
        NOTE: The lock manager is now fully async. This sync method cannot
        properly release locks. It logs a warning and returns an empty list.
        
        For production code, use the async release_lock_by_instance() method.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            Empty list with a warning log. Lock release must be done asynchronously.
        """
        logger.warning(
            f"release_locks_by_instance_sync called for instance {instance_id}. "
            "Lock release cannot be done synchronously. Use async release_lock_by_instance() instead."
        )
        return []
