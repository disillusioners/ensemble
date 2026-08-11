"""Job Queue Management API endpoints."""

import asyncio
import logging
from typing import Any

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
)

logger = logging.getLogger(__name__)

# Create router with /projects/{project_id}/queues prefix
router = APIRouter(prefix="/projects/{project_id}/queues", tags=["queues"])


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
