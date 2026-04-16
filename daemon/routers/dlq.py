"""Dead Letter Queue API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from daemon.services.dead_letter_service import DeadLetterService, DLQItemNotFoundError
from daemon.repositories.job_queue.models import DeadLetterItem as DLQModel

logger = logging.getLogger(__name__)

# Create router with /projects/{project_id}/dlq prefix
router = APIRouter(prefix="/projects/{project_id}/dlq", tags=["dlq"])

# Dependency to get DeadLetterService
# This will be set up in daemon/api.py during app initialization
_dead_letter_service: Optional[DeadLetterService] = None


def get_dead_letter_service() -> DeadLetterService:
    """Get the DeadLetterService instance.
    
    Returns:
        DeadLetterService instance.
        
    Raises:
        HTTPException: If the service is not initialized.
    """
    if _dead_letter_service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Dead letter service not initialized"}
        )
    return _dead_letter_service


def set_dead_letter_service(service: DeadLetterService) -> None:
    """Set the DeadLetterService instance (called during app startup)."""
    global _dead_letter_service
    _dead_letter_service = service


# ==================== Schemas ====================


class DLQItemResponse(BaseModel):
    """Response for a single DLQ item."""
    
    dlq_id: str = Field(..., description="Unique DLQ item identifier")
    job_id: str = Field(..., description="Original job ID")
    agent_id: str = Field(..., description="Agent ID")
    agent_dir: str = Field(..., description="Agent directory path")
    message: str = Field(..., description="Job message/content")
    source: str = Field(..., description="Job source (api, telegram, scheduler, webhook)")
    project_id: str = Field(..., description="Project ID")
    queue_id: str = Field(..., description="Queue ID")
    priority: int = Field(..., description="Job priority (1-10)")
    error_message: str = Field(..., description="Error message from failed job")
    retry_count: int = Field(..., description="Number of retries attempted")
    failed_at: str = Field(..., description="Timestamp when job failed")
    moved_to_dlq_at: str = Field(..., description="Timestamp when item was moved to DLQ")
    reason: str = Field(..., description="Reason for DLQ (MAX_RETRIES, MANUAL, etc.)")
    metadata: dict = Field(default_factory=dict, description="Job metadata")
    
    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "dlq_id": "dlq-uuid",
                "job_id": "job-uuid",
                "agent_id": "coder",
                "agent_dir": "/agents/coder",
                "message": "Fix the login bug",
                "source": "api",
                "project_id": "project-uuid",
                "queue_id": "queue-uuid",
                "priority": 5,
                "error_message": "Connection timeout after 3 retries",
                "retry_count": 3,
                "failed_at": "2025-03-15T10:00:00",
                "moved_to_dlq_at": "2025-03-15T10:05:00",
                "reason": "MAX_RETRIES",
                "metadata": {"user_id": "user-123"}
            }
        }
    }


class DLQListResponse(BaseModel):
    """Response for listing DLQ items."""
    
    items: list[DLQItemResponse] = Field(default_factory=list, description="List of DLQ items")
    total: int = Field(..., description="Total number of items")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "dlq_id": "dlq-uuid-1",
                        "job_id": "job-uuid-1",
                        "agent_id": "coder",
                        "agent_dir": "/agents/coder",
                        "message": "Fix the login bug",
                        "source": "api",
                        "project_id": "project-uuid",
                        "queue_id": "queue-uuid",
                        "priority": 5,
                        "error_message": "Connection timeout",
                        "retry_count": 3,
                        "failed_at": "2025-03-15T10:00:00",
                        "moved_to_dlq_at": "2025-03-15T10:05:00",
                        "reason": "MAX_RETRIES",
                        "metadata": {}
                    }
                ],
                "total": 1
            }
        }
    }


class DLQNotFoundResponse(BaseModel):
    """Not found error response for DLQ items."""
    
    error: str = Field(default="DLQ item not found", description="Error type")
    dlq_id: str = Field(..., description="The DLQ ID that was not found")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "DLQ item not found",
                "dlq_id": "invalid-uuid"
            }
        }
    }


class DLQReplayResponse(BaseModel):
    """Response for replaying a DLQ item."""
    
    job_id: str = Field(..., description="The replayed job ID")
    status: str = Field(..., description="New job status (should be 'pending')")
    message: str = Field(default="Job queued for replay", description="Status message")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job-uuid",
                "status": "pending",
                "message": "Job queued for replay"
            }
        }
    }


class DLQCleanupResponse(BaseModel):
    """Response for DLQ cleanup."""
    
    deleted_count: int = Field(..., description="Number of DLQ items deleted")
    message: str = Field(..., description="Cleanup status message")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "deleted_count": 5,
                "message": "Deleted 5 DLQ items"
            }
        }
    }


# ==================== Helper Functions ====================


def _dlq_to_response(dlq_item: DLQModel) -> DLQItemResponse:
    """Convert DeadLetterItem model to DLQItemResponse.
    
    Args:
        dlq_item: DeadLetterItem model instance.
        
    Returns:
        DLQItemResponse with all fields.
    """
    return DLQItemResponse(
        dlq_id=dlq_item.dlq_id,
        job_id=dlq_item.job_id,
        agent_id=dlq_item.agent_id,
        agent_dir=dlq_item.agent_dir,
        message=dlq_item.message,
        source=dlq_item.source,
        project_id=dlq_item.project_id,
        queue_id=dlq_item.queue_id,
        priority=dlq_item.priority,
        error_message=dlq_item.error_message,
        retry_count=dlq_item.retry_count,
        failed_at=dlq_item.failed_at,
        moved_to_dlq_at=dlq_item.moved_to_dlq_at,
        reason=dlq_item.reason,
        metadata=dlq_item.metadata_json if dlq_item.metadata_json else {},
    )


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=DLQListResponse,
    responses={
        200: {"description": "List of DLQ items"},
        503: {"description": "Service not initialized"},
    },
)
async def list_dlq(
    project_id: str,
    queue_id: Optional[str] = None,
    reason: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    service: DeadLetterService = Depends(get_dead_letter_service),
) -> DLQListResponse:
    """List dead letter queue items for a project.
    
    Query params:
        - queue_id: Filter by queue ID
        - reason: Filter by reason (MAX_RETRIES, MANUAL, etc.)
        - limit: Maximum number of items to return (default: 50, max: 100)
        - offset: Number of items to skip (default: 0)
    
    Returns:
        200 with list of DLQ items and total count
    """
    # Clamp limit
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    
    # List DLQ items
    items, total = service.list_dlq(
        project_id=project_id,
        queue_id=queue_id,
        reason=reason,
        limit=limit + offset,  # Fetch extra for pagination
    )
    
    # Apply pagination
    paginated_items = items[offset:offset + limit]
    
    return DLQListResponse(
        items=[_dlq_to_response(item) for item in paginated_items],
        total=len(items),
    )


@router.get(
    "/{dlq_id}",
    response_model=DLQItemResponse,
    responses={
        200: {"description": "DLQ item details"},
        404: {"model": DLQNotFoundResponse, "description": "DLQ item not found"},
        503: {"description": "Service not initialized"},
    },
)
async def get_dlq_item(
    project_id: str,
    dlq_id: str,
    service: DeadLetterService = Depends(get_dead_letter_service),
) -> DLQItemResponse:
    """Get a specific DLQ item by ID.
    
    Args:
        project_id: Project identifier from path.
        dlq_id: DLQ item identifier.
        
    Returns:
        200 with DLQ item details
        404 if DLQ item not found
    """
    dlq_item = service.get_dlq(dlq_id)
    
    if dlq_item is None:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    # Verify ownership (DLQ item belongs to this project)
    if dlq_item.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    return _dlq_to_response(dlq_item)


@router.post(
    "/{dlq_id}/replay",
    response_model=DLQReplayResponse,
    responses={
        200: {"description": "Job replayed successfully"},
        404: {"model": DLQNotFoundResponse, "description": "DLQ item not found"},
        503: {"description": "Service not initialized"},
    },
)
async def replay_dlq_item(
    project_id: str,
    dlq_id: str,
    service: DeadLetterService = Depends(get_dead_letter_service),
) -> DLQReplayResponse:
    """Replay a job from the dead letter queue.
    
    Atomically:
    1. Updates job status from DEAD_LETTER to PENDING
    2. Resets retry_count to 0
    3. Clears error_message, failed_at, started_at, completed_at, instance_id
    4. Deletes the DLQ item
    
    The job will be picked up by the JobProcessor on its next poll cycle.
    
    Args:
        project_id: Project identifier from path.
        dlq_id: DLQ item identifier.
        
    Returns:
        200 with replayed job details
        404 if DLQ item not found
    """
    # Verify DLQ item exists and belongs to project
    dlq_item = service.get_dlq(dlq_id)
    if dlq_item is None:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    if dlq_item.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    # Replay the job (atomic operation)
    try:
        job = service.replay_from_dlq(dlq_id)
        
        if job is None:
            raise HTTPException(
                status_code=500,
                detail={"error": "Failed to replay job", "message": "Job not found after replay"}
            )
        
        return DLQReplayResponse(
            job_id=job.job_id,
            status=job.status,
            message="Job queued for replay"
        )
    except DLQItemNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    except Exception as e:
        logger.error(f"Failed to replay DLQ item {dlq_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to replay job", "message": str(e)}
        )


@router.delete(
    "/{dlq_id}",
    status_code=204,
    responses={
        204: {"description": "DLQ item deleted successfully"},
        404: {"model": DLQNotFoundResponse, "description": "DLQ item not found"},
        503: {"description": "Service not initialized"},
    },
)
async def delete_dlq_item(
    project_id: str,
    dlq_id: str,
    service: DeadLetterService = Depends(get_dead_letter_service),
) -> None:
    """Delete a single DLQ item.
    
    Permanently removes the DLQ record. The original job remains in
    DEAD_LETTER status.
    
    Args:
        project_id: Project identifier from path.
        dlq_id: DLQ item identifier.
        
    Returns:
        204 if deleted successfully
        404 if DLQ item not found
    """
    # Verify DLQ item exists and belongs to project
    dlq_item = service.get_dlq(dlq_id)
    if dlq_item is None:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    if dlq_item.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    # Delete the DLQ item
    success = service.delete_dlq(dlq_id)
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail=DLQNotFoundResponse(
                error="DLQ item not found",
                dlq_id=dlq_id
            ).model_dump()
        )
    
    # Return 204 No Content
    return None


@router.delete(
    "",
    response_model=DLQCleanupResponse,
    responses={
        200: {"description": "Bulk cleanup completed"},
        503: {"description": "Service not initialized"},
    },
)
async def cleanup_dlq(
    project_id: str,
    max_age_days: int = 30,
    reason: Optional[str] = None,
    service: DeadLetterService = Depends(get_dead_letter_service),
) -> DLQCleanupResponse:
    """Bulk cleanup of DLQ items.
    
    Query params:
        - max_age_days: Delete items older than N days (default: 30)
        - reason: Optional filter by reason (MAX_RETRIES, MANUAL, etc.)
    
    Args:
        project_id: Project identifier from path.
        max_age_days: Maximum age in days for items to delete.
        reason: Optional reason filter.
        
    Returns:
        200 with count of deleted items
    """
    # Validate max_age_days
    if max_age_days < 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid parameter", "message": "max_age_days must be non-negative"}
        )
    
    # The service.cleanup_dlq() correctly converts days to hours internally
    deleted_count = service.cleanup_dlq(max_age_days=max_age_days, reason=reason)
    
    return DLQCleanupResponse(
        deleted_count=deleted_count,
        message=f"Deleted {deleted_count} DLQ items"
    )
