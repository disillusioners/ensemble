"""Instance message API endpoints."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request

from daemon.constants import SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE, SSE_TIMEOUT_S
from daemon.models import ErrorCodes, ErrorResponse, MessageCreate, MessageResponse
from daemon.models.instance import InstanceStatus
from daemon.services.live_event_hub import LiveEventHub
from sse_starlette.sse import EventSourceResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/instances", tags=["instances-messages"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


# 1. POST /instances/{instance_id}/messages - Send message
@router.post("/{instance_id}/messages")
async def send_message(instance_id: str, message: MessageCreate, request: Request) -> dict:
    """Send a message to an instance (async via queue).
    
    If the instance is PAUSED, automatically resumes it with the user's message.
    """
    manager = _get_manager(request)
    
    # Check instance exists and get its status FIRST (before any enqueue)
    try:
        instance_info = manager.get_instance_info(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}",
            ).model_dump(),
        )
    
    # Validate images
    if message.images and not manager.config.llm.model_vision:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Images provided but model_vision is not configured. "
                        "Set OPENAI_MODEL_VISION environment variable or model_vision in config.yaml.",
            ).model_dump(),
        )
    
    # --- PAUSED INSTANCE: Skip enqueue, go straight to resume ---
    if instance_info.get("status") == InstanceStatus.PAUSED.value:
        logger.info(f"Instance {instance_id[:8]}... is PAUSED, auto-resuming with user message")
        
        # Cascade resume (same pattern as resume endpoint)
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
        
        # Resume processing jobs for all resumed instances
        resume_results = {}
        for resumed_id in resume_result["resumed_ids"]:
            is_target = resumed_id == target_id
            try:
                job_result = await manager.resume_processing_job(
                    resumed_id,
                    message=message.content if is_target else "resume",
                    silent=not is_target,
                    images=message.images if is_target else None,  # Pass images for target only
                )
            except Exception as e:
                logger.warning(f"Failed to resume processing for {resumed_id[:8]}...: {e}")
                job_result = {"status": "error", "error": str(e)}
            if job_result is None:
                logger.debug(f"No active PROCESSING job for instance {resumed_id[:8]}... (was IDLE/WAITING_CHILDREN)")
            resume_results[resumed_id] = job_result if job_result is not None else {"status": "no_active_job"}
        
        return {
            "message_id": None,  # No message queued — resume injects directly
            "role": "user",
            "content": message.content,
            "thinking": None,
            "thinking_extracted": None,
            "tool_calls": None,
            "images": None,
            "auto_resumed": True,
            "resume_info": {
                "resumed": True,
                "resumed_ids": resume_result["resumed_ids"],
                "skipped_ids": resume_result["skipped_ids"],
                "target_id": target_id,
                "resume_results": resume_results,
            },
        }
    
    # --- NORMAL PATH: Not paused, enqueue message ---
    try:
        result = await manager.enqueue_message_via_jq(
            instance_id=instance_id,
            message=message.content,
            source="api",
            images=message.images,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to enqueue message: {str(e)}",
            ).model_dump(),
        )
    
    response_data = MessageResponse(
        message_id=result.message_id,
        role="assistant",
        content="",  # Response will come async
        thinking=None,
        thinking_extracted=None,
        tool_calls=None,
        images=None,  # Images are stored in message_queue, not in response
        created_at=datetime.now(timezone.utc),
    ).model_dump()
    
    response_data["auto_resumed"] = False
    response_data["resume_info"] = None
    
    return response_data


# 2. GET /instances/{instance_id}/messages/{message_id} - Get message status
@router.get("/{instance_id}/messages/{message_id}")
async def get_message_status(instance_id: str, message_id: str, request: Request):
    """Get the status of a queued message."""
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

    # Try JobQueue path first (HTTP-originated messages)
    job_item = None
    if manager._job_queue_service:
        # Find MESSAGE job with this instance_id + message_id in metadata
        jobs = manager._job_queue_service.find_active_jobs_by_instance(
            instance_id, job_type="message"
        )
        job_item = next(
            (j for j in jobs if j.job_metadata and j.job_metadata.get("message_id") == message_id),
            None,
        )

    if job_item:
        # Return job-based status
        return {
            "message_id": message_id,
            "instance_id": instance_id,
            "status": job_item.status,
            "result_summary": job_item.result_summary,
            "error": job_item.error_message,
        }

    # Fallback: existing queue stats (internal/WorkerPool messages)
    stats = manager.get_queue_stats(instance_id)
    return {
        "message_id": message_id,
        "instance_id": instance_id,
        "queue_stats": {
            "pending_count": stats.pending_count,
            "processing_count": stats.processing_count,
            "oldest_message_age_seconds": stats.oldest_message_age_seconds,
        }
    }


# 3. GET /instances/{instance_id}/events - SSE stream
@router.get("/{instance_id}/events")
async def stream_events(instance_id: str, request: Request):
    """SSE stream delivering checkpoint events."""
    manager = _get_manager(request)
    
    if manager.is_shutting_down:
        raise HTTPException(status_code=503, detail="Server is shutting down")
    
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Instance not found: {instance_id}")
    
    live_hub: LiveEventHub = request.app.state.live_hub
    
    async def event_generator() -> AsyncGenerator[dict, None]:
        # 1. Connected event
        yield {
            "event": "connected",
            "data": json.dumps({"instance_id": instance_id}),
        }
        
        # 2. Create a queue for this connection
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        await live_hub.add_connection(instance_id, queue)
        
        try:
            while True:
                if await request.is_disconnected():
                    break
                
                if manager.is_shutting_down:
                    yield {"event": "error", "data": json.dumps({"error": "server_shutdown"})}
                    break
                
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}
                    continue
                
                yield {
                    "event": event["event_type"],
                    "id": event.get("event_id", ""),
                    "data": json.dumps(event),
                }
        finally:
            await live_hub.remove_connection(instance_id, queue)
    
    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
