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
from daemon.repositories.job_queue.models import JobStatus
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


def _job_to_response(
    job,
    position: Optional[int] = None,
    message: Optional[str] = None,
) -> JobResponse:
    """Convert JobItem to JobResponse."""
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        priority=job.priority,
        agent_id=job.agent_id,
        agent_dir=job.agent_dir,
        project_id=job.project_id,
            instance_id=job.instance_id,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_summary=job.result_summary,
        error_message=job.error_message,
        position=position,
        message=message,
    )


# ==================== Endpoints ====================


@router.post(
    "",
    responses={
        200: {"description": "Job started immediately"},
        202: {"description": "Job queued"},
        422: {"model": JobValidationError, "description": "Validation error"},
    },
)
async def create_job(
    request: JobCreateRequest,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Submit a new job for processing.
    
    - If no project_id is provided, the job executes immediately
    - If project_id is provided and no lock is held, job starts immediately
    - If project_id is provided and a lock is held, job is queued
    
    Returns:
        200 with status=processing if job started immediately
        202 with status=pending if job was queued
        422 if validation errors
    """
    # Validate and resolve agent input
    try:
        from daemon.api import validate_agent_id
        resolved_agent_id, agent_path = validate_agent_id(request.agent_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid agent", "message": str(e)}
        )
    
    # Enqueue the job
    try:
        job = await service.enqueue(
            agent_id=resolved_agent_id,
            message=request.message,
            source=request.source,
            project_id=request.project_id,
            priority=request.priority,
            metadata=request.metadata,
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
        raise HTTPException(
            status_code=500,
            detail={"error": "Internal error", "message": str(e)}
        )
    
    # Determine response based on job status
    if job.status == JobStatus.PROCESSING.value:
        # Job started immediately - return 200
        return JobResponse(
            job_id=job.job_id,
            status=job.status,
            priority=job.priority,
            agent_id=job.agent_id,
            agent_dir=job.agent_dir,
            project_id=job.project_id,
        instance_id=job.instance_id,
            created_at=job.created_at,
            started_at=job.started_at,
            message="Job started immediately",
        )
    else:
        # Job is pending (queued) - return 202
        position = None
        if job.project_id:
            try:
                position = service._get_queue_position(job.job_id, job.project_id)
            except Exception:
                pass  # Best effort - position is optional
        
        response = JobResponse(
            job_id=job.job_id,
            status=job.status,
            priority=job.priority,
            agent_id=job.agent_id,
            agent_dir=job.agent_dir,
            project_id=job.project_id,
            created_at=job.created_at,
            position=position,
            message="Job queued, waiting for project lock",
        )
        return JSONResponse(
            status_code=202,
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
            position = service._get_queue_position(job.job_id, job.project_id)
        except Exception:
            pass  # Best effort
    
    return _job_to_response(job, position=position)


@router.get(
    "",
    response_model=JobListResponse,
)
async def list_jobs(
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    service: JobQueueService = Depends(get_job_queue_service),
) -> JobListResponse:
    """List jobs with optional filters.
    
    Query params:
        - status: Filter by status (pending, processing, completed, failed, cancelled)
        - project_id: Filter by project ID
        - limit: Maximum number of jobs to return (default: 50)
    
    Returns:
        200 with list of jobs and total count
    """
    # Validate status if provided
    job_status = None
    if status:
        try:
            job_status = JobStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "Invalid status", "message": f"Invalid status: {status}. Valid values: pending, processing, completed, failed, cancelled"}
            )
    
    # Clamp limit
    limit = max(1, min(limit, 100))
    
    # List jobs
    jobs = await service.list_jobs(
        status=job_status,
        project_id=project_id,
        limit=limit,
    )
    
    # Convert to response format
    job_responses = []
    for job in jobs:
        # Get position if pending
        position = None
        if job.status == JobStatus.PENDING.value and job.project_id:
            try:
                position = service._get_queue_position(job.job_id, job.project_id)
            except Exception:
                pass
        
        job_responses.append(_job_to_response(job, position=position))
    
    return JobListResponse(
        jobs=job_responses,
        total=len(job_responses),  # Note: for accurate total, would need a count method
    )


# ==================== Job Management Endpoints ====================


@router.delete(
    "/{job_id}",
    responses={
        200: {"description": "Job cancelled successfully"},
        400: {"description": "Job cannot be cancelled (already completed/failed/cancelled)"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def cancel_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Cancel a pending or processing job.
    
    - PENDING jobs are cancelled immediately
    - PROCESSING jobs are aborted and the lock is released
    
    Returns:
        200 if cancelled successfully
        400 if job is already in a terminal state (completed/failed/cancelled)
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
    
    # Return updated job status
    updated_job = await service.get_job(job_id)
    return _job_to_response(
        updated_job,
        message="Job cancelled successfully"
    )


@router.post(
    "/{job_id}/retry",
    responses={
        200: {"description": "New job created for retry"},
        400: {"description": "Job is not in FAILED state"},
        404: {"model": JobNotFoundResponse, "description": "Job not found"},
    },
)
async def retry_job(
    job_id: str,
    service: JobQueueService = Depends(get_job_queue_service),
):
    """Retry a failed job by creating a new job with the same parameters.
    
    Creates a new job with identical:
    - agent_dir
    - message
    - project_id
    - priority
    - metadata
    
    The new job will be queued and processed according to normal rules.
    
    Returns:
        200 with new job details if retry successful
        400 if original job is not in FAILED state
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
    
    # Check if job is in FAILED state
    if job.status != JobStatus.FAILED.value:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Job cannot be retried",
                "message": f"Only FAILED jobs can be retried. Current status: {job.status}",
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
            position = service._get_queue_position(new_job.job_id, new_job.project_id)
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
                            })
                        }
                        logger.info(f"Job {job_id} completed with status: {current_job.status}")
                        break

                # Wait before next poll (0.5 seconds)
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for job {job_id}")
            raise
        except Exception as e:
            logger.exception(f"Error in job SSE stream for {job_id}: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": "Stream error", "details": str(e)})
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
