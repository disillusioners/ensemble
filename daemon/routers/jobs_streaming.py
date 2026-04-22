"""Job Queue SSE Streaming API endpoints."""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from daemon.services.job_queue_service import JobQueueService
from daemon.utils import create_service_dependency
from .schemas import JobNotFoundResponse
from .jobs_crud import get_job_queue_service, TERMINAL_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ==================== Streaming Endpoints ====================


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


__all__ = ["router"]
