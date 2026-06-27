"""Job Queue SSE Streaming API endpoints."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from daemon.services.job_queue_service import JobQueueService
from .schemas import JobNotFoundResponse
from .jobs_crud import get_job_queue_service, TERMINAL_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Resolver-gated read adapter ─────────────────────────────────────────────
# Phase 2 (Batch 4b) of ``feature/virtual-job-management-surface``: when the
# ``use_virtual_job_resolver`` flag is ON, ``stream_job_events`` reads through
# :meth:`JobQueueService.get_work` (a :class:`WorkRecord`) instead of the
# JobItem-only :meth:`JobQueueService.get_job`. The two return shapes do not
# align on every field (``queue_id`` lives on JobItem but not on
# ``WorkRecord``; ``error_message`` on JobItem maps to ``error`` on
# ``WorkRecord``), so we project both shapes onto a single ``_ResolvedWork``
# view that the SSE event payloads can read from uniformly. The wire format
# stays byte-identical for the JobItem branch so the frontend does not need
# a corresponding change.


@dataclass(frozen=True)
class _ResolvedWork:
    """Normalized view over JobItem or WorkRecord for SSE event payloads.

    Fields match the JSON keys emitted by every event payload. ``queue_id``
    is set to ``None`` for ``WorkRecord``-backed rows because the unified
    view-model does not carry the queue affinity (Task rows never had one;
    JobItem rows lose it because ``WorkRecord`` is a deliberate
    denormalisation).
    """

    work_id: str
    status: str
    instance_id: str | None
    queue_id: str | None
    result_summary: str | None
    error_message: str | None

    @classmethod
    def from_job(cls, job: Any) -> "_ResolvedWork":
        """Project a JobItem (legacy path) onto the SSE view."""
        return cls(
            work_id=job.job_id,
            status=job.status,
            instance_id=job.instance_id,
            queue_id=job.queue_id,
            result_summary=job.result_summary,
            error_message=job.error_message,
        )

    @classmethod
    def from_work_record(cls, record: Any) -> "_ResolvedWork":
        """Project a WorkRecord (resolver path) onto the SSE view.

        Field renames vs JobItem:

        * ``record.error`` → ``error_message`` (keeps the wire name the
          frontend already expects).
        * ``queue_id`` is ``None`` — the resolver's :class:`WorkRecord`
          does not surface the JobItem ``queue_id`` column because the
          worker-pool ``task`` side has no equivalent concept.
        * ``status`` is already canonicalized (Task ``running`` →
          ``processing``, etc.) by the resolver, so it lines up with
          :data:`TERMINAL_STATUSES` directly.
        """
        return cls(
            work_id=record.work_id,
            status=record.status,
            instance_id=record.instance_id,
            queue_id=None,
            result_summary=record.result_summary,
            error_message=record.error,
        )

    def to_payload(self, *, work_id: str) -> dict[str, Any]:
        """Emit the connected/status_update payload keys."""
        return {
            "job_id": work_id,
            "status": self.status,
            "instance_id": self.instance_id,
            "queue_id": self.queue_id,
        }

    def to_completed_payload(self, *, work_id: str) -> dict[str, Any]:
        """Emit the completed payload keys (includes terminal-state fields)."""
        return {
            "job_id": work_id,
            "status": self.status,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "queue_id": self.queue_id,
        }


def _use_resolver(request: Request) -> bool:
    """Read the ``use_virtual_job_resolver`` flag from app config.

    Defaults to ``False`` when the manager / config are unavailable so legacy
    callers that pass a bare :class:`JobQueueService` still take the
    JobItem branch. Production wiring in ``daemon/api.py`` populates
    ``app.state.manager.config`` during startup.
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return False
    config = getattr(manager, "config", None)
    if config is None:
        return False
    job_system = getattr(config, "job_system", None)
    if job_system is None:
        return False
    return bool(getattr(job_system, "use_virtual_job_resolver", False))


async def _resolve(service: JobQueueService, work_id: str, use_resolver: bool):
    """Return a :class:`_ResolvedWork` view or ``None`` for an unknown id.

    When the resolver flag is ON, looks up through
    :meth:`JobQueueService.get_work` (a ``WorkRecord``); otherwise falls
    back to :meth:`JobQueueService.get_job` (a ``JobItem``). Returns
    ``None`` for either branch when the work id cannot be resolved —
    the route handler maps that to HTTP 404.
    """
    if use_resolver:
        record = await service.get_work(work_id)
        if record is None:
            return None
        return _ResolvedWork.from_work_record(record)
    job = await service.get_job(work_id)
    if job is None:
        return None
    return _ResolvedWork.from_job(job)


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

    Phase 2 (Batch 4b) of ``feature/virtual-job-management-surface``: when
    the ``use_virtual_job_resolver`` flag is ON, this endpoint resolves
    ``job_id`` as a unified ``work_id`` (Task or JobItem) through the
    ``WorkResolverService`` instead of a JobItem-only lookup. The wire
    format is unchanged — the ``_ResolvedWork`` adapter maps WorkRecord
    fields back onto the JobItem-shaped JSON keys the frontend already
    consumes.
    """
    use_resolver = _use_resolver(request)

    # Check work exists first (either JobItem or WorkRecord branch).
    initial = await _resolve(service, job_id, use_resolver)
    if initial is None:
        raise HTTPException(
            status_code=404,
            detail=JobNotFoundResponse(
                error="Job not found",
                job_id=job_id
            ).model_dump()
        )

    async def event_generator() -> AsyncGenerator[dict, None]:
        """Generate SSE events for job updates."""
        last_status = initial.status
        
        try:
            # Send initial connection event with current job state
            yield {
                "event": "connected",
                "data": json.dumps(initial.to_payload(work_id=job_id))
            }
            logger.info(
                f"SSE connected to work {job_id}, initial status: {initial.status} "
                f"(resolver={'on' if use_resolver else 'off'})"
            )

            # If already in terminal state, send completed and exit
            if initial.status in TERMINAL_STATUSES:
                yield {
                    "event": "completed",
                    "data": json.dumps(initial.to_completed_payload(work_id=job_id))
                }
                logger.info(f"Work {job_id} already in terminal state: {initial.status}")
                return

            # Stream events until job reaches terminal state
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    logger.info(f"Client disconnected from work {job_id} SSE stream")
                    break

                # Poll for work updates (same resolver-gated path as the initial lookup)
                current = await _resolve(service, job_id, use_resolver)
                
                if current is None:
                    # Work was deleted
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Job not found"})
                    }
                    break

                # Check for status change
                if current.status != last_status:
                    previous_status = last_status
                    last_status = current.status

                    # Send status update event — spread the connected payload
                    # then attach the previous_status field the frontend expects.
                    payload = current.to_payload(work_id=job_id)
                    payload["previous_status"] = previous_status
                    yield {
                        "event": "status_update",
                        "data": json.dumps(payload)
                    }
                    logger.debug(f"Work {job_id} status changed to: {current.status}")

                    # Check if work reached terminal state
                    if current.status in TERMINAL_STATUSES:
                        yield {
                            "event": "completed",
                            "data": json.dumps(current.to_completed_payload(work_id=job_id))
                        }
                        logger.info(f"Work {job_id} completed with status: {current.status}")
                        break

                # Wait before next poll (2 seconds)
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for work {job_id}")
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