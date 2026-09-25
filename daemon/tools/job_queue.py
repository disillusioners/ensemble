"""Job queue management tools for LangGraph agents.

Size/seam note (M3 fix round, 2026-09-03, ``feature/mission-class``;
size refresh 2026-09-22, ``feature/job-answer-tool`` M-tidier pass;
size refresh 2026-09-25, ``job-pause-resume-tools``):
this module is 4,008 lines (was 3,321 at the 2026-09-22 refresh)
and hosts BOTH the LangGraph ``@tool`` wrappers AND significant
non-tool logic (the legacy ``list_jobs`` fallback, the watch-job
immediate-notify branch, the mission-tool opt-in helper, the WC-wake
enqueue toggle resolver, and the answer-tool HTTPException →
error-string shaper). Future work should consider splitting into:

* ``job_queue_tools.py`` — the LangGraph ``@tool`` surface only
  (the ``@register_tool_category`` entries).
* ``job_queue_runtime.py`` — the legacy ``list_jobs`` resolver,
  watch-job notify branches, mission opt-in helper, answer-tool
  error shaper.

First extraction slice (action-anchored, 2026-09-22
``feature/job-answer-tool`` M-tidier round): ``_format_answer_http_error``
(``daemon/tools/job_queue.py:733-825``) — the HTTPException-to-error-
string shaper used by the ``job_answer`` tool — has no production
dependency on the rest of this module's state and is the cleanest
first extraction target. Move to a new ``daemon/tools/_answer_runtime.py``
beside its producer in ``daemon/routers/`` so the helper can also be
unit-tested without the full ``create_job_tools`` factory. Extraction
is a FOLLOW-UP PR; the docstring anchors the slice so the next refactor
pass has an unambiguous starting point.

The tool surface (additive through M3 — no removal):
job_create, job_get, job_list, job_cancel, job_retry, watch_job,
watch_jobs, plus the M2 mission-side get_mission / await_mission
/ list_mission helpers (re-exported from
``daemon.tools.missions``). The ``job_answer`` tool joins the surface
via ``create_job_tools`` (appended at END, ``feature/job-answer-tool``,
2026-09-22) — agent-facing counterpart of
``POST /api/jobs/{work_id}/answer``; both surfaces share the SAME
underlying helper (``daemon/routers/answer_helper.py``).

Toolset reshape (2026-09-19, ``feature/mission-watch-toolset``):
``watch_mission`` joins the surface via the standalone
``create_mission_watch_tools`` factory below (resolver front-end +
registration-time receipt fan-out). The ``create_job_tools`` return
list is UNCHANGED so its index pins stay green.

Tool-name discovery is frozen — adding a new tool requires a
test_frozen_tool_name_discovery entry; the house registry scans
the ``@register_tool_category`` decorators.
"""

import asyncio
import logging
import uuid
from datetime import datetime, UTC
from typing import Annotated, Any, Optional, TYPE_CHECKING

from fastapi import HTTPException
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category
from ._truncate import truncate_dict_result
from daemon import constants
from daemon.constants import INJECTION_ELIGIBLE_STATUSES
from daemon.models.common import ErrorCodes
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.services.project_normalizer import normalize_project_id
from daemon.services.queue_ref import (
    QUEUE_ALIAS_TO_CANONICAL,
    describe_valid_queues,
    is_known_alias_name,
    resolve_queue_ref,
)
from daemon.services.work_status import _derive_legacy_status

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
    from daemon.services.dead_letter_service import DeadLetterService
    from daemon.services.work_resolver import WorkRecord, WorkResolverService
    from daemon.services.mission_resolver import MissionRecord, MissionResolver
    from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
    from daemon.repositories.task.repository import TaskRepository
    from daemon.manager import InstanceManager

CATEGORY_NAME = "Job Queue"
CATEGORY_DOC = """\
Create, list, and manage jobs and job queues.
"""

TERMINAL_STATES = set(ALL_TERMINAL_STATES)

# Watch-cap ceiling shared by every watch-family tool (job_create,
# watch_job, watch_jobs, watch_mission). Single source for both the
# number and the error sentence so the per-tool wordings cannot drift
# (tidier round 2026-09-20, #7).
MAX_WATCHES_PER_INSTANCE = 50

# Typed token for the agent-readable error string emitted by the
# ``job_answer`` tool's 503 write-paused pre-check (and any future
# caller that wants to branch on it). NOT in ``ErrorCodes`` (the
# HTTP wire schema) because the HTTP route raises a plain-string 503
# with no typed token; the agent-tool is the surface that introduces
# the token so it owns the spelling. Single source for the literal so
# it cannot drift between the docstring, the pre-check, and any test
# that branches on it (tidier round 2026-09-22, #3 — Medium).
WRITE_PAUSED_TOKEN = "WRITE_PAUSED"

# Token for the pause wedge-guard surfaced in ``job_pause``'s success
# response (``_wedge_note`` field). Hoisted from a string literal in the
# closure so the spelling cannot drift between the docstring, the response
# payload, and any test that branches on it (tidier round 2026-09-25, item 5).
STUCK_AWAITING_ANSWER = "STUCK_AWAITING_ANSWER"


def _watch_cap_error(current: int, attempted_clause: str | None = None) -> str:
    """Build the shared watch-cap error sentence.

    One wording source for every watch-family tool (tidier #7). Two
    shapes, matching the two cap checks in the tools:

    * single-hit (``attempted_clause is None``) — the instance is at the
      cap already: "Maximum watch limit (50) reached for this instance"
    * would-exceed — minting ``attempted_clause`` more rows would pass
      the cap: "Would exceed maximum watch limit (50). Currently
      watching N, <attempted_clause>."

    Args:
        current: The caller's current watch count.
        attempted_clause: Tail clause naming what would be added
            (e.g. ``"trying to add 3"`` / ``"mission has 2 receipt
            watch(es)"``). ``None`` renders the single-hit shape.

    Returns:
        The error sentence (no trailing period on the single-hit shape —
        callers that add a reason append it themselves).
    """
    if attempted_clause is None:
        return (
            f"Error: Maximum watch limit ({MAX_WATCHES_PER_INSTANCE}) "
            f"reached for this instance"
        )
    return (
        f"Error: Would exceed maximum watch limit "
        f"({MAX_WATCHES_PER_INSTANCE}). Currently watching {current}, "
        f"{attempted_clause}."
    )

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

Queue targeting:
    ``queue_id`` accepts a queue ID or a system-queue alias
    (case-insensitive): system_fifo_queue|fifo,
    system_parallel_queue|parallel, system_background_queue|background,
    system_defer_queue|defer, system_kb_fifo_queue|kb_fifo. Aliases always
    resolve to the SYSTEM queue of that name — even if a user-created
    queue shares the short name. When ``queue_id`` is omitted: agents ari
    and jober default to system_parallel_queue; every other agent keeps
    the service default (system_fifo_queue for task jobs). If the
    agent-default system queue is missing for the project, an error is
    returned (provisioning bug) — the job is not silently created
    without its default queue.

    Unknown reference handling has two legs: (a) a KNOWN alias name that
    fails to resolve returns a strict error listing the valid queues;
    (b) a value that is NOT a recognized alias (e.g. a typo'd name or a
    stale ID/UUID) passes through to the service's EXISTING soft-fail —
    the job is still created, a warning is logged, and ``queue_id``
    resolves to None at enqueue time.

Args:
    agent_id: Agent ID to run the job (e.g., "developer", "leader"). Required.
    message: The instruction/message for the agent. Required.
    project_id: Project ID for isolation and routing. Optional.
    priority: Job priority 1-10 (1=lowest, 10=highest). Default: 5.
    queue_id: Queue ID or system-queue alias. Optional.
    idempotency_key: Deduplication key. Optional.
    metadata: Custom key-value metadata. Optional.
    source: Source identifier. Default: "api".

Returns:
    Dictionary with job details including job_id and status.
    ``mission_id`` is included ONLY when already known at response
    time (mirror rows / post-dispatch rows); it is ABSENT for a
    fresh pre-dispatch job — pass the returned ``job_id`` to
    ``watch_mission`` to watch the work.

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
    queue_id: Filter by queue ID or system-queue alias (system_fifo_queue|fifo, system_parallel_queue|parallel, system_background_queue|background, system_defer_queue|defer, system_kb_fifo_queue|kb_fifo; case-insensitive). Optional. Unknown alias names return an error listing valid queues.
    offset: Number of jobs to skip (default: 0).
    limit: Maximum number of jobs to return. Default: 50.
    include_deleted: Include soft-deleted jobs. Default: False.
    job_types: M2 (mission-class, 2026-09-02) — optional
        ``JobItem.job_type`` filter; accepted values are ``"task"``
        and ``"message"``. Default: BOTH kinds. Additive vs the
        legacy ``statuses`` filter, which is RETAINED through the M3
        window. On the resolver path the filter is applied
        client-side.

Note:
    ``status`` answers the transport question ("was my submission
    handled?"). For the outcome question ("is the work done?"),
    use the mission tools (``get_mission`` / ``await_mission``).
    The ``mission_ref`` cross-reference on every terminal job
    payload ties the two layers together in a single read.

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
    queue_id: The queue ID or system-queue alias (system_fifo_queue|fifo, system_parallel_queue|parallel, system_background_queue|background, system_defer_queue|defer, system_kb_fifo_queue|kb_fifo; case-insensitive) to update. Required.
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
    queue_id: Filter by queue ID or system-queue alias (system_fifo_queue|fifo, system_parallel_queue|parallel, system_background_queue|background, system_defer_queue|defer, system_kb_fifo_queue|kb_fifo; case-insensitive). Optional. Unknown alias names return an error listing valid queues.
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
        plus ``"in_progress"``.

        M2 (mission-class, 2026-09-02): the value ``"mission_terminal"``
        is OPT-IN — when included, the watcher fires ONLY when
        admission AND mission liveness are BOTH terminal (contract
        draft §3.5). Default watchers (without ``mission_terminal``)
        keep transport-only semantics — back-compat preserved.

Returns:
    Confirmation message or error.""",

    "unwatch_job": """Stop watching a job for lifecycle events.

Args:
    job_id: The handle to stop watching. Tolerant: a receipt job_id
        OR a mission_id. A mission handle resolves to every watched
        receipt of that mission and removes them all. Required.

Returns:
    Confirmation message.""",

    "list_watched_jobs": """List all jobs the current instance is watching.

Each row is labeled by resolving its handle: ``mission handle`` (the
row keys on a mission id), ``receipt of mission <id>`` (a
receipt-keyed row and the mission it belongs to), or unlabeled
(unresolvable receipt).

Returns:
    List of watched jobs with their event filters.""",

    "watch_jobs": """Watch multiple jobs for lifecycle events. Bulk version of watch_job.

Jobs already in terminal states will trigger immediate notifications.

Args:
    job_ids: List of job IDs to watch. Required.
    events: Specific terminal states to watch for. Optional.
        Default: all terminal states. M2 (mission-class): the value
        ``"mission_terminal"`` is OPT-IN with the same dual-terminal
        semantics as ``watch_job`` (contract draft §3.5).

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

Note (M2 task-only gate — mission-class, 2026-09-02): ``job_continue``
ONLY accepts ``job_type='task'`` (mission proxy). For
``job_type='message'`` (mirror receipts), the tool returns a clear
refusal pointing at ``send_message`` — the canonical mirror path.
Contract draft §3 "Plus" clause; closes the wrong-predicate trap so
an agent cannot accidentally use the work-side primitive to message.

Note (revive-once guard — W1, scoped — feature/fix-revive-guard-scope
2026-09-05): a ``job_continue`` whose target instance status is FAILED
counts as an agent-tool-initiated revival and is bound by the manager's
revive-once guard (quick-win #7). The first FAILED-continue of an
instance CONSUMES the per-child counter (granted and incremented,
counter 0→1); the SECOND FAILED-continue is refused with the same
wording as ``send_message``'s terminal-revive refusal ("Refused: Instance
'<id>' has already been revived once and failed again. Spawn a
replacement instance instead."). SCOPE: FAILED is the ONLY
``job_continue`` status that consumes the budget — ``job_continue`` is
not routed through the four-state ``send_message`` terminal-revive
branch, so COMPLETED / TERMINATED / ERROR ``job_continue`` cases do
NOT reach this guard. The COMPLETED-continue path is DELIBERATELY
EXCLUDED (it is the designed give-more-work continue flow on a
successful child, not a failure revive, so it neither increments nor
is blocked by the guard).

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
queue a durable wake turn for a WAITING_CHILDREN target (wc-wake-report-
integrity, B1 RESOLVED 2026-09-11 — the legacy
``ENSEMBLE_WC_WAKE_ENQUEUE`` flag-OFF RAM-FIFO injection route for WC
was REMOVED; WC ALWAYS routes through durable enqueue).

Routing (split):
  * ``RUNNING`` with a live graph → RAM FIFO injection via
    ``InstanceManager.set_injection(...)`` (byte-identical to pre-
    wc-wake behavior). The ``agent_node`` consumes the entry on its
    next LLM call and threads it into the conversation as a fresh
    ``HumanMessage``. Returns ``{status: \"injected\", pending_count,
    content, timestamp}``. Status flag (``injection_pending`` SSE) is
    unchanged.
  * ``RUNNING`` without a live graph (graphless / never-dispatched,
    e.g. spawn-created child cascade-paused + cascade-resumed without
    ever being dispatched) → durable wake enqueue via
    ``manager.enqueue_message(source=f\"internal_agent:{caller}\")``
    — same-second task + message materialization, busy pre-check
    included (mirrors the WC branch below; same
    ``has_instance_busy`` gate via ``manager._task_repo``).
    Returns ``{job_id, instance_id, status: \"enqueued\",
    message_id, queued: True}``. No ``injection_pending`` SSE under
    this path (the FE sees the message via the normal turn-start
    ``user_message`` pre-emit). Guard site:
    ``if not manager.has_live_graph_task(instance_id):`` (~:2316).

  * ``WAITING_CHILDREN`` → durable wake enqueue via
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
    B1 fix (2026-09-11): no flag state — WC ALWAYS routes here.

  * ``IDLE`` / ``PAUSED`` / terminal → error: use ``job_continue``
    instead (it handles wake + revive + Task creation for those
    statuses). The error text identifies the actual instance status
    and points the agent to ``job_continue``.

Eligibility (matches the routing above): RUNNING is always accepted;
WC is always accepted via the durable-enqueue branch (B1); other
statuses are rejected with the eligibility error.

Unlike ``job_continue`` (which creates a new Task and requires the
instance to be IDLE/terminal), ``job_inject`` piggybacks on the
existing turn for RUNNING targets WITH a live graph — it does NOT
spawn a new job, does NOT interrupt tool execution, and does NOT
race with the active ``enqueue_message_job`` path. For WC (B1) and
for graphless RUNNING targets (dispatch-lane stranding fix, guard
at ~:2316), ``job_inject`` moves to ``enqueue_message`` and DOES
create a new first-class turn (durable wake) — the same primitive
the agent-tool send_message uses.

Return shape (m2 fix, LOCKED C1-D3 Option A, 2026-08-30): the
``queued`` flag on the WC branch (B1: the only branch; was flag-ON WC
pre-B1) is a LITERAL ``True``, meaning "message was enqueued as a
first-class turn" — NOT the ``AsyncMessageResult.queued`` capacity
flag (a spec collision: ``AsyncMessageResult.queued`` means "blocked
at capacity" and defaults to ``False``). Mirror the HTTP lane's
200-enqueue ``MessageResponse.queued=True`` on success. The
``getattr(result, "queued", True)`` propagation that pre-m2 carried
the AsyncMessageResult field through to the tool response was a
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

    "job_answer": """Submit the orchestrator's answer to a pending question pack on a watched job.

The agent-facing counterpart of ``POST /api/jobs/{work_id}/answer`` —
delivers an answer to a pending ``ask_questions`` pack owned by the
work_id's asker instance, then resumes the asker cascade. The underlying
logic is the SAME shared helper ``answer_questions_via_instance``
(``daemon/routers/answer_helper.py``) that both HTTP surfaces use — there
is no second implementation. The route's optional ``resume_message``
body field is NOT exposed on the agent tool surface (hardcoded to
``None``).

Use this when the agent has been watching a job (``watch_job`` /
``watch_jobs``) and received a ``[JOB_EVENT] Job {work_id}...
question requested ❓`` line with a pack payload: relay the question to
the human, capture the reply, and submit it via ``job_answer``.

Args:
    work_id: The job / work_id whose instance asked the question
        (matches the ``work_id`` on the ``question requested`` event).
    answers: Dict of user-supplied answers keyed by question id
        (preferred) or question text.
    question_pack_id: The pack id echoed by the caller for the T1″
        stale-answers correlation guard. When present and ≠ the
        current pending pack's id the call is refused with a 400
        ``QUESTION_PACK_MISMATCH`` error string.

Access control: the caller's ``project_id`` must match the job's
``project_id`` (when both are set); system-default (unscoped-or-root)
callers act as global operators and may answer packs in any project.
The check reuses the same ``_check_job_access`` helper as the
``job_messages`` / ``job_tree`` / ``job_progress`` / ``job_inject``
visibility tools.

Returns:
    Dictionary with the job-addressed envelope and the helper's
    instance-addressed body: ``work_id``, ``status`` (``answered`` /
    ``already_delivered`` / ``answer_fallback_enqueued`` /
    ``no_active_job``), ``instance_id``, ``question_pack``,
    ``resume_route`` (one of ``answer_gate_existing_turn``,
    ``revived_error_target``, ``enqueue_as_fresh_message``,
    ``already_delivered``), and ``resume_info`` (cascade-resume
    details: ``resumed``, ``resumed_ids``, ``skipped_ids``,
    ``target_id``, ``resume_results``).

    ``{"error": "..."}`` on every guarded failure. Error strings
    carry the typed code (the same vocabulary the HTTP route emits)
    so a downstream agent can branch on the exact cause:

      * ``400 QUESTION_PACK_MISMATCH: ...`` — stale answers for a
        superseded pack (T1″ hijack guard); the agent should re-fetch
        the pending pack id and retry.
      * ``404 JOB_NOT_FOUND: ...`` — ``work_id`` does not resolve to
        any task or job; the work is unknown to the daemon.
      * ``404 INSTANCE_NOT_FOUND: ...`` — the resolved ``instance_id``
        is UNKNOWN to the manager (the work_id → instance_id resolver
        returned a row but the manager's instance repository does not
        know it). The work is reachable from the job surface but the
        asker is not; the answer cannot be routed. Hint: re-fetch the
        work_id and confirm the instance still exists.
      * ``404 NO_PENDING_QUESTION: ...`` — the instance exists but has
        no pending pack. Hint: for completed instances use
        ``job_continue`` instead.
      * ``410 ANSWER_TARGET_TERMINAL: ...`` — asker reached a terminal
        state while the question was pending; the answer cannot be
        delivered (no silent-revive — leader decision 1).
      * ``410 QUESTION_PACK_LOST: ...`` — durable handle but neither
        RAM pack nor ``instance_metadata`` payload survived a daemon
        restart.
      * ``503 WRITE_PAUSED: ...`` — daemon migration posture. The
        tool's own pre-check emits the typed ``WRITE_PAUSED`` token;
        a helper-race-window hit (pre-check cleared, then helper
        re-raised) surfaces as
        ``"503: Writes are paused for database migration"`` with no
        branchable token — match on the ``503`` status + ``migration``
        substring in that case.
      * ``Access denied: job does not belong to caller's project`` —
        project-scoped check refused.

Tool gate is stricter than HTTP ``/api/jobs/{work_id}/answer`` route:
the tool requires non-empty ``answers`` + non-empty ``question_pack_id``
and rejects them up-front; the HTTP route leniently defaults both
(deliberate posture — a non-tool caller can still POST partial bodies
without the strict gate, but the agent surface commits to the strict
shape so a downstream agent never sees a partial-pack error string it
did not actually trigger).

Example:
    job_answer(
        work_id="job_abc123",
        answers={"q1": "yes", "q2": "no"},
        question_pack_id="pack-xyz",
    )""",

    "job_pause": """Pause the instance behind an ACTIVE job and cascade to its lineage.

Job-shaped resolver over the EXISTING cascade layer (``pause_instance_cascade``
at ``daemon/manager.py:9599``). The agent-facing twin of
``POST /api/instances/{instance_id}/pause`` (``daemon/routers/instances.py:653``):
FE-identical by construction — same facade call, same default kwargs, same
return shape (``paused_ids`` + ``skipped_ids``).

Resolves ``job_id`` → ``WorkRecord`` via ``job_service.get_work`` (the same
resolver ``job_cancel`` uses), extracts ``instance_id``, then delegates.
Job state (admission_state/status) is NOT touched — only the instance
lineage pauses.

Use when an agent holds a job_id (not an instance_id) and wants to apply the
same lineage pause/resume semantics the FE pause endpoint exposes for an
instance_id. For the instance_id-shaped variant, see ``pause_instance``.

Args:
    job_id: The work_id of an ACTIVE (non-terminal, instance-bearing) job
        to pause. Its whole instance lineage pauses with it. Required.

Errors (returned as ``{"error": ..., "paused": False}``):

    * ``Job not found: {job_id}`` — ``job_service.get_work`` returned None.
    * ``Job {job_id[:8]}... is in a terminal state ({status}) — cannot be
      paused`` — work is DONE/DEAD/cancelled/etc. Pause on terminal work is
      a no-op and is refused.
    * ``Job {job_id[:8]}... has not started an instance yet (status={status})
      — use queue_pause(queue_id) to prevent it from starting instead`` —
      the QUEUED branch (D1). The job is in flight (pending/queued) but has
      no instance_id to pause. To prevent it from starting, pause the queue
      instead. The job's admission_state/status is NOT modified.
    * ``Access denied: job does not belong to caller's project`` — the
      project-scoped ``_check_job_access`` check refused (system-default
      callers are the global-operator tier; project-scoped callers must share
      the job's project_id).

Returns:
    ``{"paused": True, "paused_ids": [...], "skipped_ids": [...],
    "instance_id": ..., "_wedge_note": "..."}`` on success.
    ``paused_ids`` is every instance ID that transitioned to PAUSED;
    ``skipped_ids`` is every ID already paused / terminal / not found
    (idempotent — re-pausing is a no-op).

D2 (wedge guard): pausing does NOT keep an instance safe forever. The
mid-flight QA channel's ``stuck_awaiting_answer`` wedge-guard is a
FINITE 3-emission chain (``daemon/constants.py:32-46``): an event fires
at pause time (t=0), again at ~30 minutes (t=+1800s), and at ~60
minutes (t=+3600s) the asker is ESCALATED — ``manager.terminate_instance``
runs with ``terminal_reason="wedge_guard_terminated"`` and the chain
mints NO successor (``daemon/services/task_processor.py:1267-1508``).
The daemon does NOT auto-resume paused work; escalation TERMINATES the
asker, it does not pause-and-wait. If you intend to leave an instance
paused for longer than a few minutes, document the intent (e.g. via a
watched job or a critical note) so a follow-up agent can resume it
deliberately. The pause success response echoes this ``_wedge_note`` so
the destructive consequence cannot be missed by an automated caller.
""",

    "job_resume": """Resume the instance behind a PAUSED job and cascade to its lineage.

Job-shaped resolver over the EXISTING cascade layer (``resume_instance_cascade``
+ ``resume_processing_job`` at ``daemon/manager.py:9639`` / ``:9656``). The
agent-facing twin of the plain (non-gate-supersession) branch of
``POST /api/instances/{instance_id}/resume`` (``daemon/routers/instances.py:684``):
FE-identical by construction — same call shape (target continuation job with
``message="resume", silent=False`` → cascade flip → silent child resumes with
``silent=True``), same default kwargs, same return shape.

Resolves ``job_id`` → ``WorkRecord`` via ``job_service.get_work`` (the same
resolver ``job_cancel`` uses), extracts ``instance_id``, then delegates.
Job state (admission_state/status) is NOT touched — only the instance
lineage resumes.

Use when an agent holds a job_id (not an instance_id) and wants to apply the
same lineage resume semantics the FE resume endpoint exposes for an
instance_id. For the instance_id-shaped variant, see ``resume_instance``.

Terminal-job asymmetry (D3'): unlike ``job_pause``, which REFUSES terminal
jobs (returns ``{"error": "...is in a terminal state...", "paused": False}``),
this tool PASSES THROUGH on terminal jobs — FE-identical to the HTTP
resume route. A terminal job_id resolves to its ``instance_id`` (terminal
work still carries the instance binding) and the call delegates to
``resume_instance_cascade`` + ``resume_processing_job``; the response is
the standard FE passthrough shape (``resumed: True`` with whatever
``resumed_ids`` / ``skipped_ids`` the cascade yields, ``resume_results``
carrying ``no_active_job`` / ``silent_resume`` / ``error`` status from
the underlying service). This is deliberate — the FE mirror must stay
identical, and a "helpful" terminal guard on the agent surface would
break that contract. Do NOT add a terminal refusal here.

D3 (message injection): mirrors the FE plain resume path — injects the
literal message ``"resume"`` onto the target via ``resume_processing_job``.
The HTTP gate-supersession branch (when a pending question pack is on the
target) is NOT mirrored; the agent surface refuses with a clear error
instead — see the question-pack refusal below. To clear a pending
question gate via the agent surface, use ``job_answer``: its underlying
helper ``answer_questions_via_instance`` runs the ANSWER flow
(``daemon/routers/answer_helper.py`` — CAS flip pending→answered via
``set_answers``, then ``resume_processing_job`` on the cleared pack)
before resuming.

Args:
    job_id: The work_id of an ACTIVE job whose instance is paused. Its
        whole instance lineage resumes with it. Required.

Errors (returned as ``{"error": ..., "resumed": False}``):

    * ``Job not found: {job_id}`` — ``job_service.get_work`` returned None.
    * ``Job {job_id[:8]}... has not started an instance yet (status={status})
      — resume requires an instance_id`` — the QUEUED branch (D1). The job
      has no instance_id to resume; admission_state/status is NOT modified.
    * ``instance has a pending question; answer it via job_answer(work_id)
      instead of job_resume`` — pending ``question_pack`` with
      ``status == "pending"`` on the target instance. Mirrors the
      ``resume_instance`` tool's Defect-1 guard (``daemon/tools/instance.py:4677-4695``):
      the standard resume path routes through ``answer_gate_existing_turn``
      which would treat the literal ``"resume"`` message as answer content.
      To clear the gate and resume, answer the question via ``job_answer``;
      its underlying helper runs the ANSWER flow (CAS flip
      pending→answered, then ``resume_processing_job`` on the cleared pack).
    * ``Access denied: job does not belong to caller's project`` — the
      project-scoped ``_check_job_access`` check refused (system-default
      callers are the global-operator tier; project-scoped callers must share
      the job's project_id).

Returns:
    ``{"resumed": True, "resumed_ids": [...], "skipped_ids": [...],
    "target_id": ..., "resume_results": {instance_id: {"status": ...}}}``
    on success. ``resume_results`` statuses come VERBATIM from the resume
    service (``resume_processing_job``) and are never re-interpreted:
    ``"resuming"`` (turn continuation job spun), ``"silent_resume"``
    (silent checkpoint continuation), ``"wake_enqueued"`` / ``"wake_failed"``
    (parked WAITING_CHILDREN parent — inspect refusal_kind for cause),
    ``"already_resuming"`` (resume dedup guard short-circuited at
    ``manager.py:10122``), ``"deferred_report_recovery"`` (router recovered
    DEFERRED report rows; check ``recovery_count``), ``"no_active_job"``
    (nothing to continue), ``"error"`` (continuation failed — inspect the
    ``error`` field).
""",
}


def _record_mission_is_terminal(record: Optional["WorkRecord"]) -> bool:
    """True iff a :class:`WorkRecord`'s mission-side liveness is terminal.

    M2 (mission-class, 2026-09-02, ``feature/mission-class``) —
    companion helper for the ``watch_job(events='mission_terminal')``
    gating (contract draft §3.5). A watcher that opts in via
    ``mission_terminal`` fires ONLY when BOTH the admission-side
    ``status`` is terminal AND the mission-side ``mission_liveness``
    is terminal.

    ``mission_liveness`` is the canonical mission vocabulary for
    mirror rows (JobItem.job_type='message') and ``None`` for task
    rows (where the row IS its own mission — ``status`` is the
    liveness signal). So:

    * Mirror rows: ``mission_liveness in {completed, failed, cancelled}``
    * Task rows: ``status in {completed, failed, cancelled}``
    * Degraded / unresolvable rows: ``False`` — the mission
      liveness is unknown, so the dual-terminal condition fails
      closed (the watcher keeps its row alive for the eventual
      terminal event).

    Args:
        record: A :class:`WorkRecord` or ``None``.

    Returns:
        ``True`` iff the mission-side liveness is terminal; ``False``
        otherwise (live OR degraded).
    """
    if record is None:
        return False
    canonical_terminal = {"completed", "failed", "cancelled"}
    job_type = getattr(record, "job_type", None)
    if job_type == "message":
        # Mirror row — ``mission_liveness`` is the canonical mission
        # vocabulary (None means degraded ⇒ fail closed).
        mission_liveness = getattr(record, "mission_liveness", None)
        return mission_liveness in canonical_terminal
    if job_type == "task":
        # Task row — the row IS its own mission; ``status`` is the
        # mission liveness signal.
        return getattr(record, "status", None) in canonical_terminal
    # Unknown / Task-backed / degraded — fall back to ``status`` (the
    # row's canonical work-side status). For non-JobItem rows this is
    # the only liveness signal we have.
    return getattr(record, "status", None) in canonical_terminal


def _resolve_mission_record(
    resolver: "MissionResolver",
    handle: str,
    *,
    context: str,
) -> Optional["MissionRecord"]:
    """Resolve ``handle`` onto the mission read-model, degrading to None.

    Shared guard for the mission-handle branches of the watch-family
    tools (``watch_mission`` / ``unwatch_job`` / ``list_watched_jobs``) —
    replaces the triplicated try/except guards (tidier round 2026-09-20,
    High #1 + Med #2).

    Degradation contract: a resolver RAISE (transient DB error) degrades
    to ``None`` but is NEVER silent — the degraded lookup is logged at
    warning with the calling context (house pattern:
    ``daemon/tools/missions.py`` get_mission). A clean miss (no Instance
    row) returns ``None`` without logging.

    Args:
        resolver: The wired :class:`MissionResolver` (caller guarantees
            non-None).
        handle: The id to resolve (a mission_id or a watched row id).
        context: Short caller tag for the warning line
            (``"watch_mission"`` / ``"unwatch_job"`` /
            ``"list_watched_jobs"``).

    Returns:
        The :class:`MissionRecord`, or ``None`` when the handle misses
        or the resolver degraded.
    """
    try:
        return resolver.resolve(handle)
    except Exception as exc:  # noqa: BLE001 — degraded resolver → caller fallback
        logger.warning(
            "%s: mission resolver raised for handle=%r: %s — "
            "degrading to None",
            context,
            handle,
            exc,
        )
        return None


def _format_answer_http_error(
    exc: HTTPException,
    work_id: str,
    instance_id: str,
) -> dict:
    """Convert a ``HTTPException`` from ``answer_questions_via_instance``
    into a clean, agent-readable error dict.

    The helper raises ``HTTPException`` on every guarded failure with a
    typed ``ErrorResponse(code=..., message=..., details=...)`` body —
    the ``code`` is the agent-routable vocabulary (400
    ``QUESTION_PACK_MISMATCH`` / 404 ``NO_PENDING_QUESTION`` / 410
    ``ANSWER_TARGET_TERMINAL`` / ``QUESTION_PACK_LOST``). The
    write-pause guard (503) is the one exception — it raises with a
    plain ``"Writes are paused for database migration"`` STRING
    detail, NOT a typed ``ErrorResponse``, so the helper path emits
    no branchable ``WRITE_PAUSED`` token (only the tool's own
    pre-check does). The HTTP route's exception handler serializes
    the typed body; this tool has no exception handler, so we
    synthesize a single ``{"error": ...}`` string carrying:

    * the HTTP status + typed code (so an agent can branch on the
      exact cause), and
    * the helper's message verbatim (the human-readable explanation),
      plus
    * a ``job_continue`` hint on the no-pending/terminal states (the
      brief asks for this hint so the agent doesn't loop on a job
      whose asker is past the answer window).

    Args:
        exc: The :class:`HTTPException` the helper raised.
        work_id: The work_id the caller submitted (echoed in the error
            so the agent can correlate).
        instance_id: The instance_id the work resolved to (also
            echoed; useful for 410 terminal logs).

    Returns:
        ``{"error": "..."}`` dict — never raises.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        # ``answer_helper._http`` calls ``ErrorResponse(...).model_dump()``
        # which keeps ``code: ErrorCodes`` as the enum object (Pydantic's
        # default — ``mode='json'`` is needed to coerce to the string
        # value). Normalize to ``.value`` here so the agent-facing
        # error string is always a clean string regardless of how the
        # helper serialized the body.
        raw_code = detail.get("code", "")
        code = getattr(raw_code, "value", raw_code) or ""
        message = detail.get("message", "") or str(detail)
        # The HTTP status code lives on the exception itself;
        # ``detail["status"]`` is the helper's body and is the same.
        status = getattr(exc, "status_code", None) or detail.get("status")
    else:
        code = ""
        message = str(detail) if detail is not None else str(exc)
        status = getattr(exc, "status_code", None)

    code_token = f"{code}" if code else ""
    status_token = f"{status}" if status else ""

    # Friendly hint for the no-pending / terminal states (the brief
    # asks for this). For 404 NO_PENDING_QUESTION the asker exists
    # but has no pending pack — the right follow-up primitive is
    # ``job_continue``, not a retry on the same work_id.
    # INSTANCE_NOT_FOUND is a routing failure (the resolved
    # ``instance_id`` is unknown to the manager — see
    # ``answer_helper.py:25, 179-183``) and gets a DIFFERENT hint
    # because retrying with the same work_id cannot help; the
    # caller must re-fetch the work_id + confirm the instance still
    # exists. Both hint branches come from the typed ErrorCodes
    # enum so the raw-string drift class cannot re-introduce this
    # split (tidier #1 — Medium, 2026-09-22).
    no_pack_hint = ""
    if code in {
        ErrorCodes.NO_PENDING_QUESTION.value,
        ErrorCodes.ANSWER_TARGET_TERMINAL.value,
        ErrorCodes.QUESTION_PACK_LOST.value,
    }:
        no_pack_hint = (
            " — no pending question pack for this job; "
            "use job_continue for completed instances"
        )
    elif code == ErrorCodes.INSTANCE_NOT_FOUND.value:
        no_pack_hint = (
            " — resolved instance_id is unknown to the manager; "
            "re-fetch the work_id and confirm the asker instance "
            "still exists"
        )

    if code_token and status_token:
        head = f"{status_token} {code_token}:"
    elif code_token:
        head = f"{code_token}:"
    elif status_token:
        head = f"{status_token}:"
    else:
        head = "Error:"

    return {
        "error": (
            f"{head} {message} "
            f"(work_id={work_id}, instance_id={instance_id})"
            f"{no_pack_hint}"
        )
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

    # Agent-aware default queue: closure-time fetch of the caller's
    # ``default_queue`` meta field, using the established versioned-meta
    # pattern (prefer versioned meta for tagged callers, fall back to base
    # resolved meta — see instance.py tool filtering). ari/jober are
    # untagged dirs → base meta. When no explicit ``queue_id`` is supplied
    # at call time, ``job_create`` targets this queue instead of degrading
    # to the service default (system_fifo_queue for job_type=task). Any
    # fetch failure or unresolvable value logs a warning and degrades to
    # None (service default) — tool build is NEVER broken here.
    agent_default_queue: str | None = None
    if caller_agent_id:
        try:
            from ..registry import get_registry

            registry = get_registry()
            _meta = (
                registry.get_version(caller_agent_id, caller_agent_tag)
                or registry.get_resolved(caller_agent_id)
            )
            _raw = getattr(_meta, "default_queue", None)
            if isinstance(_raw, str) and _raw.strip():
                agent_default_queue = _raw.strip()
        except Exception as e:
            logger.warning(
                "job tools: could not resolve default_queue for caller %r — "
                "degrading to service default: %s",
                caller_agent_id,
                e,
            )

    class JobCreateInput(BaseModel):
        """Input schema for job_create tool."""
        agent_id: Annotated[str, Field(description="Agent ID to run the job (e.g., 'developer', 'leader')")]
        message: Annotated[str, Field(description="The instruction/message for the agent")]
        project_id: Annotated[str | None, Field(default=None, description="Project ID for isolation and routing")]
        priority: Annotated[int, Field(default=5, ge=1, le=10, description="Job priority 1-10 (1=lowest, 10=highest)")]
        queue_id: Annotated[str | None, Field(default=None, description="Queue to submit to: a queue ID or a system-queue alias (system_fifo_queue|fifo, system_parallel_queue|parallel, system_background_queue|background, system_defer_queue|defer, system_kb_fifo_queue|kb_fifo; case-insensitive). Omit to use your agent default (ari/jober: system_parallel_queue) or the service default (FIFO).")]
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
        queue_id: Annotated[str | None, Field(default=None, description="Queue to submit to: a queue ID or a system-queue alias (system_fifo_queue|fifo, system_parallel_queue|parallel, system_background_queue|background, system_defer_queue|defer, system_kb_fifo_queue|kb_fifo; case-insensitive). Omit to use your agent default (ari/jober: system_parallel_queue) or the service default (FIFO).")] = None,
        idempotency_key: Annotated[str | None, Field(default=None, description="Deduplication key")] = None,
        metadata: Annotated[dict[str, Any] | None, Field(default=None, description="Custom key-value metadata")] = None,
        source: Annotated[str, Field(default="api", description="DEPRECATED and IGNORED: server derives source unconditionally (B3.5); retained for schema compat, removal deferred")] = "api",
        watch: Annotated[bool, Field(default=False, description="Watch the job for lifecycle events")] = False,
    ) -> dict:
        """Submit a new job to the queue. Use tool_help("job_create") for details.

        queue_id accepts a queue ID or a system-queue alias (e.g. "parallel"
        = system_parallel_queue; case-insensitive). When omitted: ari/jober
        target system_parallel_queue by default; other agents get the
        service default (FIFO for task jobs).
        """
        try:
            # MAJOR-1(a) (P2.2 fix pass 2026-08-23) + MINOR-B (P2.2
            # carry-over, closed P2.3 B3.5): the source derivation is
            # server-side-UNCONDITIONAL — NO caller-supplied ``source`` is
            # ever trusted verbatim on this path. Agent callers →
            # ``agent:<caller>`` (a hostile source="telegram:attacker"
            # must never thread through dispatch to the user-origin
            # classification stamp — manager.stamp_user_origin_window /
            # upgrade_journal.classify_user_origin — that would forge
            # factor 2 of the live 3-factor gate with zero human
            # involvement). Empty caller →
            # ``internal_agent:unknown`` (F3, mirrors job_continue below):
            # NEVER the default "api" — "api" arms the gate via the
            # exact-match path, and the genuine web-UI path keeps its
            # server-stamped value on the HTTP router (jobs_crud.py), not
            # here.
            source = (
                f"agent:{caller_agent_id}"
                if caller_agent_id
                else "internal_agent:unknown"
            )
            normalized_project_id = normalize_project_id(project_id)

            # Queue reference resolution (tool-layer only — service
            # enqueue semantics untouched). Two independent behaviors:
            #
            # 1. Alias acceptance (queue_ref): an explicit ``queue_id``
            #    may be a system-queue alias (full name or short form,
            #    case-insensitive) → resolved to the project's actual
            #    queue row. Precedence: exact in-project ID first (works
            #    for UUID and seeded sys-* IDs; a cross-project ID is NOT
            #    a match → falls through to error), then alias map →
            #    canonical name → get_by_name. A KNOWN name that fails
            #    to resolve is a hard error (strict for names); an
            #    unknown non-alias ref passes through untouched so the
            #    service's soft-fail semantics stay regression-pinned.
            # 2. Agent-aware default: when ``queue_id`` is None and the
            #    caller's meta declares ``default_queue``, that system
            #    queue is resolved per-project and targeted instead of
            #    the service default. A missing queue there is a
            #    provisioning bug → loud error, never silent degrade.
            resolved_queue_id = queue_id
            queue_repo = getattr(job_service, "_queue_repo", None)
            if resolved_queue_id is not None:
                if queue_repo is not None:
                    _queue = await asyncio.to_thread(
                        resolve_queue_ref,
                        queue_repo,
                        normalized_project_id,
                        resolved_queue_id,
                    )
                    if _queue is not None:
                        resolved_queue_id = _queue.queue_id
                    elif is_known_alias_name(resolved_queue_id):
                        return {
                            "error": (
                                f"Unknown queue reference '{queue_id}' for project "
                                f"{normalized_project_id}. "
                                + describe_valid_queues(
                                    queue_repo, normalized_project_id
                                )
                            )
                        }
                    # else: not a known name → pass through verbatim;
                    # enqueue's soft-fail handles it (pinned behavior).
                # repo plumbing unavailable → pass through verbatim
                # (degrade to pre-alias behavior).
            elif agent_default_queue:
                if queue_repo is None:
                    logger.warning(
                        "job_create: caller %r has default_queue %r but no "
                        "queue repo is reachable — degrading to service default",
                        caller_agent_id,
                        agent_default_queue,
                    )
                else:
                    # Short aliases (e.g. "parallel") are reserved keys: they
                    # ALWAYS map to the system queue — a user queue literally
                    # named "parallel" can NEVER shadow the alias. Canonicalize
                    # alias keys first; values not in the alias map pass
                    # through as plain project-queue names (flexibility
                    # preserved: a user queue declared as default stays valid).
                    _lookup_name = QUEUE_ALIAS_TO_CANONICAL.get(
                        agent_default_queue.lower(), agent_default_queue
                    )
                    _default_queue = await asyncio.to_thread(
                        queue_repo.get_by_name,
                        normalized_project_id,
                        _lookup_name,
                    )
                    if _default_queue is None:
                        return {
                            "error": (
                                f"Default queue '{agent_default_queue}' (declared "
                                f"for agent '{caller_agent_id}') not found in project "
                                f"{normalized_project_id} — system queue provisioning "
                                "bug. "
                                + describe_valid_queues(
                                    queue_repo, normalized_project_id
                                )
                            )
                        }
                    resolved_queue_id = _default_queue.queue_id

            # Pre-generate job_id so we can register the watcher BEFORE
            # dispatching. This closes the TOCTOU window where a fast job
            # could complete between enqueue() and add_watch(), causing the
            # watcher to miss the terminal [JOB_EVENT] notification.
            pre_generated_job_id = str(uuid.uuid4())

            # Register watch BEFORE enqueue (if requested). If the watch
            # limit is hit, we return early without creating the job at all.
            if watch and watcher_repo is not None and current_instance_id:
                count = watcher_repo.count_watches_for_instance(current_instance_id)
                if count >= MAX_WATCHES_PER_INSTANCE:
                    return {
                        "error": _watch_cap_error(count) + ". Job was not created.",
                    }
                watcher_repo.add_watch(pre_generated_job_id, current_instance_id)

            job_item = await job_service.enqueue(
                agent_id=agent_id,
                message=message,
                source=source,
                project_id=normalized_project_id,
                priority=priority,
                metadata=metadata,
                queue_id=resolved_queue_id,
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

            # Toolset reshape (2026-09-19, design §1 A2): surface
            # ``mission_id`` ONLY when it is already known at response
            # time — mirror rows and post-dispatch rows carry
            # ``instance_id`` (= mission_id per M1 identity). A fresh
            # pre-dispatch task job has ``instance_id=None`` (the
            # instance is minted at dispatch), so the key is ABSENT and
            # the agent uses the returned ``job_id`` as the
            # ``watch_mission`` handle. Additive-only: ``to_dict()`` is
            # returned verbatim otherwise.
            response = job_item.to_dict()
            if getattr(job_item, "instance_id", None):
                response["mission_id"] = job_item.instance_id
            return response
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
        job_types: Annotated[list[str] | None, Field(
            default=None,
            description=(
                "M2 (mission-class, 2026-09-02): optional JobItem.job_type "
                "filter — accepted values are ``task`` and ``message``. "
                "Default: BOTH kinds. Additive vs the legacy ``statuses`` "
                "filter, which is RETAINED through the M3 window (contract "
                "draft §4). On the resolver path the filter is applied "
                "client-side (the resolver's ``list_work`` does not "
                "narrow by job_type today)."
            ),
        )] = None,
    ) -> dict:
        """List jobs with optional filters. Use tool_help("job_list") for details.

        ``status`` answers the transport question ("was my submission
        handled?"). For the outcome question ("is the work done?"),
        use the mission tools (``get_mission`` / ``await_mission``).
        The ``mission_ref`` cross-reference on every terminal job
        payload ties the two layers together in a single read.
        """
        try:
            # Queue-alias resolution (queue_ref): an explicit ``queue_id``
            # filter may be a system-queue alias (full name or short form,
            # case-insensitive). Known-name-miss → hard error with the
            # valid-queue listing; unknown non-alias refs pass through
            # untouched (existing filter semantics, both branches, are
            # unchanged). Alias lookups need a project scope, so they
            # resolve against the normalized project.
            resolved_queue_id = queue_id
            if resolved_queue_id is not None:
                _queue_repo = getattr(job_service, "_queue_repo", None)
                if _queue_repo is not None:
                    _queue = await asyncio.to_thread(
                        resolve_queue_ref,
                        _queue_repo,
                        normalize_project_id(project_id),
                        resolved_queue_id,
                    )
                    if _queue is not None:
                        resolved_queue_id = _queue.queue_id
                    elif is_known_alias_name(resolved_queue_id):
                        return {
                            "error": (
                                f"Unknown queue reference '{queue_id}'. "
                                + describe_valid_queues(
                                    _queue_repo, normalize_project_id(project_id)
                                )
                            )
                        }

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

            # M2 — narrow the M2 ``job_types`` filter to the accepted
            # vocabulary (unknown values degrade to empty per §8.2).
            # Applied at the service layer for the legacy branch and
            # client-side for the resolver branch (the resolver's
            # ``list_work`` does not narrow by ``job_type`` today).
            normalised_job_types: list[str] | None = None
            if job_types is not None:
                valid = {"task", "message"}
                normalised_job_types = [
                    t.strip().lower() for t in job_types
                    if t and t.strip().lower() in valid
                ]
                if not normalised_job_types:
                    # Unknown / source-less filter — empty page
                    # (degrade contract; matches the HTTP router's
                    # shape for unknown filter values).
                    return {
                        "jobs": [],
                        "count": 0,
                    }

            work_resolver: "WorkResolverService | None" = getattr(
                job_service, "_work_resolver", None
            )
            if work_resolver is None:
                # Resolver not wired — degrade gracefully to the
                # JobItem-only list rather than crash, so a partial-wiring
                # daemon still serves traffic. The ``job_types`` filter
                # is applied in SQL by the repository.
                jobs = await job_service.list_jobs(
                    statuses=normalised_statuses,
                    project_id=project_id,
                    queue_id=resolved_queue_id,
                    offset=offset,
                    limit=limit,
                    include_deleted=include_deleted,
                    job_types=normalised_job_types,
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

            # M2 — same client-side narrowing on the resolver path
            # (the resolver's ``list_work`` does not accept
            # ``job_types`` today; the filter applies post-resolution
            # to keep the tool surface uniform with the legacy
            # branch).
            if normalised_job_types is not None:
                allowed_types = set(normalised_job_types)
                records = [
                    r for r in records
                    if getattr(r, "job_type", None) in allowed_types
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

            # 2a. M2 (mission-class, 2026-09-02, ``feature/mission-class``)
            #     — task-only gate per contract draft §3 (the "Plus"
            #     clause: ``job_continue`` accepts ``job_type='task'``
            #     only; mirrors continue via ``send_message``, the
            #     canonical mirror path).
            #
            #     Rationale: ``job_continue`` was a general-purpose
            #     "follow up on a terminal job by sending a new
            #     message to its instance" primitive before the
            #     mission split. Post-M2 the two paths are distinct:
            #
            #     * ``job_type='task'`` → the row IS a mission proxy
            #       (the work). ``job_continue`` is the canonical
            #       "send a follow-up instruction to the spawned
            #       instance" path.
            #     * ``job_type='message'`` → the row is a mirror
            #       receipt of a message the user sent to the
            #       instance. ``job_continue`` was the historical
            #       shortcut for "send another message" — but the
            #       canonical mirror path is ``send_message`` directly
            #       (the message itself IS the wire). Forcing
            #       ``job_continue`` through this gate keeps the
            #       wrong-predicate trap closed: an agent cannot
            #       accidentally use the work-side primitive to
            #       message.
            #
            #     Non-JobItem work (``kind != "job"`` — Task / report)
            #     is unaffected: the gate only fires for JobItem rows
            #     whose ``job_type`` is the mirror kind. ``record.job_type``
            #     is sourced from the resolver-backed WorkRecord, so
            #     the check is consistent with the four Fix-C read
            #     surfaces (§8.2). The fallback to ``old_job.job_type``
            #     covers the legacy branch when ``job_type`` is not on
            #     the WorkRecord (older test fixtures).
            if getattr(record, "job_type", None) == "message" or (
                record.kind == "job"
                and getattr(old_job, "job_type", None) == "message"
            ):
                return {
                    "error": (
                        f"job_continue is not supported for message-type jobs "
                        f"(job_id={old_job_id}, job_type='message'). "
                        "Use send_message(instance_id=..., message=...) "
                        "to send a follow-up message directly to the "
                        "instance — that is the canonical mirror path."
                    ),
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
                # F3 (P2.2 fix pass): never mint a user-origin source on the
                # empty-caller fallback — "api" arms the live gate via the
                # exact-match path.
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
            #
            #     SCOPE (fix-revive-guard-scope, 2026-09-05):
            #     single-sourced on the canonical "REVIVE-ONCE GUARD
            #     (quick-win #7, scoped)" block in
            #     ``daemon/tools/instance.py``. Mental model: a
            #     FAILURE-revive budget; this branch always passes
            #     ``prior_status="failed"`` (the W1 gate already guards
            #     on ``InstanceStatus.FAILED.value``) so the
            #     consume-vs-non-consume decision stays in the manager
            #     without a status re-read.
            if instance_meta.status == InstanceStatus.FAILED.value:
                manager.note_agent_tool_revive(
                    instance_id, prior_status=InstanceStatus.FAILED.value
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
            # Queue-alias resolution (queue_ref): ``queue_id`` may be a
            # system-queue alias. Known-name-miss → hard error (matches
            # this tool's ``ERROR: ...`` string shape); unknown non-alias
            # refs pass through untouched (existing not-found handling).
            resolved_queue_id = queue_id
            _queue_repo = getattr(queue_mgmt_service, "_queue_repo", None)
            if _queue_repo is not None:
                _queue = await asyncio.to_thread(
                    resolve_queue_ref, _queue_repo, project_id, resolved_queue_id
                )
                if _queue is not None:
                    resolved_queue_id = _queue.queue_id
                elif is_known_alias_name(resolved_queue_id):
                    return (
                        f"ERROR: Unknown queue reference '{queue_id}'. "
                        + describe_valid_queues(_queue_repo, project_id)
                    )

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
            queue = await queue_mgmt_service.get_queue(project_id=project_id, queue_id=resolved_queue_id)
            if queue is None:
                return f"ERROR: Queue {queue_id} not found in project {project_id}."

            result = await queue_mgmt_service.update_queue(
                project_id=project_id,
                queue_id=resolved_queue_id,
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
            # Queue-alias resolution (queue_ref): known-name-miss → hard
            # error with the valid-queue listing; unknown non-alias refs
            # pass through untouched (existing DLQ filter semantics).
            resolved_queue_id = queue_id
            if resolved_queue_id is not None:
                _queue_repo = getattr(job_service, "_queue_repo", None)
                if _queue_repo is not None:
                    _queue = resolve_queue_ref(_queue_repo, project_id, resolved_queue_id)
                    if _queue is not None:
                        resolved_queue_id = _queue.queue_id
                    elif is_known_alias_name(resolved_queue_id):
                        return {
                            "error": (
                                f"Unknown queue reference '{queue_id}'. "
                                + describe_valid_queues(_queue_repo, project_id)
                            )
                        }

            items, total_count = dead_letter_service.list_dlq(
                project_id=project_id,
                queue_id=resolved_queue_id,
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
        # C2 (2026-09-25, ``fix/mission-terminal-watch-report-publish``):
        # include ``"settled"`` in the ``needs_result`` gate so
        # message-mirror JobItem WorkRecords (per_kind_status_for
        # surfaces ``"settled"`` instead of ``"completed"`` for the
        # task → mirror split-semantics shape) also trigger the
        # last-assistant-message enrichment. Without this the
        # watch_job / watch_jobs path delivers a ``[JOB_EVENT]
        # settled ✓`` body with no ``Result:`` block when the row's
        # instance is still alive enough to have a captured assistant
        # message — exactly the same class of stranding as the
        # PROCESS_REPORT skip-path notify site
        # (``task_processor._skip_task_as_completed``). The C3
        # producer-side threading covers the notifier hot path;
        # this enrichment is the second-chance catch for callers
        # who reached the watch tools with an un-enriched record.
        needs_result = (
            getattr(record, "result_summary", None) is None
            and getattr(record, "status", None) in {"completed", "settled"}
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
        events: Annotated[list[str] | None, Field(
            default=None,
            description=(
                "Specific events to watch for (default: all terminal "
                "states + in_progress). M2 (mission-class): the value "
                "``mission_terminal`` is OPT-IN — when included, the "
                "watcher fires ONLY when admission AND mission "
                "liveness are BOTH terminal (contract draft §3.5). "
                "Default watchers (without ``mission_terminal``) keep "
                "transport-only semantics — back-compat preserved."
            ),
        )] = None,
    ) -> str:
        """Watch a job for lifecycle events. If the job is already in a terminal state, immediate notification is sent.

        Use tool_help("watch_job") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # M2 (mission-class) — events validation. ``mission_terminal``
            # is OPT-IN; the default event set stays transport-only
            # (``ALL_TERMINAL_STATES`` + ``in_progress``). An
            # unknown event name is rejected with a clear list of
            # accepted values, so a typo does not silently degrade to
            # "match nothing" — same fail-closed discipline as the
            # HTTP surface's unknown-filter rejection.
            from daemon.repositories.job_queue.watcher_models import (
                ALL_WATCHABLE_EVENTS,
            )
            accepted_events = set(ALL_WATCHABLE_EVENTS) | {"mission_terminal"}
            if events is not None:
                unknown = [e for e in events if e not in accepted_events]
                if unknown:
                    return (
                        f"Error: Unknown event(s) {unknown!r}. "
                        f"Accepted values: "
                        f"{sorted(accepted_events)}."
                    )

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
            if count >= MAX_WATCHES_PER_INSTANCE:
                return _watch_cap_error(count)

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
                # M2 — mission_terminal opt-in gating. When the
                # watcher opts in via ``events=['mission_terminal']``
                # (with or without other terminal events), fire ONLY
                # when both admission AND mission liveness are
                # terminal. Default watchers (no ``mission_terminal``
                # in their events) fire on the natural terminal
                # transport event, preserving back-compat.
                fire_mission_terminal = (
                    events is not None
                    and "mission_terminal" in events
                )
                if fire_mission_terminal:
                    if not _record_mission_is_terminal(record):
                        # Mission is not yet terminal — keep the
                        # watch alive (the watcher wants to fire
                        # ONLY when the mission reaches terminal, so
                        # do NOT notify now). The future terminal
                        # event will land via the standard
                        # notify_watchers path (with the gating
                        # enforced in work_notifier).
                        return (
                            f"Watch registered for job {job_id[:8]}... "
                            f"with mission_terminal gating; will notify "
                            f"when admission AND mission liveness are "
                            f"both terminal."
                        )
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

        The handle is tolerant (toolset reshape 2026-09-19, design §7):
        it may be a receipt job_id OR a mission_id — a mission handle
        resolves to every watched receipt of that mission and removes
        them all.

        Use tool_help("unwatch_job") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # Mission-handle branch (toolset reshape §7): if the handle
            # resolves as a mission_id (= instance_id), fan the removal
            # out over every watched receipt of that mission. Receipt-
            # keyed rows are what actually fires; a mission handle alone
            # would only match a stranded pre-reshape mission-keyed row.
            mission_resolver = getattr(manager, "_mission_resolver", None) if manager is not None else None
            if mission_resolver is not None:
                mission_record = _resolve_mission_record(
                    mission_resolver, job_id, context="unwatch_job"
                )
                if mission_record is not None and mission_record.mission_id == job_id:
                    removed = 0
                    task_repo = getattr(manager, "_task_repo", None) if manager is not None else None
                    if task_repo is not None:
                        tasks = task_repo.get_by_instance(job_id)
                        for task_row in tasks:
                            if watcher_repo.remove_watch(
                                task_row.work_id, current_instance_id
                            ):
                                removed += 1
                    # Best-effort: a stranded pre-reshape mission-keyed
                    # row (never fires) is cleaned up here too.
                    if watcher_repo.remove_watch(job_id, current_instance_id):
                        removed += 1
                    if removed:
                        return (
                            f"Stopped watching mission {job_id[:8]}... "
                            f"({removed} watch row(s) removed)."
                        )
                    return (
                        f"Not watching mission {job_id[:8]}... (no "
                        f"matching receipt watches)."
                    )

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

        Rows are labeled by resolving their handles (toolset reshape
        2026-09-19, design §7): ``mission handle`` (the row keys on a
        mission id), ``receipt of mission <id>`` (a receipt-keyed row
        and the mission it belongs to), or unlabeled (unresolvable
        receipt).

        Use tool_help("list_watched_jobs") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            watches = watcher_repo.get_watches_for_instance(current_instance_id)
            if not watches:
                return "No watched jobs."

            # Label each row by resolving its handle (mission vs
            # receipt). Best-effort: unwired resolver / degraded reads
            # fall back to the plain ``receipt`` label.
            mission_resolver = (
                getattr(manager, "_mission_resolver", None)
                if manager is not None
                else None
            )
            labeled: list[tuple[object, str]] = []
            for w in watches:
                label = "receipt"
                if mission_resolver is not None:
                    mission_record = _resolve_mission_record(
                        mission_resolver, w.job_id, context="list_watched_jobs"
                    )
                    if mission_record is not None and mission_record.mission_id == w.job_id:
                        label = "mission handle"
                    else:
                        try:
                            work_record = await job_service.get_work(w.job_id)
                        except Exception as exc:  # noqa: BLE001 — degraded → plain label
                            logger.warning(
                                "list_watched_jobs: work resolver raised for "
                                "handle=%r: %s — labeling as plain receipt",
                                w.job_id,
                                exc,
                            )
                            work_record = None
                        if work_record is not None and getattr(work_record, "instance_id", None):
                            label = (
                                f"receipt of mission "
                                f"{work_record.instance_id[:8]}..."
                            )
                labeled.append((w, label))

            result_lines = [f"Watching {len(watches)} job(s):"]
            for w, label in labeled:
                result_lines.append(
                    f"  - {w.job_id[:8]}... (events: {', '.join(w.watch_events)})"
                    + (f" — {label}" if label != "receipt" else "")
                )
            return "\n".join(result_lines)
        except Exception as e:
            return f"Error listing watched jobs: {str(e)}"
    list_watched_jobs._full_doc_ = _FULL_DOCS["list_watched_jobs"]


    @register_tool_category("job")
    @tool
    async def watch_jobs(
        job_ids: Annotated[list[str], Field(description="List of job IDs to watch")],
        events: Annotated[list[str] | None, Field(
            default=None,
            description=(
                "Specific events to watch for (default: all terminal "
                "states + in_progress). M2 (mission-class): the value "
                "``mission_terminal`` is OPT-IN — same semantics as "
                "``watch_job`` (contract draft §3.5)."
            ),
        )] = None,
    ) -> str:
        """Watch multiple jobs for lifecycle events. Bulk version of watch_job.

        Use tool_help("watch_jobs") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # M2 — events validation (same shape as ``watch_job``).
            from daemon.repositories.job_queue.watcher_models import (
                ALL_WATCHABLE_EVENTS,
            )
            accepted_events = set(ALL_WATCHABLE_EVENTS) | {"mission_terminal"}
            if events is not None:
                unknown = [e for e in events if e not in accepted_events]
                if unknown:
                    return (
                        f"Error: Unknown event(s) {unknown!r}. "
                        f"Accepted values: "
                        f"{sorted(accepted_events)}."
                    )

            # Enforce max 50 watches per instance
            count = watcher_repo.count_watches_for_instance(current_instance_id)
            if count + len(job_ids) > MAX_WATCHES_PER_INSTANCE:
                return _watch_cap_error(count, f"trying to add {len(job_ids)}")

            from daemon.services.work_status import is_terminal as _is_terminal

            # M2 — bulk ``mission_terminal`` opt-in: when the bulk
            # watcher opts in via ``mission_terminal``, fire ONLY
            # when both admission AND mission liveness are terminal.
            # Default watchers (no ``mission_terminal``) fire on the
            # natural terminal transport event — back-compat.
            fire_mission_terminal = (
                events is not None
                and "mission_terminal" in events
            )

            watched = []
            already_terminal = []
            held_for_mission = []

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
                    if fire_mission_terminal and not _record_mission_is_terminal(record):
                        # Mission is not yet terminal — keep the
                        # watch alive for the future terminal event.
                        watcher_repo.add_watch(jid, current_instance_id, events)
                        held_for_mission.append(jid)
                        continue
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
            if held_for_mission:
                parts.append(
                    f"{len(held_for_mission)} job(s) terminal on transport "
                    f"but held until mission liveness is also terminal "
                    f"(mission_terminal opt-in)."
                )
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
                    from daemon.services.timestamps import coerce_to_aware_utc

                    created = coerce_to_aware_utc(
                        datetime.fromisoformat(created_raw)
                    )
                    elapsed_seconds = (
                        datetime.now(UTC) - created
                    ).total_seconds()
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

            # wc-wake-report-integrity (T7 + B1 RESOLVED 2026-09-11):
            # the eligibility check accepts RUNNING (always) AND
            # WAITING_CHILDREN (B1: WC ALWAYS routes through durable
            # ``enqueue_message`` — the legacy ``ENSEMBLE_WC_WAKE_ENQUEUE``
            # flag-OFF RAM-FIFO injection route was REMOVED entirely).
            # Other statuses (IDLE, PAUSED, terminal) still hit the
            # error path with the rewritten wording. The constant
            # ``INJECTION_ELIGIBLE_STATUSES`` is ``{"running"}`` — the
            # constant stays single-home and config-free; the WC
            # acceptance is an explicit branch at the call site,
            # mirroring the HTTP / agent-tool lanes per the dispatch
            # directive.
            current_status = instance_meta.status
            if (
                current_status not in INJECTION_ELIGIBLE_STATUSES
                and current_status != "waiting_children"
            ):
                # B1 (2026-09-11): the error text is fixed — WC no
                # longer has a flag-OFF legacy branch to preserve
                # (the kill-switch was removed).
                return {
                    "error": (
                        f"Instance is {instance_meta.status} — job_inject "
                        "injects into RUNNING turns; WAITING_CHILDREN/IDLE/"
                        "terminal targets get the message enqueued or should "
                        "use job_continue. "
                        "Use job_continue for IDLE/PAUSED/terminal instances."
                    )
                }

            # B1 WAITING_CHILDREN branch: durable wake turn via
            # ``manager.enqueue_message`` (no flag state — B1 fix).
            # The ``has_instance_busy`` pre-check (mirrors
            # ``job_continue`` 5a, :975-995) makes a WC target that
            # already has a queued wake fail fast with a clean error
            # instead of silently queueing a second turn.
            if current_status == "waiting_children":
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
            #
            # DEFECT A (dispatch-lane stranding fix, 2026-09-14): a
            # ``running`` target with NO live graph consumer must NOT
            # take the RAM-FIFO lane — nothing would ever drain
            # ``_pending_injections`` and the message would be silently
            # stranded in memory (incident 2026-09-14: spawn-created
            # children cascade-paused + cascade-resumed without ever
            # being dispatched read ``running`` while graphless). Same
            # durable-wake treatment as the WC branch above: busy
            # pre-check, then ``enqueue_message`` (same-second task +
            # message materialization), returning the ``"enqueued"``
            # shape instead of ``"injected"``.
            if not manager.has_live_graph_task(instance_id):
                if getattr(manager, "_task_repo", None) is not None:
                    has_inflight = await asyncio.to_thread(
                        manager._task_repo.has_instance_busy, instance_id
                    )
                    if has_inflight:
                        return {
                            "error": (
                                f"Instance {instance_id} has a task still "
                                "in flight — wait for it to complete "
                                "first (job_inject busy pre-check on "
                                "graphless running target)."
                            )
                        }
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
                    "queued": True,
                }

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

    class JobAnswerInput(BaseModel):
        """Input schema for job_answer tool."""

        work_id: Annotated[
            str,
            Field(
                description=(
                    "The work_id whose instance asked the question "
                    "(matches the work_id on the "
                    "``[JOB_EVENT] Job ... question requested ❓`` line "
                    "that the watcher received)."
                )
            ),
        ]
        answers: Annotated[
            dict[str, str],
            Field(
                description=(
                    "User-supplied answers. Key by question id (the id "
                    "carried in the pending pack payload) — text-keyed "
                    "fallbacks are also accepted by the helper."
                )
            ),
        ]
        question_pack_id: Annotated[
            str,
            Field(
                description=(
                    "The pack id from the pending question payload "
                    "(``question_pack.id`` echoed in the "
                    "``[JOB_EVENT]`` body). Mandatory for the T1″ "
                    "stale-answers hijack guard; a mismatch is "
                    "rejected with ``QUESTION_PACK_MISMATCH``."
                )
            ),
        ]

    @register_tool_category("job")
    @tool(args_schema=JobAnswerInput)
    # Descriptions live ONLY in ``JobAnswerInput`` (the args_schema) —
    # the signature carries plain types so the two copies cannot drift
    # (tidier #4 — Medium, ``feature/job-answer-tool`` M-tidier round,
    # 2026-09-22). Mirrors the ``watch_mission`` precedent at
    # ``daemon/tools/job_queue.py:3104-3112``.
    async def job_answer(
        work_id: str,
        answers: dict[str, str],
        question_pack_id: str,
    ) -> dict:
        """Submit the orchestrator's answer to a pending question pack on a watched job.

        Use tool_help("job_answer") for details."""
        # The job_answer tool is the agent-facing counterpart of
        # ``POST /api/jobs/{work_id}/answer`` — both surfaces share the
        # SAME underlying helper ``answer_questions_via_instance``
        # (``daemon/routers/answer_helper.py``). There is no second
        # implementation. The seam is:
        #   1. write-pause guard 503 (also enforced inside the helper)
        #   2. ``work_resolver.resolve_work(work_id)`` → ``WorkRecord``
        #      (404 when record is None or ``record.instance_id`` is None)
        #   3. project-scoped access via the SAME ``_check_job_access``
        #      helper the four visibility tools (``job_messages`` /
        #      ``job_tree`` / ``job_progress`` / ``job_inject``) use.
        #   4. ``answer_questions_via_instance(manager, instance_id,
        #      answers, question_pack_id, live_hub, resume_message)``.
        # The helper raises ``HTTPException`` on every guarded failure —
        # we catch and convert to clear agent-readable error STRINGS so
        # the tool never raises mid-tool-call (LangGraph ``@tool``
        # tools that raise bubble up the graph task). The error code +
        # human-readable message are both included so an agent can
        # branch on the exact cause (400 stale-pack → re-fetch the
        # pending pack id; 410 terminal → give up; 503 → daemon in
        # migration, retry later).
        try:
            if manager is None:
                return {
                    "error": (
                        "Instance manager not available — job_answer "
                        "requires manager access"
                    )
                }

            # 0. Write-pause guard (503 migration posture) — mirrors
            #    the HTTP route's pre-flight (jobs_management.py:1148).
            #    We refuse before doing any DB work; the helper itself
            #    also enforces this so the seam is closed end-to-end.
            if manager.is_write_paused:
                return {
                    "error": (
                        f"503 {WRITE_PAUSED_TOKEN}: writes are paused "
                        f"for database migration — retry once the "
                        f"daemon leaves migration mode"
                    )
                }

            # 1. Resolve work_id → instance_id via the SAME
            #    ``work_resolver`` the HTTP route uses
            #    (``jobs_management.py:1154-1175``). The helper accepts
            #    an instance_id only; work_id → instance_id translation
            #    is the one piece of routing that differs between the
            #    instance-addressed and job-addressed surfaces, so it
            #    stays at the tool boundary.
            work_resolver = getattr(manager, "_work_resolver", None)
            if work_resolver is None:
                return {
                    "error": (
                        "503 SERVICE_UNAVAILABLE: work resolver not "
                        "wired on this daemon"
                    )
                }

            try:
                record = await asyncio.to_thread(
                    work_resolver.resolve_work, work_id
                )
            except Exception as resolve_err:  # noqa: BLE001
                # Resolver RAISE is a transient DB failure — surface as
                # 500 so the caller knows to retry, NOT as a not-found
                # (the brief distinguishes the two).
                # Suffix-preserving truncation (judgment call, tidier
                # #11 — Medium, 2026-09-22): bound ``str(resolve_err)``
                # so internal traceback fragments do not leak into the
                # 500 error path. The full exception is already on the
                # ``logger.error(... exc_info=True)`` line above; this
                # only bounds the agent-readable surface. 500-char cap
                # is a defensive ceiling — typical ``resolve_work``
                # failures (DB connection error, row not in repository)
                # are <200 chars.
                err_text = str(resolve_err)
                if len(err_text) > 500:
                    err_text = err_text[:497] + "..."
                logger.error(
                    "job_answer: work_resolver.resolve_work(%s) "
                    "raised: %s",
                    work_id,
                    resolve_err,
                    exc_info=True,
                )
                return {
                    "error": (
                        f"500 INTERNAL_ERROR: work resolver raised "
                        f"while resolving {work_id!r}: {err_text}"
                    )
                }

            if record is None:
                return {
                    "error": (
                        f"404 JOB_NOT_FOUND: no work unit found for "
                        f"work_id {work_id!r}"
                    )
                }

            # MINOR-13 (jobs_management.py:1176-1191): a WorkRecord
            # without an instance_id would AttributeError deep inside
            # the helper; surface a typed 404 instead.
            instance_id = getattr(record, "instance_id", None)
            if not instance_id:
                return {
                    "error": (
                        f"404 JOB_NOT_FOUND: work unit {work_id!r} has "
                        f"no owning instance — the answer cannot be "
                        f"routed"
                    )
                }

            # 2. Access control — REUSE the SAME ``_check_job_access``
            #    helper the four visibility tools use. The check is
            #    applied AFTER work resolution (so the caller pays
            #    the resolve cost first) but BEFORE the helper call
            #    (so a denied caller never reaches the answer flow).
            deny = _check_job_access(manager, current_instance_id, record)
            if deny is not None:
                return deny

            # 3. The actual answer flow — delegate to the shared
            #    helper. ``live_hub`` is read defensively via getattr
            #    so the tool still works in test/partial-bootstrap
            #    contexts where the hub is not wired (mirrors the
            #    ``todo_tools`` / ``question_tools`` pattern at
            #    ``daemon/tools/instance.py:4668-4688`` — neither
            #    the HTTP nor the agent-tool lane has a different
            #    way to reach the hub; both go through
            #    ``manager._live_hub``). When ``None`` the helper's
            #    SSE emission is a no-op (best-effort, guarded) so
            #    callers still get the answer + resume. The HTTP
            #    route's ``request.app.state.live_hub`` is the SAME
            #    object — ``daemon/api.py:1261`` wires
            #    ``app.state.live_hub = manager._live_hub`` — so the
            #    push-lane delivery is preserved byte-for-byte across
            #    both surfaces (no SSE silently dropped).
            live_hub = getattr(manager, "_live_hub", None)

            from daemon.routers.answer_helper import answer_questions_via_instance

            try:
                result = await answer_questions_via_instance(
                    manager=manager,
                    instance_id=instance_id,
                    answers=answers,
                    question_pack_id=question_pack_id,
                    live_hub=live_hub,
                    resume_message=None,
                )
            except HTTPException as exc:
                # Convert the typed HTTPException the helper raised
                # into a clear agent-readable error string. The HTTP
                # ``detail`` carries ``ErrorResponse(code=..., message=
                # ...)`` — surface the code verbatim so the agent
                # can branch on it (the brief calls out 400/404/410/503
                # as the codes that matter for the answer flow).
                return _format_answer_http_error(exc, work_id, instance_id)

            # Job-addressed envelope (mirrors the HTTP route at
            # ``jobs_management.py:1225`` — surface the work_id the
            # caller used alongside the helper's instance-addressed
            # body).
            return {"work_id": work_id, **result}
        except Exception as e:  # noqa: BLE001
            # Defensive backstop — never let the tool-call raise into
            # the graph. A tool that raises mid-call bubbles the
            # exception into the graph task and strands the turn
            # (the F-pattern in earlier tool-history incidents); the
            # agent-readable error string keeps the turn alive.
            logger.error(
                "job_answer(%s) failed: %s", work_id, e, exc_info=True
            )
            return {
                "error": (
                    f"Internal error submitting answer for {work_id}: "
                    f"{type(e).__name__}"
                )
            }

    job_answer._full_doc_ = _FULL_DOCS["job_answer"]

    # ── job_pause / job_resume (job-pause-resume-tools) ──
    #
    # Job-id-shaped twin of the agent-facing pause_instance/resume_instance
    # closures (which themselves mirror the HTTP routes). Shaped on JOB_ID
    # instead of INSTANCE_ID. See the closure docstrings for the full
    # contract (gate order, wedge-guard, access-control pattern).
    #
    # Invariants (VERIFIED in the service — do NOT duplicate here):
    #   * Cascade pauses/resumes the WHOLE instance lineage (lifecycle
    #     service at ``daemon/services/instance_lifecycle.py`` — pause /
    #     resume both enumerate via repo.get_cascade_tree_ids so the
    #     ENSEMBLE_CASCADE_LINEAGE kill-switch is honored).
    #   * Already-paused / terminal / never-dispatched nodes are
    #     skipped idempotently into skipped_ids (lifecycle service).
    #   * Resume of a running-turn instance spins a message job to
    #     continue the turn → status "resuming".
    #   * Resume of a parked parent via the silent child lane returns
    #     status "silent_resume" (internal_child_noop).
    #   * Job state (admission_state / status) is NOT touched by these
    #     tools — pause/resume operates on the instance lineage, NOT on
    #     the job row.

    @register_tool_category("job")
    @tool
    async def job_pause(
        job_id: Annotated[str, Field(description="The work_id of an ACTIVE (non-terminal, instance-bearing) job to pause. Its whole instance lineage pauses with it.")],
    ) -> dict:
        """Pause the instance behind an ACTIVE job and cascade to its lineage. Use tool_help("job_pause") for details."""
        # Mirror the HTTP pause endpoint's write-paused migration gate
        # (routers/instances.py:660). Parity, not re-implementation —
        # the agent tool and the FE route refuse the same way.
        if manager is None:
            return {"error": "Instance manager not available — job_pause requires manager access", "paused": False}
        if getattr(manager, "is_write_paused", False):
            return {"error": f"503 {WRITE_PAUSED_TOKEN}: writes are paused for database migration", "paused": False}
        # 1. Resolve job_id → WorkRecord via the same resolver
        #    job_cancel uses. The brief requires resolver parity, not a
        #    new lookup path.
        try:
            record = await job_service.get_work(job_id)
        except Exception as e:  # noqa: BLE001
            logger.error("job_pause: job_service.get_work(%s) raised: %s", job_id, e, exc_info=True)
            return {"error": f"Failed to resolve {job_id}: {type(e).__name__}: {e}", "paused": False}
        if record is None:
            return {"error": f"Job not found: {job_id}", "paused": False}

        # Kind refusal — pause operates on the JOB-shaped JobItem row.
        # Task / turn / report rows have no instance_id to pause (Tasks
        # use cooperative cancel_requested via job_cancel instead).
        # Mirror the job_cancel / job_retry / job_delete / job_restore
        # siblings at :1632, :1690, :1715, :1740, :1794 — fail-closed
        # with a precise error naming the rejected kind (tidier round
        # 2026-09-25, item 2).
        if record.kind != "job":
            return {
                "error": (
                    f"Job {job_id[:8]}... is task-type work ({record.kind}), "
                    "which has no pause path — use job_cancel for tasks"
                ),
                "paused": False,
            }

        # 2. D1 — QUEUED job (no instance yet). The job is in flight
        #    (pending/queued) but has no instance_id to pause. Refuse
        #    with a clear actionable error; suggest queue-level pause.
        #    Job state MUST remain UNTOUCHED — we do NOT touch any
        #    job-side field here (no cancel, no soft-delete, no
        #    status mutation). Pure early-return.
        instance_id = getattr(record, "instance_id", None)
        if not instance_id:
            return {
                "error": (
                    f"Job {job_id[:8]}... has not started an instance yet "
                    f"(status={record.status}) — use queue_pause(queue_id) "
                    "to prevent it from starting instead"
                ),
                "paused": False,
            }

        # 3. Terminal job → clear error, refuse as no-op (brief: "Terminal
        #    job (DONE/DEAD etc.): clear error, refuse as no-op.").
        from daemon.services.work_status import is_terminal as _is_terminal
        if _is_terminal(record.status):
            return {
                "error": (
                    f"Job {job_id[:8]}... is in a terminal state "
                    f"({record.status}) — cannot be paused"
                ),
                "paused": False,
            }

        # 4. Access control — REUSE the SAME ``_check_job_access`` helper
        #    the four visibility tools use. Same project-scoped rule.
        deny = _check_job_access(manager, current_instance_id, record)
        if deny is not None:
            return {**deny, "paused": False}

        # 5. Relies on facade defaults (cascade_to_root=True,
        #    suspension_reason=None) — mirrors the HTTP pause endpoint;
        #    no new flags invented here.
        result = await manager.pause_instance_cascade(instance_id)

        # D2 (wedge guard) — surface the destructive-consequence wedge
        # chain in the success response so an automated caller cannot
        # miss it. The docstring already carries the long form; the
        # response field is the no-scroll-required short reminder.
        return {
            "paused": True,
            "paused_ids": result["paused_ids"],
            "skipped_ids": result["skipped_ids"],
            "instance_id": instance_id,
            "_wedge_note": (
                "Pausing is not forever-safe. While paused awaiting an "
                "answer, the STUCK_AWAITING_ANSWER wedge-guard fires "
                "stuck_awaiting_answer events at pause-time and again at "
                "~30 minutes; at ~60 minutes it ESCALATES by terminating "
                "the asker (terminal_reason=wedge_guard_terminated). The "
                "daemon does NOT auto-resume paused work. Document the "
                "intent and resume deliberately."
            ),
        }

    job_pause._full_doc_ = _FULL_DOCS["job_pause"]

    @register_tool_category("job")
    @tool
    async def job_resume(
        job_id: Annotated[str, Field(description="The work_id of an ACTIVE job whose instance is paused. Its whole instance lineage resumes with it.")],
    ) -> dict:
        """Resume the instance behind a PAUSED job and cascade to its lineage. Use tool_help("job_resume") for details."""
        if manager is None:
            return {"error": "Instance manager not available — job_resume requires manager access", "resumed": False}
        # Mirror the HTTP resume endpoint's write-paused migration gate
        # (routers/instances.py:693). Parity, not re-implementation.
        if getattr(manager, "is_write_paused", False):
            return {"error": f"503 {WRITE_PAUSED_TOKEN}: writes are paused for database migration", "resumed": False}
        # 1. Resolve job_id → WorkRecord (same resolver job_cancel uses).
        try:
            record = await job_service.get_work(job_id)
        except Exception as e:  # noqa: BLE001
            logger.error("job_resume: job_service.get_work(%s) raised: %s", job_id, e, exc_info=True)
            return {"error": f"Failed to resolve {job_id}: {type(e).__name__}: {e}", "resumed": False}
        if record is None:
            return {"error": f"Job not found: {job_id}", "resumed": False}

        # Kind refusal — resume operates on the JOB-shaped JobItem row.
        # Task / turn / report rows have no instance_id to resume.
        # Mirror the job_cancel / job_retry / job_delete / job_restore
        # siblings at :1632, :1690, :1715, :1740, :1794 — fail-closed
        # with a precise error naming the rejected kind (tidier round
        # 2026-09-25, item 2).
        if record.kind != "job":
            return {
                "error": (
                    f"Job {job_id[:8]}... is task-type work ({record.kind}), "
                    "which has no resume path"
                ),
                "resumed": False,
            }

        # 2. D1 — QUEUED job (no instance yet). Same refusal as job_pause
        #    (the QUEUED branch is symmetric — no instance to pause OR
        #    resume). Job state MUST remain UNTOUCHED.
        instance_id = getattr(record, "instance_id", None)
        if not instance_id:
            return {
                "error": (
                    f"Job {job_id[:8]}... has not started an instance yet "
                    f"(status={record.status}) — resume requires an instance_id"
                ),
                "resumed": False,
            }

        # 3. Defect-1 guard (review finding #4 on the sibling
        #    ``resume_instance`` tool at instance.py:4667-4695): refuse
        #    early when the target has a pending question pack. The
        #    standard resume path routes ``resume_processing_job`` →
        #    ``answer_gate_existing_turn``, which would treat the literal
        #    "resume" message as answer content. The HTTP route supersedes
        #    the gate via enqueue + cascade (gate-supersession branch);
        #    the agent surface refuses instead and points at
        #    ``job_answer`` (whose underlying helper performs the SAME
        #    gate-supersession flow before resuming). The check is
        #    best-effort: introspection errors fail OPEN so a broken
        #    question-manager surface cannot wedge restart recovery.
        # NOTE (tidier polish 2026-09-25, item 1): the guard order
        #    here is write-paused (:3521) → question-pack, matching the
        #    FE ``/instances/{id}/resume`` handler at
        #    routers/instances.py:693-694 + :710+. The sibling
        #    ``resume_instance`` tool inverts this (question-pack first);
        #    flagged for separate commission.
        qm = getattr(manager, "_question_manager", None)
        if qm is not None:
            try:
                pending_pack = qm.get_question_pack(instance_id)
            except Exception as qm_err:  # noqa: BLE001 — fail-open contract
                logger.warning(
                    "job_resume: question-pack introspection failed for "
                    f"{instance_id[:8]}...; proceeding without gate guard: {qm_err}"
                )
            else:
                if pending_pack is not None and pending_pack.status == "pending":
                    return {
                        "error": (
                            "instance has a pending question; answer it via "
                            "job_answer(work_id) instead of job_resume"
                        ),
                        "resumed": False,
                        "instance_id": instance_id,
                    }

        # 4. Access control — REUSE the SAME ``_check_job_access`` helper.
        deny = _check_job_access(manager, current_instance_id, record)
        if deny is not None:
            return {**deny, "resumed": False}

        # 5. Mirror the FE plain-path resume call shape exactly:
        #    (routers/instances.py:823-866) target turn continuation
        #    (silent=False, message="resume") → cascade flip →
        #    silent child resumes (silent=True). D3 (message injection)
        #    is "resume" verbatim — FE-identical by construction.
        try:
            job_result = await manager.resume_processing_job(
                instance_id,
                message="resume",
                silent=False,
            )
        except Exception as e:  # noqa: BLE001 — endpoint parity
            job_result = {"status": "error", "error": str(e)}
        if job_result is None:
            # Mirror the HTTP resume handler's debug log
            # (routers/instances.py:836-839) — same phrasing, same
            # instance-id truncation, same "was IDLE/WAITING_CHILDREN"
            # context. Both surfaces (tool vs HTTP) must produce
            # identical log lines for consistent production debugging.
            logger.debug(
                f"job_resume: resume_processing_job returned None for "
                f"{instance_id[:8]}... (was IDLE/WAITING_CHILDREN)"
            )
            job_result = {"status": "no_active_job"}

        result = await manager.resume_instance_cascade(instance_id)
        target_id = result.get("target_id", instance_id)

        # 6. Silent child resumes — non-target children wake from
        #    checkpoint silently. Same loop as the HTTP endpoint
        #    (routers/instances.py:851-866) and the ``resume_instance``
        #    tool (instance.py:4737-4748).
        resume_results = {instance_id: job_result}
        for rid in result["resumed_ids"]:
            if rid == target_id:
                continue  # already handled above
            child_result = await manager.resume_processing_job(
                rid,
                message="resume",
                silent=True,
            )
            if child_result is None:
                child_result = {"status": "no_active_job"}
            resume_results[rid] = child_result

        return {
            "resumed": True,
            "resumed_ids": result["resumed_ids"],
            "skipped_ids": result["skipped_ids"],
            "target_id": target_id,
            "resume_results": resume_results,
        }

    job_resume._full_doc_ = _FULL_DOCS["job_resume"]

    return [
        job_create, job_get, job_list, job_cancel, job_retry,
        job_delete, job_restore, queue_list, queue_create,
        queue_update, dlq_list, dlq_replay,
        job_continue,   # moved to end of non-watch tools (was index 7)
        job_messages, job_tree, job_progress, job_inject,
        watch_job, unwatch_job, list_watched_jobs, watch_jobs,
        job_answer,     # appended at END — never insert (positional-index trap)
        job_pause,      # job-pause-resume-tools (2026-09-25) — appended at END
        job_resume,     # job-pause-resume-tools (2026-09-25) — appended at END
    ]


def create_mission_watch_tools(
    job_service: "JobQueueService",
    mission_resolver: "MissionResolver",
    task_repo: "TaskRepository | None" = None,
    watcher_repo: "JobWatcherRepository | None" = None,
    current_instance_id: str = "",
) -> list:
    """Create the mission-watch tool (``watch_mission``), wired against
    injected services.

    Toolset-reshape (2026-09-19, ``feature/mission-watch-toolset``; design
    ``.agents/shared/planning/watch-notification-reliability/toolset-reshape-design.md``
    §1/§3/§4): ``watch_mission`` is THE durable watch for mission-shaped
    work. It is a **resolver front-end + registration-time fan-out** —
    NOT a new watcher table and NOT a mission-keyed row:

    * ``target`` accepts a mission_id (= instance_id) OR a job reference
      (the receipt ``job_create``/``job_continue`` returned — the same
      resolver front-end pattern ``job_continue`` uses).
    * The resolved mission's current receipts are enumerated via
      ``task_repo.get_by_instance`` (ALL Tasks, newest first, no status
      filter — terminal Tasks persist, and mirror receipts are
      ``Task.work_id`` rows too), then filtered to currently-LIVE
      receipts (``get_work`` + terminal-status check): ONE
      ``job_watchers`` row is registered per LIVE receipt with
      ``events=["mission_terminal"]`` (HOLD semantics — the engine's
      ``work_notifier`` gate fires the row only when the mission's
      liveness is terminal); already-settled receipts arm nothing.
    * The engine's notify path is strictly receipt-keyed
      (``get_watchers_for_job`` queries ``WHERE job_id == work_id``), so
      a mission-keyed row would never resolve and strand silently —
      receipt-keyed rows are the only shape that fires.
    * ``add_watch`` is an atomic UPSERT on (job_id, instance_id), so a
      re-watch after ``job_continue`` updates existing rows and mints
      rows only for genuinely new receipts.
    * The 50-watch cap is replicated counting EVERY row minted — the
      terminal filter runs first, so only LIVE receipts mint rows
      against the cap (a 99-settled/1-live mission arms 1 row).
    * An already-terminal mission is a NO-REPLAY short-circuit: settled
      receipts mint no rows and fire nothing — the tool reply itself
      carries the terminal reason/status (live receipts of a
      dead_letter-since-revived mission stay armed for the next flip).

    Mission-not-yet-born edge (design §2b): a receipt from ``job_create``
    resolves with ``instance_id=None`` until dispatch; registering on
    that pre-generated receipt UUID is valid by construction — its Task
    row (``work_id = job_id``) lands at dispatch and enters the engine's
    terminal candidate set. A mission handle with zero receipts has
    nothing valid to key on (mission-keyed rows never resolve) and is
    rejected with an explicit message.

    This factory mirrors the ``create_mission_tools`` assembly precedent
    (``daemon/tools/missions.py``): one standalone factory with injected
    dependencies, appended to the daemon-wide tool list by
    ``daemon/tools/instance.py``. It deliberately lives beside the other
    watch tools (``watch_job`` / ``unwatch_job`` / ``list_watched_jobs``)
    because it WRITES ``job_watchers`` rows — the mission tools module
    is contractually read-only.

    Args:
        job_service: JobQueueService for work resolution
            (``get_work``) and immediate terminal notification
            (``notify_watchers``).
        mission_resolver: The wired-in :class:`MissionResolver` —
            resolves ``target`` onto the mission read-model (liveness +
            terminal_reason, including the W4 ``dead_letter`` flip).
        task_repo: TaskRepository for receipt enumeration
            (``get_by_instance``). Optional so partial-wiring test
            doubles degrade to an explicit error rather than crash.
        watcher_repo: JobWatcherRepository for registration. Optional
            (same degrade posture as ``watch_job``).
        current_instance_id: The watching instance's ID — every minted
            row keys on it.

    Returns:
        A list with the single ``watch_mission`` tool callable.
    """

    class WatchMissionInput(BaseModel):
        """Input schema for watch_mission tool."""
        target: Annotated[str, Field(
            description=(
                "What to watch: a mission_id (= instance_id) OR a job "
                "reference — the job_id receipt returned by job_create / "
                "job_continue. The receipt form works even before the "
                "mission is dispatched."
            )
        )]
        events: Annotated[list[str] | None, Field(
            default=None,
            description=(
                "Watch events. Default (recommended): ['mission_terminal'] "
                "— the watch fires ONLY when admission AND mission "
                "liveness are both terminal (HOLD semantics). Explicit "
                "transport event names are accepted for advanced use."
            ),
        )] = None

    @register_tool_category("mission")
    @tool(args_schema=WatchMissionInput)
    # Descriptions live ONLY in ``WatchMissionInput`` (the args_schema) —
    # the signature carries plain types so the two copies cannot drift
    # (tidier #6).
    async def watch_mission(
        target: str,
        events: list[str] | None = None,
    ) -> str:
        """Watch a MISSION (not a receipt) and be revived at mission-terminal.

        Accepts a mission_id or the job_id receipt returned by
        job_create / job_continue, and arms one watcher row per
        currently-live receipt (already-settled receipts are skipped —
        no replay of historical receipts).

        Use tool_help("watch_mission") for details."""
        try:
            if watcher_repo is None:
                return "Error: Watch functionality not available"
            if not current_instance_id:
                return "Error: No instance context"

            # Events validation — same fail-closed shape as ``watch_job``
            # (an unknown event name is rejected, never silently degraded
            # to "match nothing"). Default = mission-terminal HOLD.
            from daemon.repositories.job_queue.watcher_models import (
                ALL_WATCHABLE_EVENTS,
            )
            accepted_events = set(ALL_WATCHABLE_EVENTS) | {"mission_terminal"}
            effective_events = (
                list(events) if events else ["mission_terminal"]
            )
            unknown = [e for e in effective_events if e not in accepted_events]
            if unknown:
                return (
                    f"Error: Unknown event(s) {unknown!r}. "
                    f"Accepted values: {sorted(accepted_events)}."
                )

            # ── Resolver front-end (job_continue pattern) ─────────────
            # target may be a mission_id (= instance_id) or a job
            # reference. Mission side first, then the work side.
            mission_id: str | None = None
            mission_record: "MissionRecord | None" = None
            pre_dispatch_receipt: str | None = None
            candidate = _resolve_mission_record(
                mission_resolver, target, context="watch_mission"
            )
            if candidate is not None and candidate.mission_id == target:
                mission_id = target
                mission_record = candidate
            else:
                record: "WorkRecord | None" = await job_service.get_work(target)
                if record is None:
                    return (
                        f"Error: Could not resolve {target[:8]}... as a "
                        "mission_id or job reference."
                    )
                if record.instance_id:
                    mission_id = record.instance_id
                    mission_record = _resolve_mission_record(
                        mission_resolver, mission_id, context="watch_mission"
                    )
                else:
                    # Pre-dispatch receipt (job_create returned, instance
                    # not minted yet): the receipt UUID IS the future
                    # mission's first task receipt (its Task row lands at
                    # dispatch with work_id = job_id). Register on it —
                    # mission-not-born means not terminal, so the watch
                    # simply waits. A mission_id key would strand here
                    # (watcher rows resolve by job_id only).
                    pre_dispatch_receipt = target

            # ── Pre-mission registration ──────────────────────────────
            if pre_dispatch_receipt is not None:
                count = watcher_repo.count_watches_for_instance(
                    current_instance_id
                )
                if count >= MAX_WATCHES_PER_INSTANCE:
                    return _watch_cap_error(count)
                watcher_repo.add_watch(
                    pre_dispatch_receipt, current_instance_id, effective_events
                )
                return (
                    f"Watch registered on receipt "
                    f"{pre_dispatch_receipt[:8]}... (mission not yet "
                    f"dispatched). Will notify when admission AND mission "
                    f"liveness are both terminal."
                )

            # ── Receipt enumeration (registration-time fan-out) ───────
            if task_repo is None:
                return (
                    "Error: Receipt enumeration unavailable (task "
                    "repository not wired). Watch the job_id receipt "
                    "returned by job_create instead."
                )
            tasks = task_repo.get_by_instance(mission_id)
            receipts = [t.work_id for t in tasks]
            if not receipts:
                # Nothing valid to key on — mission-keyed rows never
                # resolve (the engine queries job_watchers by job_id).
                # Reject explicitly rather than strand a row silently.
                return (
                    f"Error: Mission {mission_id[:8]}... has no receipts "
                    "(no Task rows exist for it yet). Create work with "
                    "job_create and watch_mission the returned job_id — "
                    "the receipt registers before the mission exists."
                )

            # ── F1: registration-time terminal filter (2026-09-23) ────
            # Arm ONLY currently-live receipts. A receipt whose task
            # status is ALREADY terminal at registration time settled
            # in a previous epoch — arming it makes every
            # mission-instance terminal flip (chat missions flip
            # ``completed`` after EVERY turn and revive on the next
            # message) re-fire the whole historical receipt set
            # (incident 2026-09-23: 9/14-row duplicate [JOB_EVENT]
            # bursts; the documented re-call-after-job_continue
            # workflow UPSERT-recreated the rows each turn). With the
            # filter the re-call is a DELTA-ARM: only new live receipts
            # get rows, so re-calling can no longer resurrect settled
            # receipts. An unresolvable receipt (``get_work`` → None)
            # is treated as live — arming is harmless (notify resolves
            # the work record first and no-ops), while skipping could
            # silently drop a genuinely-live receipt.
            from daemon.services.work_status import is_terminal as _is_terminal

            live_receipts: list[str] = []
            settled_count = 0
            for receipt_work_id in receipts:
                receipt_record = await job_service.get_work(receipt_work_id)
                if (
                    receipt_record is not None
                    and _is_terminal(receipt_record.status)
                ):
                    settled_count += 1
                else:
                    live_receipts.append(receipt_work_id)

            # ── 50-watch cap — count EVERY row minted ─────────────────
            count = watcher_repo.count_watches_for_instance(current_instance_id)
            if count + len(live_receipts) > MAX_WATCHES_PER_INSTANCE:
                return _watch_cap_error(
                    count, f"mission has {len(live_receipts)} receipt watch(es)"
                )

            # ── Register one row per LIVE receipt (UPSERT-safe) ───────
            for receipt_work_id in live_receipts:
                watcher_repo.add_watch(
                    receipt_work_id, current_instance_id, effective_events
                )

            # ── Already-terminal mission → NO historical replay (F1) ──
            # Terminal set {completed, failed, cancelled, dead_letter}:
            # ``terminal_reason`` is non-None exactly when the mission is
            # terminal (it mirrors the liveness for terminal instances
            # and flips to ``dead_letter`` under the W4 hazard) — EXCEPT
            # the dead_letter-since-revived row: the resolver keeps
            # ``terminal_reason="dead_letter"`` on a mission whose
            # liveness has since returned to non-terminal (revive; W4
            # hazard encoding). Replying "already terminal" to a live,
            # since-revived mission is misleading — cross-check liveness
            # before the short-circuit (M2, review council 2026-09-19).
            # v0.13.12 mission-live guard (9e596604) composes unchanged:
            # a dead_letter-since-revived mission arms its live receipts
            # above and waits. What F1 removes is the register-then-
            # NOTIFY arm: with settled receipts skipped at registration
            # there is nothing historical left to fire, so an
            # already-terminal mission replays nothing (its live
            # receipts — the contradictory W4-adjacent shape — stay
            # armed for the next flip instead of being fired mid-call).
            terminal_reason = getattr(mission_record, "terminal_reason", None)
            liveness = getattr(mission_record, "liveness", None)
            mission_actually_terminal = (
                terminal_reason is not None
                and liveness in {"completed", "failed", "cancelled"}
            )
            if mission_actually_terminal:
                return (
                    f"Mission {mission_id[:8]}... is already terminal "
                    f"({terminal_reason}). Armed {len(live_receipts)} "
                    f"live receipt(s), {settled_count} already-settled "
                    f"receipt(s) skipped — no historical replay."
                )

            skipped_note = (
                f"; {settled_count} already-settled receipt(s) skipped"
                if settled_count
                else ""
            )
            return (
                f"Mission watch registered: armed {len(live_receipts)} "
                f"live receipt(s) of mission {mission_id[:8]}... "
                f"(events: {', '.join(effective_events)})"
                f"{skipped_note}. Will notify at mission-terminal "
                f"liveness — the row is HELD (NOT claimed at "
                f"receipt-settle) until the canonical "
                f"``evaluate_mission_live`` guard confirms the parent "
                f"instance + every descendant is terminal. "
                f"Re-call watch_mission after job_continue — new "
                f"receipts are not auto-watched; the re-call is a "
                f"delta-arm (already-settled receipts are skipped, "
                f"never replayed)."
            )
        except Exception as e:
            return f"Error watching mission: {str(e)}"
    watch_mission._full_doc_ = (
        "Watch a MISSION (not a receipt) for mission-terminal events.\n\n"
        "Resolver front-end: ``target`` accepts a mission_id (= "
        "instance_id) OR a job reference (the job_id receipt returned "
        "by job_create / job_continue — the receipt form works even "
        "before the mission is dispatched). The tool resolves the "
        "mission, enumerates EVERY receipt that exists at call time "
        "(all Task.work_ids for the mission's instance), and arms ONE "
        "job_watchers row per currently-LIVE receipt with events="
        "['mission_terminal'] (HOLD semantics: the row fires only when "
        "admission AND mission liveness are both terminal). Receipts "
        "that are ALREADY terminal at call time are skipped — they "
        "settled in a previous epoch and are never replayed.\n\n"
        "Semantics (C1, 2026-09-25, ``fix/mission-terminal-watch-report-publish``):\n"
        "    * A ``mission_terminal`` watcher row is HELD (DB-row "
        "preserved) until the mission's liveness is genuinely terminal. "
        "Receipt settlement alone does NOT claim the row — the row "
        "fires when the canonical ``evaluate_mission_live`` guard "
        "(``daemon/services/mission_live_guard.py``) confirms the "
        "parent instance is terminal AND every descendant is terminal. "
        "Pre-C1 the row was claimed/deleted at the FIRST receipt "
        "settlement — that lost mission_terminal events when receipt "
        "settlement preceded mission-terminal (the 2026-09-25 "
        "incident pattern, mission 36be8aef). C1 replaces that proxy "
        "check with the canonical guard.\n"
        "    * Multi-kind retire rule (C1 commission-mandated, "
        "supersedes A1 closure): rows subscribing to BOTH a transport "
        "kind AND ``mission_terminal`` are HELD until the LAST firing "
        "event (``mission_terminal``). The transport-kind fire is "
        "delivered read-only (no CAS claim); the mission-terminal fire "
        "is the LAST firing event and CAS-claims the row. Pre-C1 the "
        "row was CAS-claimed at receipt-settle and the mission-terminal "
        "fire was silently DROPPED; C1 reverses that.\n"
        "    * One mission-terminal produces N [JOB_EVENT]s for N "
        "watched receipts — the FIRST event after your watch is the "
        "signal; the rest are echoes. Act once.\n"
        "    * Re-call watch_mission after every job_continue — "
        "receipts minted after this call are NOT auto-watched. The "
        "re-call is a delta-arm: already-settled receipts are "
        "skipped, never replayed, so re-calling cannot duplicate "
        "deliveries.\n"
        "    * Delivery is AT-MOST-ONCE with possible delay: the "
        "registration-time classification gates ROW EXISTENCE only — "
        "the emit-time CAS claim is the authoritative exactly-once "
        "gate. A receipt that settles between classification and "
        "mint delivers at the NEXT mission-terminal flip or boot "
        "sweep (delayed, never duplicated).\n"
        "    * A revived mission needs a FRESH watch_mission — the row "
        "is HELD (NOT claimed/deleted) at receipt-settle and only "
        "claims when mission-terminal liveness fires; the event "
        "carries no epoch (call get_mission for details).\n"
        "    * An already-terminal mission replays NOTHING: its "
        "settled receipts are skipped (no rows, no immediate "
        "notification). A dead_letter-since-revived mission arms its "
        "live receipts and waits.\n"
        "    * The 50-watch cap counts every minted row (N live "
        "receipts = N rows).\n\n"
        "Args:\n"
        "    target: mission_id OR the job_id receipt from "
        "job_create / job_continue.\n"
        "    events (default ['mission_terminal']): Watch events to "
        "subscribe.\n\n"
        "Returns:\n"
        "    str: Registration confirmation (or an explicit error for "
        "unresolvable targets / zero-receipt missions / cap overflow)."
    )

    return [watch_mission]


__all__ = ["create_job_tools", "create_mission_watch_tools", "TERMINAL_STATES"]
