"""Instance message API endpoints."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request

from daemon.constants import SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE, SSE_TIMEOUT_S
from daemon.models import ErrorCodes, ErrorResponse, MessageCreate, MessageResponse
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.live_event_hub import LiveEventHub
from daemon.services.work_status import canonicalize_status
from sse_starlette.sse import EventSourceResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/instances", tags=["instances-messages"])


# D13 status canonicalization (Critical Fix 3, 2026-06-27).
# The ``/messages/{message_id}`` endpoint translates the post-D13
# ``Task`` status (``running``) back to the pre-D13 ``JobItem`` status
# (``processing``) the API contract promised — frontend clients compare
# ``status === 'processing'`` directly. The translation is delegated to
# the single shared vocabulary in ``daemon.services.work_status`` so
# this router does not carry its own status map.


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
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

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
    # Phase 3: the POC feature-flag check
    # (``ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED``) lives in the
    # centralized :meth:`InstanceManager._enqueue_message_with_flag`
    # helper. When ON, the helper routes through
    # :meth:`enqueue_message_job` so the JobItem mirror is created
    # alongside the Task row (WorkResolver facade reads both sides
    # of the union). When OFF, the existing D13 flow writes
    # Task+MessageQueue only — the frozen baseline. Both paths return
    # an ``AsyncMessageResult`` with the same shape, so callers see
    # no API-level difference.
    try:
        result = await manager._enqueue_message_with_flag(
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
        # Phase 3: propagate the dispatch work unit id so callers can track
        # the job through the WorkResolver facade. ``AsyncMessageResult.job_id``
        # is set to the JobItem mirror (when the flag is ON) or to the shared
        # Task work_id (flag OFF). Both are opaque UUID4 strings.
        job_id=result.job_id,
    ).model_dump()
    
    response_data["auto_resumed"] = False
    response_data["resume_info"] = None
    
    return response_data


# 2. GET /instances/{instance_id}/messages/{message_id} - Get message status
@router.get("/{instance_id}/messages/{message_id}")
async def get_message_status(instance_id: str, message_id: str, request: Request):
    """Get the status of a queued message.

    D13 (Phase 2): rewritten to query the ``task`` table instead of
    ``job_queue_items``. After D13, messages no longer create
    ``JobItem`` rows — they create ``Task`` rows via the unified
    WorkerPool path. The HTTP ``send_message`` route returns a
    ``job_id`` backed by a ``Task.id`` (see
    :meth:`InstanceMessagingService.enqueue_message` for the adapter
    contract).

    Response shape is preserved: ``message_id``, ``instance_id``,
    ``status``, ``result_summary``, ``error``. The ``status`` field
    maps the ``Task.status`` enum (``pending`` / ``running`` /
    ``completed`` / ``failed`` / ``cancelled`` / ``paused``) — the
    frontend treats these the same as the previous
    ``JobItem.status`` values.

    Fallback: if no Task row exists for the message_id (e.g., internal
    WorkerPool messages that use a different code path), return the
    ``get_queue_stats`` summary as before.
    """
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

    # D13: look up the Task row by message_id. The task table is
    # indexed on message_id (see daemon/repositories/task/models.py)
    # so this is a single indexed SELECT.
    if manager._task_repo is not None:
        try:
            task_row = await asyncio.to_thread(
                manager._task_repo.get_by_message, message_id
            )
        except Exception as e:
            logger.warning(
                f"get_message_status: task lookup failed for "
                f"message {message_id[:8]}...: {e}"
            )
            task_row = None

        if task_row is not None:
            # Map Task.result (JSON text) to result_summary, Task.error
            # to error. The frontend's status display logic is
            # job_type-agnostic so these field names are preserved.
            result_summary = None
            if task_row.result:
                try:
                    import json as _json
                    parsed = _json.loads(task_row.result)
                    # result_summary expects a string; serialize the
                    # parsed payload so the frontend gets a readable
                    # value regardless of the original shape.
                    result_summary = (
                        parsed if isinstance(parsed, str) else _json.dumps(parsed)
                    )
                except Exception:
                    result_summary = task_row.result
            # Critical Fix 3 (D13): map the Task.status enum back to
            # the pre-D13 JobItem status contract. ``Task.status``
            # values are ``pending``, ``running``, ``paused``,
            # ``completed``, ``failed``, ``cancelled``; the API
            # contract promises ``pending``, ``processing``,
            # ``paused``, ``completed``, ``failed``, ``cancelled``.
            # The frontend compares ``status === 'processing'``
            # directly; returning ``"running"`` would silently break
            # the UI. The mapping is one-way — the DB stores the
            # canonical Task status, the response is the legacy API
            # contract.
            # The DB stores the canonical Task status (``running``); the
            # response uses the legacy API contract (``processing``). The
            # translation goes through the single shared vocabulary in
            # ``work_status`` so no router carries its own status map.
            mapped_status = canonicalize_status(task_row.status)
            return {
                "message_id": message_id,
                "instance_id": instance_id,
                "status": mapped_status,
                "result_summary": result_summary,
                "error": task_row.error,
            }

    # Fallback: no Task row found (e.g., internal WorkerPool messages
    # that didn't go through enqueue_message). Return queue stats so
    # the frontend can still show pending/processing counts.
    stats = await manager.get_queue_stats(instance_id)
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

        # 2. Create a queue and register it BEFORE any helper that
        # broadcasts through the live hub. Otherwise the broadcast finds
        # zero registered connections and the event is silently dropped.
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        await live_hub.add_connection(instance_id, queue)

        # 2b. Initial context_usage snapshot. Lets the FE indicator populate
        # immediately on connect, without waiting for the next message
        # round-trip. The helper is a no-op if the instance has no messages
        # yet and is cheap to call. The event lands on the queue above and
        # is yielded by the consumer loop below.
        #
        # Run it as a background task so any DB I/O in get_messages happens
        # off the SSE send-path. create_task + add_done_callback keeps the
        # task tracked so we don't leak on early disconnect.
        async def _initial_snapshot() -> None:
            try:
                await manager._messaging_service.emit_context_usage_for_instance(instance_id)
            except Exception as e:
                logger.debug(f"Failed to emit initial context usage: {e}")

        snapshot_task = asyncio.create_task(_initial_snapshot())

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
            # Cancel the initial snapshot if it's still running so we don't
            # do a wasted checkpointer round-trip after the client has gone.
            if not snapshot_task.done():
                snapshot_task.cancel()
            await live_hub.remove_connection(instance_id, queue)
    
    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
