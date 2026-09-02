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
# Phase 7 (Job as Queue Proxy): every read path resolves through
# :meth:`JobQueueService.get_work` (a :class:`WorkRecord`) so the
# ``status`` is sourced from the joined Instance rather than the
# JobItem mirror. The resolver is the only read path; the prior
# kill-switch has been removed. ``_ResolvedWork`` projects the unified
# WorkRecord view onto the SSE wire-format keys the frontend already
# consumes; the ``queue_id`` field is forced to ``None`` because the
# WorkRecord does not surface the JobItem-only queue affinity.


@dataclass(frozen=True)
class _ResolvedWork:
    """Normalized view over a :class:`WorkRecord` for SSE event payloads.

    Phase 7 (Job as Queue Proxy): the only construction path is
    :meth:`from_work_record`. ``queue_id`` is always ``None`` because
    the unified WorkRecord view-model does not carry the JobItem
    queue affinity (Task rows never had one; JobItem rows lose it
    because WorkRecord is a deliberate denormalisation).

    Fix C — read-model split: the SSE payload now carries the two
    additive split-semantics fields (``job_type`` /
    ``mission_liveness``) so the FE work-view can render mission
    vs mirror rows distinctly. The fields are sourced from the
    resolver's :class:`WorkRecord` so all four read surfaces
    (``work_resolver`` primary + ``jobs_crud`` /
    ``jobs_management`` delegation / ``jobs_streaming`` here)
    agree on the split semantics. See
    ``docs/job-task-system.md §8.2`` for the contract.
    """

    work_id: str
    status: str
    instance_id: str | None
    queue_id: str | None
    result_summary: str | None
    error_message: str | None
    job_type: str | None
    mission_liveness: str | None

    @classmethod
    def from_work_record(cls, record: Any) -> "_ResolvedWork":
        """Project a :class:`WorkRecord` (resolver path) onto the SSE view.

        Field renames vs JobItem:

        * ``record.error`` → ``error_message`` (keeps the wire name the
          frontend already expects).
        * ``queue_id`` is ``None`` — the resolver's :class:`WorkRecord`
          does not surface the JobItem ``queue_id`` column because the
          worker-pool ``task`` side has no equivalent concept.
        * ``status`` is already canonicalized by the resolver — Task
          ``running`` → ``processing``, and Instance statuses (e.g.
          ``error`` → ``failed``, ``terminated`` → ``cancelled``) pass
          through the same canonical map. The output lines up with
          :data:`TERMINAL_STATUSES` directly.
        * ``job_type`` / ``mission_liveness`` — Fix C additive fields.
          Pass through verbatim from the WorkRecord so the FE can
          render the mission/mirror split; ``None`` for non-job rows
          (reports) where the concept does not apply.
        """
        return cls(
            work_id=record.work_id,
            status=record.status,
            instance_id=record.instance_id,
            queue_id=None,
            result_summary=record.result_summary,
            error_message=record.error,
            job_type=getattr(record, "job_type", None),
            mission_liveness=getattr(record, "mission_liveness", None),
        )

    def to_payload(self, *, work_id: str) -> dict[str, Any]:
        """Emit the connected/status_update payload keys."""
        payload: dict[str, Any] = {
            "job_id": work_id,
            "status": self.status,
            "instance_id": self.instance_id,
            "queue_id": self.queue_id,
        }
        # Fix C — surface the split-semantics fields on every SSE
        # payload so the FE work-view can distinguish mission vs
        # mirror rows without a second resolver round-trip. Backward
        # compatible: ``None`` for rows where the concept does not
        # apply (Task/report rows) — older FE clients ignore the
        # extra keys.
        payload["job_type"] = self.job_type
        payload["mission_liveness"] = self.mission_liveness
        return payload

    def to_completed_payload(self, *, work_id: str) -> dict[str, Any]:
        """Emit the completed payload keys (includes terminal-state fields)."""
        payload: dict[str, Any] = {
            "job_id": work_id,
            "status": self.status,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "queue_id": self.queue_id,
        }
        # Fix C — same as ``to_payload``; the completed event also
        # needs the split-semantics fields so the FE can mark a
        # completed mirror with a still-running mission correctly.
        payload["job_type"] = self.job_type
        payload["mission_liveness"] = self.mission_liveness
        return payload


def _use_resolver(request: Request) -> bool:  # noqa: ARG001 — kept for legacy test compatibility
    """Deprecated stub — Phase 7: kill-switch removed.

    Always returns ``True``. The resolver is the only read path; the
    parameter is retained only because legacy tests in
    ``tests/unit/routers/test_jobs_streaming_resolver.py`` still pass
    it to the SSE endpoint handlers.
    """
    return True


async def _resolve(service: JobQueueService, work_id: str, use_resolver: bool):  # noqa: ARG001 — see _use_resolver
    """Return a :class:`_ResolvedWork` view or ``None`` for an unknown id.

    Phase 7 (Job as Queue Proxy): the only path is the resolver.
    The ``use_resolver`` parameter is retained for backward
    compatibility with existing tests but has no effect — both
    branches collapse to the same resolver-backed result.

    Returns ``None`` when the resolver cannot resolve the work id
    (either ``get_work`` returned ``None`` or the underlying
    ``WorkResolverService`` is not wired) — the route handler maps
    that to HTTP 404.
    """
    record = await service.get_work(work_id)
    if record is None:
        return None
    return _ResolvedWork.from_work_record(record)


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

    Phase 1 (Job as Queue Proxy): every read in this endpoint
    resolves through the ``WorkResolverService`` (via
    ``JobQueueService.get_work``). Execution state — including
    ``status`` — is sourced from the joined Instance rather than
    the JobItem mirror. Phase 4 froze the ``status`` column at the
    INSERT default; ``admission_state`` is the sole authority and
    is read only by internal state-machine code. The
    wire format is unchanged: ``_ResolvedWork.from_work_record``
    projects the WorkRecord onto the same JSON keys the frontend
    already consumes.
    """
    # Phase 7: resolver is unconditional. The ``_use_resolver`` call
    # and the ``use_resolver`` argument to ``_resolve`` are no-ops
    # retained for legacy test compatibility.
    use_resolver = _use_resolver(request)

    # Check work exists first. Phase 7: every branch goes through the
    # resolver — see ``_resolve``.
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
                f"SSE connected to work {job_id}, initial status: {initial.status}"
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