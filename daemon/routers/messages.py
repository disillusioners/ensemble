"""Instance message API endpoints."""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request

from daemon.constants import SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE, SSE_TIMEOUT_S
from daemon.models import ErrorCodes, ErrorResponse, MessageCreate, MessageResponse
from daemon.models.instance import InstanceStatus
from daemon.services.live_event_hub import LiveEventHub
from sse_starlette.sse import EventSourceResponse


router = APIRouter(prefix="/instances", tags=["instances-messages"])

# Module-level manager reference (set during app startup)
_manager: Optional["InstanceManager"] = None


def set_manager(manager: "InstanceManager") -> None:
    """Set the InstanceManager instance (called during app startup)."""
    global _manager
    _manager = manager


def _get_manager(request: Request) -> "InstanceManager":
    """Get the manager instance from request state or module-level reference.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        InstanceManager instance.
        
    Raises:
        HTTPException: If manager is not available.
    """
    # Try request state first (set during startup)
    if hasattr(request.app.state, "manager") and request.app.state.manager is not None:
        return request.app.state.manager
    # Fall back to module-level reference
    if _manager is None:
        raise HTTPException(
            status_code=503,
            detail="Manager not initialized"
        )
    return _manager


# 1. POST /instances/{instance_id}/messages - Send message
@router.post("/{instance_id}/messages", response_model=MessageResponse)
async def send_message(instance_id: str, message: MessageCreate, request: Request):
    """Send a message to an instance (async via queue)."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Check if images are provided but vision model is not configured
    if message.images and not manager.config.llm.model_vision:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Images provided but model_vision is not configured. "
                        "Set OPENAI_MODEL_VISION environment variable or model_vision in config.yaml."
            ).model_dump()
        )

    # Enqueue the message (non-blocking)
    try:
        result = await manager.enqueue_message(
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
                message=f"Failed to enqueue message: {str(e)}"
            ).model_dump()
        )

    # Create response with queued status
    now = datetime.now(timezone.utc)

    return MessageResponse(
        message_id=result.message_id,
        role="assistant",
        content="",  # Response will come async
        thinking=None,
        thinking_extracted=None,
        tool_calls=None,
        images=None,  # Images are stored in message_queue, not in response
        created_at=now,
    )


# 2. GET /instances/{instance_id}/messages/{message_id} - Get message status
@router.get("/{instance_id}/messages/{message_id}")
async def get_message_status(instance_id: str, message_id: str, request: Request):
    """Get the status of a queued message."""
    manager = _get_manager(request)
    
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )
    
    # Get queue stats
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
        manager.get_instance(instance_id)
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
