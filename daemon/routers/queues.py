"""Job Queue Management API endpoints."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from daemon.repositories import SQLModelProjectRepository
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from .schemas import (
    JobQueueResponse,
    JobQueueListResponse,
    JobQueueCreateRequest,
    JobQueueUpdateRequest,
    JobQueueNotFoundResponse,
    ProjectNotFoundResponse,
    EnsureSystemQueuesResponse,
    DeferBlockResponse,
)

if TYPE_CHECKING:
    from daemon.services.defer_block_resolver import DeferBlockResolver

# Runtime import — the helper is used by the route handler below. The
# TYPE_CHECKING-only ``DeferBlockResolver`` above is intentional
# (avoids a circular import surface at module load — see also the
# ``_defer_block_resolver: "DeferBlockResolver | None"`` annotation
# below). The helper is imported at runtime because it has zero
# resolver-side dependencies beyond the dataclass it consumes.
from daemon.services.defer_block_resolver import _holder_to_response  # noqa: E402

logger = logging.getLogger(__name__)

# Create router with /projects/{project_id}/queues prefix
router = APIRouter(prefix="/projects/{project_id}/queues", tags=["queues"])

# System-scoped queue surface (mounted under /api next to `router`):
# endpoints that are NOT per-project live here. Current tenant:
# GET /api/queues/defer-blocked (defer-gate transparency, §8.5).
system_queues_router = APIRouter(prefix="/queues", tags=["queues"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager

# Dependency to get JobQueueMgmtService
# This will be set up in daemon/api.py during app initialization
_job_queue_mgmt_service: JobQueueMgmtService | None = None


def get_mgmt_service() -> JobQueueMgmtService:
    """Get the JobQueueMgmtService instance.
    
    Returns:
        JobQueueMgmtService instance.
        
    Raises:
        HTTPException: If the service is not initialized.
    """
    if _job_queue_mgmt_service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Queue management service not initialized"}
        )
    return _job_queue_mgmt_service


def set_job_queue_mgmt_service(service: JobQueueMgmtService) -> None:
    """Set the JobQueueMgmtService instance (called during app startup)."""
    global _job_queue_mgmt_service
    _job_queue_mgmt_service = service


# Dependency to get SQLModelProjectRepository
_project_repo: SQLModelProjectRepository | None = None


def get_project_repository() -> SQLModelProjectRepository:
    """Get the SQLModelProjectRepository instance.

    Returns:
        SQLModelProjectRepository instance.

    Raises:
        HTTPException: If the repository is not initialized.
    """
    if _project_repo is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Project repository not initialized"}
        )
    return _project_repo


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    """Set the SQLModelProjectRepository instance (called during app startup)."""
    global _project_repo
    _project_repo = repo


def _queue_to_response(queue_data) -> JobQueueResponse:
    """Convert queue data to JobQueueResponse.
    
    Handles both dict (from list_queues) and JobQueue model objects.
    
    Args:
        queue_data: Either a JobQueue object or a dictionary from list_queues().
        
    Returns:
        JobQueueResponse with all queue fields.
    """
    if isinstance(queue_data, dict):
        return JobQueueResponse(
            queue_id=queue_data["queue_id"],
            project_id=queue_data["project_id"],
            queue_name=queue_data["queue_name"],
            queue_type=queue_data["queue_type"],
            concurrency_limit=queue_data["concurrency_limit"],
            is_system=queue_data["is_system"],
            is_paused=queue_data["is_paused"],
            description=queue_data.get("description"),
            created_at=queue_data["created_at"],
            updated_at=queue_data["updated_at"],
            active_jobs=queue_data.get("active_jobs", 0),
            pending_jobs=queue_data.get("pending_jobs", 0),
            bad_state_jobs=queue_data.get("bad_state_jobs", 0),
        )
    else:
        # JobQueue model object
        return JobQueueResponse(
            queue_id=queue_data.queue_id,
            project_id=queue_data.project_id,
            queue_name=queue_data.queue_name,
            queue_type=queue_data.queue_type,
            concurrency_limit=queue_data.concurrency_limit,
            is_system=queue_data.is_system,
            is_paused=queue_data.is_paused,
            description=queue_data.description,
            created_at=queue_data.created_at,
            updated_at=queue_data.updated_at,
            active_jobs=0,
            pending_jobs=0,
            bad_state_jobs=0,
        )


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=JobQueueListResponse,
    responses={
        200: {"description": "List of queues for the project"},
        503: {"description": "Service not initialized"},
    },
)
async def list_queues(
    project_id: str,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueListResponse:
    """List all queues for a project.
    
    Returns both system and custom queues with job counts.
    
    Args:
        project_id: Project identifier from path.
        
    Returns:
        200 with list of queues and total count
    """
    queues = await service.list_queues(project_id)
    
    return JobQueueListResponse(
        queues=[_queue_to_response(q) for q in queues],
        total=len(queues),
    )


@router.post(
    "/ensure-system",
    response_model=EnsureSystemQueuesResponse,
    responses={
        200: {"description": "System queues ensured"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
        503: {"description": "Service not initialized"},
    },
)
async def ensure_system_queues(
    project_id: str,
    request: Request,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> EnsureSystemQueuesResponse:
    """Ensure all system queues exist for a project.

    Creates any missing system queues (system_fifo_queue, system_parallel_queue,
    system_kb_fifo_queue, system_defer_queue, system_background_queue).
    Idempotent - safe to call multiple times.

    Args:
        project_id: Project identifier from path.

    Returns:
        200 with lists of existing and created queue names
        404 if project doesn't exist
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # Validate project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )

    result = await service.ensure_system_queues(project_id)
    total = len(result["existing_queues"]) + len(result["created_queues"])

    return EnsureSystemQueuesResponse(
        project_id=project_id,
        existing_queues=result["existing_queues"],
        created_queues=result["created_queues"],
        total_system_queues=total,
    )


@router.post(
    "",
    response_model=JobQueueResponse,
    status_code=201,
    responses={
        201: {"description": "Queue created successfully"},
        400: {"description": "Validation error or reserved name"},
        409: {"description": "Queue with name already exists"},
        503: {"description": "Service not initialized"},
    },
)
async def create_queue(
    project_id: str,
    request: Request,
    body: JobQueueCreateRequest,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueResponse:
    """Create a new custom queue for a project.
    
    Args:
        project_id: Project identifier from path.
        request: Queue creation parameters.
        
    Returns:
        201 with created queue details
        400 if validation fails or reserved name
        409 if queue with name already exists
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        queue = await service.create_queue(
            project_id=project_id,
            queue_name=body.queue_name,
            queue_type=body.queue_type,
            concurrency_limit=body.concurrency_limit,
            description=body.description,
        )
        return _queue_to_response(queue)
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(
                status_code=409,
                detail={"error": "Queue already exists", "message": error_msg}
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "Validation error", "message": error_msg}
        )


@router.get(
    "/{queue_id}",
    response_model=JobQueueResponse,
    responses={
        200: {"description": "Queue details"},
        404: {"description": "Queue not found"},
        503: {"description": "Service not initialized"},
    },
)
async def get_queue(
    project_id: str,
    queue_id: str,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueResponse:
    """Get queue details by ID.
    
    Args:
        project_id: Project identifier from path.
        queue_id: Queue identifier.
        
    Returns:
        200 with queue details
        404 if queue not found or not owned by project
    """
    queue_data = await service.get_queue_with_counts(project_id, queue_id)
    
    if queue_data is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Queue not found"}
        )
    
    return _queue_to_response(queue_data)


@router.patch(
    "/{queue_id}",
    response_model=JobQueueResponse,
    responses={
        200: {"description": "Queue updated successfully"},
        400: {"description": "Validation error"},
        404: {"description": "Queue not found"},
        503: {"description": "Service not initialized"},
    },
)
async def update_queue(
    project_id: str,
    queue_id: str,
    request: Request,
    body: JobQueueUpdateRequest,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueResponse:
    """Update a queue's fields.
    
    Args:
        project_id: Project identifier from path.
        queue_id: Queue identifier.
        request: Fields to update.
        
    Returns:
        200 with updated queue details
        400 if validation fails
        404 if queue not found or not owned by project
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        # Build updates dict from non-None fields
        updates = {}
        if body.queue_name is not None:
            updates["queue_name"] = body.queue_name
        if body.queue_type is not None:
            updates["queue_type"] = body.queue_type
        if body.concurrency_limit is not None:
            updates["concurrency_limit"] = body.concurrency_limit
        if body.is_paused is not None:
            updates["is_paused"] = body.is_paused
        if body.description is not None:
            updates["description"] = body.description
        
        if not updates:
            raise HTTPException(
                status_code=400,
                detail={"error": "No fields to update"}
            )
        
        queue = await service.update_queue(project_id, queue_id, **updates)
        
        if queue is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found"}
            )
        
        # Get queue with actual job counts
        queue_data = await service.get_queue_with_counts(project_id, queue_id)
        return _queue_to_response(queue_data)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Validation error", "message": str(e)}
        )


@router.delete(
    "/{queue_id}",
    responses={
        200: {"description": "Queue deleted successfully"},
        403: {"description": "Cannot delete system queue"},
        404: {"description": "Queue not found"},
        409: {"description": "Queue has processing jobs"},
        503: {"description": "Service not initialized"},
    },
)
async def delete_queue(
    project_id: str,
    queue_id: str,
    request: Request,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> dict:
    """Delete a queue.
    
    PENDING jobs are reassigned to system FIFO queue before deletion.
    Cannot delete system queues or queues with PROCESSING jobs.
    
    Args:
        project_id: Project identifier from path.
        queue_id: Queue identifier.
        
    Returns:
        200 with deletion status
        403 if attempting to delete system queue
        404 if queue not found or not owned by project
        409 if queue has processing jobs
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        result = await service.delete_queue(project_id, queue_id)
        return result
    except ValueError as e:
        error_msg = str(e)
        if "Cannot delete system queue" in error_msg:
            raise HTTPException(
                status_code=403,
                detail={"error": "Cannot delete system queue"}
            )
        elif "processing jobs" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail={"error": "Queue has processing jobs"}
            )
        elif "not found" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found"}
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "Delete failed", "message": error_msg}
        )


@router.post(
    "/{queue_id}/start",
    response_model=JobQueueResponse,
    responses={
        200: {"description": "Queue started (resumed)"},
        404: {"description": "Queue not found"},
        503: {"description": "Service not initialized"},
    },
)
async def start_queue(
    project_id: str,
    queue_id: str,
    request: Request,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueResponse:
    """Resume a paused queue.
    
    Sets is_paused=False to allow jobs to be processed.
    
    Args:
        project_id: Project identifier from path.
        queue_id: Queue identifier.
        
    Returns:
        200 with updated queue details
        404 if queue not found or not owned by project
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        queue = await service.start_queue(project_id, queue_id)
        
        if queue is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found"}
            )
        
        # Get queue with actual job counts
        queue_data = await service.get_queue_with_counts(project_id, queue_id)
        return _queue_to_response(queue_data)
    except ValueError as e:
        # Ownership mismatch from service
        raise HTTPException(
            status_code=404,
            detail={"error": "Queue not found"}
        )


@router.post(
    "/{queue_id}/stop",
    response_model=JobQueueResponse,
    responses={
        200: {"description": "Queue stopped (paused)"},
        404: {"description": "Queue not found"},
        503: {"description": "Service not initialized"},
    },
)
async def stop_queue(
    project_id: str,
    queue_id: str,
    request: Request,
    service: JobQueueMgmtService = Depends(get_mgmt_service),
) -> JobQueueResponse:
    """Pause a queue.
    
    Sets is_paused=True to prevent job processing.
    
    Args:
        project_id: Project identifier from path.
        queue_id: Queue identifier.
        
    Returns:
        200 with updated queue details
        404 if queue not found or not owned by project
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        queue = await service.stop_queue(project_id, queue_id)
        
        if queue is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found"}
            )
        
        # Get queue with actual job counts
        queue_data = await service.get_queue_with_counts(project_id, queue_id)
        return _queue_to_response(queue_data)
    except ValueError as e:
        # Ownership mismatch from service
        raise HTTPException(
            status_code=404,
            detail={"error": "Queue not found"}
        )


# ==================== Defer-blocked transparency surface ====================
# Read-only mirror of the defer gate's busy-set (2026-09-04,
# docs/job-task-system.md §8.5). Mirrors the repo's resolver DI pattern
# (module-level resolver global + setter called from daemon/api.py
# lifespan startup + Depends factory raising 503-if-unwired — the
# daemon/routers/missions.py shape, which mirrors work.py, which
# mirrors this file).


_defer_block_resolver: "DeferBlockResolver | None" = None


def get_defer_block_resolver() -> "DeferBlockResolver":
    """Return the wired-in :class:`DeferBlockResolver`, or 503.

    Returns:
        The DeferBlockResolver instance.

    Raises:
        HTTPException: 503 if the resolver has not been initialized
            via :func:`set_defer_block_resolver` during app startup.
    """
    if _defer_block_resolver is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Defer-block resolver service not initialized"},
        )
    return _defer_block_resolver


def set_defer_block_resolver(resolver: "DeferBlockResolver") -> None:
    """Set the :class:`DeferBlockResolver` instance.

    Called from ``daemon/api.py`` lifespan startup, wired against the
    same READ-only ``JobRepository`` the missions resolver consumes.
    Idempotent — calling multiple times replaces the singleton.

    Args:
        resolver: The DeferBlockResolver singleton (READ-only; zero
            admission-state writers — census frozen at 23).
    """
    global _defer_block_resolver
    _defer_block_resolver = resolver


@system_queues_router.get(
    "/defer-blocked",
    response_model=DeferBlockResponse,
    summary="Show the defer gate's busy-set witnesses (read-only)",
    responses={
        200: {"description": "Current defer-gate hold state + witnesses"},
        503: {"description": "Defer-block resolver service not initialized"},
    },
)
async def get_defer_blocked(
    resolver: "DeferBlockResolver" = Depends(get_defer_block_resolver),
) -> DeferBlockResponse:
    """Report what the defer gate actually sees — its witnesses, enumerated.

    The defer gate can hold indefinitely on a busy-set witness that no
    surface shows (the live case: a paused instance occupying the
    gate's busy-set). This endpoint enumerates those witnesses with the
    gate's OWN predicate composition — the witness SELECT is derived
    from the same ``_idle_predicate_sql`` body constants the gate path
    evaluates (``JobRepository.has_active_non_deferred_work``), so
    display truth == gate truth by construction; a re-implementation
    of the predicate here is the defect class this surface forbids.

    Severity shapes (docs §8.5, display-side reading of the payload):

    * AMBER — some holder has ``kind == "paused"`` OR
      ``kind == "stalled"`` (both operator-actionable: paused ⇒
      resume/terminate; stalled ⇒ force-complete the holder's settled
      mirrors, the WS4 cleanup mechanic). The FE distinguishes the
      two kinds in the tooltip wording.
    * INFO — holders exist, all ``kind == "live"`` (the gate is
      honoring ordinary live work).
    * RED anomaly — ``pending_count > 0`` AND ``holders == []``
      (defer work is queued while the gate reports no witness).

    Purity: zero DML on the path; ``2 + len(holders)`` SELECTs per
    call (the witness SELECT + the defer-lane pending count + one
    WS1 carve-out EXISTS per dedup'd holder for the stall
    classification), flat-ish as the witness count grows (the stall
    budget is bounded by the dedup'd holder count, not by raw witness
    count). DB errors propagate (queues-family posture — no §8.2
    degrade shape): the gate itself fails CLOSED, and this surface
    never serves a body that could falsely claim the gate is open.

    Args:
        resolver: Injected DeferBlockResolver (via Depends).

    Returns:
        :class:`DeferBlockResponse` — hold state, defer-lane pending
        count, and the enumerated holders (paused > stalled > live,
        each ascending by instance_id).
    """
    # Sync resolver call inside the async handler — the
    # ``list_missions`` precedent (mission_resolver.resolve_page is
    # sync; two fast SELECTs, no event-loop-relevant latency).
    snapshot = resolver.resolve()
    return DeferBlockResponse(
        defer_blocked=snapshot.defer_blocked,
        pending_count=snapshot.pending_count,
        holders=[_holder_to_response(h) for h in snapshot.holders],
    )


__all__ = [
    # ── Routers (mounted in daemon/api.py under /api) ───────────────────
    "router",  # per-project /projects/{project_id}/queues prefix
    "system_queues_router",  # system /queues prefix; hosts /defer-blocked
    # ── Public DI symbols (consumed by daemon/api.py lifespan + tests) ──
    "set_defer_block_resolver",  # called from api.py startup
    "get_defer_block_resolver",  # 503-if-unwired factory
    "set_job_queue_mgmt_service",  # called from api.py startup
    "get_mgmt_service",  # 503-if-unwired factory
    "set_project_repository",  # called from api.py startup
    "get_project_repository",  # 503-if-unwired factory
]
