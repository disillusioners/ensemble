"""Job Queue CRUD API endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.project_normalizer import normalize_project_id
from daemon.repositories.job_queue.models import JobStatus
from daemon.constants import DEFAULT_JOB_LIST_LIMIT, MAX_JOB_LIST_LIMIT
from daemon.utils import create_service_dependency, validate_agent_id
from .schemas import (
    JobCreateRequest,
    JobResponse,
    JobListResponse,
    JobValidationError,
    JobNotFoundResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Service dependencies
get_job_queue_service = create_service_dependency(JobQueueService)
get_dead_letter_svc = create_service_dependency(DeadLetterService)

# Terminal statuses for job lifecycle
TERMINAL_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.DEAD_LETTER.value,
}


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _job_to_response(
    job,
    position: int | None = None,
    message: str | None = None,
    dlq_reason: str | None = None,
    retry_count: int | None = None,
    moved_to_dlq_at: str | None = None,
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


# ==================== CRUD Endpoints ====================


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
    request: Request,
    body: JobCreateRequest,
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
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # Validate: queue_id requires project_id
    if body.queue_id and not body.project_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "Validation Error", "message": "project_id is required when queue_id is specified"}
        )

    # Validate and resolve agent input
    try:
        resolved_agent_id, agent_path = validate_agent_id(body.agent_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid agent", "message": str(e)}
        )
    
    # Normalize project_id for defense-in-depth consistency
    normalized_project_id = normalize_project_id(body.project_id)

    # Enqueue the job (service.enqueue handles idempotency check internally)
    try:
        job = await service.enqueue(
            agent_id=resolved_agent_id,
            message=body.message,
            source=body.source,
            project_id=normalized_project_id,
            priority=body.priority,
            metadata=body.metadata,
            queue_id=body.queue_id,
            idempotency_key=body.idempotency_key,
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
    if body.idempotency_key and job.idempotency_key == body.idempotency_key:
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
    status: str | None = None,
    project_id: str | None = None,
    queue_id: str | None = None,
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


__all__ = ["router", "_job_to_response", "TERMINAL_STATUSES"]
