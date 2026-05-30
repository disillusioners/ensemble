"""Global notification API endpoints via SSE."""

import asyncio
import json

from fastapi import APIRouter, Request

from daemon.constants import SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE, SSE_TIMEOUT_S
from sse_starlette.sse import EventSourceResponse


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _get_broadcaster(request: Request):
    """Get the NotificationBroadcaster from the manager."""
    manager = request.app.state.manager
    if manager is not None:
        return manager._notification_broadcaster
    return None


def _get_manager(request: Request):
    """Get the InstanceManager from app state."""
    return request.app.state.manager


# GET /api/notifications/stream - SSE notification stream
@router.get("/stream")
async def stream_notifications(request: Request):
    """SSE stream delivering global notification events.

    Streams notifications for root instance completion to all connected clients.
    Each notification includes: instance_id, agent_id, name, status, timestamp.
    """
    broadcaster = _get_broadcaster(request)

    if broadcaster is None:
        # Return error response if broadcaster not initialized
        async def error_generator():
            yield {
                "event": "error",
                "data": json.dumps({"error": "notification_service_not_initialized"}),
            }

        return EventSourceResponse(error_generator())

    async def event_generator():
        # Create a queue for this connection
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)

        # Register connection and get connection ID
        connection_id = await broadcaster.add_connection(queue)

        try:
            # Send connected event
            yield {
                "event": "connected",
                "data": json.dumps({"status": "connected"}),
            }

            while True:
                if await request.is_disconnected():
                    break

                try:
                    notification = await asyncio.wait_for(
                        queue.get(), timeout=SSE_TIMEOUT_S
                    )
                    notification_event = notification.get("event_type")
                    if notification_event == "instance_created":
                        yield {
                            "event": "instance_created",
                            "data": json.dumps(notification),
                        }
                    else:
                        yield {
                            "event": "notification",
                            "data": json.dumps(notification),
                        }
                except asyncio.TimeoutError:
                    # No message within timeout, loop continues
                    # sse_starlette handles ping via ping=SSE_PING_INTERVAL parameter
                    pass

        finally:
            # Cleanup connection on disconnect
            await broadcaster.remove_connection(connection_id)

    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
