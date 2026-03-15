"""Task Queue API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from daemon.services.task_queue_service import TaskQueueService
from daemon.repositories.task_queue.models import TaskStatus
from .schemas import (
    TaskCreateRequest,
    TaskResponse,
    TaskListResponse,
    TaskValidationError,
    TaskNotFoundResponse,
)

# Create router with /api/tasks prefix
router = APIRouter(prefix="/tasks", tags=["tasks"])


# Dependency to get TaskQueueService
# This will be set up in daemon/api.py during app initialization
_task_queue_service: Optional[TaskQueueService] = None


def get_task_queue_service() -> TaskQueueService:
    """Get the TaskQueueService instance.
    
    Returns:
        TaskQueueService instance.
        
    Raises:
        HTTPException: If the service is not initialized.
    """
    if _task_queue_service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Task queue service not initialized"}
        )
    return _task_queue_service


def set_task_queue_service(service: TaskQueueService) -> None:
    """Set the TaskQueueService instance (called during app startup)."""
    global _task_queue_service
    _task_queue_service = service


def _task_to_response(
    task,
    position: Optional[int] = None,
    message: Optional[str] = None,
) -> TaskResponse:
    """Convert TaskQueueItem to TaskResponse."""
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        priority=task.priority,
        agent_dir=task.agent_dir,
        project_id=task.project_id,
        session_id=task.session_id,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        result_summary=task.result_summary,
        error_message=task.error_message,
        position=position,
        message=message,
    )


# ==================== Endpoints ====================


@router.post(
    "",
    responses={
        200: {"description": "Task started immediately"},
        202: {"description": "Task queued"},
        422: {"model": TaskValidationError, "description": "Validation error"},
    },
)
async def create_task(
    request: TaskCreateRequest,
    service: TaskQueueService = Depends(get_task_queue_service),
):
    """Submit a new task for processing.
    
    - If no project_id is provided, the task executes immediately
    - If project_id is provided and no lock is held, task starts immediately
    - If project_id is provided and a lock is held, task is queued
    
    Returns:
        200 with status=processing if task started immediately
        202 with status=pending if task was queued
        422 if validation errors
    """
    try:
        # Validate agent_dir exists (similar to existing API pattern)
        from daemon.api import validate_agent_dir
        validate_agent_dir(request.agent_dir)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid agent_dir", "message": str(e)}
        )
    
    # Enqueue the task
    try:
        task = await service.enqueue(
            agent_dir=request.agent_dir,
            message=request.message,
            source=request.source,
            project_id=request.project_id,
            priority=request.priority,
            metadata=request.metadata,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=TaskValidationError(
                error="Validation Error",
                details=[{"field": str(err["loc"][0]) if err["loc"] else "unknown", "message": err["msg"]} 
                        for err in e.errors()]
            ).model_dump()
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "Internal error", "message": str(e)}
        )
    
    # Determine response based on task status
    if task.status == TaskStatus.PROCESSING.value:
        # Task started immediately - return 200
        return TaskResponse(
            task_id=task.task_id,
            status=task.status,
            priority=task.priority,
            agent_dir=task.agent_dir,
            project_id=task.project_id,
            session_id=task.session_id,
            created_at=task.created_at,
            started_at=task.started_at,
            message="Task started immediately",
        )
    else:
        # Task is pending (queued) - return 202
        position = None
        if task.project_id:
            try:
                position = service._get_queue_position(task.task_id, task.project_id)
            except Exception:
                pass  # Best effort - position is optional
        
        response = TaskResponse(
            task_id=task.task_id,
            status=task.status,
            priority=task.priority,
            agent_dir=task.agent_dir,
            project_id=task.project_id,
            created_at=task.created_at,
            position=position,
            message="Task queued, waiting for project lock",
        )
        return JSONResponse(
            status_code=202,
            content=response.model_dump()
        )


@router.get(
    "/{task_id}",
    responses={
        200: {"description": "Task details"},
        404: {"model": TaskNotFoundResponse, "description": "Task not found"},
    },
)
async def get_task(
    task_id: str,
    service: TaskQueueService = Depends(get_task_queue_service),
) -> TaskResponse:
    """Get task status and details by ID.
    
    Returns:
        200 with task details
        404 if task doesn't exist
    """
    task = await service.get_task(task_id)
    
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=TaskNotFoundResponse(
                error="Task not found",
                task_id=task_id
            ).model_dump()
        )
    
    # Get position if task is pending
    position = None
    if task.status == TaskStatus.PENDING.value and task.project_id:
        try:
            position = service._get_queue_position(task.task_id, task.project_id)
        except Exception:
            pass  # Best effort
    
    return _task_to_response(task, position=position)


@router.get(
    "",
    response_model=TaskListResponse,
)
async def list_tasks(
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    service: TaskQueueService = Depends(get_task_queue_service),
) -> TaskListResponse:
    """List tasks with optional filters.
    
    Query params:
        - status: Filter by status (pending, processing, completed, failed, cancelled)
        - project_id: Filter by project ID
        - limit: Maximum number of tasks to return (default: 50)
    
    Returns:
        200 with list of tasks and total count
    """
    # Validate status if provided
    task_status = None
    if status:
        try:
            task_status = TaskStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "Invalid status", "message": f"Invalid status: {status}. Valid values: pending, processing, completed, failed, cancelled"}
            )
    
    # Clamp limit
    limit = max(1, min(limit, 100))
    
    # List tasks
    tasks = await service.list_tasks(
        status=task_status,
        project_id=project_id,
        limit=limit,
    )
    
    # Convert to response format
    task_responses = []
    for task in tasks:
        # Get position if pending
        position = None
        if task.status == TaskStatus.PENDING.value and task.project_id:
            try:
                position = service._get_queue_position(task.task_id, task.project_id)
            except Exception:
                pass
        
        task_responses.append(_task_to_response(task, position=position))
    
    return TaskListResponse(
        tasks=task_responses,
        total=len(task_responses),  # Note: for accurate total, would need a count method
    )
