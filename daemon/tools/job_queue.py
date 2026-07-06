"""Job queue management tools for LangGraph agents."""

import asyncio
from datetime import datetime
from typing import Annotated, Any, TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from ._truncate import truncate_dict_result
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.services.project_normalizer import normalize_project_id
from daemon.services.work_status import _derive_legacy_status

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
    from daemon.services.dead_letter_service import DeadLetterService
    from daemon.services.work_resolver import WorkRecord, WorkResolverService
    from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
    from daemon.manager import InstanceManager

CATEGORY_NAME = "Job Queue"
CATEGORY_DOC = """\
Create, list, and manage jobs and job queues.
"""

TERMINAL_STATES = set(ALL_TERMINAL_STATES)


# `WorkRecord.to_dict()` (defined in `daemon.services.work_resolver`) is
# the canonical serializer for the virtual job surface. It handles
# tz-aware / tz-naive `created_at` normalisation so both routers and
# MCP tools get a byte-identical JSON shape for the same WorkRecord.

# Full documentation strings for each tool
_FULL_DOCS = {
    "job_create": """Submit a new job to the queue.

Jobs are processed by agents asynchronously. The job will be queued
and picked up by the job processor when capacity is available.

Args:
    agent_id: Agent ID to run the job (e.g., "developer", "leader"). Required.
    message: The instruction/message for the agent. Required.
    project_id: Project ID for isolation and routing. Optional.
    priority: Job priority 1-10 (1=lowest, 10=highest). Default: 5.
    queue_id: Specific queue to submit to. Optional.
    idempotency_key: Deduplication key. Optional.
    metadata: Custom key-value metadata. Optional.
    source: Source identifier. Default: "api".

Returns:
    Dictionary with job details including job_id and status.

Example:
    job_create(
        agent_id="developer",
        message="Fix the login bug in auth.py",
        project_id="proj_123",
        priority=7
    )""",

    "job_get": """Get job details by ID.

Args:
    job_id: The job ID to look up.

Returns:
    Dictionary with full job details, or error if not found.""",

    "job_list": """List jobs with optional filters.

Args:
    statuses: Filter by status - "pending", "processing", "completed", "failed", "cancelled", "dead_letter". Natural aliases also accepted (e.g. "running" → processing, "done" → completed, "waiting" → pending). Case-insensitive. Optional.
    project_id: Filter by project ID. Optional.
    queue_id: Filter by queue ID. Optional.
    offset: Number of jobs to skip (default: 0).
    limit: Maximum number of jobs to return. Default: 50.
    include_deleted: Include soft-deleted jobs. Default: False.

Note:
    ``job_list`` routes through ``work_resolver.list_work`` which
    only honours ``queue_id`` for JobItem rows. Task rows have no
    queue affinity and will be included in the result regardless
    of the supplied ``queue_id``. To get strict queue-only
    filtering, pass ``statuses=["pending", "processing"]`` so the
    result excludes terminal Task rows, or post-filter the returned
    records client-side.

    **Shows root-instance work by default** (``root_only=True``). The
    jober manages work it bound to a root instance; child-instance
    turns/reports (rows whose backing instance has a non-null
    ``parent_id``) are internal mechanics of that root's job and
    have **no link back to the originating ``job_id``**, so they are
    filtered out by the resolver. ``process_report`` rows that
    target the parent instance (per ``child_reports.py``) are
    kept — they're the parent's inbound notification, not the
    child's private execution.

Returns:
    Dictionary with jobs list and count.""",

    "job_cancel": """Cancel a pending or processing job.

Semantics differ by kind (Phase 2 Batch 4a):
    * ``job`` (dispatch-queue): atomic — the row is marked CANCELLED
      immediately and the row is gone. Use ``job_get`` to verify.
    * ``task`` (worker-pool): cooperative — sets ``cancel_requested``
      on the underlying Task row; the worker thread observes the flag
      on its next heartbeat and stops gracefully. The row stays in
      ``running`` until the worker yields.

Args:
    job_id: The work_id to cancel.

Returns:
    Confirmation message or error.""",

    "job_retry": """Retry a failed job.

Args:
    job_id: The job ID to retry.

Returns:
    Confirmation message or error.""",

    "job_delete": """Soft delete a job.

Args:
    job_id: The job ID to delete.

Returns:
    Confirmation message or error.""",

    "job_restore": """Restore a soft-deleted job.

Args:
    job_id: The job ID to restore.

Returns:
    Confirmation message or error.""",

    "queue_list": """List all queues for a project.

Args:
    project_id: The project ID to list queues for.

Returns:
    Dictionary with queues list and count.""",

    "queue_create": """Create a new queue for a project.

Args:
    project_id: The project ID. Required.
    queue_name: Unique queue name within the project. Required.
    queue_type: Queue type - "fifo" or "parallel". Default: "fifo".
    concurrency_limit: Max concurrent jobs. Default: 1 (required for FIFO).
    description: Queue description. Optional.

Returns:
    Confirmation message with queue_id.""",

    "queue_update": """Update queue settings.

Args:
    queue_id: The queue ID to update. Required.
    project_id: The project ID (for ownership validation). Required.
    queue_name: New queue name. Optional.
    concurrency_limit: New concurrency limit. Optional.
    is_paused: Pause or resume the queue. Optional.

Returns:
    Confirmation message.""",

    "dlq_list": """List dead letter queue items.

Dead letter queue contains failed jobs that exceeded retry limits
or were moved there for manual inspection.

Args:
    project_id: Filter by project ID. Required.
    queue_id: Filter by queue ID. Optional.
    limit: Maximum items to return. Default: 50.

Returns:
    Dictionary with DLQ items, count, and total.""",

    "dlq_replay": """Replay a job from the dead letter queue.

This resets the job to pending status and removes it from the DLQ.
The job will be picked up for processing again.

Args:
    dlq_id: The DLQ entry ID (not the job_id). Required.

Returns:
    Confirmation message.""",

    "watch_job": """Watch a job for lifecycle events.

If the job is already in a terminal state (completed, failed, cancelled, dead_letter),
an immediate notification is sent. Otherwise, you will receive a message when the job reaches a terminal state.

Args:
    job_id: The job ID to watch. Required.
    events: Specific terminal states to watch for. Optional.
        Default: all terminal states ["completed", "failed", "cancelled", "dead_letter"]

Returns:
    Confirmation message or error.""",

    "unwatch_job": """Stop watching a job for lifecycle events.

Args:
    job_id: The job ID to stop watching. Required.

Returns:
    Confirmation message.""",

    "list_watched_jobs": """List all jobs the current instance is watching.

Returns:
    List of watched jobs with their event filters.""",

    "watch_jobs": """Watch multiple jobs for lifecycle events. Bulk version of watch_job.

Jobs already in terminal states will trigger immediate notifications.

Args:
    job_ids: List of job IDs to watch. Required.
    events: Specific terminal states to watch for. Optional.
        Default: all terminal states

Returns:
    Summary of watches registered and immediate notifications sent.""",

    "job_continue": """Continue a completed/terminal job by sending a new message to its instance.

Looks up the instance_id from the old (terminal) job, validates that the
instance is healthy (not terminated/errored/paused), and enqueues a new
MESSAGE job to the same instance via the JobQueue path. The instance
retains its conversation context from the original job.

Args:
    old_job_id: Job ID of a terminal job to continue from. Required.
    message: New message/instruction to send to the instance. Required.

Returns:
    Dictionary with old_job_id, instance_id, message_id, new_job_id, status.

Example:
    job_continue(
        old_job_id="job_abc123",
        message="Now add unit tests for the login flow"
    )""",
}


def create_job_tools(
    job_service: "JobQueueService",
    queue_mgmt_service: "JobQueueMgmtService",
    dead_letter_service: "DeadLetterService",
    current_instance_id: str = "",
    agent_id: str = "",
    watcher_repo: "JobWatcherRepository | None" = None,
    manager: "InstanceManager | None" = None,
):
    """Create job queue management tools with injected services.

    Args:
        job_service: JobQueueService instance for job operations.
        queue_mgmt_service: JobQueueMgmtService instance for queue management.
        dead_letter_service: DeadLetterService instance for DLQ operations.
        current_instance_id: The current instance ID.
        agent_id: The current agent ID.
        watcher_repo: JobWatcherRepository instance for watch functionality. Optional.
        manager: InstanceManager for tools that need access to instance/messaging APIs (e.g., job_continue). Optional.

    Returns:
        List of tool functions for job queue management.
    """
    caller_agent_id = agent_id

    class JobCreateInput(BaseModel):
        """Input schema for job_create tool."""
        agent_id: Annotated[str, Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")]
        message: Annotated[str, Field(description="The instruction/message for the agent")]
        project_id: Annotated[str | None, Field(default=None, description="Project ID for isolation and routing")]
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10 (1=lowest, 10=highest)")]
        queue_id: Annotated[str | None, Field(default=None, description="Specific queue to submit to")]
        idempotency_key: Annotated[str | None, Field(default=None, description="Deduplication key")]
        metadata: Annotated[dict[str, Any] | None, Field(default=None, description="Custom key-value metadata")]
        source: Annotated[str, Field(default="api", description="Source identifier")]
        watch: Annotated[bool, Field(default=False, description="Watch the job for lifecycle events")]

    @register_tool_category("job")
    @tool(args_schema=JobCreateInput)
    async def job_create(
        agent_id: Annotated[str, Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")],
        message: Annotated[str, Field(description="The instruction/message for the agent")],
        project_id: Annotated[str | None, Field(default=None, description="Project ID for isolation and routing")] = None,
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10")] = 5,
        queue_id: Annotated[str | None, Field(default=None, description="Specific queue to submit to")] = None,
        idempotency_key: Annotated[str | None, Field(default=None, description="Deduplication key")] = None,
        metadata: Annotated[dict[str, Any] | None, Field(default=None, description="Custom key-value metadata")] = None,
        source: Annotated[str, Field(default="api", description="Source identifier")] = "api",
        watch: Annotated[bool, Field(default=False, description="Watch the job for lifecycle events")] = False,
    ) -> dict:
        """Submit a new job to the queue. Use tool_help("job_create") for details."""
        try:
            # Override source if using default "api" and called by an agent
            if source == "api" and caller_agent_id:
                source = f"agent:{caller_agent_id}"
            normalized_project_id = normalize_project_id(project_id)
            job_item = await job_service.enqueue(
                agent_id=agent_id,
                message=message,
                source=source,
                project_id=normalized_project_id,
                priority=priority,
                metadata=metadata,
                queue_id=queue_id,
                idempotency_key=idempotency_key,
            )
            # Register watch if requested (job is PENDING here, no race with observer)
            if watch and watcher_repo is not None and current_instance_id:
                count = watcher_repo.count_watches_for_instance(current_instance_id)
                if count >= 50:
                    return {
                        "error": "Maximum watch limit (50) reached for this instance",
                        "job_id": job_item.job_id,
                        # F16 fix: route through ``_derive_legacy_status``
                        # so the watch-limit error response carries the
                        # discriminator-aware status (a ``done`` job with
                        # ``terminal_reason='failed'`` reports
                        # ``"failed"`` instead of the lossy ``"completed"``).
                        "status": _derive_legacy_status(
                            job_item.admission_state,
                            getattr(job_item, "terminal_reason", None),
                        ),
                    }
                watcher_repo.add_watch(job_item.job_id, current_instance_id)
            return job_item.to_dict()
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to create job: {str(e)}"}
    job_create._full_doc_ = _FULL_DOCS["job_create"]

    @register_tool_category("job")
    @tool
    async def job_get(job_id: str) -> dict:
        """Get job details by ID. Use tool_help("job_get") for details."""
        try:
            # Phase 7: only the resolver path remains. ``service.get_work``
            # resolves either a Task row (worker-pool side) or a JobItem
            # row (dispatch-queue side) onto the unified WorkRecord
            # view-model.
            record = await job_service.get_work(job_id)
            if record is None:
                return {"error": "Job not found"}
            return record.to_dict()
        except Exception as e:
            return {"error": f"Failed to get job: {str(e)}"}
    job_get._full_doc_ = _FULL_DOCS["job_get"]


    @register_tool_category("job")
    @tool
    async def job_list(
        project_id: Annotated[str | None, Field(default=None, description="Filter by project ID")] = None,
        statuses: Annotated[list[str] | None, Field(
            default=None,
            description="Filter by job status. Valid values: pending, processing, completed, failed, cancelled, dead_letter. Aliases accepted: running (=processing), done (=completed), error (=failed), waiting (=pending). Case-insensitive."
        )] = None,
        queue_id: Annotated[str | None, Field(default=None, description="Filter by queue ID")] = None,
        offset: Annotated[int, Field(default=0, ge=0, description="Number of jobs to skip")] = 0,
        limit: Annotated[int, Field(default=50, ge=1, le=100, description="Maximum jobs to return")] = 50,
        include_deleted: Annotated[bool, Field(default=False, description="Include soft-deleted jobs")] = False,
    ) -> dict:
        """List jobs with optional filters. Use tool_help("job_list") for details."""
        try:
            # Phase 7: resolver is always ON. Route through
            # ``work_resolver.list_work`` so the list is the UNION of
            # pending jobs AND running tasks.
            #
            # Status-alias normalisation (``"running"`` → ``"processing"``
            # etc.) runs on the input list before either branch so the
            # natural-language aliases documented in the tool signature
            # work. The normalised values are already in the canonical
            # vocabulary ``list_work`` understands.
            #
            # The ``queue_id`` filter is JobItem-only — the Task table
            # has no queue concept. We issue the resolver call regardless
            # and accept that Task rows may show up that wouldn't have
            # under the legacy list; the surface is intentionally widened
            # (this is the whole point of the virtual job surface).
            # Callers that want ``queue_id`` filtering semantics on the
            # resolver path can post-filter the returned records.
            from daemon.services.job_queue_service import normalize_statuses
            normalised_statuses = normalize_statuses(statuses)

            work_resolver: "WorkResolverService | None" = getattr(
                job_service, "_work_resolver", None
            )
            if work_resolver is None:
                # Resolver not wired — degrade gracefully to the
                # JobItem-only list rather than crash, so a partial-wiring
                # daemon still serves traffic.
                jobs = await job_service.list_jobs(
                    statuses=normalised_statuses,
                    project_id=project_id,
                    queue_id=queue_id,
                    offset=offset,
                    limit=limit,
                    include_deleted=include_deleted,
                )
                result = {
                    "jobs": [job.to_dict() for job in jobs],
                    "count": len(jobs),
                }
                return truncate_dict_result(result, list_key="jobs", limit=limit)

            records = await asyncio.to_thread(
                work_resolver.list_work,
                project_id=project_id,
                instance_id=None,
                status=normalised_statuses[0] if (
                    normalised_statuses and len(normalised_statuses) == 1
                ) else None,
                kind=None,
                root_only=True,  # P-A: jober manages root-instance work only
            )

            # ``list_work`` only accepts a single ``status`` string
            # — the resolver surface is intentionally narrow. When
            # the caller supplies multiple statuses we issue the
            # unfiltered resolver call and post-filter by the
            # canonical status set so multi-status requests like
            # ``statuses=["completed", "failed"]`` don't silently
            # widen to "all records". The single-status case is
            # already handled by the resolver-level filter above.
            if normalised_statuses and len(normalised_statuses) > 1:
                allowed_statuses = set(normalised_statuses)
                records = [
                    r for r in records if r.status in allowed_statuses
                ]

            # ``list_work`` doesn't support pagination — the legacy
            # ``list_jobs`` accepted ``offset``/``limit``/``include_deleted``.
            # Apply them client-side: skip soft-deleted records
            # (WorkRecords don't carry deleted_at, but Task rows
            # never have a deleted state), apply offset, then limit.
            page = records[offset : offset + limit]
            result = {
                "jobs": [r.to_dict() for r in page],
                "count": len(page),
            }
            return truncate_dict_result(result, list_key="jobs", limit=limit)
        except Exception as e:
            return {"error": f"Failed to list jobs: {str(e)}"}
    job_list._full_doc_ = _FULL_DOCS["job_list"]


    @register_tool_category("job")
    @tool
    async def job_cancel(job_id: str) -> str:
        """Cancel a pending or processing job. Use tool_help("job_cancel") for details."""
        try:
            # Phase 7: the work_id resolves to either a Task row
            # (worker-pool side, requires cooperative
            # ``cancel_requested``) or a JobItem row (dispatch-queue
            # side, instant ``cancel_job``). The semantics differ: task
            # cancellation sets a flag the worker thread checks on its
            # next iteration, so the row stays RUNNING until the worker
            # yields; JobItem cancellation is atomic and flips the row
            # to CANCELLED immediately. The tool docstring is updated
            # to flag this.
            # Phase 7: resolver is always ON. ``get_work`` resolves the work_id
            # to either a Task row (cooperative cancel via
            # ``cancel_requested``) or a JobItem row (instant atomic
            # ``cancel_job``).
            record = await job_service.get_work(job_id)
            if record is None:
                return f"ERROR: Could not cancel job {job_id}. Job not found."

            # Phase 4 partial collapse (2026-07-06): the previous
            # ``kind="turn"`` (process_message) / ``kind="report"``
            # (process_report / send_report) split on Task rows is
            # gone — ``kind="turn"`` Tasks no longer exist (turns are
            # JobItems from the entry point on). The only remaining
            # Task-side kind is ``"report"``. Both ``kind="report"``
            # (Task) and ``kind="job"`` (JobItem) still go through the
            # cooperative ``request_cancel`` vs instant ``cancel_job``
            # split as before; only ``kind="job"`` (JobItem rows) goes
            # through the instant ``cancel_job`` path.
            if record.kind != "job":
                # Cooperative task cancel: look up the Task by
                # ``work_id`` and call ``task_repo.request_cancel``
                # which sets ``cancel_requested=True``. The worker
                # thread observes the flag on its next heartbeat
                # and stops gracefully — the row stays RUNNING in
                # the meantime (this is by design; instant task
                # kill would orphan in-flight graph state).
                if manager is None or getattr(manager, "_task_repo", None) is None:
                    return (
                        f"ERROR: Could not cancel job {job_id}. "
                        "Task repository not available for task-kind cancellation."
                    )
                task = await asyncio.to_thread(
                    manager._task_repo.get_by_work_id, job_id
                )
                if task is None:
                    return (
                        f"ERROR: Could not cancel job {job_id}. "
                        "Task row missing despite resolver match."
                    )
                cancelled = await asyncio.to_thread(
                    manager._task_repo.request_cancel, task.id
                )
                if cancelled:
                    return (
                        f"Cancel requested for job {job_id[:8]}... "
                        "(cooperative — the running task will stop at its next checkpoint)."
                    )
                return (
                    f"ERROR: Could not cancel job {job_id}. "
                    "Task is not in a cancellable state (must be RUNNING with no cancel pending)."
                )

            # JobItem branch — instant atomic cancel via the
            # existing ``cancel_job`` path.
            success = await job_service.cancel_job(job_id)
            if success:
                return f"Job {job_id} cancelled successfully."
            return f"ERROR: Could not cancel job {job_id}. Job may not be in a cancellable state."
        except Exception as e:
            return f"ERROR: Failed to cancel job {job_id}: {str(e)}"
    job_cancel._full_doc_ = _FULL_DOCS["job_cancel"]


    @register_tool_category("job")
    @tool
    async def job_retry(job_id: str) -> str:
        """Retry a failed job. Use tool_help("job_retry") for details."""
        try:
            # P-D (Phase 5, 2026-06-27): resolve the work_id FIRST so we
            # can return a precise error if the caller passed a task-type
            # work_id (``kind != "job"``). Without this guard a task
            # work_id falls straight through to the JobItem-only retry
            # path and returns the generic "may not be retryable" message
            # — which is misleading because Task rows have no retry
            # semantics by design.
            record = await job_service.get_work(job_id)
            if record is not None and record.kind != "job":
                return (
                    f"ERROR: Operation not applicable: {job_id[:8]}... is "
                    f"task-type work ({record.kind}), which has no retry path."
                )

            job_item = await job_service.retry_job(job_id)
            if job_item is not None:
                return f"Job {job_id} retry initiated successfully."
            return f"ERROR: Could not retry job {job_id}. Job may not be in a retryable state."
        except Exception as e:
            return f"ERROR: Failed to retry job {job_id}: {str(e)}"
    job_retry._full_doc_ = _FULL_DOCS["job_retry"]


    @register_tool_category("job")
    @tool
    async def job_delete(job_id: str) -> str:
        """Soft delete a job. Use tool_help("job_delete") for details."""
        try:
            # P-D (Phase 5, 2026-06-27): precise error for task-type
            # work_ids — see ``job_retry`` comment for rationale.
            # Tasks are not soft-deletable, so the JobItem-only
            # ``soft_delete_job`` path is wrong for them.
            record = await job_service.get_work(job_id)
            if record is not None and record.kind != "job":
                return (
                    f"ERROR: Operation not applicable: {job_id[:8]}... is "
                    f"task-type work ({record.kind}), which has no delete path."
                )

            job_item = await job_service.soft_delete_job(job_id)
            if job_item is not None:
                return f"Job {job_id} deleted successfully."
            return f"ERROR: Could not delete job {job_id}. Job may not exist."
        except Exception as e:
            return f"ERROR: Failed to delete job {job_id}: {str(e)}"
    job_delete._full_doc_ = _FULL_DOCS["job_delete"]


    @register_tool_category("job")
    @tool
    async def job_restore(job_id: str) -> str:
        """Restore a soft-deleted job. Use tool_help("job_restore") for details."""
        try:
            # P-D (Phase 5, 2026-06-27): precise error for task-type
            # work_ids — see ``job_retry`` comment for rationale.
            # Tasks are not soft-deletable, so a restore against a
            # task work_id is meaningless.
            record = await job_service.get_work(job_id)
            if record is not None and record.kind != "job":
                return (
                    f"ERROR: Operation not applicable: {job_id[:8]}... is "
                    f"task-type work ({record.kind}), which has no restore path."
                )

            job_item = await job_service.restore_job(job_id)
            if job_item is not None:
                return f"Job {job_id} restored successfully."
            return f"ERROR: Could not restore job {job_id}. Job may not exist or may not be deleted."
        except Exception as e:
            return f"ERROR: Failed to restore job {job_id}: {str(e)}"
    job_restore._full_doc_ = _FULL_DOCS["job_restore"]

    class JobContinueInput(BaseModel):
        """Input schema for job_continue tool."""
        old_job_id: Annotated[str, Field(description="Job ID of a terminal job to continue from")]
        message: Annotated[str, Field(description="New message/instruction to send to the instance")]

    @register_tool_category("job")
    @tool(args_schema=JobContinueInput)
    async def job_continue(
        old_job_id: Annotated[str, Field(description="Job ID of a terminal job to continue from")],
        message: Annotated[str, Field(description="New message/instruction to send to the instance")],
    ) -> dict:
        """Continue a completed job by sending a new message to its instance.

        Use tool_help("job_continue") for details."""
        try:
            # P-B (Phase 5, 2026-06-27): rewrite the LOOKUP half of
            # ``job_continue`` to be resolver-aware. The previous
            # implementation called ``job_service.get_job`` which is
            # JobItem-only, so a task work_id (the typical handle the
            # jober holds for continued-instance work — see plan
            # §1.3 / D14 test #9) flowed straight to the "Job not
            # found" error. The fix is to resolve ``old_job_id`` via
            # ``job_service.get_work`` (kind-agnostic), then route
            # the rest of the validation by ``record.kind``. Everything
            # AFTER the lookup (soft-delete guard, terminal check,
            # instance_status pre-check, in-flight Task pre-check,
            # ``enqueue_message``) keys on ``instance_id`` and stays
            # exactly as-is.
            record = await job_service.get_work(old_job_id)
            if record is None:
                return {"error": f"Job {old_job_id} not found"}

            # 1a. Reject soft-deleted jobs — JobItem-only guard.
            #     ``WorkRecord`` has no ``deleted_at`` field; the
            #     soft-delete concept only exists on JobItem. When
            #     ``kind == "job"`` we do a cheap ``get_job`` for
            #     the column; when ``kind != "job"`` (task / turn /
            #     report) we SKIP the check — tasks are not
            #     soft-deletable, so the check is meaningless and
            #     would also force a second lookup for nothing.
            if record.kind == "job":
                # Reviewer W3 — race guard: if ``get_work`` resolved
                # a job but the follow-up ``get_job`` returns None,
                # the row was deleted between the two calls.
                # Reject as deleted rather than fall through to
                # ``enqueue_message`` against a phantom work_id.
                old_job = await job_service.get_job(old_job_id)
                if old_job is None:
                    return {"error": f"Job {old_job_id} has been deleted and cannot be continued"}
                if old_job.deleted_at is not None:
                    return {"error": f"Job {old_job_id} has been deleted and cannot be continued"}

            # 2. Validate the work is in a terminal state.
            #    Use the canonical vocabulary via
            #    ``work_status.is_terminal`` so Task
            #    ``"running"`` (canonical "processing") and JobItem
            #    ``"processing"`` agree. Terminal set is the same
            #    as the JobItem-only ``TERMINAL_STATES`` but goes
            #    through one helper for both sides.
            from daemon.services.work_status import is_terminal as _is_terminal
            if not _is_terminal(record.status):
                return {
                    "error": (
                        f"Job {old_job_id} is not in a terminal state (current: {record.status}). "
                        "Only completed/failed/cancelled/dead_letter jobs can be continued."
                    )
                }

            # 3. Extract instance_id from the WorkRecord
            #     (present on both Task and JobItem sides).
            instance_id = record.instance_id
            if not instance_id:
                return {"error": f"Job {old_job_id} has no associated instance_id"}

            # 4. Check manager is available
            if manager is None:
                return {"error": "Instance manager not available — job_continue requires manager access"}

            # 5. Pre-check instance status (the JobQueue dispatch path
            #    silently enqueues for terminated/error/paused instances,
            #    so guard explicitly here).
            instance_meta = manager._instance_repository.get(instance_id)
            if instance_meta is None:
                return {"error": f"Instance {instance_id} not found"}
            if instance_meta.status in (
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
            ):
                return {"error": f"Instance is {instance_meta.status} — spawn a new instance instead"}
            if instance_meta.status == InstanceStatus.PAUSED.value:
                return {"error": "Instance is paused — unpause it first"}

            # 5a. Pre-check: reject if the instance has an in-flight Task. After
            #     D13 (Phase 2 of the decouple-architecture migration),
            #     messages create ``Task`` rows instead of ``JobItem``
            #     rows — the previous ``find_processing_message_jobs_by_instance``
            #     check became a no-op pass-through (it always returned []).
            #     Replaced with ``TaskRepository.has_inflight_task(instance_id)``
            #     which checks for ANY PENDING or RUNNING ``task`` row
            #     belonging to the instance.
            #
            #     PAUSED tasks are intentionally EXCLUDED by
            #     ``has_inflight_task`` — paused tasks are not actively
            #     driving the graph, so a ``job_continue`` against a paused
            #     instance is allowed to proceed (the instance is already
            #     in a quiescent state and the user is opting to enqueue
            #     more work). The companion primitive
            #     ``TaskRepository.find_paused_or_running_by_instance``
            #     does include PAUSED — it is the root-vs-child routing
            #     decision for ``resume_processing_job``, which needs to
            #     recognise paused state to fire checkpoint resume.
            #
            #     The check is sync (TaskRepository.has_inflight_task is a
            #     pure DB query); wrap in asyncio.to_thread so the event
            #     loop isn't blocked.
            if manager._task_repo is not None:
                has_inflight = await asyncio.to_thread(
                    manager._task_repo.has_inflight_task, instance_id
                )
                if has_inflight:
                    return {"error": f"Instance {instance_id} has a task still in flight — wait for it to complete first"}

            # 6. Send message via the inline message-Job path (Phase 5 cutover) — creates
            # a JobItem mirror alongside the Task row so ``new_job_id`` below
            # is a real JobItem. The legacy flag-checked dispatcher and the
            # Task-only fallback were removed in Phase 5.
            result = await manager.enqueue_message_job(
                instance_id=instance_id,
                message=message,
                source=f"agent:{caller_agent_id}" if caller_agent_id else "api",
            )

            # 7. Return new job_id (provided by AsyncMessageResult)
            return {
                "old_job_id": old_job_id,
                "instance_id": instance_id,
                "message_id": result.message_id,
                "new_job_id": result.job_id,
                "status": result.status,
            }
        except Exception as e:
            return {"error": f"Failed to continue job: {str(e)}"}
    job_continue._full_doc_ = _FULL_DOCS["job_continue"]

    @register_tool_category("job")
    @tool
    async def queue_list(project_id: str) -> dict:
        """List all queues for a project. Use tool_help("queue_list") for details."""
        try:
            queues = await queue_mgmt_service.list_queues(project_id)
            return {
                "queues": queues,
                "count": len(queues),
            }
        except Exception as e:
            return {"error": f"Failed to list queues: {str(e)}"}
    queue_list._full_doc_ = _FULL_DOCS["queue_list"]

    @register_tool_category("job")
    @tool
    async def queue_create(
        project_id: Annotated[str, Field(description="The project ID")],
        queue_name: Annotated[str, Field(description="Unique queue name within the project")],
        queue_type: Annotated[str, Field(default="fifo", description="Queue type: 'fifo' or 'parallel'")] = "fifo",
        concurrency_limit: Annotated[int, Field(default=1, ge=1, le=20, description="Max concurrent jobs")] = 1,
        description: Annotated[str | None, Field(default=None, description="Queue description")] = None,
    ) -> str:
        """Create a new queue for a project. Use tool_help("queue_create") for details."""
        try:
            queue = await queue_mgmt_service.create_queue(
                project_id=project_id,
                queue_name=queue_name,
                queue_type=queue_type,
                concurrency_limit=concurrency_limit,
                description=description,
            )
            return f"Queue '{queue_name}' created successfully. Queue ID: {queue.queue_id}"
        except ValueError as e:
            return f"ERROR: {str(e)}"
        except Exception as e:
            return f"ERROR: Failed to create queue: {str(e)}"
    queue_create._full_doc_ = _FULL_DOCS["queue_create"]

    @register_tool_category("job")
    @tool
    async def queue_update(
        queue_id: Annotated[str, Field(description="The queue ID to update")],
        project_id: Annotated[str, Field(description="The project ID (for ownership validation)")],
        queue_name: Annotated[str | None, Field(default=None, description="New queue name")] = None,
        concurrency_limit: Annotated[int | None, Field(default=None, ge=1, le=20, description="New concurrency limit")] = None,
        is_paused: Annotated[bool | None, Field(default=None, description="Pause or resume the queue")] = None,
    ) -> str:
        """Update queue settings. Use tool_help("queue_update") for details."""
        try:
            # Build updates dict from non-None params
            updates: dict[str, Any] = {}
            if queue_name is not None:
                updates["queue_name"] = queue_name
            if concurrency_limit is not None:
                updates["concurrency_limit"] = concurrency_limit
            if is_paused is not None:
                updates["is_paused"] = is_paused

            if not updates:
                return "ERROR: No updates provided."

            # Get queue to verify it exists
            queue = await queue_mgmt_service.get_queue(project_id=project_id, queue_id=queue_id)
            if queue is None:
                return f"ERROR: Queue {queue_id} not found in project {project_id}."

            result = await queue_mgmt_service.update_queue(
                project_id=project_id,
                queue_id=queue_id,
                **updates,
            )
            if result is not None:
                return f"Queue {queue_id} updated successfully."
            return f"ERROR: Queue {queue_id} not found."
        except ValueError as e:
            return f"ERROR: {str(e)}"
        except Exception as e:
            return f"ERROR: Failed to update queue: {str(e)}"
    queue_update._full_doc_ = _FULL_DOCS["queue_update"]

    @register_tool_category("job")
    @tool
    def dlq_list(
        project_id: Annotated[str, Field(description="Filter by project ID")],
        queue_id: Annotated[str | None, Field(default=None, description="Filter by queue ID")] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=100, description="Maximum items to return")] = 50,
    ) -> dict:
        """List dead letter queue items. Use tool_help("dlq_list") for details."""
        try:
            items, total_count = dead_letter_service.list_dlq(
                project_id=project_id,
                queue_id=queue_id,
                limit=limit,
            )
            result = {
                "items": [item.to_dict() for item in items],
                "count": len(items),
                "total": total_count,
            }
            return truncate_dict_result(result, list_key="items", limit=limit)
        except Exception as e:
            return {"error": f"Failed to list DLQ items: {str(e)}"}
    dlq_list._full_doc_ = _FULL_DOCS["dlq_list"]

    @register_tool_category("job")
    @tool
    def dlq_replay(dlq_id: str) -> str:
        """Replay a job from the dead letter queue. Use tool_help("dlq_replay") for details."""
        try:
            job_item = dead_letter_service.replay_from_dlq(dlq_id)
            if job_item is not None:
                return f"DLQ entry {dlq_id} replayed successfully. Job {job_item.job_id} is now pending."
            return f"ERROR: Could not replay DLQ entry {dlq_id}."
        except Exception as e:
            return f"ERROR: Failed to replay DLQ entry {dlq_id}: {str(e)}"
    dlq_replay._full_doc_ = _FULL_DOCS["dlq_replay"]

    @register_tool_category("job")
    @tool
    async def watch_job(
        job_id: Annotated[str, Field(description="The job ID to watch")],
        events: Annotated[list[str] | None, Field(default=None, description="Specific events to watch for (default: all terminal states)")] = None,
    ) -> str:
        """Watch a job for lifecycle events. If the job is already in a terminal state, immediate notification is sent.

        Use tool_help("watch_job") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # Phase 7: the only lookup path is the resolver. Unknown work_ids
            # surface as a clean not-found rather than falling back to
            # a JobItem-direct read.
            from daemon.services.work_status import is_terminal as _is_terminal

            record: "WorkRecord | None" = await job_service.get_work(job_id)
            if record is None:
                # Phase 7: no legacy fallback. If the resolver cannot
                # resolve the work_id, the work is unknown to the
                # system — surface a clean not-found rather than
                # fall back to a JobItem-direct read.
                return f"Error: Job {job_id} not found"

            # Enforce max 50 watches per instance
            count = watcher_repo.count_watches_for_instance(current_instance_id)
            if count >= 50:
                return f"Error: Maximum watch limit (50) reached for this instance"

            # Terminal state check — includes dead_letter
            if _is_terminal(record.status):
                # Register watch first, then notify (notify_watchers sends + cleans up)
                watcher_repo.add_watch(job_id, current_instance_id, events)
                # notify_watchers in Phase 2 Batch 2 is itself
                # resolver-aware — it accepts the work_id (here
                # ``job_id``) and routes through WorkResolverService.
                # ``record.error`` carries the canonical error message
                # regardless of which table backed the row.
                await job_service.notify_watchers(
                    job_id, record.status, record.error
                )
                return f"Job {job_id[:8]}... is already {record.status}. Immediate notification sent."

            # Register watch
            watcher_repo.add_watch(job_id, current_instance_id, events)
            # Phase 5 (2026-06-23): the CorrelationManager
            # ``register_job_send`` helper is REMOVED. The DependencyBus
            # is keyed on ``source_task_id`` (Task.id, integer) but
            # ``watch_job`` watches a ``job_id`` (JobItem.job_id, string)
            # — these are different concepts with no direct mapping.
            # The bus-based re-trigger path is therefore not applicable
            # here. The JobWatcher + ``notify_watchers`` path above
            # already delivers the terminal-event notification to the
            # watching instance; no additional correlation tracking is
            # needed. The parent's job continues independently of the
            # watched job's lifecycle (this was the documented behavior
            # even under CM — the parent's job was only blocked if CM
            # saw the watched job's resolution as a child-response
            # correlation, which was a separate code path in
            # ``child_reports._process_child_completion_and_notify_parent``).
            return f"Watch registered for job {job_id[:8]}... Will notify on terminal state changes."
        except Exception as e:
            return f"Error watching job: {str(e)}"
    watch_job._full_doc_ = _FULL_DOCS["watch_job"]


    @register_tool_category("job")
    @tool
    async def unwatch_job(
        job_id: Annotated[str, Field(description="The job ID to stop watching")],
    ) -> str:
        """Stop watching a job for lifecycle events.

        Use tool_help("unwatch_job") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            removed = watcher_repo.remove_watch(job_id, current_instance_id)
            if removed:
                return f"Stopped watching job {job_id[:8]}..."
            return f"Not watching job {job_id[:8]}..."
        except Exception as e:
            return f"Error unwatching job: {str(e)}"
    unwatch_job._full_doc_ = _FULL_DOCS["unwatch_job"]


    @register_tool_category("job")
    @tool
    async def list_watched_jobs() -> str:
        """List all jobs the current instance is watching.

        Use tool_help("list_watched_jobs") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            watches = watcher_repo.get_watches_for_instance(current_instance_id)
            if not watches:
                return "No watched jobs."

            result_lines = [f"Watching {len(watches)} job(s):"]
            for w in watches:
                result_lines.append(f"  - {w.job_id[:8]}... (events: {', '.join(w.watch_events)})")
            return "\n".join(result_lines)
        except Exception as e:
            return f"Error listing watched jobs: {str(e)}"
    list_watched_jobs._full_doc_ = _FULL_DOCS["list_watched_jobs"]


    @register_tool_category("job")
    @tool
    async def watch_jobs(
        job_ids: Annotated[list[str], Field(description="List of job IDs to watch")],
        events: Annotated[list[str] | None, Field(default=None, description="Specific events to watch for (default: all terminal states)")] = None,
    ) -> str:
        """Watch multiple jobs for lifecycle events. Bulk version of watch_job.

        Use tool_help("watch_jobs") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # Enforce max 50 watches per instance
            count = watcher_repo.count_watches_for_instance(current_instance_id)
            if count + len(job_ids) > 50:
                return f"Error: Would exceed maximum watch limit (50). Currently watching {count}, trying to add {len(job_ids)}."

            from daemon.services.work_status import is_terminal as _is_terminal

            watched = []
            already_terminal = []

            for jid in job_ids:
                # Phase 7: resolver is the only lookup path. Unknown
                # work_ids are skipped silently on the bulk path — see
                # the no-resolver fallback comment below for the
                # rationale.
                record: "WorkRecord | None" = await job_service.get_work(jid)

                if record is None:
                    # Phase 7: no legacy fallback. Skip silently on
                    # the bulk path — ``watch_job`` (single-job) tool
                    # surfaces "Error: Job … not found" to the agent,
                    # but on the bulk path the caller already passed
                    # in a list and the absence of any matched job is
                    # communicated via the empty ``watched`` /
                    # ``already_terminal`` lists.
                    continue

                if _is_terminal(record.status):
                    # Register watch first, then notify (notify_watchers sends + cleans up)
                    watcher_repo.add_watch(jid, current_instance_id, events)
                    await job_service.notify_watchers(
                        jid, record.status, record.error
                    )
                    already_terminal.append(jid)
                else:
                    watcher_repo.add_watch(jid, current_instance_id, events)
                    watched.append(jid)

            parts = []
            if watched:
                parts.append(f"Registered watches for {len(watched)} job(s).")
            if already_terminal:
                parts.append(f"{len(already_terminal)} job(s) already terminal — immediate notification sent.")
            return " ".join(parts) if parts else "No valid jobs found."
        except Exception as e:
            return f"Error watching jobs: {str(e)}"
    watch_jobs._full_doc_ = _FULL_DOCS["watch_jobs"]

    return [
        job_create, job_get, job_list, job_cancel, job_retry,
        job_delete, job_restore, queue_list, queue_create,
        queue_update, dlq_list, dlq_replay,
        job_continue,   # moved to end of non-watch tools (was index 7)
        watch_job, unwatch_job, list_watched_jobs, watch_jobs,
    ]


__all__ = ["create_job_tools", "TERMINAL_STATES"]
