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
)
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.task.models import TaskStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError
from daemon.services.project_normalizer import normalize_project_id
from daemon.services.work_notifier import _format_status_display, notify_work_watchers
from daemon.services.work_status import (
    _derive_legacy_status,
    is_terminal as _work_status_is_terminal,
)
from daemon.registry import get_registry

logger = logging.getLogger(__name__)


# Maximum length of the ``task_message`` snapshot stored on
# ``skill_usage_records.task_message`` — the CAPTURED-skill
# evolution flow forwards this string into the skill-keeper
# LLM prompt, so we cap it to keep prompts bounded. Strings
# longer than this are truncated with ``TASK_MESSAGE_TRUNCATION_MARKER``
# appended so the skill-keeper knows the content was clipped.
TASK_MESSAGE_MAX_LEN: int = 1000
TASK_MESSAGE_TRUNCATION_MARKER: str = "...[truncated]"


def _extract_task_message_from_messages(messages: list) -> str:
    """Return the first ``type='human'`` message's content (truncated).

    Reused by :meth:`JobQueueService._get_task_details` and (via the
    same constants) by the process_message path in
    :mod:`daemon.services.task_processor`. Picks the FIRST
    ``type='human'`` row in queue order — messages are enqueued in
    chronological order so this is the user's earliest original
    request that kicked off the task. Returns ``""`` (empty string,
    NOT None — consumers expect str) when no human message exists
    or the content is non-string.

    Args:
        messages: Iterable of message rows with ``.type`` and
            ``.content`` attributes (typically the
            :class:`MessageQueue` SQLModel).

    Returns:
        The first human message's content, truncated to
        ``TASK_MESSAGE_MAX_LEN`` characters with
        ``TASK_MESSAGE_TRUNCATION_MARKER`` appended if it exceeded
        the cap. ``""`` when no human message is present.
    """
    task_message = ""
    for message in messages:
        if getattr(message, "type", None) == "human":
            content = getattr(message, "content", "") or ""
            if isinstance(content, str) and len(content) > TASK_MESSAGE_MAX_LEN:
                # Truncate to MAX_LEN minus the marker length so the
                # final string never exceeds the cap.
                content = (
                    content[: TASK_MESSAGE_MAX_LEN - len(TASK_MESSAGE_TRUNCATION_MARKER)]
                    + TASK_MESSAGE_TRUNCATION_MARKER
                )
            task_message = content if isinstance(content, str) else ""
            break
    return task_message


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
# ``status="paused"`` resolve to the canonical string; pause is
# non-terminal (paused jobs keep their admission_state='active' and
# can be resumed back to 'processing').
#
# M3 (mission-class, 2026-09-03, ``feature/mission-class``) — the
# transport-receipt terminal for mirror rows (``job_type='message'``)
# is ``settled`` (ADR-MISSION-01 §6.6 I3 amendment). Per-kind matching
# happens in ``_canonical_to_job_filters`` / ``_derive_legacy_status``
# — ``settled`` is a synonym for the legacy mirror-terminal path, NOT
# a synonym for ``completed`` (which still maps task rows). Added as
# an identity entry so ``normalize_statuses("settled") == "settled"``
# and the legacy-statuses filter (``statuses="settled"``) round-trips
# the way callers expect.
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
    "settled": "settled",  # M3 identity — mirror-receipt terminal (see above)
}


def normalize_statuses(statuses: list[str] | None) -> list[str] | None:
    """Resolve natural-language status aliases to canonical job status values.

    - Case-insensitive (lowercases before lookup)
    - If a status is already a canonical value, keeps it as-is (backward compatible)
    - If a status is not a known alias, passes it through unchanged (let SQL return empty)

    M3 (mission-class, 2026-09-03, ``feature/mission-class``) — the
    ``done`` alias expands to BOTH ``completed`` (task terminal) AND
    ``settled`` (mirror terminal) so pre-M3 callers that filtered by
    ``statuses=["done"]`` continue to see every terminal row —
    pre-M3 alias semantics were "any terminal", which both
    ``completed`` (task) AND ``settled`` (mirror) satisfy. The
    expansion is applied at normalize time so the SQL filter sees
    the canonical token list (the SQL filter is
    ``terminal_reason/job_type``-aware via
    ``_canonical_to_job_filters`` and per-kind matching).
    """
    if not statuses:
        return statuses
    out: list[str] = []
    for s in statuses:
        # ``done`` is special-cased: it is the pre-M3 "any terminal"
        # alias and must expand to BOTH task-terminal AND
        # mirror-terminal tokens (per A6 leader adjudication).
        if s.lower() == "done":
            out.extend(["completed", "settled"])
            continue
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
        # Phase 7: every read path goes through WorkResolverService
        # unconditionally.

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

    # Phase 7: every read path goes through WorkResolverService
        # unconditionally.

    async def notify_watchers(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        progress: str | None = None,
        result_summary: str | None = None,
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
                job_id, status, error, progress, result_summary
            )

        # Delegate to the centralized helper. The helper atomically
        # claims watchers via DELETE...RETURNING, resolves the work
        # record through ``self._work_resolver``, builds the
        # ``[JOB_EVENT]`` payload, and enqueues per-watcher. The
        # ``progress=`` kwarg is forwarded so the legacy
        # ``in_progress`` callers (job_feedback_observer) keep working.
        # ``result_summary=`` overrides the resolver's value (the
        # terminal writer already fetched the instance's final
        # message — the resolver returns None for job-kind work).
        return await notify_work_watchers(
            job_id,
            status,
            error=error,
            instance_manager=self._instance_manager,
            work_resolver=work_resolver,
            watcher_repo=self._watcher_repo,
            progress=progress,
            result_summary=result_summary,
        )

    async def _notify_watchers_legacy(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        progress: str | None = None,
        result_summary: str | None = None,
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
                    # Phase 7: result_summary column dropped; terminal result
                    # detail is sourced from Instance/WorkRecord via the resolver,
                    # or from the caller-supplied ``result_summary`` override.
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
                # Map admission_state to representative legacy status
                # string so the watcher filter (which checks against
                # legacy ``watch_events`` like "completed",
                # "dead_letter") matches.
                # F16 fix: route through ``_derive_legacy_status`` so a
                # ``done`` job with ``terminal_reason='failed'`` /
                # ``'cancelled'`` / ``'aborted'`` no longer
                # mis-reports as ``"completed"`` (the lossy raw-map
                # behaviour). Mirrors the F3 fix in
                # ``WorkResolverService._job_to_record``.
                #
                # M3 (mission-class, 2026-09-03) — pass ``job_type``
                # so the per-kind dispatch in ``_derive_legacy_status``
                # applies: mirror rows surface ``"settled"`` (the
                # transport-receipt terminal) instead of ``"completed"``
                # (which now belongs only to the work/mission layer).
                # Task rows keep ``"completed"`` unchanged (a task job
                # IS its own mission; the work word stays).
                legacy_status = _derive_legacy_status(
                    job.admission_state,
                    getattr(job, "terminal_reason", None),
                    getattr(job, "job_type", None),
                )
                # Phase 7: error_message column dropped; error text lives on
                # Instance/WorkRecord via the resolver.
                await self.notify_watchers(watch.job_id, legacy_status, None)
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
        agent_tag: str | None = None,
        job_id: str | None = None,
    ) -> JobItem:
        """Submit a job for processing.

        Jobs are always created as PENDING. The JobProcessor picks up pending
        jobs, transitions them to PROCESSING, spawns instances, and enqueues
        messages for processing.

        If project_id is set but queue_id is None, the job is assigned to the
        project's "system_fifo_queue" (for TASK jobs) automatically.

        With idempotency_key: if a job with the same key exists and is non-terminal,
        returns the existing job instead of creating a duplicate.

        Phase 5 (Option B): ``job_type="message"`` is ACCEPTED. Messages
        flow through the queue and route to the pre-created Task via the
        message branch in ``JobProcessor._process_next_job``.

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
            job_type: Job type — ``"task"`` (default) or ``"message"``.
            instance_id: Optional pre-set instance ID (for message jobs,
                        this is the existing target instance).
            agent_tag: Optional agent version tag (e.g. ``"v2"``). When set, ``agent_dir``
                       is resolved from the versioned variant of the agent instead of the
                       base. Falls back to base metadata when the versioned variant
                       doesn't exist or when this is ``None``.
            job_id: Optional explicit JobItem UUID. When supplied, the
                    repository uses this exact ID; this is used by Option B
                    to link ``JobItem.job_id`` to ``Task.work_id``.

        Returns:
            JobItem with PENDING status (or existing non-terminal job if idempotent).

        Raises:
            ValueError: If ``project_id`` cannot be normalized.
        """
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
            # Version-aware: when ``agent_tag`` is set, prefer the
            # versioned agent's directory; otherwise fall back to the
            # base metadata (matches the pattern used in
            # ``instance_lifecycle.py`` / ``instance_messaging.py``).
            registry = get_registry()
            agent_meta = registry.get_version(agent_id, agent_tag) or registry.get_resolved(agent_id)
            if agent_meta is None:
                raise ValueError(f"Agent not found: {agent_id}")
            agent_dir = str(agent_meta.path)

            # Resolve queue_id for projects (needed for the INSERT row)
            # Phase 5 (Option B): both TASK and MESSAGE jobs reach this
            # point. The D13 "message-rejection guard" was removed in
            # Phase 5 — messages now flow through the standard queue
            # path. The default-queue lookup is keyed by ``job_type``
            # so a message that fails to resolve a queue_id falls back
            # to ``system_parallel_queue`` (concurrency_limit > 1,
            # allows messages to interleave) rather than serializing
            # on ``system_fifo_queue`` (concurrency_limit = 1, which
            # would block unrelated messages behind the failed lookup).
            resolved_queue_id = queue_id
            if resolved_queue_id is None:
                default_queue_name = (
                    "system_parallel_queue"
                    if job_type == "message"
                    else "system_fifo_queue"
                )
                queue = await asyncio.to_thread(
                    self._queue_repo.get_by_name, project_id, default_queue_name
                )
                queue_kind = "parallel" if job_type == "message" else "fifo"
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
                job_id=job_id,
                agent_tag=agent_tag,  # F1: persist for retry-time recovery
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
        # Version-aware: when ``agent_tag`` is set, prefer the
        # versioned agent's directory; otherwise fall back to the
        # base metadata (matches the pattern used in
        # ``instance_lifecycle.py`` / ``instance_messaging.py``).
        registry = get_registry()
        agent_meta = registry.get_version(agent_id, agent_tag) or registry.get_resolved(agent_id)
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)

        # Resolve queue_id for projects
        # Phase 5 (Option B): both TASK and MESSAGE jobs reach this
        # point. The D13 "message-rejection guard" was removed in
        # Phase 5. The default-queue lookup is keyed by ``job_type``
        # — messages fall back to ``system_parallel_queue`` (lets
        # unrelated messages run concurrently) rather than serializing
        # on ``system_fifo_queue`` (concurrency_limit = 1).
        resolved_queue_id = queue_id
        if queue_id is None:
            # project_id is always valid after normalize_project_id()
            default_queue_name = (
                "system_parallel_queue"
                if job_type == "message"
                else "system_fifo_queue"
            )
            queue = await asyncio.to_thread(
                self._queue_repo.get_by_name, project_id, default_queue_name
            )
            queue_kind = "parallel" if job_type == "message" else "fifo"
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
            job_id=job_id,
            agent_tag=agent_tag,  # F1: persist for retry-time recovery
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

    async def cancel_task_by_work_id(self, work_id: str) -> bool:
        """Cancel a Task-backed virtual job by its ``work_id``.

        Write-side facade for the virtual-job surface (Part B,
        revive-fix follow-up, 2026-07-01). The read side resolves a
        ``work_id`` to either a JobItem or a Task, but the cancel path
        was JobItem-only → a Task ``work_id`` returned 404 from
        ``DELETE /api/jobs/{work_id}``. This helper closes that gap.

        Semantics mirror the claim lifecycle so the cancel is always
        safe:

        * a **RUNNING** task is cancelled *cooperatively*
          (:meth:`request_cancel`) — the worker observes the flag at
          its next heartbeat and stops gracefully, so in-flight
          LangGraph state isn't orphaned.
        * a **PENDING** / **PAUSED** task (never claimed, no worker
          holding it) is cancelled *directly and atomically*
          (:meth:`cancel_task`).

        Returns ``True`` if the task was cancelled (or cancel
        requested), ``False`` if the ``work_id`` resolves to no Task,
        the resolver/task-repo is unwired, or the task was already
        terminal.
        """
        instance_manager = getattr(self, "_instance_manager", None)
        task_repo = getattr(instance_manager, "_task_repo", None) if instance_manager else None
        if task_repo is None:
            return False
        task = await asyncio.to_thread(task_repo.get_by_work_id, work_id)
        if task is None:
            return False
        if task.status == TaskStatus.RUNNING.value:
            return await asyncio.to_thread(task_repo.request_cancel, task.id)
        # pending / paused — no worker holds it; direct atomic cancel.
        cancelled = await asyncio.to_thread(
            task_repo.cancel_task, task.id, "cancelled via virtual-job cancel"
        )
        return cancelled is not None

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
        # no legacy status translation. A non-cancellable admission state
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
            target_status="cancelled",
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

    async def cleanup_non_terminal_jobs(self) -> dict[str, int]:
        """Cancel ALL non-terminal, non-deleted jobs ("system reset").

        Drives the ``POST /api/jobs/cleanup`` endpoint. Splits the work
        into five buckets so each side uses the right cancellation tool:

        * **queued** — a single batch UPDATE on the JobItem table
          (``admission_state='queued' → 'done'``,
          ``terminal_reason='cancelled'``). No lock to release, no
          instance to terminate — just stamp the terminal discriminator
          atomically.
        * **active** — one ``cancel_job`` call per row. Each call
          cascades termination to the underlying instance (releasing
          locks, cancelling children) before stamping the row. The
          single-job ``cancel_job`` already handles all the bookkeeping
          (lock release → ``_finalize_terminal(Decision.NO_RETRY)`` →
          ``notify_watchers``) so reuse keeps the cleanup semantics
          byte-for-byte identical to a user-initiated single cancel.
        * **orphan reaper** — force-finalize ``job_type='message'``
          mirrors whose instance is already terminal or missing (a
          ghost active row that slipped through the observer feedback
          path).
        * **bad-state Tasks** — Tasks stuck in ``paused``/``pending``
          whose linked JobItem is already terminal; reconciled to
          ``CANCELLED`` in batch.
        * **instance reaper** (Bucket 5) — non-terminal ``instances``
          rows with no live JobItem AND no live Task. These are
          instances whose work was just cancelled by the prior four
          buckets but whose own ``status`` did not advance to a
          terminal value (e.g. orphan reaper case where the
          ``cancel_job`` cascade did not run, or a crash-after-cancel
          gap that left the instance stuck in ``running``/``paused``).
          Each is transitioned to ``TERMINATED`` via
          :meth:`SQLModelInstanceRepository.transition_status_if`,
          which is race-safe (``WHERE status IN (:allowed_from)``
          prevents clobbering a concurrent terminal-state write).

        Already-terminal jobs (``admission_state IN ('done', 'dead')``)
        and instances (``status`` already in the terminal set) are left
        untouched.

        Returns:
            ``{"cancelled_queued": N, "cancelled_active": M,
            "orphaned_reaped": G, "reconciled_bad_state": T,
            "terminated_instances": Z, "total_processed": N + M}``
            where ``cancelled_queued`` is the batch UPDATE rowcount,
            ``cancelled_active`` is the number of active-side
            ``cancel_job`` calls that returned ``True``, and
            ``terminated_instances`` is the number of zombie instance
            rows flipped to ``TERMINATED`` by Bucket 5. The
            ``orphaned_reaped``, ``reconciled_bad_state`` and
            ``terminated_instances`` counters are excluded from
            ``total_processed`` because they reconcile / terminate
            other tables (``job_queue_items`` for orphans,
            ``task`` for bad-state, ``instances`` for zombies), not
            the JobItem rows the first two buckets handle.
        """
        # 1) Queued batch — single SQL UPDATE, no per-row logic needed.
        cancelled_queued = await asyncio.to_thread(
            self._repository.batch_cancel_queued
        )

        # 2) Active side — cancel each row through the existing
        # ``cancel_job`` so the instance termination cascade runs.
        # Snapshot first so the cancel loop is bounded (new jobs
        # enqueued during the loop are out of scope — they post-date
        # the cleanup request).
        active_jobs = await asyncio.to_thread(
            self._repository.find_active_jobs
        )
        cancelled_active = 0
        for job in active_jobs:
            try:
                success = await self.cancel_job(job.job_id)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "cleanup_non_terminal_jobs: cancel_job(%s) failed: %s",
                    job.job_id[:8], exc,
                )
                continue
            if success:
                cancelled_active += 1

        # 3) Orphan reaper — the prior two paths intentionally exclude
        # ``job_type='message'`` JobItems (they are mirrors of Task
        # rows). If a worker finalized without dispatching the
        # observer feedback (process killed mid-ack, DB write race),
        # the JobItem stays ``admission_state='active'`` forever —
        # no instance to terminate, no lock to release, but the queue
        # counter is inflated and the FE list shows the job as
        # completed (via the resolver) while the badge says ``active``.
        # Reap those now so a System Cleanup button press also drains
        # the ghost rows. The orphan-reaper is its own best-effort
        # loop — failures here must not block the main counters.
        orphaned_reaped = 0
        try:
            orphans = await asyncio.to_thread(
                self._repository.find_orphan_active_jobs
            )
            for orphan in orphans:
                try:
                    reaped = await asyncio.to_thread(
                        self._repository.force_finalize_orphan,
                        orphan.job_id,
                        "cancelled",
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning(
                        "cleanup_non_terminal_jobs: "
                        "force_finalize_orphan(%s) failed: %s",
                        orphan.job_id[:8], exc,
                    )
                    continue
                if reaped is not None:
                    orphaned_reaped += 1
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "cleanup_non_terminal_jobs: orphan reap pass failed: %s",
                exc,
            )

        # 4) Bad-state Task reconciliation — Tasks stuck in paused/pending
        # whose linked JobItem is already terminal (done/dead). These are
        # NOT JobItem rows (the prior three buckets handle those), so the
        # count is excluded from ``total_processed`` (same treatment as
        # ``orphaned_reaped``). Best-effort: failures here must not block
        # the main counters.
        reconciled_bad_state = 0
        try:
            # Deferred import to avoid a circular import at module load
            # (task.repository imports from daemon.services).
            from daemon.repositories.task.repository import TaskRepository

            task_repo = (
                getattr(self._instance_manager, "_task_repo", None)
                if self._instance_manager
                else None
            )
            if task_repo is None:
                # Construct a transient repo from the JobRepository engine.
                engine = getattr(self._repository, "engine", None)
                if engine is not None:
                    task_repo = TaskRepository(engine)
            if task_repo is not None:
                reconciled_bad_state = await asyncio.to_thread(
                    task_repo.batch_reconcile_bad_state_tasks
                )
                if reconciled_bad_state > 0:
                    logger.info(
                        "cleanup_non_terminal_jobs: reconciled_bad_state=%d",
                        reconciled_bad_state,
                    )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "cleanup_non_terminal_jobs: "
                "batch_reconcile_bad_state_tasks failed: %s",
                exc,
            )

        # 5) Instance-Level Reaper — non-terminal ``instances`` rows with
        # no live JobItem AND no live Task. Runs AFTER buckets 1-4 so
        # instances whose work was just cancelled by the active-side
        # ``cancel_job`` cascade get re-evaluated (the cascade terminates
        # the instance, but a race / partial-cancel / observer-feedback
        # gap can leave the instance stuck in ``running``/``paused``/
        # ``idle``/``queued``/``waiting``/``waiting_children`` even though
        # all of its JobItems and Tasks are gone).
        #
        # WS4 MISSION LENS (2026-09-06, ``fix/defer-self-witness-and-
        # cleanup``): an instance's OWN queued defer-lane rows no longer
        # shield it from this reaper (the self-shield exemption). The
        # live ops unstick (incident 6bc61f42 / job 47161b1e) proved a
        # stalled holder — mirrors-only witness, the WS2 ``stalled``
        # class — survived every cleanup press forever because its own
        # queued defer mirrors satisfied the old live-JobItem clause.
        # The exemption lives in the shared scan
        # (``SQLModelInstanceRepository._build_zombie_scan_sql``) and is
        # mirrored by the C3 TOCTOU re-check (``_has_live_work``) so the
        # re-check cannot re-shield what the scan exempted. LIVE
        # missions are NEVER bulk-terminated here: a running/paused
        # Task, an ACTIVE JobItem on any lane, a queued job on a
        # non-defer lane, or non-terminal children all still protect
        # the instance. The ``terminate_instance`` cascade deletes the
        # holder's Task rows; its queued defer mirrors stay AS-IS (the
        # constitution-era mirror protection — cleanup does NOT cancel
        # mirrors) and are subsequently aborted by the dispatch loop's
        # queued-message-on-terminal-instance path
        # (``atomic_transition`` → ``terminal_reason='aborted'``), so
        # no revive wedge is created.
        #
        # C2 (2026-08-12): terminate each zombie through the full
        # ``InstanceManager.terminate_instance()`` cascade instead of a
        # raw ``transition_status_if`` UPDATE. The raw UPDATE skipped
        # 15+ cleanup steps (child cascade, in-memory cleanup, MCP
        # connections, graph-task cancellation, ``job_locks`` release,
        # ``task`` deletion, hierarchy links, event emission). The full
        # ``terminate_instance()`` is idempotent — it short-circuits on
        # already-terminal instances — so a TOCTOU loss vs. another
        # cleanup path is safe.
        #
        # C3 (2026-08-12): re-verify the instance still has no live
        # work immediately before ``terminate_instance``. The zombie
        # scan and the per-row termination are not in the same DB
        # transaction, so a concurrent dispatch may have inserted a
        # new JobItem or Task between the scan and the termination.
        # ``_has_live_work`` checks both surfaces — JobItem by
        # ``find_jobs_by_instance`` (the same predicate the scan uses)
        # and Task by ``has_instance_busy`` (PENDING/RUNNING/PAUSED —
        # the full live-Task predicate, the same canonical
        # single-query check the ``claim_pending_task`` per-instance
        # guard and the ``job_continue`` concurrency gate now use).
        #
        # ``terminated_instances`` is excluded from ``total_processed``
        # (same treatment as ``orphaned_reaped`` and
        # ``reconciled_bad_state``) because it operates on the
        # ``instances`` table, not ``job_queue_items``.
        terminated_instances = 0
        try:
            instance_repo = (
                getattr(self._instance_manager, "_instance_repository", None)
                if self._instance_manager
                else None
            )
            if instance_repo is not None:
                zombie_ids = await asyncio.to_thread(
                    instance_repo.find_zombie_instances
                )
                for zid in zombie_ids:
                    try:
                        # C3: TOCTOU re-check — verify the instance still
                        # has no live work right before terminating. A
                        # concurrent dispatch may have created new work
                        # between the scan and this termination.
                        if self._has_live_work(zid):
                            logger.info(
                                "cleanup_non_terminal_jobs: "
                                "skip zombie %s — live work appeared after scan",
                                zid[:8],
                            )
                            continue
                        # C2: Use full ``terminate_instance()`` instead
                        # of raw UPDATE. This cascades to children,
                        # releases locks, deletes tasks, closes MCP
                        # connections, etc. Idempotent — safe if already
                        # terminal. ``terminate_instance`` is async so
                        # we ``await`` directly (no ``asyncio.to_thread``
                        # wrapper needed, unlike the sync
                        # ``transition_status_if`` we used to call).
                        await self._instance_manager.terminate_instance(zid)
                        terminated_instances += 1
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.warning(
                            "cleanup_non_terminal_jobs: "
                            "terminate zombie %s failed: %s",
                            zid[:8], exc,
                        )
                        continue
                if terminated_instances > 0:
                    logger.info(
                        "cleanup_non_terminal_jobs: "
                        "terminated_instances=%d",
                        terminated_instances,
                    )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "cleanup_non_terminal_jobs: "
                "zombie instance reaper failed: %s",
                exc,
            )

        total = cancelled_queued + cancelled_active
        logger.info(
            "cleanup_non_terminal_jobs: cancelled_queued=%d cancelled_active=%d "
            "orphaned_reaped=%d reconciled_bad_state=%d "
            "terminated_instances=%d total=%d",
            cancelled_queued, cancelled_active, orphaned_reaped,
            reconciled_bad_state, terminated_instances, total,
        )
        return {
            "cancelled_queued": cancelled_queued,
            "cancelled_active": cancelled_active,
            "orphaned_reaped": orphaned_reaped,
            "reconciled_bad_state": reconciled_bad_state,
            "terminated_instances": terminated_instances,
            "total_processed": total,
        }
    
    async def force_complete_defer_holder(self, instance_id: str) -> dict[str, Any]:
        """Terminate a stalled defer-gate holder instance ("force-complete").

        WS4 holder action (2026-09-06, ``fix/defer-self-witness-and-
        cleanup``) — the PRIMARY unstick path for the WS2 ``stalled``
        class: a holder whose gate-busy state is EXCLUSIVELY its own
        settled message mirrors has no live work, so terminating the
        instance IS the force-complete (the mirrors are already
        ``admission_state='done'``; the busy-set witness is the
        instance's non-terminal status, and the
        ``terminate_instance`` cascade flips exactly that).

        SAFETY GUARD (never trust the caller-reported kind): the
        mirrors-only state is re-derived at execution time via the
        canonical probe ``JobRepository.has_active_non_deferred_work(
        None, requester_instance_id=<holder>)`` — the SAME WS1
        carve-out gate body WS2's stall classification uses, system
        scope. The action proceeds ONLY when the probe returns
        ``False`` (the holder's own mirrors were the ONLY busy rows
        system-wide). WS2's ratified STRICT semantics make mixed
        stalled+live holders structurally impossible, so probe=False
        is exactly the safe-to-terminate set. The probe is
        fail-CLOSED (returns True on DB error) so a degraded probe
        refuses the action.

        Census discipline: this method performs NO ``admission_state``
        write of its own — the termination flows through the existing
        :meth:`InstanceManager.terminate_instance` cascade (an
        instances-table writer), so the constitution's writer census
        stays at 23.

        Args:
            instance_id: The holder instance to force-complete.

        Returns:
            ``{"instance_id": str, "terminated": bool, "probe_busy": bool}``
            where ``terminated=False`` means the guard refused (the
            caller should surface a conflict).

        Raises:
            LookupError: The instance does not exist.
        """
        instance_repo = (
            getattr(self._instance_manager, "_instance_repository", None)
            if self._instance_manager
            else None
        )
        if instance_repo is None:
            raise LookupError(
                "force_complete_defer_holder: instance repository unavailable"
            )
        instance = await asyncio.to_thread(instance_repo.get, instance_id)
        if instance is None:
            raise LookupError(f"Instance {instance_id} does not exist")

        # Guard: re-derive mirrors-only at execution time (system
        # scope + the holder as requester — the WS2 stalled probe).
        # fail-CLOSED: a probe error returns True (busy) and refuses.
        probe_busy = await asyncio.to_thread(
            self._repository.has_active_non_deferred_work,
            None,
            instance_id,
        )
        if probe_busy:
            logger.warning(
                "force_complete_defer_holder: REFUSED for %s — "
                "guard probe reports live non-defer work (mixed or "
                "live holder); force-complete is only safe when the "
                "holder's own mirrors are the ONLY busy rows",
                instance_id[:8],
            )
            return {
                "instance_id": instance_id,
                "terminated": False,
                "probe_busy": True,
            }

        # Reuse the full termination cascade (C2 lineage) — idempotent
        # on already-terminal instances, so a TOCTOU loss vs. another
        # cleanup path is safe.
        await self._instance_manager.terminate_instance(instance_id)
        logger.info(
            "force_complete_defer_holder: terminated stalled holder "
            "%s (guard probe reported mirrors-only)",
            instance_id[:8],
        )
        return {
            "instance_id": instance_id,
            "terminated": True,
            "probe_busy": False,
        }

    async def resend_deferred_foreground(self, instance_id: str) -> dict[str, Any]:
        """Cancel the holder's queued defer-lane jobs and re-send their
        message content as FOREGROUND messages.

        WS4 holder action (2026-09-06) — recovers deferred messages a
        stalled/paused holder is pinning: each queued defer-lane
        JobItem on the holder's own books is cancelled through the
        existing single-job :meth:`cancel_job` path, its authoritative
        Task (``Task.work_id == JobItem.job_id`` on the job-driven
        path) is cancelled through the existing virtual-job cancel
        (:meth:`cancel_task_by_work_id` — atomic for PENDING, so the
        union stays consistent and no bad-state shape is minted), and
        its ``message`` content is re-enqueued via the public
        ``enqueue_message_job`` front primitive (the same call
        ``POST /api/instances/{id}/messages`` makes). The re-enqueue is
        a NEW foreground message job (``is_deferred=False`` — the
        default), NOT a mirror mutation: the JobItem side goes through
        the registered ``create`` writer and the cancelled side through
        the registered ``cancel_job`` writer, so the census stays at
        23.

        Live unstick reference (2026-09-06 incident 47161b1e /
        6bc61f42, ``data/logs/ensemble.log`` 16:22–16:24 UTC): the
        manual sequence was ``POST /api/jobs/{id}/cancel`` →
        ``POST /api/jobs/cleanup`` (reaped the idle holder) →
        ``POST /api/instances/{id}/messages`` (revived + new message
        job). This method compresses the cancel + re-enqueue legs into
        one holder-targeted action; the reaper leg stays a cleanup
        press (or the force-complete action).

        Args:
            instance_id: The holder instance whose queued defer jobs
                should be re-sent foreground.

        Returns:
            ``{"instance_id": str, "found_defer_jobs": int,
            "cancelled_defer_jobs": int, "resend_results": list[dict],
            "skipped_empty_content": int}`` — ``resend_results`` rows
            carry ``cancelled_job_id`` and (on success) the new
            foreground ``job_id`` / ``message_id``; failures are
            recorded per-row under ``error`` and do not abort the
            loop.

        Raises:
            LookupError: The instance does not exist.
        """
        instance_repo = (
            getattr(self._instance_manager, "_instance_repository", None)
            if self._instance_manager
            else None
        )
        if instance_repo is None:
            raise LookupError(
                "resend_deferred_foreground: instance repository unavailable"
            )
        instance = await asyncio.to_thread(instance_repo.get, instance_id)
        if instance is None:
            raise LookupError(f"Instance {instance_id} does not exist")

        # The holder's OWN live jobs (queued + active), ordered by
        # created_at — the defer-lane QUEUED subset is the re-send
        # scope. ``active`` defer rows are NOT touched (they are
        # already executing); non-defer lanes are not the defer
        # unstick's business.
        live_jobs = await asyncio.to_thread(
            self._repository.find_jobs_by_instance, instance_id
        )
        queue_repo = getattr(self, "_queue_repo", None)
        defer_jobs: list[Any] = []
        for job in live_jobs:
            if job.admission_state != AdmissionState.QUEUED.value:
                continue
            queue_type: str | None = None
            if queue_repo is not None and job.queue_id:
                try:
                    queue = await asyncio.to_thread(queue_repo.get, job.queue_id)
                    queue_type = getattr(queue, "queue_type", None) if queue else None
                except Exception as exc:  # noqa: BLE001 — classify best-effort
                    logger.warning(
                        "resend_deferred_foreground: queue lookup failed "
                        "for %s (queue %s): %s",
                        job.job_id[:8], job.queue_id, exc,
                    )
            # Fail-CLOSED on unknown lanes: a job whose lane cannot be
            # proven defer is left alone.
            if queue_type == "defer":
                defer_jobs.append(job)

        cancelled = 0
        skipped_empty = 0
        resend_results: list[dict[str, Any]] = []
        for job in defer_jobs:
            content = (job.message or "").strip()
            try:
                cancel_ok = await self.cancel_job(job.job_id)
            except Exception as exc:  # noqa: BLE001 — per-row best-effort
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "error": f"cancel failed: {exc}",
                })
                continue
            if not cancel_ok:
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "error": "cancel returned False (already terminal?)",
                })
                continue
            cancelled += 1
            # WS4: also cancel the mirror's authoritative Task (the
            # shared linkage makes ``Task.work_id == JobItem.job_id``
            # on the job-driven path) so the union stays consistent —
            # a cancelled mirror over a live PENDING defer Task is
            # exactly the bad-state shape. Best-effort: a missing
            # task repo / already-terminal task yields False and is
            # reported, not raised. Task-level writes are NOT census
            # writers; this reuses the existing virtual-job cancel.
            task_cancelled = False
            try:
                task_cancelled = await self.cancel_task_by_work_id(job.job_id)
            except Exception as exc:  # noqa: BLE001 — per-row best-effort
                logger.warning(
                    "resend_deferred_foreground: task cancel failed for "
                    "%s: %s", job.job_id[:8], exc,
                )
            if not content:
                skipped_empty += 1
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "task_cancelled": task_cancelled,
                    "skipped": "empty message content — nothing to re-send",
                })
                continue
            if self._instance_manager is None:
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "task_cancelled": task_cancelled,
                    "error": "instance manager unavailable — cannot re-enqueue",
                })
                continue
            try:
                result = await self._instance_manager.enqueue_message_job(
                    instance_id=instance_id,
                    message=content,
                    source="api",
                )
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "task_cancelled": task_cancelled,
                    "job_id": getattr(result, "job_id", None),
                    "message_id": getattr(result, "message_id", None),
                })
            except Exception as exc:  # noqa: BLE001 — per-row best-effort
                resend_results.append({
                    "cancelled_job_id": job.job_id,
                    "task_cancelled": task_cancelled,
                    "error": f"re-enqueue failed: {exc}",
                })

        logger.info(
            "resend_deferred_foreground: holder=%s found=%d cancelled=%d "
            "resent=%d skipped_empty=%d",
            instance_id[:8], len(defer_jobs), cancelled,
            sum(1 for r in resend_results if r.get("job_id")), skipped_empty,
        )
        return {
            "instance_id": instance_id,
            "found_defer_jobs": len(defer_jobs),
            "cancelled_defer_jobs": cancelled,
            "resend_results": resend_results,
            "skipped_empty_content": skipped_empty,
        }

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

    def _has_live_work(self, instance_id: str) -> bool:
        """Return True if ``instance_id`` has any live (non-terminal) work.

        C3 (2026-08-12) — TOCTOU guard for the Bucket 5 zombie reaper.
        Between the ``find_zombie_instances`` scan and the
        ``terminate_instance`` call, a concurrent dispatch may have
        created new live work for the instance. This helper performs
        a fast re-check of the SAME predicates used by the scan so a
        newly-revived instance is not killed mid-dispatch:

        * **JobItem**: any row on :class:`JobRepository` with
          ``admission_state IN ('queued','active')`` and
          ``deleted_at IS NULL`` (the live-JobItem predicate the
          scan uses). Implemented via :meth:`JobRepository.
          find_jobs_by_instance`, which already filters on
          :data:`ACTIVE_ADMISSION_STATES`. Any non-empty result is
          considered live work.
        * **Task**: any PENDING/RUNNING/PAUSED row on
          :class:`TaskRepository` — the full live-Task predicate the
          scan uses. Implemented via the single canonical query
          :meth:`TaskRepository.has_instance_busy` — replacing the
          prior 2-probe TOCTOU approximation
          (``has_inflight_task`` (PENDING+RUNNING) + ``get_by_instance``
          loop checking PAUSED). The 2-probe pattern had a TOCTOU
          window: a Task could be inserted in PENDING between the
          first probe and the second probe, or the get_by_instance
          loop could miss a row inserted between the probe and the
          status read. ``has_instance_busy`` is a single atomic
          SELECT against the indexed ``ix_task_instance_id`` so
          there is no inter-probe gap. The widened status set
          (PENDING + RUNNING + PAUSED) is the live-Task predicate
          used at every Python-level busy-check surface in the
          daemon (``job_continue``, bus crash recovery, zombie
          reaper, pause/quiesce helpers); the
          :meth:`claim_pending_task` per-instance guard SQL keeps
          its narrower ``status='running'`` S3 invariant (the guard
          shares an atomic statement with the claim itself, so the
          broader predicate is NOT applied inside the claim). See
          :meth:`TaskRepository.has_instance_busy` for the full
          call-site list.

        Conservative-fail strategy: if the instance manager or any
        required repository is unavailable, returns ``False`` so the
        reaper falls through to ``terminate_instance`` — the
        ``terminate_instance`` cascade is itself idempotent and
        short-circuits on already-terminal instances, so the worst
        case is a no-op rather than a stuck zombie.

        Both repository calls are wrapped in ``try/except`` because
        a transient DB error must not block the cleanup path — if
        either probe fails we conservatively assume there is NO new
        live work (so the candidate keeps its place in the reaper)
        and let ``terminate_instance`` do its own safety check.

        Args:
            instance_id: The instance ID to re-check.

        Returns:
            True iff either live JobItem or live Task exists for the
            instance; False otherwise (or on any probe failure /
            missing repo).
        """
        if not self._instance_manager:
            return False

        # JobItem re-check — use the same ``find_jobs_by_instance``
        # the cancel path uses, scoped to one instance_id. The
        # method filters on ``ACTIVE_ADMISSION_STATES`` so we
        # automatically get the live-only predicate.
        #
        # WS4 mission lens: the re-check MUST match the scan
        # predicate (the C3 invariant — "re-check of the SAME
        # predicates used by the scan"), and the scan now exempts
        # the instance's OWN queued defer-lane rows (the self-shield
        # exemption). Without the matching exemption here, every
        # newly-eligible stalled holder would be re-shielded at this
        # line by its own mirrors and the lens would never fire.
        # ``active`` rows of any lane and queued rows on non-defer /
        # unknown lanes still count as live (fail-CLOSED on unknown
        # queue ids — same posture as the SQL arm).
        try:
            live_jobs = self._repository.find_jobs_by_instance(instance_id)
            if live_jobs:
                queue_repo = getattr(self, "_queue_repo", None)
                for job in live_jobs:
                    if job.admission_state == AdmissionState.ACTIVE.value:
                        return True
                    if queue_repo is None:
                        # Cannot classify the lane — fail CLOSED
                        # (treat as live; the reaper skips).
                        return True
                    queue = queue_repo.get(job.queue_id) if job.queue_id else None
                    queue_type = (
                        getattr(queue, "queue_type", None) if queue else None
                    )
                    if queue_type is None or queue_type != "defer":
                        return True
                    # Queued on a defer lane — does not witness.
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            logger.debug(
                "cleanup_non_terminal_jobs: "
                "_has_live_work JobItem probe failed for %s: %s",
                instance_id[:8], exc,
            )

        # Task re-check — single canonical query (Bug-1 fix,
        # 2026-08-12). Replaces the prior 2-probe
        # ``has_inflight_task`` (PENDING+RUNNING) +
        # ``get_by_instance``-PAUSED-loop with a single atomic
        # SELECT against ``ix_task_instance_id``. The widened
        # status set (PENDING + RUNNING + PAUSED) matches
        # ``claim_pending_task``'s per-instance guard and the
        # defer/background gates — one canonical "is this
        # instance busy?" predicate across the daemon.
        task_repo = getattr(self._instance_manager, "_task_repo", None)
        if task_repo is not None:
            try:
                if task_repo.has_instance_busy(instance_id):
                    return True
            except Exception as exc:  # noqa: BLE001 — best-effort probe
                logger.debug(
                    "cleanup_non_terminal_jobs: "
                    "_has_live_work Task has_instance_busy probe "
                    "failed for %s: %s",
                    instance_id[:8], exc,
                )

        return False

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
        # F1: Carry over the original agent_tag so a retried job for a
        # versioned agent (e.g. agent_dir contains [v2]) re-enqueues
        # into the versioned directory instead of silently downgrading
        # to the base agent_dir.
        new_job = await self.enqueue(
            agent_id=job.agent_id,
            message=job.message,
            source=job.source,
            project_id=job.project_id,
            queue_id=job.queue_id,  # Carry over the original queue
            priority=job.priority,
            metadata=job.job_metadata,
            agent_tag=job.agent_tag,  # F1: preserve version tag through retry
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
        job_types: list[str] | None = None,
    ) -> list[JobItem]:
        """List jobs with optional filters.

        Args:
            statuses: Optional list of status filters.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            offset: Number of jobs to skip.
            limit: Maximum number of jobs to return.
            include_deleted: Whether to include soft-deleted jobs.
            job_types: M2 (mission-class, 2026-09-02,
                ``feature/mission-class``) — optional list of
                ``JobItem.job_type`` values to include
                (``"task"`` / ``"message"``). ``None`` (default)
                returns both kinds. Additive vs the legacy
                ``statuses`` filter, which is RETAINED through the M3
                window (contract draft §4).

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
            job_types=job_types,
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
            status string returned for the API consumer
            (``"completed"`` / ``"failed"`` / ``"cancelled"`` /
            ``"pending"`` for queued retry / ``"dead_letter"`` for
            DLQ). An empty string indicates no UPDATE was applied
            (caller should fall back to legacy atomic cancel).
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

                # Phase 7c: terminal_reason discriminator for
                # ``admission_state='done'`` rows. Computed here from
                # ``derived_status`` (the same source the legacy
                # ``status`` mirror used to be derived from) so a
                # completed-by-Instance-status is recorded as
                # ``"completed"``, an error/failed-by-Instance-status
                # as ``"failed"``, and a cancel path (always
                # ``target_status='cancelled'``) as ``"cancelled"``.
                # RETRY and DEAD_LETTER branches deliberately skip
                # this — their target admission states are 'queued'
                # and 'dead' respectively, not 'done', so
                # terminal_reason stays unset (NULL).
                terminal_reason = self._derive_terminal_reason(
                    derived_status
                )

                update_kwargs: dict[str, Any] = {"terminal_reason": terminal_reason}
                if derived_status == "completed":
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

                await self._record_task_metrics(
                    canonical_job_id,
                    derived_status,
                )

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
                        final_status = "pending"
                        # W1 fix: release the lock the job was holding
                        # BEFORE the ACTIVE→QUEUED transition so a
                        # subsequent cancel of the queued job (which
                        # routes through ``_finalize_terminal`` with
                        # ``_dispatch_skipped=True`` and therefore
                        # releases no lock) does not leak the lock
                        # indefinitely. Idempotent w.r.t. the
                        # ``finally`` block below: ``release_by_job``
                        # returns False on no-op so the second release
                        # is a harmless no-op.
                        if (
                            canonical_project_id
                            and canonical_queue_id
                        ):
                            try:
                                # W3 symmetry: same 5s timeout as the
                                # finally-block release below.
                                await asyncio.wait_for(
                                    self._lock_manager.release_queue_lock(
                                        canonical_project_id,
                                        canonical_queue_id,
                                        canonical_job_id,
                                    ),
                                    timeout=5,
                                )
                            except asyncio.TimeoutError:
                                logger.warning(
                                    f"_finalize_terminal RETRY: lock "
                                    f"release timed out after 5s for "
                                    f"job {canonical_job_id[:8]}..."
                                )
                            except Exception as e:
                                logger.warning(
                                    f"_finalize_terminal RETRY: lock "
                                    f"release failed for job "
                                    f"{canonical_job_id[:8]}...: {e}"
                                )
                    else:
                        # maybe_retry moved it to DLQ (retries
                        # exhausted); surface that to the caller.
                        final_status = "dead_letter"

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
                        from_status="processing",
                        to_status="dead_letter",
                    )
                    final_status = "dead_letter"
                else:
                    await asyncio.to_thread(
                        self._dlq_service.move_to_dlq_standalone,
                        canonical_job_id,
                        reason="MANUAL",
                        from_admission_state=AdmissionState.ACTIVE.value,
                    )
                    final_status = "dead_letter"
                await self._record_task_metrics(canonical_job_id, "failed")
        finally:
            # ── Step 3: release lock (always, even on error) ────────
            # F4/F7 fix: scope lock release to the specific
            # ``(project_id, queue_id, job_id)`` triple, NOT the whole
            # instance. The previous ``release_by_instance`` call
            # deleted EVERY lock for the instance — including locks
            # held by sibling jobs (other queues, other jobs) — which
            # caused over-admission past concurrency limits when a
            # job's terminalization on the instance coincided with
            # other in-flight jobs.
            #
            # Three paths:
            #
            # 1. ``_dispatch_skipped=True``: job never held a lock
            #    (e.g. queued-but-not-dispatched). No lock to release.
            # 2. ``canonical_project_id`` AND ``canonical_queue_id``
            #    AND ``canonical_job_id`` populated: scoped release
            #    via ``release_queue_lock`` (which delegates to
            #    ``LockRepository.release_by_job``).
            # 3. Virtual job (``canonical_job_id is None``) OR
            #    JobItem missing ``project_id``/``queue_id``: fall
            #    back to ``release_by_instance`` with a WARNING.
            #    Virtual jobs (post-D13, dispatched directly via the
            #    Task table) never hold a per-queue lock, so a
            #    per-instance sweep is harmless for them; the
            #    fallback exists for defense-in-depth and the
            #    JobItem-corrupt edge case.
            if _dispatch_skipped:
                # Path 1: queued / never-dispatched. Nothing to release.
                pass
            elif (
                canonical_job_id is not None
                and canonical_project_id
                and canonical_queue_id
            ):
                # Path 2: scoped release — only THIS job's lock is
                # deleted; sibling jobs' locks survive.
                try:
                    # W3 fix: wrap the async lock release in
                    # ``asyncio.wait_for`` so the boundary cannot block
                    # forever if the lock manager hangs. The sync twin
                    # already uses ``future.result(timeout=5)`` — this
                    # makes the async / sync paths symmetric with a
                    # shared 5-second deadline.
                    await asyncio.wait_for(
                        self._lock_manager.release_queue_lock(
                            canonical_project_id,
                            canonical_queue_id,
                            canonical_job_id,
                        ),
                        timeout=5,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"_finalize_terminal: lock release timed out "
                        f"after 5s for job {canonical_job_id[:8]}..."
                    )
                except Exception as e:
                    logger.warning(
                        f"_finalize_terminal: failed to release lock "
                        f"for job {canonical_job_id[:8]}...: {e}"
                    )
            else:
                # Path 3: virtual job OR JobItem missing
                # project_id/queue_id. Fall back to instance-wide
                # release — this preserves the legacy
                # ``release_by_instance`` semantics for callers that
                # supply no concrete ``(project_id, queue_id,
                # job_id)`` triple.
                if canonical_instance_id:
                    logger.warning(
                        f"_finalize_terminal: falling back to "
                        f"release_by_instance (canonical_job_id="
                        f"{canonical_job_id!r}, "
                        f"project_id={canonical_project_id!r}, "
                        f"queue_id={canonical_queue_id!r})"
                    )
                    try:
                        # W3 fix: mirror Path 2's timeout so the
                        # instance-wide fallback cannot block forever.
                        await asyncio.wait_for(
                            self._lock_manager.release_by_instance(
                                canonical_instance_id
                            ),
                            timeout=5,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            f"_finalize_terminal: "
                            f"release_by_instance timed out after 5s "
                            f"for instance "
                            f"{canonical_instance_id[:8]}..."
                        )
                    except Exception as e:
                        logger.warning(
                            f"_finalize_terminal: failed to release "
                            f"lock for instance "
                            f"{canonical_instance_id[:8]}...: {e}"
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
            return "failed"
        try:
            instance = self._instance_manager._instance_repository.get(
                instance_id
            )
        except Exception:
            instance = None
        if instance is None:
            return "failed"
        if instance.status == InstanceStatus.COMPLETED.value:
            return "completed"
        if instance.status in (
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ):
            return "failed"
        if instance.status == InstanceStatus.TERMINATED.value:
            return "cancelled"
        return "failed"

    def _derive_terminal_reason(self, derived_status: str) -> str:
        """Map a derived terminal status to the Phase 7c ``terminal_reason`` discriminator.

        The mapping is the natural one-to-one correspondence between
        the legacy ``status`` mirror and ``terminal_reason``:

          * ``completed`` → ``"completed"``
          * ``failed``    → ``"failed"``
          * ``cancelled`` → ``"cancelled"``

        Anything else falls back to ``"failed"`` (matches the
        ``_derive_terminal_status_from_instance`` fallback) so a
        future terminal spelling the discriminator hasn't been taught
        about still writes a valid discriminator value.

        Args:
            derived_status: The terminal status string produced by
                :meth:`_derive_terminal_status_from_instance` (or
                supplied as ``target_status``).

        Returns:
            The corresponding ``terminal_reason`` discriminator value.
        """
        if derived_status == "completed":
            return "completed"
        if derived_status == "cancelled":
            return "cancelled"
        # "failed" or any future unknown terminal spelling.
        return "failed"

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

    # ─── Skill Metrics Recording (Tier 0 — FREE) ───

    async def _record_task_metrics(
        self,
        canonical_job_id: str,
        derived_status: str,
    ) -> None:
        """Record Phase 4 skill metrics after a terminal job write.

        Args:
            canonical_job_id: Canonical JobItem ID whose terminal write
                succeeded.
            derived_status: Terminal status discriminator; ``"completed"``
                records a successful task, all other values record failure.
        """
        try:
            instance_manager = getattr(self, "_instance_manager", None)
            metrics_service = (
                getattr(instance_manager, "_skill_metrics_service", None)
                if instance_manager is not None
                else None
            )
            if metrics_service is None:
                return

            task_details = await self._get_task_details(canonical_job_id)
            if task_details is None:
                return

            await metrics_service.record_task_completion(
                instance_id=task_details["instance_id"],
                agent_id=task_details["agent_id"],
                project_id=task_details.get("project_id"),
                task_succeeded=(derived_status == "completed"),
                iterations=task_details.get("iterations", 0),
                duration_seconds=task_details.get("duration_seconds", 0),
                task_message=task_details.get("task_message", ""),
            )
        except Exception as exc:
            logger.warning(
                f"Skill metrics recording failed for {canonical_job_id}: {exc}"
            )

    def _record_task_metrics_sync(
        self,
        canonical_job_id: str,
        derived_status: str,
    ) -> None:
        """Record Phase 4 skill metrics from a synchronous finalizer.

        Args:
            canonical_job_id: Canonical JobItem ID whose terminal write
                succeeded.
            derived_status: Terminal status discriminator; ``"completed"``
                records a successful task, all other values record failure.
        """
        try:
            instance_manager = getattr(self, "_instance_manager", None)
            metrics_service = (
                getattr(instance_manager, "_skill_metrics_service", None)
                if instance_manager is not None
                else None
            )
            if metrics_service is None:
                return
            if self._loop is None or not self._loop.is_running():
                return

            details_future = asyncio.run_coroutine_threadsafe(
                self._get_task_details(canonical_job_id),
                self._loop,
            )
            task_details = details_future.result(timeout=5)
            if task_details is None:
                return

            metrics_future = asyncio.run_coroutine_threadsafe(
                metrics_service.record_task_completion(
                    instance_id=task_details["instance_id"],
                    agent_id=task_details["agent_id"],
                    project_id=task_details.get("project_id"),
                    task_succeeded=(derived_status == "completed"),
                    iterations=task_details.get("iterations", 0),
                    duration_seconds=task_details.get(
                        "duration_seconds", 0
                    ),
                    task_message=task_details.get("task_message", ""),
                ),
                self._loop,
            )
            metrics_future.result(timeout=5)
        except asyncio.TimeoutError as exc:
            logger.warning(
                f"Skill metrics recording (sync) failed for "
                f"{canonical_job_id}: {exc}"
            )
        except Exception as exc:
            logger.warning(
                f"Skill metrics recording (sync) failed for "
                f"{canonical_job_id}: {exc}"
            )

    async def _get_task_details(self, job_id: str) -> dict | None:
        """Extract per-task details for the Phase 4 skill metrics hook.

        Phase 4 of the Skill Evolution System. Called from the
        :meth:`_finalize_terminal` metrics hook immediately after the
        terminal write succeeds. Returns the fields
        :class:`SkillMetricsService.record_task_completion` needs:

        * ``instance_id`` and ``agent_id`` — sourced directly from the
          :class:`JobItem` row (the job is the canonical handle; the
          instance is recorded at claim time and the agent_id is set
          at enqueue time).
        * ``project_id`` — sourced from the JobItem, falls back to
          ``None`` when the job is project-less (rare for system
          queues but tolerated by the metrics service).
        * ``iterations`` — count of AI (``type='agent'``)
          messages created on or after the job's ``created_at`` on
          the instance's message queue. This is the best available
          approximation of "LLM iterations the task consumed" without
          a dedicated ``task_metrics`` table. Returns ``0`` when the
          message queue repository is unavailable or the lookup raises
          — a missing count must never block the completion hook.
        * ``duration_seconds`` — wall-clock seconds since the job's
          ``created_at`` (an ISO-8601 string or ``datetime``). Falls
          back to ``0`` on any parse failure.
        * ``task_message`` — the FIRST ``type='human'`` message's
          content on the same queue, truncated to
          :data:`TASK_MESSAGE_MAX_LEN` (1000) characters with a
          ``...[truncated]`` marker when longer. Fed into the
          CAPTURED skill-evolution flow as the canonical "what the
          user asked" snapshot. Empty string (``""``) when no human
          message exists or the queue repo is unavailable — NEVER
          None, because consumers expect ``str``.

        Args:
            job_id: The canonical :class:`JobItem` ID (already
                resolved by the caller).

        Returns:
            A dict with keys ``instance_id``, ``agent_id``,
            ``project_id``, ``iterations``, ``duration_seconds``,
            ``task_message``; ``None`` when the job row is missing
            (the caller treats this as "no metrics to record" and
            skips the hook).
        """
        try:
            job = await asyncio.to_thread(self._repository.get, job_id)
        except Exception as exc:
            logger.debug(
                f"_get_task_details: failed to fetch job "
                f"{job_id[:8]}...: {exc}"
            )
            return None
        if job is None:
            return None

        instance_id = getattr(job, "instance_id", None)
        agent_id = getattr(job, "agent_id", None)
        project_id = getattr(job, "project_id", None)

        # ── Duration ──────────────────────────────────────────────
        duration_seconds = 0
        created_at = getattr(job, "created_at", None)
        job_created_at: datetime | None = None
        if isinstance(created_at, datetime):
            job_created_at = created_at
            if job_created_at.tzinfo is None:
                job_created_at = job_created_at.replace(tzinfo=timezone.utc)
        elif isinstance(created_at, str) and created_at:
            try:
                # ``fromisoformat`` accepts the ``+00:00`` suffix
                # directly; replace a literal ``Z`` (zulu) with the
                # ``+00:00`` form for max compatibility.
                job_created_at = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
                if job_created_at.tzinfo is None:
                    job_created_at = job_created_at.replace(
                        tzinfo=timezone.utc
                    )
            except (ValueError, TypeError):
                job_created_at = None
        if job_created_at is not None:
            delta = datetime.now(timezone.utc) - job_created_at
            duration_seconds = max(0, int(delta.total_seconds()))

        # ── Iterations ────────────────────────────────────────────
        # Count ``type='agent'`` rows on the instance's message queue
        # created on or after this job's ``created_at`` — best-effort
        # approximation for "LLM iterations the task consumed". The
        # ``message_queue`` table stores both incoming prompts
        # (``type='human'``) and agent responses (``type='agent'``);
        # only the agent-side rows for the current task count toward
        # the iteration tally. The ``_queue_repository`` handle is
        # reached via the InstanceManager facade; missing facade OR
        # missing repository OR a query error all collapse to ``0`` so
        # the completion hook never raises.
        #
        # ``task_messages`` is the list of pre-filtered messages for
        # THIS task — it's reused below by the ``task_message``
        # extraction so the CAPTURED flow's "what the user asked"
        # snapshot never costs a second DB roundtrip.
        iterations = 0
        task_messages: list = []
        if instance_id:
            try:
                instance_manager = getattr(
                    self, "_instance_manager", None
                )
                queue_repo = (
                    getattr(
                        instance_manager, "_queue_repository", None
                    )
                    if instance_manager is not None
                    else None
                )
                if queue_repo is not None:
                    messages = await asyncio.to_thread(
                        queue_repo.get_by_instance, instance_id
                    )
                    task_messages = messages or []
                    if job_created_at is None:
                        logger.debug(
                            f"_get_task_details: job {job_id[:8]}... "
                            f"has no parseable created_at; counting all "
                            f"agent messages"
                        )
                    else:
                        try:
                            filtered_messages = []
                            for message in task_messages:
                                msg_created_at = getattr(
                                    message, "created_at", None
                                )
                                if isinstance(msg_created_at, datetime):
                                    parsed_msg_created_at = msg_created_at
                                elif (
                                    isinstance(msg_created_at, str)
                                    and msg_created_at
                                ):
                                    parsed_msg_created_at = datetime.fromisoformat(
                                        msg_created_at.replace("Z", "+00:00")
                                    )
                                else:
                                    raise ValueError(
                                        "missing message created_at"
                                    )
                                if parsed_msg_created_at.tzinfo is None:
                                    parsed_msg_created_at = (
                                        parsed_msg_created_at.replace(
                                            tzinfo=timezone.utc
                                        )
                                    )
                                if parsed_msg_created_at >= job_created_at:
                                    filtered_messages.append(message)
                            task_messages = filtered_messages
                        except (ValueError, TypeError) as exc:
                            logger.debug(
                                f"_get_task_details: failed to filter "
                                f"agent messages by created_at for job "
                                f"{job_id[:8]}...; counting all agent "
                                f"messages: {exc}"
                            )
                    iterations = sum(
                        1
                        for m in task_messages
                        if getattr(m, "type", None) == "agent"
                    )
            except Exception as exc:
                logger.debug(
                    f"_get_task_details: failed to count agent "
                    f"messages for instance "
                    f"{(instance_id or '')[:8]}...: {exc}"
                )
                iterations = 0
                task_messages = []

        # ── task_message ─────────────────────────────────────────
        # Extract the FIRST ``type='human'`` message's content as
        # the canonical "what the user asked" snapshot for the
        # CAPTURED skill-evolution flow. Reuses the already-loaded
        # ``task_messages`` list to avoid a second DB roundtrip.
        # Truncated to ``TASK_MESSAGE_MAX_LEN`` (1000) chars with a
        # ``...[truncated]`` marker so the snapshot never blows up
        # the skill-keeper LLM prompt. Empty string when no human
        # message exists or the queue repo is missing — NOT None;
        # consumers expect ``str``.
        task_message = _extract_task_message_from_messages(task_messages)

        return {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "project_id": project_id,
            "iterations": iterations,
            "duration_seconds": duration_seconds,
            "task_message": task_message,
        }

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
        requester_instance_id: str | None = None,
    ) -> JobItem | None:
        """Select the next eligible job from pending list, respecting defer + background semantics.

        Defer jobs are only returned when no non-defer work (active or pending) exists.
        Background jobs are only returned when no non-background work
        exists across ANY project (system-wide scope — see
        :meth:`TaskRepository.has_active_non_background_work` for the rationale).
        (defer-leak fix, 2026-07-23: defer work now counts as non-background work.)

        This ensures defer queues and background queues don't start processing
        while their respective gate lanes are still active.

        **WS1 requester-instance carve-out (2026-09-06):** when the
        caller passes ``requester_instance_id`` (the candidate instance
        being admitted), the per-defer-candidate gate check excludes
        the candidate's OWN settled mirrors from the busy-set. The
        legacy clause stays UNTOUCHED — a live foreground turn yields
        an ACTIVE job which still witnesses correctly. When
        ``requester_instance_id`` is ``None`` (system-scope and legacy
        callers), the pre-WS1 shape is preserved and the gate is
        project-scoped only.

        Args:
            pending: List of pending jobs (ordered by priority desc, created_at asc).
            project_id: Project ID for the DEFER idle check (project-scoped).
                The BACKGROUND idle check is system-wide and ignores this
                argument — the background predicate is always system-wide
                (Phase 3 background seam, 2026-07-14).
            requester_instance_id: Optional candidate instance for the
                WS1 requester-instance carve-out. When ``None`` (the
                default — system-scope and legacy callers), the
                no-carve-out body is used and semantics are identical
                to pre-WS1. When set, each defer candidate is
                evaluated with its OWN ``instance_id`` (the carve-out
                is per-candidate) so a self-witnessing candidate is
                admitted while OTHER candidates with other witnesses
                remain blocked.

        Returns:
            Next eligible JobItem, or None if no eligible jobs.
        """
        if not pending:
            return None

        # Batch-fetch queue types to avoid N+1 queries. Stored as the
        # queue_type STRING (e.g. "fifo", "parallel", "defer",
        # "background") rather than the previous bool — the
        # three-way admission decision (normal / defer / background)
        # needs to distinguish both gate lanes, not collapse them.
        unique_queue_ids = {job.queue_id for job in pending if job.queue_id}
        queue_type_map: dict[str, str] = {}  # queue_id -> queue_type string
        for qid in unique_queue_ids:
            queue = await asyncio.to_thread(self._queue_repo.get, qid)
            # Default to "fifo" for missing/unknown queues so an
            # unknown queue is treated as a normal (always-eligible)
            # queue — matches the pre-existing
            # ``queue_type == "defer" if queue else False`` fallback
            # (an unknown queue defaulted to ``is_defer=False`` and was
            # therefore eligible). The string default preserves that
            # backward-compatible fallback.
            queue_type_map[qid] = queue.queue_type if queue else "fifo"

        # Compute the two idle flags ONCE per call (gate A also runs
        # both checks in :meth:`JobProcessor._process_next_job`, so the
        # two paths are guaranteed to observe the same state at the
        # same logical instant — not byte-for-byte identical SQL
        # execution but both run before either Gate A or Gate B's
        # admission decision, closing the race window that the prior
        # TOCTOU defer-queue bugfix closed for DEFER).
        #
        # Phase 1 (defer-seam bugfix 2026-07-01): Gate B now consults
        # the shared ``TaskRepository.has_active_non_deferred_work``
        # predicate. The shared predicate returns ``True`` iff any row
        # in ``task`` has ``status IN ('pending','running')`` and
        # ``is_deferred = false``, optionally scoped by project — so
        # the claim path and the admission probe never disagree.
        #
        # Phase 3 background seam (2026-07-14): added the sister
        # ``has_active_non_background_work`` predicate for the
        # BACKGROUND gate. System-wide scope — passes ``None`` for
        # ``project_id`` explicitly so the caller's ``project_id``
        # argument is NOT silently mis-used as a project filter on a
        # system-wide predicate.
        #
        # Access: ``TaskRepository`` lives on the InstanceManager
        # (``manager._task_repo`` set up in ``InstanceManager.initialize``,
        # exposed here as ``self._instance_manager._task_repo``). That mirrors
        # the established pattern of accessing sub-repositories through the
        # instance manager (see e.g. ``self._instance_manager._instance_repository``).
        instance_manager = getattr(self, "_instance_manager", None)
        task_repo = (
            getattr(instance_manager, "_task_repo", None)
            if instance_manager is not None
            else None
        )

        # DEFER gate: project-scoped check, "is non-deferred work
        # active in THIS project (excluding the candidate's own
        # settled mirrors when the WS1 requester-instance carve-out
        # is in effect)?". Only computed when at least one pending job
        # is a defer job — short-circuits the SQL round trip when the
        # pending list has no defer candidates.
        #
        # The job-side result is cached PER-CANDIDATE so each defer
        # candidate is evaluated against the gate using its OWN
        # ``requester_instance_id`` (the WS1 carve-out is per-
        # candidate, not per-call). The task-side result is cached
        # PER-PROJECT (it is project-scoped, not per-candidate).
        defer_job_results: dict[str | None, bool] = {}
        defer_task_result: bool | None = None

        async def _resolve_defer_gate(
            candidate_requester: str | None,
        ) -> bool:
            """Resolve the WS1 carve-out gate for ONE defer candidate.

            Returns True iff the gate is BUSY (non-deferred work is
            active EXCLUDING the candidate's own settled mirrors when
            ``candidate_requester`` is set). Caches the job-side
            result per-candidate and the task-side result per-project.
            Fail-CLOSED on DB error.
            """
            nonlocal defer_job_results, defer_task_result

            non_defer_active = False
            job_result: bool | None = None
            repo = getattr(self, "_repository", None)
            if (
                repo is not None
                and hasattr(repo, "has_active_non_deferred_work")
            ):
                # Per-candidate cache key (the carve-out is per-candidate).
                cache_key = candidate_requester
                if cache_key in defer_job_results:
                    job_result = defer_job_results[cache_key]
                    non_defer_active = job_result
                else:
                    try:
                        result = await asyncio.to_thread(
                            repo.has_active_non_deferred_work,
                            project_id,
                            candidate_requester,
                        )
                    except Exception as e:
                        logger.warning(
                            f"_select_next_eligible_job: defer job predicate "
                            f"raised {e!r} for project_id={project_id!r}, "
                            f"requester_instance_id={candidate_requester!r} — "
                            f"failing CLOSED (non_defer_active=True)"
                        )
                        non_defer_active = True
                        defer_job_results[cache_key] = True
                    else:
                        if isinstance(result, bool):
                            job_result = result
                            non_defer_active = result
                            defer_job_results[cache_key] = result

            # W3 (fail-CLOSED, Phase-4 review Issue #1 fix): the task-
            # side leg runs for EVERY clean job-leg False. A PRIOR
            # candidate's job-leg error must NOT suppress this leg —
            # a skipped second leg would admit the candidate while
            # task-only non-defer work is active (the exact silent
            # release the W3 rule bans; pinned by
            # ``test_gateb_jobleg_error_does_not_skip_task_leg``).
            # The candidate whose OWN job-leg raised is already held
            # above (``non_defer_active=True`` short-circuits this
            # guard).
            if not non_defer_active:
                if task_repo is None:
                    if job_result is None:
                        non_defer_active = True
                elif defer_task_result is None:
                    try:
                        defer_task_result = bool(
                            await asyncio.to_thread(
                                task_repo.has_active_non_deferred_work,
                                project_id,
                            )
                        )
                        non_defer_active = defer_task_result
                    except Exception as e:
                        logger.warning(
                            f"_select_next_eligible_job: defer task predicate "
                            f"raised {e!r} for project_id={project_id!r} — "
                            f"failing CLOSED (non_defer_active=True)"
                        )
                        defer_task_result = True
                        non_defer_active = True
                else:
                    non_defer_active = defer_task_result
            return non_defer_active

        # BACKGROUND gate: system-wide check, "is non-deferred,
        # non-background work active ANYWHERE?". Only computed when
        # at least one pending job is a background job — same
        # short-circuit rationale as the defer gate above.
        non_background_active = False
        has_background_candidate = any(
            queue_type_map.get(job.queue_id) == "background" for job in pending
        )
        if has_background_candidate:
            # Sister check to the defer branch above. The job predicate is
            # system-wide (hence ``None``); the task predicate additionally
            # covers active tasks with no backing JobItem.
            #
            # W3 (fail-CLOSED): wrap both predicate calls in try/except
            # so a transient DB failure is treated as "active"
            # (non_background_active=True). Mirrors the defer branch
            # above and the JobProcessor._background_idle_check
            # posture.
            repo = getattr(self, "_repository", None)
            job_result: bool | None = None
            if (
                repo is not None
                and hasattr(repo, "has_active_non_background_work")
            ):
                try:
                    result = await asyncio.to_thread(
                        repo.has_active_non_background_work, None
                    )
                except Exception as e:
                    logger.warning(
                        f"_select_next_eligible_job: background job predicate "
                        f"raised {e!r} — failing CLOSED "
                        f"(non_background_active=True)"
                    )
                    non_background_active = True
                else:
                    if isinstance(result, bool):
                        job_result = result
                        non_background_active = result

            if not non_background_active:
                if task_repo is None:
                    # Same conservative posture as the defer gate when
                    # neither predicate can be evaluated.
                    if job_result is None:
                        non_background_active = True
                else:
                    try:
                        non_background_active = bool(
                            await asyncio.to_thread(
                                task_repo.has_active_non_background_work, None
                            )
                        )
                    except Exception as e:
                        logger.warning(
                            f"_select_next_eligible_job: background task predicate "
                            f"raised {e!r} — failing CLOSED "
                            f"(non_background_active=True)"
                        )
                        non_background_active = True

        # Iterate through pending jobs and select first eligible.
        # Three-way admission decision:
        #   * FIFO / PARALLEL (and any unknown queue_type — see the
        #     default-fallback comment above): always eligible.
        #   * DEFER: eligible iff the per-candidate gate returns
        #     False (with the WS1 requester-instance carve-out
        #     excluding the candidate's own settled mirrors). The
        #     requester instance is the candidate JobItem's OWN
        #     ``instance_id`` when present (the per-candidate
        #     carve-out); otherwise the caller-supplied
        #     ``requester_instance_id`` (the legacy single-request
        #     path) or ``None`` (system-scope and legacy callers
        #     see the pre-WS1 shape).
        #   * BACKGROUND: eligible iff ``non_background_active`` is
        #     False. Background is held back by EITHER non-deferred
        #     work OR other background work anywhere in the system.
        for job in pending:
            queue_type = queue_type_map.get(job.queue_id, "fifo")
            if queue_type == "defer":
                # WS1 per-candidate carve-out: prefer the candidate's
                # own instance_id (when present) so the candidate's
                # OWN settled mirrors do NOT witness against the
                # candidate. Fall back to the caller-supplied
                # requester_instance_id (legacy single-request path)
                # for the rare case where the candidate has no
                # instance_id; fall back to ``None`` (system-scope
                # callers see the pre-WS1 shape).
                candidate_requester = (
                    getattr(job, "instance_id", None)
                    or requester_instance_id
                )
                # NB: the gate resolves ONLY inside this arm, so with
                # no defer candidates in ``pending`` the SQL round trip
                # is short-circuited structurally (the former explicit
                # ``has_defer_candidate`` wrapper here was provably
                # always-True — it is ``any(...)`` over the same
                # pending list with the same map — and its dead
                # else-arm was removed; Phase-4 review Issue #3).
                if await _resolve_defer_gate(candidate_requester):
                    continue
                return job
            if queue_type == "background":
                if not non_background_active:
                    return job
                # Otherwise skip this background job and continue checking
                continue
            # FIFO / PARALLEL / unknown: always safe to return
            return job
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
                    # Phase 5 (Option B): message jobs target an EXISTING
                    # instance. If that instance is already terminal, the
                    # message cannot be delivered.
                    #
                    # B3 fix (v2): the v1 implementation called
                    # ``complete_job(FAILED)``, which delegates to
                    # ``_finalize_terminal``. ``_finalize_terminal`` only
                    # handles ``admission_state='active'`` rows — for a
                    # ``queued`` job it sets ``_dispatch_skipped=True`` and
                    # returns without changing state, creating an INFINITE
                    # retry loop (``start_job`` returns ``None`` →
                    # dispatch refetches the same queued row → loops
                    # forever). The fix routes the queued → terminal
                    # transition through ``atomic_transition`` directly,
                    # which issues a single UPDATE with the
                    # ``WHERE admission_state = 'queued'`` SQL guard.
                    # ``terminal_reason='aborted'`` matches the model
                    # docstring's "instance-terminated cascade" semantics
                    # (models.py:372) and the state machine validates
                    # ``(QUEUED, DONE)`` as a legal transition
                    # (job_state_machine.py:55).
                    #
                    # ``error_message`` was dropped from the ``JobItem``
                    # schema in Phase 5 (see ``_REMOVED_JOB_COLUMNS`` in
                    # repository.py:47) — the abort cause is captured
                    # structurally via ``terminal_reason='aborted'`` and
                    # the warning log carries the human-readable detail.
                    #
                    # TASK jobs continue to use the existing
                    # ``clear stale instance_id`` flow below (re-spawn
                    # on next ``start_job``), which is the correct
                    # semantic for fresh-instance tasks.
                    if job.job_type == "message":
                        logger.warning(
                            f"start_job: message job {job_id[:8]}... "
                            f"targets terminal instance "
                            f"{job.instance_id[:8]}... "
                            f"({instance.status}) — aborting job "
                            f"to break the retry loop "
                            f"(target instance {job.instance_id} is "
                            f"terminal ({instance.status}))"
                        )
                        try:
                            await asyncio.to_thread(
                                self._repository.atomic_transition,
                                job_id,
                                from_status=AdmissionState.QUEUED.value,
                                to_status=AdmissionState.DONE.value,
                                terminal_reason="aborted",
                            )
                        except InvalidTransitionError as trans_err:
                            # Race: a concurrent writer flipped the row
                            # out of ``queued`` between our read and our
                            # UPDATE. The dispatch loop will re-evaluate
                            # on the next tick; the new state is whatever
                            # the winner set (done/dead/active), so we
                            # don't need to retry.
                            logger.info(
                                f"start_job: atomic_transition lost the "
                                f"race for message job {job_id[:8]}... "
                                f"targeting terminal instance "
                                f"{job.instance_id[:8]}...: "
                                f"{trans_err}"
                            )
                        except Exception as abort_err:
                            logger.exception(
                                f"start_job: failed to abort message "
                                f"job {job_id[:8]}... targeting "
                                f"terminal instance: {abort_err}"
                            )
                        # Return None to keep the contract:
                        # ``start_job`` returning None means "do not
                        # process this job in this dispatch attempt".
                        # The terminal transition has moved the job out
                        # of ``admission_state='queued'`` so the next
                        # dispatch loop iteration will skip it.
                        return None
                    # Stale instance ref: the instance pointed to by this
                    # job is already terminal. Clear the ref so a fresh
                    # instance is assigned below. Applies to TASK jobs.
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

        # Generate instance_id:
        # - For message jobs: preserve the existing instance_id (the target instance)
        # - For task jobs: mint a fresh UUID for the new instance
        if job.job_type == "message" and job.instance_id:
            instance_id = job.instance_id  # preserve existing target
        else:
            instance_id = str(uuid.uuid4())  # fresh for new task instances
        
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
            target_status = "completed"
        elif demand_state == DemandState.CANCELLED:
            target_status = "cancelled"
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
                if decision == Decision.RETRY and final_status != "dead_letter":
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
            target_status = "completed"
        elif demand_state == DemandState.CANCELLED:
            target_status = "cancelled"
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
                if decision == Decision.RETRY and final_status != "dead_letter":
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
        canonical_project_id: str | None = None
        canonical_queue_id: str | None = None
        canonical_instance_id: str | None = instance_id

        if job_id is not None:
            job = self._repository.get(job_id)
            if job is not None:
                canonical_job_id = job.job_id
                canonical_project_id = job.project_id
                canonical_queue_id = job.queue_id
                if job.instance_id:
                    canonical_instance_id = job.instance_id
        else:
            jobs = self._repository.find_jobs_by_instance(instance_id)
            for candidate in jobs:
                if candidate.admission_state == AdmissionState.ACTIVE.value:
                    canonical_job_id = candidate.job_id
                    canonical_project_id = candidate.project_id
                    canonical_queue_id = candidate.queue_id
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
                # Phase 7c: terminal_reason discriminator mirrors the
                # async twin — see ``_finalize_terminal`` for the full
                # rationale. Computed from ``derived_status`` so a
                # completed-by-Instance-status is recorded as
                # ``"completed"``, an error/failed-by-Instance-status as
                # ``"failed"``, and a cancel path (always
                # ``target_status='cancelled'``) as ``"cancelled"``.
                terminal_reason = self._derive_terminal_reason(
                    derived_status
                )
                update_kwargs: dict[str, Any] = {"terminal_reason": terminal_reason}
                if derived_status == "completed":
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
                self._record_task_metrics_sync(
                    canonical_job_id,
                    derived_status,
                )

            elif decision == Decision.RETRY:
                if self._retry_engine is None:
                    decision = Decision.DEAD_LETTER
                else:
                    retried = self._retry_engine.maybe_retry(canonical_job_id)
                    if retried is not None:
                        final_status = "pending"
                        # W1 fix: mirror the async twin's lock release
                        # on the ACTIVE→QUEUED retry transition.
                        # Without this, a subsequent cancel of the
                        # queued job routes through this same method
                        # with ``_dispatch_skipped=True`` (because the
                        # job is now ``admission_state='queued'``), and
                        # Path 1 of the finally block below releases
                        # nothing — the lock leaks indefinitely. Release
                        # here, before the transition is observed by
                        # any concurrent caller, so the queued job
                        # starts clean. The finally-block release is
                        # idempotent (``release_by_job`` returns False
                        # on no-op), so the double release is safe.
                        if (
                            canonical_project_id
                            and canonical_queue_id
                            and self._loop
                            and self._loop.is_running()
                        ):
                            try:
                                future = asyncio.run_coroutine_threadsafe(
                                    self._lock_manager.release_queue_lock(
                                        canonical_project_id,
                                        canonical_queue_id,
                                        canonical_job_id,
                                    ),
                                    self._loop,
                                )
                                future.result(timeout=5)
                            except Exception as e:
                                logger.warning(
                                    "_finalize_terminal_sync RETRY: "
                                    "lock release failed for job %s: %s",
                                    canonical_job_id[:8],
                                    e,
                                )
                    else:
                        final_status = "dead_letter"

            if decision == Decision.DEAD_LETTER:
                if self._dlq_service is None:
                    self._repository.atomic_transition(
                        canonical_job_id,
                        from_status="processing",
                        to_status="dead_letter",
                    )
                    final_status = "dead_letter"
                else:
                    self._dlq_service.move_to_dlq_standalone(
                        canonical_job_id,
                        reason="MANUAL",
                        from_admission_state=AdmissionState.ACTIVE.value,
                    )
                    final_status = "dead_letter"
                self._record_task_metrics_sync(canonical_job_id, "failed")
        finally:
            # F4/F7 fix (mirrors the async twin): scope lock release
            # to the specific ``(project_id, queue_id, job_id)``
            # triple. Three paths — see the async ``_finalize_terminal``
            # for full rationale.
            if _dispatch_skipped:
                # Path 1: never dispatched. Nothing to release.
                pass
            elif (
                canonical_job_id is not None
                and canonical_project_id
                and canonical_queue_id
            ):
                # Path 2: scoped release via the manager's
                # ``release_queue_lock`` (delegates to
                # ``LockRepository.release_by_job``).
                if self._loop and self._loop.is_running():
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            self._lock_manager.release_queue_lock(
                                canonical_project_id,
                                canonical_queue_id,
                                canonical_job_id,
                            ),
                            self._loop,
                        )
                        future.result(timeout=5)
                    except Exception as e:
                        logger.warning(
                            "_finalize_terminal_sync: lock release "
                            "failed for job %s: %s",
                            canonical_job_id[:8],
                            e,
                        )
                else:
                    # C3 fix: previously the sync twin silently skipped
                    # the lock release when the event loop was unset /
                    # closed, leaking the lock with no diagnostic. Log
                    # a WARNING so operators can trace which job's lock
                    # was leaked and why the release was skipped.
                    logger.warning(
                        "_finalize_terminal_sync skipped lock release "
                        "for job %s (project=%s, queue=%s, instance=%s) "
                        "— event loop unavailable",
                        canonical_job_id,
                        canonical_project_id,
                        canonical_queue_id,
                        canonical_instance_id,
                    )
            else:
                # Path 3: virtual job OR JobItem missing
                # project_id/queue_id. Fall back to instance-wide
                # release (legacy semantics).
                if canonical_instance_id and self._loop and self._loop.is_running():
                    logger.warning(
                        "_finalize_terminal_sync: falling back to "
                        "release_by_instance (canonical_job_id="
                        f"{canonical_job_id!r}, "
                        f"project_id={canonical_project_id!r}, "
                        f"queue_id={canonical_queue_id!r})"
                    )
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
                            "_finalize_terminal_sync: lock release failed "
                            "for %s: %s",
                            canonical_instance_id[:8],
                            e,
                        )
                elif canonical_instance_id:
                    # C3 fix: mirror Path 2's diagnostic for the
                    # instance-wide fallback. Without this, a
                    # close-during-finalize race leaks the lock with no
                    # signal to the operator.
                    logger.warning(
                        "_finalize_terminal_sync skipped "
                        "release_by_instance fallback for instance %s "
                        "(job=%s, project=%s, queue=%s) "
                        "— event loop unavailable",
                        canonical_instance_id,
                        canonical_job_id,
                        canonical_project_id,
                        canonical_queue_id,
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
