"""Job queue management tools for LangGraph agents."""

import asyncio
import logging
import uuid
from datetime import datetime, UTC
from typing import Annotated, Any, Optional, TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from ._truncate import truncate_dict_result
from daemon import constants
from daemon.constants import INJECTION_ELIGIBLE_STATUSES
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.services.instance_messaging import _resolve_wc_wake_enqueue_enabled
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

logger = logging.getLogger(__name__)


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

Note (revive-once guard — W1): a ``job_continue`` whose target instance
status is FAILED counts as an agent-tool-initiated revival and is bound
by the manager's revive-once guard (quick-win #7). The first
FAILED-continue of an instance is granted and increments the per-child
counter; the SECOND FAILED-continue is refused with the same wording as
``send_message``'s terminal-revive refusal ("Refused: Instance '<id>'
has already been revived once and failed again. Spawn a replacement
instance instead."). The COMPLETED-continue path is DELIBERATELY
EXCLUDED — it is the designed give-more-work continue flow on a
successful child, not a failure revive, so it neither increments nor
is blocked by the guard.

Example:
    job_continue(
        old_job_id="job_abc123",
        message="Now add unit tests for the login flow"
    )""",

    "job_messages": """Get conversation messages for a job's instance tree.

Collects messages from the root instance and all descendants spawned by
the job, reading from LangGraph checkpoints. Messages include role,
content snippet (first 200 chars), and tool call names (arguments
truncated to 100 chars for safety).

Security: tool_call arguments are truncated and outputs are omitted
to prevent leakage of secrets, file contents, or credentials.

Access control: the caller's project_id must match the job's
project_id (when both are set); system-default (unscoped-or-root) callers
act as global operators and may access jobs in any project.

Args:
    job_id: The job ID to inspect. Required.
    limit: Max messages to return (default 50, max 200).
    offset: Pagination offset (default 0).

Returns:
    Dictionary with job_id, root_instance, child_instances,
    messages list, total_messages, and pagination metadata.
    Returns {"error": "..."} on failure.

Example:
    job_messages(job_id="job_abc123", limit=20)""",

    "job_tree": """Get the instance hierarchy tree for a job.

Shows all instances spawned by the job in a nested tree structure.
Each node has instance_id, agent_id, agent_name, status, and children.
Counts total and active (non-terminal) instances.

Terminal statuses: completed, terminated, error, failed.

Access control: the caller's project_id must match the job's
project_id (when both are set); system-default (unscoped-or-root) callers
act as global operators and may access jobs in any project.

Args:
    job_id: The job ID to inspect. Required.

Returns:
    Dictionary with job_id, tree (nested dict), total_instances,
    active_instances, and truncated flag.
    Returns {"error": "..."} on failure.

Example:
    job_tree(job_id="job_abc123")""",

    "job_progress": """Get a progress snapshot for a running job.

Pulls the current state of a job's instance: status, elapsed time
since creation, last assistant message (truncated to 200 chars), and
instance tree counts (total, active, completed).

Active = not in terminal status (completed, terminated, error, failed).

Access control: the caller's project_id must match the job's
project_id (when both are set); system-default (unscoped-or-root) callers
act as global operators and may access jobs in any project.

Args:
    job_id: The job ID to check. Required.

Returns:
    Dictionary with job_id, status, elapsed_seconds,
    last_assistant_message, and instance_tree.
    Returns {"error": "..."} on failure.

Example:
    job_progress(job_id="job_abc123")""",

    "job_inject": """Inject a message into a RUNNING job's instance mid-execution, or
queue a durable wake turn for a WAITING_CHILDREN target (under the
``ENSEMBLE_WC_WAKE_ENQUEUE`` flag-ON routing pivot — wc-wake-report-
integrity, LOCKED C1-D3 Option A, 2026-08-30).

Routing (split):
  * ``RUNNING`` → RAM FIFO injection via
    ``InstanceManager.set_injection(...)`` (byte-identical to pre-
    wc-wake behavior). The ``agent_node`` consumes the entry on its
    next LLM call and threads it into the conversation as a fresh
    ``HumanMessage``. Returns ``{status: \"injected\", pending_count,
    content, timestamp}``. Status flag (``injection_pending`` SSE) is
    unchanged.

  * ``WAITING_CHILDREN`` → depends on the
    ``ENSEMBLE_WC_WAKE_ENQUEUE`` kill-switch (``decisions.md`` C1-Q2):
      - **Flag OFF (default — legacy FIFO injection):** same as
        ``RUNNING`` above — ``set_injection`` is called, returns
        ``{status: \"injected\", pending_count, ...}``. The message
        sits in the parked parent's FIFO until the next ``agent_node``
        pass when a child report wakes the parent. This is the
        documented revert path; an operator can flip the flag and
        restart to switch to the new behavior.
      - **Flag ON (post-flip):** durable wake enqueue via
        ``manager.enqueue_message(source=f\"internal_agent:{caller}\")``
        — durable ``MessageQueue`` row + ``Task``, WC→RUNNING flip,
        real wake, first-class turn. Returns
        ``{job_id, instance_id, status: \"enqueued\", message_id,
        queued: True}``. A ``has_instance_busy`` pre-check (mirrors
        ``job_continue`` 5a, :975-995) makes a WC target that
        already has a queued wake fail fast with a clean error
        instead of silently queueing a second turn. No
        ``injection_pending`` SSE under this path (the FE sees the
        message via the normal turn-start ``user_message`` pre-emit).

  * ``IDLE`` / ``PAUSED`` / terminal → error: use ``job_continue``
    instead (it handles wake + revive + Task creation for those
    statuses). The error text identifies the actual instance status
    and points the agent to ``job_continue``.

Eligibility (matches the routing above): RUNNING is always accepted;
WC is accepted via the flag branch; other statuses are rejected with
the eligibility error.

Unlike ``job_continue`` (which creates a new Task and requires the
instance to be IDLE/terminal), ``job_inject`` piggybacks on the
existing turn for RUNNING targets — it does NOT spawn a new job, does
NOT interrupt tool execution, and does NOT race with the active
``enqueue_message_job`` path. Under flag ON for WC, ``job_inject``
moves to ``enqueue_message`` and DOES create a new first-class turn
(durable wake) — the same primitive the agent-tool send_message uses.

Return shape (m2 fix, LOCKED C1-D3 Option A, 2026-08-30): the
``queued`` flag on the flag-ON WC branch is a LITERAL ``True``,
meaning "message was enqueued as a first-class turn" — NOT the
``AsyncMessageResult.queued`` capacity flag (a spec collision:
``AsyncMessageResult.queued`` means "blocked at capacity" and
defaults to ``False``). Mirror the HTTP lane's 200-enqueue
``MessageResponse.queued=True`` on success. The ``getattr(result,
"queued", True)`` propagation that pre-m2 carried the
AsyncMessageResult field through to the tool response was a
silent-spec-collision defect; the literal ``True`` matches the
LOCKED decisions.md C1-D3 contract.

Access control: the caller's project_id must match the job's
project_id (when both are set); system-default (unscoped-or-root) callers
act as global operators and may access jobs in any project.

Args:
    job_id: The job ID whose instance will receive the injection. Required.
    message: Text to inject into the live turn. Required.

Returns:
    Dictionary with shape depending on routing branch:
      * ``{job_id, instance_id, status: \"injected\", pending_count,
        content, timestamp}`` — RUNNING (always) and WAITING_CHILDREN
        under flag OFF.
      * ``{job_id, instance_id, status: \"enqueued\", message_id,
        queued: True}`` — WAITING_CHILDREN under flag ON.
      * ``{error: \"...\"}`` on eligibility rejection, busy pre-check,
        or any other failure.

Example:
    job_inject(job_id=\"job_abc123\", message=\"Also remember to add tests\")""",
}


def _check_job_access(
    manager: "InstanceManager | None",
    current_instance_id: str,
    record: "WorkRecord",
) -> Optional[dict]:
    """Project-scoped C2 access check for job-visibility tools.

    Returns ``None`` when access is allowed, or a ``{"error": ...}`` dict
    that the caller should return verbatim when access is denied.

    Semantics:
      * No ``current_instance_id`` (caller is anonymous) → allowed.
      * ``record.project_id`` is None (legacy/unscoped job) → allowed.
      * Caller instance not found in the repo → allowed (fail-open,
        matches the pre-extraction behaviour).
      * Caller's ``project_id`` is None → allowed (fail-open).
      * Caller's ``project_id == constants.SYSTEM_DEFAULT_PROJECT_ID``
        → allowed (the "global operator" tier — chat-facing agents
        such as Ari/Jober run in the system-default project and need
        cross-project visibility to manage jobs in any project).
      * Otherwise: caller and job must share the same project_id;
        mismatch → access denied.

    The system-default constant is read via ``constants.SYSTEM_DEFAULT_PROJECT_ID``
    at call time so that the repo-wide autouse fixture that patches the
    module attribute (``tests/conftest.py:_ensure_system_default_project_id``
    at ``tests/conftest.py:706-734``) takes effect. Reading the constant
    via ``from daemon.constants import …`` at module load would bind the
    pre-patch value and silently bypass the global-operator tier in tests.
    This file used to ship a redundant local autouse fixture that
    duplicated the conftest patch; it was removed because the conftest
    fixture already covers every test in the suite.

    Pre-bootstrap guard: if ``constants.SYSTEM_DEFAULT_PROJECT_ID`` is
    ``None`` (startup not yet complete), the helper falls back to the
    legacy strict-match behaviour — system-default callers are denied
    rather than given free reign. Production never hits this path:
    ``ensure_system_default_project`` runs in the API lifespan startup
    before any tool call can land.

    System-default membership is granted to (1) any instance created
    without an explicit project (all of ``None``, ``""``, ``"null"``,
    ``"none"`` are normalized via ``project_normalizer.normalize_project_id``
    to the system-default UUID at spawn time) AND (2) ALL legacy
    ``project_id IS NULL`` instance rows backfilled to the system-default
    project at API lifespan startup (``daemon/api.py:512-528`` runs an
    idempotent ``backfill_system_default_project_id`` sweep on every
    boot). Consequence: system-default membership is the global-operator
    tier — it grants cross-project access to all four visibility tools
    (``job_messages``, ``job_tree``, ``job_progress``, ``job_inject``),
    including the ``job_inject`` write primitive. This is intentional
    and user-approved design: project-based gating, not agent-based — the
    four visibility tools are chat-facing primitives that Ari/Jober and
    other ops-tier agents use to manage jobs in any project, and the
    alternative (per-agent allow-list) was rejected as a scaling hazard
    for the orchestrator fleet.
    """
    if not (current_instance_id and record.project_id):
        return None

    if manager is None:
        # Defense-in-depth: unreachable at all 4 current call sites
        # (each performs ``if manager is None: return None`` before
        # invoking this helper). Kept so a future unguarded call site
        # can't accidentally reintroduce the pre-helper C2 regression.
        return None

    caller = manager._instance_repository.get(current_instance_id)
    if caller is None or caller.project_id is None:
        return None

    # Read at call time so test monkeypatching of the module attribute wins.
    system_default = constants.SYSTEM_DEFAULT_PROJECT_ID
    if system_default is not None and caller.project_id == system_default:
        return None

    if caller.project_id != record.project_id:
        logger.warning(
            "job access denied: caller=%s caller_project=%s job=%s job_project=%s",
            current_instance_id, caller.project_id, getattr(record, "work_id", None), record.project_id,
        )
        return {"error": "Access denied: job does not belong to caller's project"}

    return None


def create_job_tools(
    job_service: "JobQueueService",
    queue_mgmt_service: "JobQueueMgmtService",
    dead_letter_service: "DeadLetterService",
    current_instance_id: str = "",
    agent_id: str = "",
    watcher_repo: "JobWatcherRepository | None" = None,
    manager: "InstanceManager | None" = None,
    agent_tag: str | None = None,
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
        agent_tag: Optional caller's version tag (e.g., ``"v2"``) — threaded into
            ``job_create`` so the enqueued job resolves to the correct versioned
            ``agent_dir`` instead of the base. Forwarded from
            ``create_instance_tools(version_tag=...)`` via
            ``create_job_tools_if_available``.

    Returns:
        List of tool functions for job queue management.
    """
    caller_agent_id = agent_id
    # F2: capture the caller's version tag so ``job_create`` (agent-facing)
    # threads it into ``job_service.enqueue(agent_tag=...)``. When None
    # (e.g. non-versioned callers), the enqueue falls back to base resolution.
    caller_agent_tag = agent_tag

    class JobCreateInput(BaseModel):
        """Input schema for job_create tool."""
        agent_id: Annotated[str, Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")]
        message: Annotated[str, Field(description="The instruction/message for the agent")]
        project_id: Annotated[str | None, Field(default=None, description="Project ID for isolation and routing")]
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10 (1=lowest, 10=highest)")]
        queue_id: Annotated[str | None, Field(default=None, description="Specific queue to submit to")]
        idempotency_key: Annotated[str | None, Field(default=None, description="Deduplication key")]
        metadata: Annotated[dict[str, Any] | None, Field(default=None, description="Custom key-value metadata")]
        source: Annotated[str, Field(default="api", description="DEPRECATED and IGNORED (NIT-7, P2.3 review cycle 1): the server derives source UNCONDITIONALLY since B3.5 (agent:<caller> for agent callers, internal_agent:unknown otherwise) — any value passed here has no effect. Param retained purely for schema compat; removal deferred.")]
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
        source: Annotated[str, Field(default="api", description="DEPRECATED and IGNORED: server derives source unconditionally (B3.5); retained for schema compat, removal deferred")] = "api",
        watch: Annotated[bool, Field(default=False, description="Watch the job for lifecycle events")] = False,
    ) -> dict:
        """Submit a new job to the queue. Use tool_help("job_create") for details."""
        try:
            # MAJOR-1(a) (P2.2 fix pass 2026-08-23) + MINOR-B (P2.2
            # carry-over, closed P2.3 B3.5): the source derivation is
            # server-side-UNCONDITIONAL — NO caller-supplied ``source`` is
            # ever trusted verbatim on this path. Agent callers →
            # ``agent:<caller>`` (a hostile source="telegram:attacker"
            # must never thread through dispatch to the user-origin
            # whitelist stamp — manager.stamp_user_origin_window /
            # USER_ORIGIN_SOURCES — that would forge factor 2 of the live
            # 3-factor gate with zero human involvement). Empty caller →
            # ``internal_agent:unknown`` (F3, mirrors job_continue below):
            # NEVER the default "api" — "api" is whitelisted, and the
            # genuine web-UI path keeps its server-stamped value on the
            # HTTP router (jobs_crud.py), not here.
            source = (
                f"agent:{caller_agent_id}"
                if caller_agent_id
                else "internal_agent:unknown"
            )
            normalized_project_id = normalize_project_id(project_id)

            # Pre-generate job_id so we can register the watcher BEFORE
            # dispatching. This closes the TOCTOU window where a fast job
            # could complete between enqueue() and add_watch(), causing the
            # watcher to miss the terminal [JOB_EVENT] notification.
            pre_generated_job_id = str(uuid.uuid4())

            # Register watch BEFORE enqueue (if requested). If the watch
            # limit is hit, we return early without creating the job at all.
            if watch and watcher_repo is not None and current_instance_id:
                count = watcher_repo.count_watches_for_instance(current_instance_id)
                if count >= 50:
                    return {
                        "error": "Maximum watch limit (50) reached for this instance. Job was not created.",
                    }
                watcher_repo.add_watch(pre_generated_job_id, current_instance_id)

            job_item = await job_service.enqueue(
                agent_id=agent_id,
                message=message,
                source=source,
                project_id=normalized_project_id,
                priority=priority,
                metadata=metadata,
                queue_id=queue_id,
                idempotency_key=idempotency_key,
                # F2: thread the caller's version tag so a versioned agent
                # (e.g. ``reviewer[v2]``) creating a job targets the same
                # versioned ``agent_dir`` instead of the base. Falls back to
                # base resolution when None (non-versioned caller).
                agent_tag=caller_agent_tag,
                # Pass the pre-generated job_id so the watch registered above
                # matches the actual job in the queue.
                job_id=pre_generated_job_id,
            )

            # Idempotency: enqueue() may return an existing job with a
            # different job_id than our pre-generated one (dedup hit). In
            # that case, re-register the watch against the actual job_id
            # and clean up the stale pre-generated watch (best-effort).
            if watch and watcher_repo is not None and current_instance_id:
                if job_item.job_id != pre_generated_job_id:
                    watcher_repo.add_watch(job_item.job_id, current_instance_id)
                    try:
                        watcher_repo.remove_watch(pre_generated_job_id, current_instance_id)
                    except Exception:
                        pass  # best-effort cleanup; stale row is harmless

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

            # 5a. Pre-check: reject if the instance has any live Task. After
            #     D13 (Phase 2 of the decouple-architecture migration),
            #     messages create ``Task`` rows instead of ``JobItem``
            #     rows — the previous ``find_processing_message_jobs_by_instance``
            #     check became a no-op pass-through (it always returned []).
            #     Replaced with ``TaskRepository.has_instance_busy(instance_id)``
            #     which checks for ANY PENDING, RUNNING, or PAUSED
            #     ``task`` row belonging to the instance — the canonical
            #     "is this instance busy?" predicate.
            #
            #     Bug-1 fix (2026-08-12): the prior ``has_inflight_task``
            #     gate was PENDING + RUNNING only. A PAUSED task was
            #     treated as "not busy" and ``job_continue`` was allowed
            #     to enqueue a follow-up message against a paused
            #     instance — a concurrency leak: the user has explicitly
            #     paused the instance, so the live Task still owns the
            #     per-instance serialization slot, and a follow-up
            #     enqueue would race the resume. ``has_instance_busy``
            #     widens the status set to PENDING + RUNNING + PAUSED
            #     so a paused instance is correctly recognised as
            #     busy. Sister primitive to the
            #     ``TaskRepository.find_paused_or_running_by_instance``
            #     selector used by ``resume_processing_job`` — both
            #     include PAUSED for the same reason (paused work is
            #     live work).
            #
            #     The check is sync (TaskRepository.has_instance_busy is a
            #     pure DB query); wrap in asyncio.to_thread so the event
            #     loop isn't blocked.
            if getattr(manager, "_task_repo", None) is not None:
                has_inflight = await asyncio.to_thread(
                    manager._task_repo.has_instance_busy, instance_id
                )
                if has_inflight:
                    return {"error": f"Instance {instance_id} has a task still in flight — wait for it to complete first"}

            # 5b. Revive-once guard (W1, FAILED branch only).
            #    ``RECOVERY_GUIDANCE_HINT`` (daemon/services/error_reporting.py)
            #    bounds child revives to AT MOST ONE, then
            #    spawn-a-replacement — previously LLM-enforced only. The
            #    COMPLETED branch of ``job_continue`` is DELIBERATELY
            #    EXCLUDED — it is the designed give-more-work continue
            #    flow on a successful child, not a failure revive — so
            #    it neither increments nor is blocked by the guard. ONLY
            #    the FAILED branch counts against the once-bound: the
            #    FIRST FAILED-continue of an instance is granted (counter
            #    0→1, message dispatched); the SECOND is refused with
            #    the same wording as ``send_message``'s terminal-revive
            #    refusal, mirroring ``RECOVERY_GUIDANCE_HINT`` semantics.
            #    The refusal returns BEFORE ``enqueue_message_job`` so a
            #    refused continue dispatches NOTHING; the counter
            #    increment sits AFTER ``enqueue_message_job`` deliberately
            #    (matches the W2/Polish#1 convention in
            #    ``daemon/tools/instance.py``) — a transient enqueue
            #    failure leaves the child eligible for a future attempt.
            #    Sits AFTER the in-flight Task gate deliberately: a
            #    busy-queue rejection must not consume the child's
            #    revive budget (no revive happened).

            # 5c. Revive-once guard — REFUSAL CHECK (W1, FAILED branch only).
            #     Companion to the 5b comment + the 6b increment below.
            #     Sits AFTER the in-flight Task gate deliberately (a
            #     busy-queue rejection must not consume the revive budget
            #     — no revive happened) and BEFORE ``enqueue_message_job``
            #     (the refused continue dispatches NOTHING — same shape
            #     as ``send_message``'s terminal-revive refusal).
            #     COMPLETED-continue never reaches this check: the
            #     give-more-work flow is excluded from the guard. The
            #     refusal wording is the same string
            #     ``daemon/tools/instance.py`` returns on its second
            #     agent-tool revive attempt — locked at the spec level so
            #     the agent-facing guidance is identical across paths.
            if instance_meta.status == InstanceStatus.FAILED.value:
                if manager.get_agent_tool_revive_count(instance_id) >= 1:
                    return {
                        "error": (
                            f"Refused: Instance '{instance_id}' has already "
                            f"been revived once and failed again. Spawn a "
                            f"replacement instance instead."
                        )
                    }

            # 6. Send message via the inline message-Job path (Phase 5 cutover) — creates
            # a JobItem mirror alongside the Task row so ``new_job_id`` below
            # is a real JobItem. The legacy flag-checked dispatcher and the
            # Task-only fallback were removed in Phase 5.
            result = await manager.enqueue_message_job(
                instance_id=instance_id,
                message=message,
                # F3 (P2.2 fix pass): never mint a whitelisted source on the
                # empty-caller fallback — "api" is in USER_ORIGIN_SOURCES.
                source=f"agent:{caller_agent_id}" if caller_agent_id else "internal_agent:unknown",
            )

            # 6b. Counter increment AFTER successful enqueue_message_job
            #     (W1, ordering convention from W2/Polish#1 in
            #     ``daemon/tools/instance.py``) — the FAILED-branch
            #     revive grant is only consumed when the dispatch has
            #     actually happened. COMPLETED-continue never reaches
            #     this increment; it is the excluded give-more-work
            #     flow. A transient ``enqueue_message_job`` exception
            #     above leaves the child eligible for a future attempt.
            if instance_meta.status == InstanceStatus.FAILED.value:
                manager.note_agent_tool_revive(instance_id)

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

    async def _enrich_terminal_record(record):
        """Fetch ``result_summary``/``error`` from the instance when the
        WorkRecord has ``None`` for them.

        Context: the WorkResolver at
        ``daemon/services/work_resolver.py`` deliberately returns
        ``result_summary=None`` and ``error=None`` for ``kind="job"``
        records (Phase 5 dropped the JobItem mirror columns). The
        natural-completion path in ``job_feedback_observer`` works
        around this by fetching the actual content from the instance
        via ``manager._get_last_assistant_message_raw`` before calling
        ``notify_watchers``. ``watch_job``/``watch_jobs`` need the
        same enrichment so the ``[JOB_EVENT]`` notification has a
        populated ``Result:`` block when the caller watches an
        already-terminal JobItem.

        Best-effort: any failure (manager not wired, instance missing,
        fetch exception) leaves the record untouched — the
        notification still goes out, just with ``result_summary=None``
        which degrades to no ``Result:`` block (matches prior
        behavior).
        """
        if manager is None or not getattr(record, "instance_id", None):
            return record
        needs_result = (
            getattr(record, "result_summary", None) is None
            and getattr(record, "status", None) == "completed"
        )
        needs_error = (
            getattr(record, "error", None) is None
            and getattr(record, "status", None) in {"failed", "dead_letter"}
        )
        if not needs_result and not needs_error:
            return record
        try:
            # 2026-08-11: terminal enrichment path (caller already
            # determined the record is terminal or errored — see
            # ``needs_result`` / ``needs_error`` gates above). Leave
            # defaults (skip_repair=False, agent_id=None) so repair
            # runs; the exclusion check is bypassed because this
            # helper only has ``record.instance_id`` available.
            fetched = await manager._get_last_assistant_message_raw(
                record.instance_id
            )
        except Exception:
            # best-effort — notification still fires with whatever the
            # record already carries
            return record
        if needs_result and fetched:
            record.result_summary = fetched
        elif needs_result and record.status == "completed":
            # Match the observer's fallback so the ``Result:`` block
            # always renders a non-empty body for completed jobs whose
            # instance produced no captureable assistant message.
            record.result_summary = "Job completed (no agent response captured)"
        # ``error`` is sourced separately by the instance-status path;
        # the manager's last-assistant-message raw hook does not carry
        # the failure message, so we leave ``error`` untouched here.
        return record

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
                # Enrich from the instance before notifying. The
                # WorkResolver returns ``result_summary=None`` for
                # ``kind="job"`` records (Phase 5 dropped the JobItem
                # mirror columns); without this enrichment the
                # ``[JOB_EVENT]`` notification would be missing its
                # ``Result:`` block. Mirrors the natural-completion
                # path in ``job_feedback_observer`` — see
                # ``_enrich_terminal_record`` above.
                record = await _enrich_terminal_record(record)
                # Register watch first, then notify (notify_watchers sends + cleans up)
                watcher_repo.add_watch(job_id, current_instance_id, events)
                # notify_watchers in Phase 2 Batch 2 is itself
                # resolver-aware — it accepts the work_id (here
                # ``job_id``) and routes through WorkResolverService.
                # ``error`` and ``result_summary`` are sourced from the
                # record (now possibly enriched from the instance).
                await job_service.notify_watchers(
                    job_id,
                    record.status,
                    error=record.error,
                    result_summary=record.result_summary,
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
                    # Enrich from the instance before notifying.
                    # Same rationale as in ``watch_job`` (single-job)
                    # — see ``_enrich_terminal_record`` above. Without
                    # this the bulk-path notifications on terminal
                    # ``kind="job"`` records would be missing the
                    # ``Result:`` block.
                    record = await _enrich_terminal_record(record)
                    # Register watch first, then notify (notify_watchers sends + cleans up)
                    watcher_repo.add_watch(jid, current_instance_id, events)
                    await job_service.notify_watchers(
                        jid,
                        record.status,
                        error=record.error,
                        result_summary=record.result_summary,
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

    # --------------------------------------------------------
    # P0 Job Visibility Tools — job_messages & job_tree
    # --------------------------------------------------------
    # These tools read conversation messages and the instance
    # hierarchy spawned by a job. Data sources:
    #   - job_service.get_work(job_id) → WorkRecord (.instance_id)
    #   - manager._instance_repository (get, get_children, get_tree_ids)
    #   - manager.get_messages(instance_id) → checkpoint messages
    # There is NO event table; events are transient SSE only.

    @register_tool_category("job")
    @tool
    async def job_messages(
        job_id: Annotated[str, Field(description="Job ID to inspect")],
        limit: Annotated[int, Field(default=50, ge=1, le=200, description="Max messages to return")] = 50,
        offset: Annotated[int, Field(default=0, ge=0, description="Pagination offset")] = 0,
    ) -> dict:
        """Get conversation messages for a job's instance tree.

        Collects messages from the root instance and all descendants.
        Use tool_help("job_messages") for details."""
        try:
            record = await job_service.get_work(job_id)
            if record is None:
                return {"error": f"Job {job_id} not found"}

            instance_id = record.instance_id
            if not instance_id:
                return {"error": f"Job {job_id} has no associated instance_id"}

            if manager is None:
                return {"error": "Instance manager not available"}

            # C2: Project-scoped access control. See ``_check_job_access``
            # docstring for the system-default (global-operator) carve-out
            # used by chat-facing agents such as Ari/Jober.
            deny = _check_job_access(manager, current_instance_id, record)
            if deny is not None:
                return deny

            root_instance = manager._instance_repository.get(instance_id)
            if root_instance is None:
                return {"error": f"Instance {instance_id} not found"}

            all_instance_ids = manager._instance_repository.get_tree_ids(instance_id)

            # Safety cap: reading LangGraph checkpoints for many instances
            # can be slow. Direct the caller to job_tree for an overview.
            if len(all_instance_ids) > 20:
                return {
                    "error": (
                        f"Instance tree too large ({len(all_instance_ids)} instances) "
                        "— use job_tree for overview"
                    )
                }

            # Build an instance_id → agent_id lookup for tagging messages.
            agent_map: dict[str, str | None] = {instance_id: record.agent_id or root_instance.agent_id}
            for child_id in all_instance_ids:
                if child_id == instance_id:
                    continue
                child_inst = manager._instance_repository.get(child_id)
                agent_map[child_id] = child_inst.agent_id if child_inst else None

            # W2: Fetch messages concurrently (max 5 parallel) to avoid
            # sequential checkpoint reads.
            sem = asyncio.Semaphore(5)

            async def _fetch_messages(iid: str) -> tuple[str, list[dict]]:
                async with sem:
                    try:
                        msgs = await manager.get_messages(iid)
                    except Exception as e:
                        logger.warning(
                            "Failed to read messages for instance %s: %s: %s",
                            iid, type(e).__name__, e,
                        )
                        return iid, []
                    return iid, msgs

            fetch_results = await asyncio.gather(
                *[_fetch_messages(iid) for iid in all_instance_ids]
            )

            collected: list[dict] = []
            for iid, msgs in fetch_results:
                for msg in msgs:
                    summary: dict = {
                        "instance_id": iid,
                        "agent_id": agent_map.get(iid),
                        "role": msg.get("role", "unknown"),
                        "content_snippet": (msg.get("content") or "")[:200],
                    }
                    if msg.get("tool_calls"):
                        summary["tool_calls"] = [
                            {
                                "name": tc.get("name", "unknown"),
                                "arguments_snippet": (str(tc.get("args") or tc.get("arguments") or ""))[:100],
                            }
                            for tc in msg["tool_calls"]
                        ]
                    collected.append(summary)

            total = len(collected)
            paginated = collected[offset:offset + limit]

            child_instances = [
                {"instance_id": ci, "agent_id": agent_map.get(ci)}
                for ci in all_instance_ids
                if ci != instance_id
            ]

            return {
                "job_id": job_id,
                "root_instance": {
                    "instance_id": instance_id,
                    "agent_id": record.agent_id or root_instance.agent_id,
                },
                "child_instances": child_instances,
                "messages": paginated,
                "total_messages": total,
                "returned_count": len(paginated),
                "has_more": (offset + len(paginated)) < total,
                "next_offset": (offset + len(paginated)) if (offset + len(paginated)) < total else None,
            }
        except Exception as e:
            logger.error("Failed to get job messages for %s: %s", job_id, e, exc_info=True)
            return {"error": "Internal error reading job messages"}

    job_messages._full_doc_ = _FULL_DOCS["job_messages"]

    @register_tool_category("job")
    @tool
    async def job_tree(
        job_id: Annotated[str, Field(description="Job ID to inspect")],
    ) -> dict:
        """Get the instance hierarchy tree for a job.

        Shows all instances spawned by the job in a tree structure.
        Use tool_help("job_tree") for details."""
        try:
            record = await job_service.get_work(job_id)
            if record is None:
                return {"error": f"Job {job_id} not found"}

            instance_id = record.instance_id
            if not instance_id:
                return {"error": f"Job {job_id} has no associated instance_id"}

            if manager is None:
                return {"error": "Instance manager not available"}

            # C2: Project-scoped access control. See ``_check_job_access``
            # docstring for the system-default (global-operator) carve-out
            # used by chat-facing agents such as Ari/Jober.
            deny = _check_job_access(manager, current_instance_id, record)
            if deny is not None:
                return deny

            root = manager._instance_repository.get(instance_id)
            if root is None:
                return {"error": f"Instance {instance_id} not found"}

            terminal_statuses = {
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            }

            # W1: Use get_tree_ids() BFS (batched, depth-limited to 256)
            # instead of recursive get_children() to avoid N+1 queries.
            all_ids = manager._instance_repository.get_tree_ids(instance_id)

            MAX_TREE_NODES = 200
            truncated = len(all_ids) > MAX_TREE_NODES

            # Bulk-load all instances in one query pass (avoid N+1).
            # Build a flat dict of instance_id -> Instance.
            instance_map: dict[str, Any] = {}
            for iid in all_ids:
                inst = manager._instance_repository.get(iid)
                if inst is not None:
                    instance_map[iid] = inst

            # Build parent -> [children] lookup from instances.parent_id.
            children_map: dict[str, list] = {}
            for iid, inst in instance_map.items():
                pid = inst.parent_id
                if pid and pid in instance_map:
                    if pid not in children_map:
                        children_map[pid] = []
                    children_map[pid].append(inst)

            # Build tree recursively from the flat maps (no DB queries here).
            seen: set[str] = set()
            def _build_node(iid: str) -> dict:
                if iid in seen:
                    logger.warning("Circular reference detected: instance %s already visited in tree", iid)
                    return {"instance_id": iid, "_cycle": True}
                seen.add(iid)
                inst = instance_map.get(iid)
                if inst is None:
                    return {"instance_id": iid}
                node = {
                    "instance_id": inst.instance_id,
                    "agent_id": inst.agent_id,
                    "agent_name": inst.agent_name,
                    "status": inst.status,
                    "children": [],
                }
                for child in children_map.get(iid, []):
                    node["children"].append(_build_node(child.instance_id))
                return node

            tree_node = _build_node(instance_id)

            # Count total and active (non-terminal) instances across the tree.
            def _count(node: dict) -> tuple[int, int]:
                # Cycle nodes have no "status" key — don't count them as active.
                if node.get("_cycle"):
                    return 0, 0
                total = 1
                active = 0 if node.get("status") in terminal_statuses else 1
                for child in node.get("children", []):
                    t, a = _count(child)
                    total += t
                    active += a
                return total, active

            total_count, active_count = _count(tree_node)

            return {
                "job_id": job_id,
                "tree": tree_node,
                "total_instances": total_count,
                "active_instances": active_count,
                "truncated": truncated,
            }
        except Exception as e:
            logger.error("Failed to get job tree for %s: %s", job_id, e, exc_info=True)
            return {"error": "Internal error reading job tree"}

    job_tree._full_doc_ = _FULL_DOCS["job_tree"]

    @register_tool_category("job")
    @tool
    async def job_progress(
        job_id: Annotated[str, Field(description="Job ID to check progress for")],
    ) -> dict:
        """Get a progress snapshot for a running job.

        Use tool_help("job_progress") for details."""
        try:
            record = await job_service.get_work(job_id)
            if record is None:
                return {"error": f"Job {job_id} not found"}

            instance_id = record.instance_id
            if not instance_id:
                return {"error": f"Job {job_id} has no associated instance_id"}

            if manager is None:
                return {"error": "Instance manager not available"}

            # C2: Project-scoped access control. See ``_check_job_access``
            # docstring for the system-default (global-operator) carve-out
            # used by chat-facing agents such as Ari/Jober.
            deny = _check_job_access(manager, current_instance_id, record)
            if deny is not None:
                return deny

            root_instance = manager._instance_repository.get(instance_id)
            if root_instance is None:
                return {"error": f"Instance {instance_id} not found"}

            # Compute elapsed time since the root instance was created.
            # ``created_at`` is an ISO string that may be tz-naive; assume
            # UTC when no tz info is present (matches daemon clock).
            created_raw = root_instance.created_at
            if created_raw is None:
                elapsed_seconds = 0.0
            else:
                try:
                    created = datetime.fromisoformat(created_raw)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=UTC)
                    elapsed_seconds = (datetime.now(UTC) - created).total_seconds()
                except Exception as e:
                    logger.warning(
                        "Failed to parse created_at %r for instance %s: %s: %s",
                        created_raw, instance_id, type(e).__name__, e,
                    )
                    elapsed_seconds = 0.0

            # Fetch the root instance's messages and extract the most recent
            # assistant message. Mirrors the safety pattern in job_messages
            # (try/except → empty list on failure so callers degrade gracefully).
            try:
                root_messages = await manager.get_messages(instance_id)
            except Exception as e:
                logger.warning(
                    "Failed to read messages for instance %s: %s: %s",
                    instance_id, type(e).__name__, e,
                )
                root_messages = []

            last_assistant: dict | None = None
            for msg in root_messages:
                if msg.get("role") == "assistant":
                    last_assistant = msg
            last_assistant_payload: dict | None = None
            if last_assistant is not None:
                last_assistant_payload = {
                    "content_snippet": (last_assistant.get("content") or "")[:200],
                    "timestamp": last_assistant.get("created_at"),
                }

            # Walk the instance tree once to count active vs completed.
            # Reuse the same terminal-status set as job_tree for consistency.
            terminal_statuses = {
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            }

            all_ids = manager._instance_repository.get_tree_ids(instance_id)
            active_count = 0
            completed_count = 0
            for iid in all_ids:
                inst = manager._instance_repository.get(iid)
                if inst is None:
                    continue
                if inst.status in terminal_statuses:
                    completed_count += 1
                else:
                    active_count += 1

            return {
                "job_id": job_id,
                "status": root_instance.status,
                "elapsed_seconds": round(elapsed_seconds, 1),
                "last_assistant_message": last_assistant_payload,
                "instance_tree": {
                    "total_instances": len(all_ids),
                    "active_instances": active_count,
                    "completed_instances": completed_count,
                },
            }
        except Exception as e:
            logger.error("Failed to get job progress for %s: %s", job_id, e, exc_info=True)
            return {"error": "Internal error reading job progress"}

    job_progress._full_doc_ = _FULL_DOCS["job_progress"]

    @register_tool_category("job")
    @tool
    async def job_inject(
        job_id: Annotated[str, Field(description="Job ID whose instance will receive the injection")],
        message: Annotated[str, Field(description="Text to inject into the live turn")],
    ) -> dict:
        """Inject a message into a RUNNING job's instance mid-execution.

        Use tool_help("job_inject") for details."""
        try:
            record = await job_service.get_work(job_id)
            if record is None:
                return {"error": f"Job {job_id} not found"}

            instance_id = record.instance_id
            if not instance_id:
                return {"error": f"Job {job_id} has no associated instance_id"}

            if manager is None:
                return {"error": "Instance manager not available"}

            # Access control: project-scoped check (same as job_messages).
            # See ``_check_job_access`` docstring for the system-default
            # (global-operator) carve-out used by chat-facing agents.
            deny = _check_job_access(manager, current_instance_id, record)
            if deny is not None:
                return deny

            instance_meta = manager._instance_repository.get(instance_id)
            if instance_meta is None:
                return {"error": f"Instance {instance_id} not found"}

            # wc-wake-report-integrity (T7 + C1-Q2): the eligibility
            # check accepts RUNNING (always) AND WAITING_CHILDREN
            # (both flag states — legacy FIFO under flag OFF,
            # ``enqueue_message`` under flag ON per LOCKED C1-D3
            # Option A). Other statuses (IDLE, PAUSED, terminal)
            # still hit the error path with the rewritten wording.
            # The constant ``INJECTION_ELIGIBLE_STATUSES`` was shrunk
            # to ``{\"running\"}`` in T2 — the constant stays single-home
            # and config-free; the WC acceptance is an explicit branch
            # at the call site, mirroring the HTTP / agent-tool lanes
            # per the dispatch directive.
            current_status = instance_meta.status
            if (
                current_status not in INJECTION_ELIGIBLE_STATUSES
                and current_status != "waiting_children"
            ):
                # D3 (2026-08-30 pre-flip batch): the error TEXT branches on
                # the kill-switch. Flag OFF shows the byte-faithful legacy
                # string from 1f8f8ed4 — OFF is the instant-revert path, so
                # the revert contract is byte-compatible, not just behavioral
                # (the eligibility CONDITION above is identical in both
                # states). Flag ON shows the routing-pivot wording.
                if _resolve_wc_wake_enqueue_enabled():
                    return {
                        "error": (
                            f"Instance is {instance_meta.status} — job_inject "
                            "injects into RUNNING turns; WAITING_CHILDREN/IDLE/"
                            "terminal targets get the message enqueued (WC under "
                            "the flag-ON routing pivot) or should use job_continue. "
                            "Use job_continue for IDLE/PAUSED/terminal instances."
                        )
                    }
                return {
                    "error": (
                        f"Instance is {instance_meta.status} — job_inject "
                        "only works on RUNNING or WAITING_CHILDREN instances. "
                        "Use job_continue for IDLE/terminal instances."
                    )
                }

            # Flag-ON WAITING_CHILDREN branch: durable wake turn via
            # ``manager.enqueue_message`` (LOCKED C1-D3 Option A). The
            # ``has_instance_busy`` pre-check (mirrors ``job_continue``
            # 5a, :975-995) makes a WC target that already has a queued
            # wake fail fast with a clean error instead of silently
            # queueing a second turn.
            if current_status == "waiting_children" and _resolve_wc_wake_enqueue_enabled():
                if getattr(manager, "_task_repo", None) is not None:
                    has_inflight = await asyncio.to_thread(
                        manager._task_repo.has_instance_busy, instance_id
                    )
                    if has_inflight:
                        return {
                            "error": (
                                f"Instance {instance_id} has a task still "
                                "in flight — wait for it to complete "
                                "first (job_inject busy pre-check on WC)."
                            )
                        }
                # Durable wake enqueue. source carries the agent-tool
                # caller provenance (mirrors the agent-tool injection
                # branch's ``source=f"internal_agent:{caller}"`` shape).
                # ``current_instance_id`` is the calling agent — use it
                # when present, otherwise the empty-caller fallback
                # (``internal_agent:unknown``).
                caller = current_instance_id or "unknown"
                result = await manager.enqueue_message(
                    instance_id=instance_id,
                    message=message,
                    source=f"internal_agent:{caller}",
                )
                return {
                    "job_id": job_id,
                    "instance_id": instance_id,
                    "status": "enqueued",
                    "message_id": getattr(result, "message_id", None),
                    # m2 fix: literal ``True`` per the LOCKED C1-D3
                    # contract (``decisions.md`` C1-D3 Option A, leader-
                    # locked 2026-08-30) — the flag means "message was
                    # enqueued as a first-class turn" on the job_inject
                    # lane, NOT the ``AsyncMessageResult.queued``
                    # capacity flag (a spec collision: that field is
                    # "blocked at capacity" and defaults to ``False``).
                    # Mirror the HTTP lane's 200-enqueue ``MessageResponse.
                    # queued=True`` on success.
                    "queued": True,
                }

            # RUNNING (always) and WAITING_CHILDREN (flag OFF legacy)
            # both fall through here: ``set_injection`` appends to the
            # RAM FIFO; the agent_node consumes it on its next LLM
            # call. Byte-identical to pre-T7 behavior for RUNNING; for
            # flag-OFF WC this is the documented revert path.
            entry = manager.set_injection(instance_id, message)
            pending_count = manager.get_injection_count(instance_id)

            return {
                "job_id": job_id,
                "instance_id": instance_id,
                "status": "injected",
                "pending_count": pending_count,
                "content": entry.get("content"),
                "timestamp": entry.get("timestamp"),
            }
        except Exception as e:
            logger.error("Failed to inject message for job %s: %s", job_id, e, exc_info=True)
            return {"error": "Internal error injecting message"}

    job_inject._full_doc_ = _FULL_DOCS["job_inject"]

    return [
        job_create, job_get, job_list, job_cancel, job_retry,
        job_delete, job_restore, queue_list, queue_create,
        queue_update, dlq_list, dlq_replay,
        job_continue,   # moved to end of non-watch tools (was index 7)
        job_messages, job_tree, job_progress, job_inject,
        watch_job, unwatch_job, list_watched_jobs, watch_jobs,
    ]


__all__ = ["create_job_tools", "TERMINAL_STATES"]
