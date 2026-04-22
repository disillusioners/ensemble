"""Job Queue Management API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.repositories.job_queue.models import JobStatus
from daemon.utils import create_service_dependency
from .schemas import (
    JobResponse,
    JobNotFoundResponse,
)
from .jobs_crud import (
    get_job_queue_service,
    get_dead_letter_svc,
    TERMINAL_STATUSES,
    _job_to_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ==================== Management Endpoints ====================


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


__all__ = ["router"]
