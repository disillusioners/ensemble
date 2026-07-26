"""Instance message API endpoints."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, Response

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


# Statuses that route through the RAM injection slot (Phase 2 / Task 3).
# RUNNING — the agent is in an active LLM turn; set injection so the
# agent_node picks it up on its next pull-and-clear step.
# WAITING_CHILDREN — parent is parked waiting for child completion
# reports; the slot survives until the next agent turn resumes.
_INJECTION_ELIGIBLE_STATUSES = frozenset({
    InstanceStatus.RUNNING.value,
    InstanceStatus.WAITING_CHILDREN.value,
})


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _get_live_hub(request: Request) -> LiveEventHub | None:
    """Get the LiveEventHub from app state, or None if not initialized.

    Phase 2 / W5: injection events reuse ``LiveEventHub.stream_message``
    with custom ``event_type`` — no new method on the hub. ``None`` is
    returned for safety when the app is constructed without a hub (tests,
    half-initialized bootstrap); callers skip the SSE emit in that case
    so the injection path still succeeds.
    """
    return getattr(request.app.state, "live_hub", None)


def _build_injection_payload(
    instance_id: str,
    event_type: str,
    content: str | None,
    timestamp: str | None,
    pending_count: int | None = None,
) -> dict[str, Any]:
    """Build the SSE payload for an injection event.

    The shape mirrors the Phase 3 contract documented in
    ``.agents/shared/planning/user-msg-injection/phase3-plan.md``::

        {
            "instance_id": str,
            "event_type": str,
            "content": str | None,
            "timestamp": str | None,
            "pending_count": int | None,  # only set on injection_pending
        }

    ``stream_message`` wraps this dict under ``event["message"]`` when
    it serializes the SSE frame, so Phase 3 frontend reads the same
    shape via ``event.message.{instance_id,event_type,content,timestamp,pending_count}``.

    ``pending_count`` is included on ``injection_pending`` events so the
    frontend can show a "N messages queued" indicator without an extra
    round-trip. It is omitted (or ``None``) on lifecycle events that
    don't carry queue depth.
    """
    payload: dict[str, Any] = {
        "instance_id": instance_id,
        "event_type": event_type,
        "content": content,
        "timestamp": timestamp,
    }
    if pending_count is not None:
        payload["pending_count"] = pending_count
    return payload


async def _emit_injection_sse(
    live_hub: LiveEventHub | None,
    instance_id: str,
    event_type: str,
    content: str | None,
    timestamp: str | None,
    pending_count: int | None = None,
) -> None:
    """Fire-and-forget SSE emit for an injection lifecycle event.

    W5 contract: reuses ``stream_message`` with a custom ``event_type``.
    No new method is added to ``LiveEventHub``. If no SSE connection is
    registered for this instance, ``stream_message`` silently drops the
    event — callers do not need to gate on connection counts.

    Failure mode: SSE errors are logged at WARNING and swallowed because
    the API contract has already been honored (injection is stored, or
    the slot is cleared). The LLM turn must not be blocked by SSE.
    """
    if live_hub is None:
        return
    payload = _build_injection_payload(
        instance_id, event_type, content, timestamp, pending_count
    )
    try:
        await live_hub.stream_message(
            instance_id,
            message=payload,
            event_type=event_type,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            f"[Injection] SSE emit failed for {event_type} on "
            f"{instance_id[:8]}...: {type(e).__name__}: {e}"
        )


# 1. POST /instances/{instance_id}/messages - Send message
@router.post("/{instance_id}/messages")
async def send_message(
    instance_id: str,
    message: MessageCreate,
    request: Request,
    response: Response,
) -> dict:
    """Send a message to an instance (async via queue).

    Routing table (Phase 2 / Task 3, C4):
        * RUNNING / WAITING_CHILDREN → set RAM injection slot, emit
          ``injection_pending`` SSE, return **202 Accepted**.
        * PAUSED → existing auto-resume behavior (**NO CHANGE — C4**):
          cascade-resume + resume_processing_job, return 200.
        * IDLE / terminal → existing enqueue_message path (**NO CHANGE**):
          return 200.

    Empty / whitespace-only content is rejected with 400 (S4) before
    any routing decision is made.
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

    # S4: Empty content validation. Applies to ALL paths (injection,
    # PAUSED auto-resume, IDLE/terminal enqueue) so a frontend typo
    # never produces a wasted turn. Positioned BEFORE the PAUSED branch
    # so the behavior is uniform; the PAUSED branch's auto-resume logic
    # is otherwise unchanged.
    if not message.content or not message.content.strip():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Message content cannot be empty",
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

    # Capture status once — used in the routing decision below.
    current_status = instance_info.get("status")

    # --- PAUSED INSTANCE: Skip enqueue, go straight to resume ---
    # C4: This branch is intentionally UNCHANGED. The existing auto-resume
    # flow (``resume_instance_cascade`` + ``resume_processing_job``) is a
    # load-bearing code path that supports vision image propagation and
    # must not return 409. PAUSED continues to return 200 with the same
    # payload shape so the frontend's existing pause→resume UX is
    # unaffected by the injection work in Phase 2.
    if current_status == InstanceStatus.PAUSED.value:
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

    # --- INJECTION PATH (Phase 3 / Tasks 3, 5): RUNNING / WAITING_CHILDREN ---
    # The agent is in an active turn (RUNNING) or parked waiting for child
    # completion reports (WAITING_CHILDREN). The injection queue is
    # RAM-only (Phase 1 W1) — the agent_node pulls + clears the queue on
    # its next invocation and threads each resulting HumanMessage into
    # the LLM call.
    #
    # Phase 3 append-list semantics (Task 5): ``set_injection`` appends
    # to the queue. The single-message ``injection_cleared`` event is
    # GONE — no replacement ever happens. The new lifecycle is
    # ``injection_pending`` (one per message) → ``injection_consumed``
    # (one, for all messages) when the agent picks up the queue.
    if current_status in _INJECTION_ELIGIBLE_STATUSES:
        live_hub = _get_live_hub(request)

        # W5: stream_message with custom event_type — no new method
        # added to LiveEventHub.
        entry = manager.set_injection(instance_id, message.content)
        pending_count = manager.get_injection_count(instance_id)

        await _emit_injection_sse(
            live_hub,
            instance_id,
            event_type="injection_pending",
            content=entry.get("content"),
            timestamp=entry.get("timestamp"),
            pending_count=pending_count,
        )

        # 202 Accepted (NEW) signals to the frontend that the request is
        # acknowledged but the user turn will be absorbed asynchronously
        # by the agent_node on its next pull, NOT through the job queue.
        # 200 is reserved for PAUSED auto-resume and IDLE/terminal enqueue.
        # ``pending_count`` is included so the FE can show a "N messages
        # queued" indicator without a separate GET round-trip.
        response.status_code = 202
        return {
            "status": "injected",
            "instance_id": instance_id,
            "content": entry.get("content"),
            "timestamp": entry.get("timestamp"),
            "pending_count": pending_count,
        }

    # --- NORMAL PATH: IDLE / terminal → existing enqueue_message ---
    # Phase 2 note: IDLE / WAITING / QUEUED / COMPLETED / ERROR / FAILED
    # / TERMINATED all fall through to this branch as before. The state
    # routing above only diverts RUNNING / WAITING_CHILDREN to the
    # injection slot. Anything that does not match an injection-eligible
    # status continues to flow through ``enqueue_message_job`` so the
    # legacy message-queue semantics are preserved.
    # Phase 5 (cutover): the public message-Job path is the only path.
    # Every HTTP POST /messages NORMAL branch creates a JobItem mirror
    # alongside the Task row so the WorkResolver facade can read both
    # sides of the union. The legacy flag-checked helper and the
    # Task-only fallback were removed.
    try:
        result = await manager.enqueue_message_job(
            instance_id=instance_id,
            message=message.content,
            source="api",
            images=message.images,
            queue_id=message.queue_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to enqueue message: {str(e)}",
            ).model_dump(),
        )

    queued = getattr(result, "queued", False)

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
        queued=queued,
    ).model_dump()
    
    response_data["auto_resumed"] = False
    response_data["resume_info"] = None

    return response_data


# 1b. GET /instances/{instance_id}/injection - Pending injection status
# Phase 3 / Task 6: Fallback query endpoint for the frontend to reconcile
# pending injection state when SSE events were missed (e.g. mid-stream
# reconnect, dropped events during a long-lived connection). Reads the
# same RAM queue that ``send_message`` writes to and that the agent_node
# pulls+clears from. Returns ``pending=False`` when the queue is empty —
# the absence of an injection is a valid steady state, not an error.
#
# Phase 3: append-list semantics — the queue can hold multiple messages.
# The response includes ``pending_count`` (queue depth) and the
# OLDEST pending entry's content + timestamp under
# ``content`` / ``timestamp`` for backward compatibility with the
# Phase 2 single-slot response shape. Clients that need the full queue
# contents can call this endpoint repeatedly and correlate via the
# SSE ``injection_pending`` stream (which surfaces the queue depth on
# every message).
@router.get("/{instance_id}/injection")
async def get_pending_injection(instance_id: str, request: Request) -> dict:
    """Return the pending injection queue for ``instance_id``, if any.

    Response shape::

        {
            "instance_id": str,
            "pending": bool,
            "pending_count": int,
            "content": str | None,
            "timestamp": str | None,
        }

    ``pending_count`` is the depth of the queue (0 when empty). ``content``
    and ``timestamp`` reflect the OLDEST pending entry — the entry that
    will be injected first — for backward compatibility with the
    Phase 2 single-slot shape. ``pending=False`` is returned when the
    queue is empty, the common case for IDLE/terminal instances and
    for RUNNING instances whose agent_node has already consumed the
    previous queue.
    """
    manager = _get_manager(request)

    # Verify the instance exists so a typo'd ID surfaces as 404 rather
    # than a confusing ``pending=False`` for a non-existent instance.
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}",
            ).model_dump(),
        )

    queue = manager.get_injection(instance_id)
    pending_count = manager.get_injection_count(instance_id)
    head = queue[0] if queue else None
    return {
        "instance_id": instance_id,
        "pending": head is not None,
        "pending_count": pending_count,
        "content": head.get("content") if head is not None else None,
        "timestamp": head.get("timestamp") if head is not None else None,
    }


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

        # 2c. Initial question-pack replay. Mirrors the
        # ``GET /{instance_id}/question`` endpoint in
        # ``daemon/routers/instances.py``: a client that connects (or
        # reconnects) after the ``question_pack`` SSE event was already
        # emitted should still see the current pending pack on its connect
        # path. Best-effort — any failure is logged at DEBUG and swallowed
        # so a slow replay never blocks the SSE loop. Run as a
        # ``create_task`` for symmetry with ``snapshot_task`` above
        # (keeps the SSE send-path off the critical path even though
        # ``get_question_pack`` is in-memory and cheap).
        async def _initial_question_pack_replay() -> None:
            try:
                # Lazy import keeps the service out of every other path
                # that doesn't need it (matches the convention used by
                # the answer endpoint in instances.py).
                from daemon.services.question_manager import pack_to_dict
                pending_pack = manager._question_manager.get_question_pack(
                    instance_id
                )
                if pending_pack is not None and pending_pack.status == "pending":
                    await live_hub.stream_question_pack(
                        instance_id,
                        pack_to_dict(pending_pack),
                    )
            except Exception as e:
                logger.debug(
                    f"Failed to replay pending question pack on connect: {e}"
                )

        question_replay_task = asyncio.create_task(_initial_question_pack_replay())

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
            # Cancel the initial snapshot / question-pack replay if still
            # running so we don't do a wasted checkpointer round-trip /
            # question-pack replay after the client has gone.
            if not snapshot_task.done():
                snapshot_task.cancel()
            if not question_replay_task.done():
                question_replay_task.cancel()
            await live_hub.remove_connection(instance_id, queue)
    
    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
