"""Job Queue API endpoints."""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.models import JobStatus
from daemon.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, DEFAULT_JOB_LIST_LIMIT, MAX_JOB_LIST_LIMIT
from .schemas import (
    JobCreateRequest,
    JobResponse,
    JobListResponse,
    JobValidationError,
    JobNotFoundResponse,
)

logger = logging.getLogger(__name__)

# Create router with /api/jobs prefix
router = APIRouter(prefix="/jobs", tags=["jobs"])

# Terminal statuses for job lifecycle
TERMINAL_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.DEAD_LETTER.value,
}

# Dependency to get JobQueueService
# This will be set up in daemon/api.py during app initialization
_job_queue_service: Optional[JobQueueService] = None


def get_job_queue_service() -> JobQueueService:
    """Get the JobQueueService instance.
    
    Returns:
        JobQueueService instance.
        
    Raises:
        HTTPException: If the service is not initialized.
    """
    if _job_queue_service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Job queue service not initialized"}
        )
    return _job_queue_service


def set_job_queue_service(service: JobQueueService) -> None:
    """Set the JobQueueService instance (called during app startup)."""
    global _job_queue_service
    _job_queue_service = service


# Dependency to get DeadLetterService
_dead_letter_service: Optional[DeadLetterService] = None


def get_dead_letter_svc() -> DeadLetterService:
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


def _job_to_response(
    job,
    position: Optional[int] = None,
    message: Optional[str] = None,
    dlq_reason: Optional[str] = None,
    retry_count: Optional[int] = None,
    moved_to_dlq_at: Optional[str] = None,
) -> JobResponse:
    """Convert JobItem to JobResponse."""
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
        queue_id=job.queue_id,
        instance_id=job.instance_id,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_summary=job.result_summary,
        error_message=job.error_message,
        source=job.source,
        job_metadata=job.job_metadata,
        cancelled_at=job.cancelled_at,
        idempotency_key=job.idempotency_key,
        position=position,
        message=message or job.message,
        dlq_reason=dlq_reason,
        retry_count=retry_count,
        moved_to_dlq_at=moved_to_dlq_at,
        deleted_at=job.deleted_at,
    )


# ==================== Endpoints ====================


@router.post(
    "",
    response_model=JobResponse,
    responses={
        201: {"description": "Job created"},
        200: {"description": "Existing job returned (idempotent)"},
        422: {"model": JobValidationError, "description": "Validation error"},
    },
)
async def create_job(
    request: JobCreateRequest,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Submit a new job for processing.
    
    Jobs are queued and processed by the JobProcessor. The job starts
    as PENDING and transitions to PROCESSING when picked up by the processor.
    
    With idempotency_key: if a job with the same key exists and is non-terminal,
    returns HTTP 200 with the existing job instead of creating a duplicate.
    
    Returns:
        201 with job details (new job created)
        200 with job details (existing non-terminal job returned)
        422 if validation errors
    """
    # Validate: queue_id requires project_id
    if request.queue_id and not request.project_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "Validation Error", "message": "project_id is required when queue_id is specified"}
        )

    # Validate and resolve agent input
    try:
        from daemon.utils import validate_agent_id
        resolved_agent_id, agent_path = validate_agent_id(request.agent_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid agent", "message": str(e)}
        )
    
    # Enqueue the job (service.enqueue handles idempotency check internally)
    try:
        job = await service.enqueue(
            agent_id=resolved_agent_id,
            message=request.message,
            source=request.source,
            project_id=request.project_id,
            priority=request.priority,
            metadata=request.metadata,
            queue_id=request.queue_id,
            idempotency_key=request.idempotency_key,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=JobValidationError(
                error="Validation Error",
                details=[{"field": str(err["loc"][0]) if err["loc"] else "unknown", "message": err["msg"]} 
                        for err in e.errors()]
            ).model_dump()
        )
    except Exception as e:
        logger.error(f"Failed to enqueue job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "Job submission failed", "message": "An internal error occurred while submitting the job"}
        )
    
    # Check if this was an idempotent return (job existed before this request)
    # This is detected by checking if the returned job has the same idempotency_key
    # and was already non-pending when returned
    is_idempotent_return = False
    if request.idempotency_key and job.idempotency_key == request.idempotency_key:
        if job.status != JobStatus.PENDING.value:
            is_idempotent_return = True
    
    # Job is always PENDING at creation - return position if project_id provided
    position = None
    if job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass  # Best effort - position is optional
    
    response = _job_to_response(job, position=position, message="Job queued for processing")
    
    # Return 200 for idempotent returns, 201 for new jobs
    return JSONResponse(
        status_code=200 if is_idempotent_return else 201,
        content=response.model_dump()
    )


@router.get(
    "/{job_id}",
    responses={
        200: {"description": "Job details"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def get_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
) -> JobResponse:
    """Get job status and details by ID.
    
    Returns:
        200 with job details
        404 if job doesn't exist
    """
    job = await service.get_job(job_id)
    
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )
    
    # Get position if job is pending
    position = None
    if job.status == JobStatus.PENDING.value and job.project_id:
        try:
            position = await service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass  # Best effort

    # Get DLQ info if job is in dead_letter state
    dlq_reason = None
    retry_count = None
    moved_to_dlq_at = None
    if job.status == JobStatus.DEAD_LETTER.value:
        dlq_item = dlq_service.get_dlq_by_job_id(job_id)
        if dlq_item:
            dlq_reason = dlq_item.reason
            retry_count = dlq_item.retry_count
            moved_to_dlq_at = dlq_item.moved_to_dlq_at

    return _job_to_response(
        job, 
        position=position,
        dlq_reason=dlq_reason,
        retry_count=retry_count,
        moved_to_dlq_at=moved_to_dlq_at,
    )


@router.get(
    "",
    response_model=JobListResponse,
)
async def list_jobs(
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    queue_id: Optional[str] = None,
    limit: int = DEFAULT_JOB_LIST_LIMIT,
    include_deleted: bool = False,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
) -> JobListResponse:
    """List jobs with optional filters.
    
    Query params:
        - status: Filter by status(es), comma-separated (pending, processing, completed, failed, cancelled, dead_letter)
        - project_id: Filter by project ID
        - queue_id: Filter by queue ID
        - limit: Maximum number of jobs to return (default: 50)
        - include_deleted: Include soft-deleted jobs (default: False)
    
    Returns:
        200 with list of jobs and total count
    """
    # Parse and validate statuses if provided
    statuses = None
    if status:
        # Parse, deduplicate, and normalize
        status_list = list(dict.fromkeys(
            s.strip().lower() for s in status.split(',') if s.strip()
        ))
        if len(status_list) > 20:
            raise HTTPException(
                status_code=400,
                detail={"error": "Too many status filters", "message": "Maximum 20 status values allowed"}
            )
        invalid_statuses = [s for s in status_list if not JobStatus.is_valid(s)]
        if invalid_statuses:
            raise HTTPException(
                status_code=400,
                detail={"error": "Invalid status", "message": f"Invalid status: {', '.join(invalid_statuses)}. Valid values: pending, processing, completed, failed, cancelled, dead_letter"}
            )
        statuses = status_list
    
    # Clamp limit
    limit = max(1, min(limit, MAX_JOB_LIST_LIMIT))
    
    # Validate: queue_id requires project_id
    if queue_id and not project_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "Validation Error", "message": "project_id is required when queue_id is specified"}
        )
    
    # Validate queue belongs to project (IDOR protection)
    if queue_id and project_id:
        from daemon.routers.queues import get_mgmt_service
        try:
            mgmt = get_mgmt_service()
            queue = await mgmt.get_queue(project_id, queue_id)
            if queue is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "Queue not found", "message": f"Queue {queue_id} not found for project {project_id}"}
                )
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail={"error": "Queue not found", "message": f"Queue {queue_id} not found for project {project_id}"}
            )
    
    # List jobs
    jobs = await service.list_jobs(
        statuses=statuses,
        project_id=project_id,
        limit=limit,
        queue_id=queue_id,
        include_deleted=include_deleted,
    )
    
    # Convert to response format
    job_responses = []
    for job in jobs:
        # Get position if pending
        position = None
        if job.status == JobStatus.PENDING.value and job.project_id:
            try:
                position = await service._get_queue_position(job.job_id, job.project_id)
            except Exception:
                pass
        
        # Get DLQ info if job is in dead_letter state
        dlq_reason = None
        retry_count = None
        moved_to_dlq_at = None
        if job.status == JobStatus.DEAD_LETTER.value:
            dlq_item = dlq_service.get_dlq_by_job_id(job.job_id)
            if dlq_item:
                dlq_reason = dlq_item.reason
                retry_count = dlq_item.retry_count
                moved_to_dlq_at = dlq_item.moved_to_dlq_at
        
        job_responses.append(_job_to_response(
            job, 
            position=position,
            dlq_reason=dlq_reason,
            retry_count=retry_count,
            moved_to_dlq_at=moved_to_dlq_at,
        ))
    
    return JobListResponse(
        jobs=job_responses,
        total=len(job_responses),  # Note: for accurate total, would need a count method
    )


# ==================== Job Management Endpoints ====================


@router.delete(
    "/{job_id}",
    responses={
        200: {"description": "Job cancelled or soft-deleted successfully"},
        400: {"description": "Job already deleted"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def delete_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Delete (cancel or soft-delete) a job.
    
    - If job is PENDING or PROCESSING → cancel (existing behavior)
    - If job is in terminal state (completed, failed, cancelled, dead_letter) → soft delete
    - If job is already deleted → return 400
    
    Returns:
        200 if cancelled/soft-deleted successfully
        400 if job is already deleted
        404 if job not found
    """
    job = await service.get_job(job_id)
    
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )
    
    # Check if already deleted
    if job.deleted_at is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job already deleted",
                "message": "This job has already been soft-deleted",
                "job_id": job_id,
            }
        )
    
    # Handle based on status
    if job.status in TERMINAL_STATUSES:
        # Terminal state → soft delete
        updated_job = await service.soft_delete_job(job_id)
        if updated_job is None:
            raise HTTPException(
                status_code=500,
                detail={"error": "Failed to soft-delete job"}
            )
        return _job_to_response(
            updated_job,
            message="Job soft-deleted successfully"
        )
    else:
        # PENDING or PROCESSING → cancel
        success = await service.cancel_job(job_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Failed to cancel job",
                    "message": f"Could not cancel job in state: {job.status}",
                }
            )
        updated_job = await service.get_job(job_id)
        return _job_to_response(
            updated_job,
            message="Job cancelled successfully"
        )


@router.post(
    "/{job_id}/cancel",
    responses={
        200: {"description": "Job cancelled successfully"},
        400: {"description": "Job cannot be cancelled"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def cancel_job_endpoint(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Cancel a pending or processing job.
    
    Explicit cancel endpoint for API consumers who want clear cancel semantics.
    
    Returns:
        200 if cancelled successfully
        400 if job is already in a terminal state or deleted
        404 if job not found
    """
    job = await service.get_job(job_id)
    
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )
    
    # Check if already deleted
    if job.deleted_at is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be cancelled",
                "message": "This job has already been soft-deleted",
                "job_id": job_id,
            }
        )
    
    # Check if job is in a cancellable state
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be cancelled",
                "message": f"Job is already in terminal state: {job.status}",
                "current_status": job.status,
            }
        )
    
    # Cancel the job
    success = await service.cancel_job(job_id)
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Failed to cancel job",
                "message": f"Could not cancel job in state: {job.status}",
            }
        )
    
    updated_job = await service.get_job(job_id)
    return _job_to_response(
        updated_job,
        message="Job cancelled successfully"
    )


@router.post(
    "/{job_id}/restore",
    responses={
        200: {"description": "Job restored successfully"},
        400: {"description": "Job cannot be restored (not deleted or terminal)"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def restore_job_endpoint(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Restore a soft-deleted job.
    
    Returns:
        200 with restored job
        400 if job is not deleted or is in terminal state
        404 if job not found
    """
    job = await service.get_job(job_id)
    
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )
    
    # Check if job was deleted
    if job.deleted_at is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be restored",
                "message": "This job has not been soft-deleted",
                "job_id": job_id,
            }
        )
    
    # Check if job is in a terminal state (restore not allowed for terminal jobs)
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be restored",
                "message": f"Cannot restore a job in terminal state: {job.status}. Retry the job instead.",
                "current_status": job.status,
            }
        )
    
    # Restore the job
    restored_job = await service.restore_job(job_id)
    
    if restored_job is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to restore job"}
        )
    
    return _job_to_response(
        restored_job,
        message="Job restored successfully"
    )


@router.post(
    "/{job_id}/retry",
    responses={
        200: {"description": "Job requeued for retry"},
        400: {"description": "Job cannot be retried"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
        422: {"description": "DEAD_LETTER entry not found for job"},
    },
)
async def retry_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
    dlq_service: DeadLetterService = Depends(get_dead_letter_svc),
):
    """Retry a job by re-queuing it for processing.
    
    - FAILED jobs: Creates a NEW job with the same parameters (leaves original as FAILED)
    - DEAD_LETTER jobs: Resets the existing job to PENDING via DLQ replay
    
    Returns:
        200 with job details if retry successful
        400 if job is in neither FAILED nor DEAD_LETTER state
        404 if job not found
        422 if DEAD_LETTER entry not found for DEAD_LETTER job
    """
    job = await service.get_job(job_id)
    
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )
    
    # Handle DEAD_LETTER jobs - replay from DLQ
    if job.status == JobStatus.DEAD_LETTER.value:
        # Find the DLQ entry for this job
        dlq_item = dlq_service.get_dlq_by_job_id(job_id)
        
        if dlq_item is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "DEAD_LETTER entry not found",
                    "message": f"Job {job_id} is in DEAD_LETTER state but no DLQ entry exists",
                    "job_id": job_id,
                }
            )
        
        # Replay from DLQ - resets job to PENDING and deletes DLQ entry atomically
        try:
            updated_job = dlq_service.replay_from_dlq(dlq_item.dlq_id)
        except Exception as e:
            logger.error(f"Failed to replay job {job_id} from DLQ: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Failed to replay job",
                    "message": str(e),
                }
            )
        
        # Get position in queue
        position = None
        if updated_job.project_id:
            try:
                position = await service._get_queue_position(updated_job.job_id, updated_job.project_id)
            except Exception:
                pass

        return _job_to_response(
            updated_job,
            position=position,
            message="Job replayed from DEAD_LETTER queue",
            dlq_reason=dlq_item.reason,
            retry_count=dlq_item.retry_count,
            moved_to_dlq_at=dlq_item.moved_to_dlq_at,
        )
    
    # Handle FAILED jobs - create new job with same parameters
    if job.status != JobStatus.FAILED.value:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be retried",
                "message": f"Only FAILED or DEAD_LETTER jobs can be retried. Current status: {job.status}",
                "current_status": job.status,
            }
        )
    
    # Retry the job - creates a new job with same parameters
    new_job = await service.retry_job(job_id)
    
    if new_job is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Failed to retry job",
                "message": "Could not create retry job",
            }
        )
    
    # Get position if job is pending
    position = None
    if new_job.status == JobStatus.PENDING.value and new_job.project_id:
        try:
            position = await service._get_queue_position(new_job.job_id, new_job.project_id)
        except Exception:
            pass

    return _job_to_response(
        new_job,
        position=position,
        message="Job queued for retry"
    )


# ==================== SSE Endpoint ====================


@router.get(
    "/{job_id}/events",
    responses={
        200: {"description": "SSE stream for job events"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def stream_job_events(
    job_id: str,
    request: Request,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """SSE stream for real-time job updates.
    
    Streams events including:
    - connected: Initial connection event
    - status_update: Job status changes
    - completed: Job reached terminal state (completed, failed, or cancelled)
    - keepalive: Periodic keepalive to prevent timeout
    
    The stream automatically closes when the job reaches a terminal state.
    """
    # Check job exists first
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    async def event_generator() -> AsyncGenerator[dict, None]:
        """Generate SSE events for job updates."""
        last_status = job.status
        
        try:
            # Send initial connection event with current job state
            yield {
                "event": "connected",
                "data": json.dumps({
                    "job_id": job_id,
                    "status": job.status,
                    "instance_id": job.instance_id,
                    "queue_id": job.queue_id,
                })
            }
            logger.info(f"SSE connected to job {job_id}, initial status: {job.status}")

            # If already in terminal state, send completed and exit
            if job.status in TERMINAL_STATUSES:
                yield {
                    "event": "completed",
                    "data": json.dumps({
                        "job_id": job_id,
                        "status": job.status,
                        "result_summary": job.result_summary,
                        "error_message": job.error_message,
                        "queue_id": job.queue_id,
                    })
                }
                logger.info(f"Job {job_id} already in terminal state: {job.status}")
                return

            # Stream events until job reaches terminal state
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    logger.info(f"Client disconnected from job {job_id} SSE stream")
                    break

                # Poll for job updates
                current_job = await service.get_job(job_id)
                
                if current_job is None:
                    # Job was deleted
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Job not found"})
                    }
                    break

                # Check for status change
                if current_job.status != last_status:
                    previous_status = last_status
                    last_status = current_job.status
                    
                    # Send status update event
                    yield {
                        "event": "status_update",
                        "data": json.dumps({
                            "job_id": job_id,
                            "status": current_job.status,
                            "instance_id": current_job.instance_id,
                            "previous_status": previous_status,
                            "queue_id": current_job.queue_id,
                        })
                    }
                    logger.debug(f"Job {job_id} status changed to: {current_job.status}")

                    # Check if job reached terminal state
                    if current_job.status in TERMINAL_STATUSES:
                        yield {
                            "event": "completed",
                            "data": json.dumps({
                                "job_id": job_id,
                                "status": current_job.status,
                                "result_summary": current_job.result_summary,
                                "error_message": current_job.error_message,
                                "queue_id": current_job.queue_id,
                            })
                        }
                        logger.info(f"Job {job_id} completed with status: {current_job.status}")
                        break

                # Wait before next poll (2 seconds)
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for job {job_id}")
            raise
        except Exception as e:
            logger.exception(f"Error in job SSE stream for {job_id}: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": "Stream error", "details": "An internal error occurred in the stream"})
            }

    # Return EventSourceResponse with custom keepalive
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
        ping=5,  # Send keepalive every 5 seconds
    )


# Backward compatibility aliases
TaskResponse = JobResponse
TaskListResponse = JobListResponse
TaskCreateRequest = JobCreateRequest
TaskValidationError = JobValidationError
TaskNotFoundResponse = JobNotFoundResponse
