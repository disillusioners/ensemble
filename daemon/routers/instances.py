"""Instance management API endpoints."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from daemon.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from daemon.models import (
    ErrorCodes,
    ErrorResponse,
    InstanceCreate,
    InstanceInfo,
    InstanceListResponse,
    InstanceStatus,
    ResumeRequest,
)
from daemon.utils import parse_utc_datetime

logger = logging.getLogger(__name__)


# Maximum allowed length (in characters) for a todo comment. Enforced at
# both the HTTP boundary (400) and inside ``TodoManager.set_comment``
# (ValueError) for defense in depth.
MAX_COMMENT_LENGTH = 1000


class TodoCommentRequest(BaseModel):
    """Request body for setting a comment on a todo item.

    Attributes:
        comment: The annotation text. Empty string clears the comment.
            Hard-capped at :data:`MAX_COMMENT_LENGTH` characters; an explicit
            check in the endpoint raises ``400`` (rather than the default
            Pydantic ``422``) so clients see a uniform error shape with the
            rest of the API.
    """

    comment: str = Field(
        default="",
        description=(
            "Comment text. Empty string clears the existing comment. "
            f"Maximum {MAX_COMMENT_LENGTH} characters."
        ),
    )


class TodoEdgeRequest(BaseModel):
    """Request body for adding or removing a directed todo edge.

    Used by the ``POST /{instance_id}/todos/edges`` and
    ``DELETE /{instance_id}/todos/edges`` endpoints.

    Attributes:
        from_id: ID of the predecessor (source) node. Must be an
            existing node in the instance's todo graph.
        to_id: ID of the successor (target) node. Must be an
            existing node in the instance's todo graph.
    """

    from_id: str = Field(
        ...,
        description="ID of the predecessor node (edge source).",
    )
    to_id: str = Field(
        ...,
        description="ID of the successor node (edge target).",
    )


class TodoSubtaskCreateRequest(BaseModel):
    """Request body for adding a sub-task to a todo node.

    Attributes:
        text: Sub-task description. Non-empty (Pydantic min_length=1) and
            hard-capped at 500 characters via ``max_length=500``. Empty
            / over-length inputs are rejected by Pydantic's auto-422
            before reaching the endpoint — no extra handler needed.
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Sub-task text. 1-500 characters.",
    )


class TodoSubtaskUpdateRequest(BaseModel):
    """Request body for updating a sub-task's status.

    Attributes:
        status: New status. Normalized by the endpoint (lowercased,
            stripped) to either ``"pending"`` or ``"done"``. Sub-task
            statuses are STRICTLY BINARY — ``"in_progress"`` and its
            aliases are rejected with ``400``. The endpoint rejects any
            value that doesn't normalize cleanly to the binary set.
        auto_complete: When ``True`` AND every sub-task on the parent
            node is ``"done"`` AND the parent's own status is not
            already ``"done"``, the parent is auto-completed. Defaults
            to ``False``. See
            :meth:`TodoGraphManager.update_subtask` for the full
            vacuous-truth policy.
    """

    status: str = Field(
        ...,
        description="New sub-task status; normalizes to 'pending' or 'done'.",
    )
    auto_complete: bool = Field(
        default=False,
        description=(
            "If True, auto-complete the parent node when all its "
            "sub-tasks are done."
        ),
    )


class AnswerRequest(BaseModel):
    """Request body for ``POST /api/instances/{id}/answer``.

    Carries the user's answers to a pending question pack. The shape
    of ``answers`` is intentionally flexible — callers may key by
    question id (preferred) or by question text (for ad-hoc clients
    that didn't capture the auto-generated ids).

    Attributes:
        answers: User-supplied answer dict. Shape is unconstrained
            (any JSON-serializable dict); the manager stores it
            verbatim and the resume-message formatter iterates it.
    """

    answers: dict = Field(
        default_factory=dict,
        description=(
            "User-supplied answers. Shape is flexible: prefer keying "
            "by question id (the field returned in the pending SSE "
            "event) — text-keyed fallbacks are also accepted."
        ),
    )


# Create router with /instances prefix
router = APIRouter(prefix="/instances", tags=["instances"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        InstanceManager instance.
    """
    return request.app.state.manager


# 1. POST /instances - Spawn instance
@router.post("", response_model=InstanceInfo, status_code=201)
async def create_instance(
    instance_create: InstanceCreate,
    request: Request,
) -> InstanceInfo:
    """Spawn a new instance."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Generate instance_id upfront so MCP preload can use it
    instance_id = instance_create.instance_id or str(uuid.uuid4())
    
    try:
        # Prefer agent_id over agent_dir
        instance_id = await manager.spawn_instance_with_mcp(
            agent_id=instance_create.agent_id,
            instance_id=instance_id,
            project_id=instance_create.project_id,
        )
    except ValueError as e:
        error_msg = str(e)
        if "Max instances limit" in error_msg:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    code=ErrorCodes.MAX_INSTANCES_EXCEEDED,
                    message=error_msg
                ).model_dump()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=error_msg
                ).model_dump()
            )

    # Get instance info from database
    instance_meta = manager.get_instance_info(instance_id)
    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        title=instance_meta.get("title"),
        children=instance_meta.get("children", []),
        mcp_tool_names=instance_meta.get("metadata", {}).get("mcp_tool_names"),
        created_at=parse_utc_datetime(instance_meta["created_at"]),
        updated_at=parse_utc_datetime(instance_meta.get("updated_at")),
        project_id=instance_meta.get("project_id"),
        pending_count=(await manager.get_queue_stats(instance_id)).get("pending_count"),
    )


# 2. GET /instances - List instances
@router.get("", response_model=InstanceListResponse)
async def list_instances(
    request: Request,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
    project_id: str | None = Query(None, description="Filter instances by project ID"),
    exclude_kb: bool = Query(True, description="Exclude KB-related instances (experiencer, kb-importer)"),
) -> InstanceListResponse:
    """List instances with pagination.

    Pagination is root-based: only root instances (parent_id IS NULL or empty)
    are counted and paginated. ALL descendants of each root in the current page
    are loaded via BFS and included in the flat result list.

    Args:
        request: FastAPI request object.
        limit: Maximum number of root instances to return (default: 10, max: 100).
        offset: Number of root instances to skip (default: 0, min: 0).
        project_id: Filter instances by project ID (optional).
        exclude_kb: Exclude KB-related instances (experiencer, kb-importer) when True (default: True).
            Applies to both root counting and descendant loading.
    """
    manager = _get_manager(request)

    # Input validation
    limit = max(1, min(limit, MAX_PAGE_LIMIT))  # Clamp to 1-MAX_PAGE_LIMIT
    offset = max(0, offset)  # Ensure non-negative
    
    instances_data, total = manager.list_instances(
        limit=limit,
        offset=offset,
        project_id=project_id,
        exclude_kb=exclude_kb,
        include_descendants=True,
    )
    instances = []
    for inst in instances_data:
        instances.append(InstanceInfo(
            instance_id=inst["instance_id"],
            agent_id=inst["agent_id"],
            agent_dir=inst["agent_dir"],
            status=InstanceStatus(inst["status"]),
            parent_id=inst.get("parent_id"),
            title=inst.get("title"),
            children=inst.get("children", []),
            mcp_tool_names=inst.get("metadata", {}).get("mcp_tool_names"),
            created_at=parse_utc_datetime(inst["created_at"]),
            updated_at=parse_utc_datetime(inst.get("updated_at")),
            project_id=inst.get("project_id"),
        ))
    
    has_more = (offset + limit) < total
    
    return InstanceListResponse(
        instances=instances,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more
    )


# 3. GET /instances/{instance_id} - Get instance info
@router.get("/{instance_id}", response_model=InstanceInfo)
async def get_instance(
    instance_id: str,
    request: Request,
) -> InstanceInfo:
    """Get instance information."""
    manager = _get_manager(request)
    
    try:
        instance_meta = manager.get_instance_info(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        title=instance_meta.get("title"),
        children=instance_meta.get("children", []),
        mcp_tool_names=instance_meta.get("metadata", {}).get("mcp_tool_names"),
        created_at=parse_utc_datetime(instance_meta["created_at"]),
        updated_at=parse_utc_datetime(instance_meta.get("updated_at")),
        project_id=instance_meta.get("project_id"),
        pending_count=(await manager.get_queue_stats(instance_id)).get("pending_count"),
    )


# 4. DELETE /instances/{instance_id} - Terminate instance
@router.delete("/{instance_id}")
async def terminate_instance(
    instance_id: str,
    request: Request,
    hard_delete: bool = Query(
        False,
        description=(
            "When True, hard-delete DB records for the entire instance "
            "tree (instances.db + checkpoints.db) AFTER the standard "
            "terminate cascade. Removes ``instances`` rows, "
            "``instance_hierarchy`` rows, ``tasks``/``events``/``message_queue`` "
            "rows, AND the job-queue sub-tree (``job_queue_items``, "
            "``job_watchers``, ``job_locks``) in FK-safe order. Sweeps "
            "checkpoint threads for every member of the tree via the "
            "CheckpointerAdapter. Destructive — use only from admin "
            "cleanup paths."
        ),
    ),
) -> dict:
    """Terminate an instance.

    Default (``hard_delete=False``): runs the standard in-memory
    terminate cascade — graph task cancellation, request-registry
    cleanup, live_hub close, MCP close, todo clear, job-state
    transitions (PROCESSING → CANCELLED via ``complete_job``;
    PENDING/FAILED → ``cancel_job``), project-lock release. The
    ``instances`` row and dependent rows remain in ``instances.db``
    for the maintenance service to sweep on the TTL/history-cap
    cycle.

    When ``hard_delete=True``: same as above, PLUS a one-shot
    destructive delete from BOTH databases:

    1. Snapshot the tree (root + all descendants) via
       ``instance_repository.get_tree_ids`` — taken BEFORE
       ``terminate_instance`` because the in-memory cascade can
       rewrite ``instance_hierarchy`` rows.
    2. Run ``terminate_instance`` (in-memory cleanup, status
       transition, child cascade, job-state transitions).
    3. FK-safe DB cascade via
       ``instance_repository.hard_delete_tree`` (job_locks →
       job_queue_items → job_watchers → tasks → events →
       message_queue → instance_hierarchy → instances).
    4. Per-instance checkpoint sweep via
       ``checkpointer.adelete_thread``.

    The endpoint returns ``{"terminated": True, "hard_deleted": True,
    ...}`` on success. ``hard_deleted`` is omitted when
    ``hard_delete=False``.
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check instance exists — same 404 mapping as every other
    # instance-scoped endpoint in this router. The hard-delete path
    # requires a real instance row to enumerate the tree (snapshot
    # falls back to [instance_id] when the row is missing, but the
    # caller still benefits from a uniform 404 contract).
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    if hard_delete:
        # Destructive operator path: full DB cascade for the whole tree
        # + checkpoint sweep. Errors propagate as 500 (handled by
        # FastAPI default exception handler); the cascade is
        # transactional at the row level so a mid-cascade failure rolls
        # back cleanly and the caller can retry.
        result = await manager.hard_delete_instance(instance_id)
        return {
            "terminated": True,
            "hard_deleted": True,
            "root_instance_id": result.get("root_instance_id"),
            "tree_ids": result.get("tree_ids", []),
            "checkpoint_threads_deleted": result.get("checkpoint_threads_deleted", 0),
            "checkpoint_errors": result.get("checkpoint_errors", []),
            "db_counts": result.get("counts", {}),
        }

    await manager.terminate_instance(instance_id)

    return {"terminated": True}


# 5. POST /instances/{instance_id}/pause - Pause instance
@router.post("/{instance_id}/pause")
async def pause_instance(
    instance_id: str,
    request: Request,
) -> dict:
    """Pause an instance and cascade to children."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    result = await manager.pause_instance_cascade(instance_id)
    return {
        "paused": True,
        "paused_ids": result["paused_ids"],
        "skipped_ids": result["skipped_ids"],
    }


# 6. POST /instances/{instance_id}/resume - Resume instance
@router.post("/{instance_id}/resume")
async def resume_instance(
    instance_id: str,
    request: Request,
    body: ResumeRequest | None = None,
) -> dict:
    """Resume a paused instance and cascade to children.
    Re-executes existing PROCESSING jobs from checkpoint with optional message."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    message_text = (body.message.strip() if body and body.message else None) or "resume"

    # Cascade resume (sets PAUSED→RUNNING for target + children)
    result = await manager.resume_instance_cascade(instance_id)
    target_id = result.get("target_id", instance_id)

    # Resume processing jobs for all resumed instances (including children)
    # Target instance gets the user message; children resume silently from checkpoint
    resume_results = {}
    for rid in result["resumed_ids"]:
        is_target = rid == target_id
        job_result = await manager.resume_processing_job(
            rid,
            message=message_text if is_target else "resume",
            silent=not is_target,
        )
        if job_result is None:
            logger.debug(f"No active PROCESSING job for instance {rid[:8]}... (was IDLE/WAITING_CHILDREN)")
        resume_results[rid] = job_result if job_result is not None else {"status": "no_active_job"}

    return {
        "resumed": True,
        "resumed_ids": result["resumed_ids"],
        "skipped_ids": result["skipped_ids"],
        "target_id": target_id,
        "resume_results": resume_results,
    }


# 6b. POST /instances/{instance_id}/answer - Submit answers to a pending question pack
#
# Phase 2 / question-tool. Mirrors the PAUSED-branch fan-out from
# ``daemon/routers/messages.py:198-249`` (F10) because
# ``pause_instance_cascade`` cascades to children — the answer endpoint
# must mirror this fan-out. The target instance gets the formatted
# Q↔A HumanMessage; children resume silently from checkpoint.
@router.post("/{instance_id}/answer")
async def answer_questions(
    instance_id: str,
    body: AnswerRequest,
    request: Request,
) -> dict:
    """Submit answers to a pending question pack; resume the instance cascade.

    The instance must have a pending question pack (set by the
    ``question`` tool before the graph paused). Stores the answers,
    emits a ``question_pack`` SSE event with ``status="answered"``, then
    mirrors the PAUSED-branch resume fan-out: cascade-resumes target +
    all paused children, and for each resumed instance calls
    ``resume_processing_job`` — the target receives the formatted
    Q↔A HumanMessage; children resume silently from their checkpoint.

    The Q↔A HumanMessage intentionally echoes the question text (F7
    compaction safety) so the agent can correlate Q↔A from this message
    alone even after the original tool call result has been compacted.

    Errors:
        * ``404`` if the instance is unknown to the manager.
        * ``404`` if no pending (or answered — second answer is allowed)
          question pack exists for the instance.
        * ``503`` if the daemon is in write-paused mode (migration).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )

    # 1. Validate instance exists — uniform 404 contract with every other
    #    instance-scoped endpoint in this router.
    await _check_instance_exists(manager, instance_id)

    # 2. Store answers via QuestionManager. ``None`` means there is no
    #    pack for this instance — surface as 404 so the frontend can
    #    distinguish "answer without a question" from "answer stored".
    #    We import here to avoid pulling the service into every
    #    request that doesn't need it (lazy import).
    from daemon.services.question_manager import pack_to_dict

    pack = manager._question_manager.set_answers(instance_id, body.answers)
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=(
                    f"No question pack for instance {instance_id!r}. "
                    "The user can only answer a question that was "
                    "actually asked."
                ),
            ).model_dump(),
        )

    # 3. Emit SSE: question_pack with status="answered". Best-effort —
    #    the resume cascade must proceed even if SSE fails. Mirrors
    #    Phase 1's pattern where the tool emits SSE synchronously
    #    BEFORE setting the pause flag, for the same F3 timing reason.
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_question_pack(instance_id, pack_to_dict(pack))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"question_pack SSE emission failed for answer on "
                f"instance {instance_id}: {e}"
            )

    # 4. Format answers as a HumanMessage string. The echo of question
    #    text is F7 — even if the tool result was compacted, the agent
    #    can correlate Q↔A from this message alone.
    answer_lines = ["Here are the user's answers to your questions:", ""]
    for i, q in enumerate(pack.questions):
        # Prefer keying by id (the canonical contract); fall back to
        # text-keyed lookups for ad-hoc clients that didn't capture the
        # auto-generated ids.
        answer = (
            pack.answers.get(q.id)
            if q.id in pack.answers
            else pack.answers.get(q.text, "(no answer)")
        )
        answer_lines.append(f"**Q{i + 1}:** {q.text}")
        answer_lines.append(f"**A{i + 1}:** {answer}")
        answer_lines.append("")
    answer_msg = "\n".join(answer_lines)

    # 5. Mirror the PAUSED-branch fan-out from messages.py:198-249 (F10).
    #    ``pause_instance_cascade`` cascades to children, so the resume
    #    must too. Target gets the answer HumanMessage; children resume
    #    silently from their checkpoint — they were paused as a side
    #    effect of the parent's question and don't need a new message.
    try:
        resume_result = await manager.resume_instance_cascade(instance_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to resume instance: {e}",
            ).model_dump(),
        )

    target_id = resume_result.get("target_id", instance_id)

    resume_results: dict = {}
    for resumed_id in resume_result["resumed_ids"]:
        is_target = resumed_id == target_id
        try:
            job_result = await manager.resume_processing_job(
                resumed_id,
                message=answer_msg if is_target else "resume",
                silent=not is_target,
            )
        except Exception as e:
            logger.warning(
                f"Failed to resume processing for "
                f"{resumed_id[:8]}...: {e}"
            )
            job_result = {"status": "error", "error": str(e)}
        if job_result is None:
            logger.debug(
                f"No active PROCESSING job for instance "
                f"{resumed_id[:8]}... (was IDLE/WAITING_CHILDREN)"
            )
        resume_results[resumed_id] = (
            job_result if job_result is not None else {"status": "no_active_job"}
        )

    return {
        "status": "answered",
        "instance_id": instance_id,
        "question_pack": pack_to_dict(pack),
        "resume_info": {
            "resumed": True,
            "resumed_ids": resume_result["resumed_ids"],
            "skipped_ids": resume_result["skipped_ids"],
            "target_id": target_id,
            "resume_results": resume_results,
        },
    }


# 7. POST /instances/{instance_id}/stop - Deprecated: use POST /pause instead
@router.post("/{instance_id}/stop", deprecated=True)
async def stop_instance_deprecated(
    instance_id: str,
    request: Request,
) -> dict:
    """Deprecated: Use POST /pause instead."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    return await pause_instance(instance_id, request)


# 7. GET /instances/{instance_id}/messages - Get message history
@router.get("/{instance_id}/messages")
async def get_messages(
    instance_id: str,
    request: Request,
) -> list[dict]:
    """Get message history for an instance."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Get message history from LangGraph checkpoints
    return await manager.get_messages(instance_id)


# ---------------------------------------------------------------------------
# Todo list endpoints (read + comment annotation + graph edges)
# ---------------------------------------------------------------------------


async def _check_instance_exists(manager: Any, instance_id: str) -> None:
    """Raise 404 if the instance is unknown to the manager.

    Centralizes the ``KeyError → 404`` mapping that every instance-scoped
    endpoint performs; the todo endpoints below reuse this helper.
    """
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}",
            ).model_dump(),
        )


# 8. GET /instances/{instance_id}/todos - Get the instance's todo list
@router.get("/{instance_id}/todos")
async def get_instance_todos(
    instance_id: str,
    request: Request,
) -> list[dict]:
    """Return the instance's full todo list as a JSON array.

    Each item shape (frozen Phase 1 schema — exactly seven keys):
        ``{
            "id": "n-a1b2c3d4",          # Stable node identity (n-prefixed)
            "index": 0,                  # Insertion-order position (preserved)
            "text": "...",               # Description
            "status": "pending|in_progress|done",
            "comment": "...",            # User annotation (may be empty)
            "next_ids": ["n-..."],       # Successor node IDs (may be [])
            "subtasks": [...]            # Sub-task checklist (each {id, text, status}); [] when none
        }``

    The response is the **augmented** graph view: each node carries its
    ``id`` and ``next_ids`` adjacency list, but the legacy ``index`` field
    is PRESERVED so old clients (Angular ``track item.index``) keep
    working without DOM teardown.

    Returns an empty list ``[]`` if the instance has no todo list (the
    underlying state is in-memory and ephemeral — a freshly-spawned
    instance has no list until the agent runs ``todo_create``).
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)
    return manager._todo_manager.get_all(instance_id)


# 8a. GET /instances/{instance_id}/todos/graph - Structured {nodes, edges}
@router.get("/{instance_id}/todos/graph")
async def get_instance_todo_graph(
    instance_id: str,
    request: Request,
) -> dict:
    """Return the instance's todo graph as ``{"nodes": [...], "edges": [...]}.

    Edges are derived from per-node ``next_ids`` adjacency lists and
    returned as ``{"from": str, "to": str}`` dicts — the same shape
    accepted by ``create_graph`` inputs. Each node dict conforms to the
    **frozen seven-key schema** (``id``, ``index``, ``text``, ``status``,
    ``comment``, ``next_ids``, ``subtasks``); the ``subtasks`` field is
    the per-node checklist of ``{id, text, status}`` dicts (``[]`` when
    no sub-tasks).

    Prefer this endpoint over ``GET /todos`` when the consumer needs
    explicit edge enumeration (e.g., graph rendering); for plain list
    rendering the augmented list endpoint above is sufficient.

    Returns ``{"nodes": [], "edges": []}`` if the instance has no todo
    list.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)
    return manager._todo_manager.get_graph(instance_id)


# 8b. POST /instances/{instance_id}/todos/edges - Add a directed edge
#
# CRITICAL: this route is declared BEFORE the
# ``POST /{instance_id}/todos/{node_id}/comment`` catch-all so the
# literal segment ``edges`` is never captured by the ``{node_id}``
# path parameter. FastAPI's route matching is order-sensitive.
@router.post("/{instance_id}/todos/edges")
async def add_todo_edge(
    instance_id: str,
    body: TodoEdgeRequest,
    request: Request,
) -> dict:
    """Add a directed edge ``from_id → to_id`` to the todo graph.

    Request body: ``{"from_id": str, "to_id": str}``.

    The edge is rejected if either node does not exist, if ``from_id``
    equals ``to_id`` (self-loop), or if adding the edge would create a
    cycle in the graph (DAG invariant). The endpoint returns the updated
    graph (``{"nodes": [...], "edges": [...]}``) on success and emits a
    ``todo_update`` SSE event so the frontend re-renders.

    Errors:
        * ``404`` if the instance is unknown to the manager.
        * ``400`` if either node is missing, ``from_id == to_id``, or
          adding the edge would introduce a cycle.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    result = manager._todo_manager.add_edge(
        instance_id, body.from_id, body.to_id
    )
    if result is None:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=(
                    f"Cannot add edge {body.from_id} -> {body.to_id}: "
                    f"node(s) not found or edge would create a cycle"
                ),
            ).model_dump(),
        )

    # Best-effort SSE re-emit so the frontend re-renders. Mirrors the
    # ``_emit_update`` helper pattern in ``daemon.tools.todo_tools`` —
    # any hub failure is logged and swallowed so a transport hiccup
    # never blocks the write.
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for add_edge on "
                f"instance {instance_id} ({body.from_id} -> {body.to_id}): {e}"
            )

    return result


# 8c. DELETE /instances/{instance_id}/todos/edges - Remove a directed edge
@router.delete("/{instance_id}/todos/edges")
async def remove_todo_edge(
    instance_id: str,
    body: TodoEdgeRequest,
    request: Request,
) -> dict:
    """Remove a directed edge ``from_id → to_id`` from the todo graph.

    Request body: ``{"from_id": str, "to_id": str}``.

    Returns the updated graph (``{"nodes": [...], "edges": [...]}``) on
    success and emits a ``todo_update`` SSE event so the frontend
    re-renders. Removing a non-existent edge is a no-op-miss reported as
    ``404``.

    Errors:
        * ``404`` if the instance is unknown to the manager, either
          node is missing, or the edge does not exist.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    result = manager._todo_manager.remove_edge(
        instance_id, body.from_id, body.to_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=(
                    f"Edge {body.from_id} -> {body.to_id} not found "
                    f"for instance {instance_id}"
                ),
            ).model_dump(),
        )

    # Best-effort SSE re-emit (same pattern as add_todo_edge above).
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for remove_edge on "
                f"instance {instance_id} ({body.from_id} -> {body.to_id}): {e}"
            )

    return result


# 8d. POST /instances/{instance_id}/todos/{node_id}/subtasks - Add a sub-task
#
# Declared BEFORE the ``/todos/{node_id}/comment`` catch-all so FastAPI's
# order-sensitive matcher prefers this literal ``subtasks`` segment.
@router.post("/{instance_id}/todos/{node_id}/subtasks")
async def add_todo_subtask(
    instance_id: str,
    node_id: str,
    body: TodoSubtaskCreateRequest,
    request: Request,
) -> dict:
    """Append a sub-task to the checklist of ``node_id``.

    Request body: ``{"text": "..."}``. ``text`` is required, non-empty,
    and capped at 500 characters (enforced by Pydantic). The sub-task ID
    is auto-generated (``s-`` + 8 hex chars) — clients do not supply it.
    New sub-tasks always start in ``"pending"`` state.

    Returns ``{"todos": [...], "reminder": str}`` on success (pass-through
    from the manager) and emits a ``todo_update`` SSE event so the
    frontend re-renders the checklist.

    Errors:
        * ``404`` if the instance is unknown to the manager.
        * ``404`` if ``node_id`` does not reference an existing node.
        * ``422`` (auto from Pydantic) if ``text`` is empty or
          exceeds 500 characters.
        * ``400`` if the parent node already has
          :data:`MAX_SUBTASKS_PER_NODE` sub-tasks
          (i.e. the per-node cap is exceeded).
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    try:
        result = manager._todo_manager.add_subtask(
            instance_id, node_id, body.text
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=str(e),
            ).model_dump(),
        )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=f"Todo node {node_id!r} not found",
            ).model_dump(),
        )

    # Best-effort SSE re-emit (same pattern as add_todo_edge above).
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for add_subtask on "
                f"instance {instance_id} node {node_id}: {e}"
            )

    return result


# 8e. PATCH /instances/{instance_id}/todos/{node_id}/subtasks/{subtask_id} - Update sub-task
#
# Declared BEFORE the ``/todos/{node_id}/comment`` catch-all so FastAPI's
# order-sensitive matcher treats ``subtasks`` as a literal segment, not
# a ``node_id`` value.
@router.patch("/{instance_id}/todos/{node_id}/subtasks/{subtask_id}")
async def update_todo_subtask(
    instance_id: str,
    node_id: str,
    subtask_id: str,
    body: TodoSubtaskUpdateRequest,
    request: Request,
) -> dict:
    """Update a sub-task's status, with optional parent auto-completion.

    Request body: ``{"status": "...", "auto_complete": bool}``. ``status``
    is normalized (lowercase + stripped); valid values normalize to
    ``"pending"`` or ``"done"`` only — ``"in_progress"`` and its aliases
    are rejected with ``400`` because sub-tasks are a strictly binary
    checklist.

    When ``auto_complete=True`` and all sub-tasks on the parent node
    are ``"done"`` AND the parent is not already ``"done"``, the parent
    is auto-completed (the response's ``auto_completed`` flag is set to
    ``True``). A node with zero sub-tasks is never auto-completed on
    this path (vacuous-truth guard — see manager).

    Returns ``{"todos": [...], "reminder": str, "auto_completed": bool}``
    on success (pass-through from the manager) and emits a ``todo_update``
    SSE event.

    Errors:
        * ``404`` if the instance is unknown to the manager.
        * ``404`` if ``node_id`` or ``subtask_id`` does not exist.
        * ``400`` if ``status`` does not normalize to ``"pending"`` or
          ``"done"``.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    # Normalize status: lowercase + strip. Reject anything that doesn't
    # normalize cleanly to the binary set.
    normalized = body.status.strip().lower()
    if normalized not in ("pending", "done"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=(
                    f"Invalid sub-task status {body.status!r}: must be "
                    f"'pending' or 'done' (in_progress is not allowed for "
                    f"sub-tasks)"
                ),
            ).model_dump(),
        )

    result = manager._todo_manager.update_subtask(
        instance_id, node_id, subtask_id, normalized, body.auto_complete
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=(
                    f"Sub-task {subtask_id!r} not found for node "
                    f"{node_id!r} on instance {instance_id}"
                ),
            ).model_dump(),
        )

    # Best-effort SSE re-emit (same pattern as add_todo_edge above).
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for update_subtask on "
                f"instance {instance_id} node {node_id} subtask "
                f"{subtask_id}: {e}"
            )

    return result


# 8f. DELETE /instances/{instance_id}/todos/{node_id}/subtasks/{subtask_id} - Remove sub-task
#
# Declared BEFORE the ``/todos/{node_id}/comment`` catch-all so FastAPI's
# order-sensitive matcher treats ``subtasks`` as a literal segment.
@router.delete("/{instance_id}/todos/{node_id}/subtasks/{subtask_id}")
async def remove_todo_subtask(
    instance_id: str,
    node_id: str,
    subtask_id: str,
    request: Request,
) -> dict:
    """Remove a sub-task from a node's checklist.

    Returns ``{"todos": [...], "reminder": str}`` on success (pass-
    through from the manager) and emits a ``todo_update`` SSE event so
    the frontend re-renders the checklist without the removed item.

    Errors:
        * ``404`` if the instance is unknown to the manager.
        * ``404`` if ``node_id`` or ``subtask_id`` does not exist.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    result = manager._todo_manager.remove_subtask(
        instance_id, node_id, subtask_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=(
                    f"Sub-task {subtask_id!r} not found for node "
                    f"{node_id!r} on instance {instance_id}"
                ),
            ).model_dump(),
        )

    # Best-effort SSE re-emit (same pattern as add_todo_edge above).
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for remove_subtask on "
                f"instance {instance_id} node {node_id} subtask "
                f"{subtask_id}: {e}"
            )

    return result


# 9. POST /instances/{instance_id}/todos/{node_id}/comment - Annotate a todo node
#
# Declared AFTER the literal ``/todos/edges`` routes so the static
# ``edges`` segment is matched first by FastAPI's order-sensitive route
# matcher.
@router.post("/{instance_id}/todos/{node_id}/comment")
async def set_todo_comment(
    instance_id: str,
    node_id: str,
    body: TodoCommentRequest,
    request: Request,
) -> dict:
    """Set a comment on a todo node identified by ``node_id``.

    ``node_id`` may be either:
        * A **node ID** (e.g., ``"n-a1b2c3d4"``) — the preferred form.
          Generated node IDs are always ``n-`` prefixed and therefore
          never all-numeric, so they never collide with the legacy
          numeric-index path.
        * A **numeric index** (e.g., ``"0"``) — resolved to the Nth
          node by insertion order. Preserved for backward compatibility
          with the pre-Phase-3 contract.

    Detection is automatic via ``node_id.isdigit()`` — no client opt-in
    required.

    Request body: ``{"comment": "user comment text"}``. Empty / missing
    ``comment`` is treated as an empty string (clears any prior comment).

    The endpoint emits a ``todo_update`` SSE event after the mutation so
    any connected frontend re-renders the annotated list immediately.
    SSE emission is best-effort: a hub failure does not roll back the
    comment.

    Errors:
        * ``400`` if the supplied ``comment`` exceeds :data:`MAX_COMMENT_LENGTH`
          characters. We enforce this explicitly here (rather than relying on
          Pydantic's auto-422) so the API returns a uniform error shape.
        * ``404`` if ``node_id`` does not reference an existing node on
          the instance's todo list.

    Returns:
        The updated node dict on success.
    """
    manager = _get_manager(request)
    await _check_instance_exists(manager, instance_id)

    # Explicit length guard — returns 400 (not Pydantic's default 422) for
    # uniform error shape with the rest of the API. The same limit is also
    # enforced inside ``TodoManager.set_comment`` (ValueError) as defense in
    # depth, but at that layer it would surface as a generic 500-equivalent.
    if len(body.comment) > MAX_COMMENT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=(
                    f"Comment exceeds maximum length of "
                    f"{MAX_COMMENT_LENGTH} characters"
                ),
            ).model_dump(),
        )

    try:
        if node_id.isdigit():
            # Backward-compat: treat all-numeric path param as insertion-
            # order index. Generated node IDs are ``n-`` prefixed and
            # therefore never all-numeric, so there is no collision risk.
            updated = manager._todo_manager.set_comment_by_index(
                instance_id, int(node_id), body.comment
            )
        else:
            updated = manager._todo_manager.set_comment(
                instance_id, node_id, body.comment
            )
    except ValueError as e:
        # node_id/index out of range OR instance has no todo list yet.
        # We return 404 because the *addressed resource* (the todo node
        # identified by ``node_id``) does not exist — the URL points to
        # a non-existent node, not to an otherwise-valid endpoint whose
        # payload is malformed. This matches standard REST semantics: a
        # missing addressed resource is a 404.
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TODO_NOT_FOUND,
                message=f"Todo node {node_id!r} not found",
            ).model_dump(),
        ) from e

    # Best-effort SSE re-emit so the frontend re-renders. Mirrors the
    # ``_emit_update`` helper pattern in ``daemon.tools.todo_tools`` —
    # any hub failure is logged and swallowed so a transport hiccup
    # never blocks the write.
    live_hub = getattr(request.app.state, "live_hub", None)
    if live_hub is not None:
        try:
            await live_hub.stream_todo_update(
                instance_id,
                manager._todo_manager.get_all(instance_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"todo SSE emission failed for comment on "
                f"instance {instance_id} node {node_id}: {e}"
            )

    return updated
