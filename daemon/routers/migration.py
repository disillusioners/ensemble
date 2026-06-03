"""Migration API endpoints (SQLite → PostgreSQL).

Exposes the :class:`daemon.services.migration_worker.MigrationWorker` as a
small REST surface plus an SSE stream for live progress events.

Endpoints::

    GET  /api/migration/availability
    POST /api/migration/start
    GET  /api/migration/status
    POST /api/migration/cancel
    GET  /api/migration/events        (SSE)

The worker is expected to be attached to ``app.state.migration_worker``
during application lifespan setup. The dependency below raises 500 if it
has not been wired in yet (matches the conventions used by other routers
that pull services off of ``app.state``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from daemon.constants import SSE_PING_INTERVAL
from daemon.services.migration_worker import MigrationState
from sse_starlette.sse import EventSourceResponse

if TYPE_CHECKING:
    from daemon.services.migration_worker import MigrationWorker

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/migration", tags=["migration"])


# ── Dependency ──────────────────────────────────────────────────────────────


def get_migration_worker(request: Request) -> "MigrationWorker":
    """Resolve the :class:`MigrationWorker` from ``app.state``.

    The worker is created and attached to ``app.state.migration_worker``
    during the FastAPI lifespan (a separate wiring task). Until that
    wiring lands, every endpoint will fail fast with HTTP 500 rather
    than crash on an ``AttributeError``.

    Args:
        request: The current FastAPI request (used to reach ``app.state``).

    Returns:
        The initialized :class:`MigrationWorker` instance.

    Raises:
        HTTPException: 500 if the worker has not been initialized yet.
    """
    worker = getattr(request.app.state, "migration_worker", None)
    if worker is None:
        raise HTTPException(
            status_code=500,
            detail="Migration worker not initialized",
        )
    return worker


# ── Helpers ─────────────────────────────────────────────────────────────────


def _availability_to_response(availability: dict[str, Any]) -> dict[str, Any]:
    """Translate the worker's availability dict to the public API shape.

    The worker returns ``can_migrate``/``is_sqlite``/``pg_env_available``/
    ``reasons``; the public contract uses ``migration_available``,
    ``current_database``, ``postgres_configured``, ``can_start``.
    """
    is_sqlite = bool(availability.get("is_sqlite"))
    pg_configured = bool(availability.get("pg_env_available"))
    can_start = bool(availability.get("can_migrate"))

    return {
        "migration_available": can_start,
        "current_database": "sqlite" if is_sqlite else "postgres",
        "postgres_configured": pg_configured,
        "can_start": can_start,
    }


def _strip_internal_fields(progress: dict[str, Any]) -> dict[str, Any]:
    """Remove private fields (``_timestamp``) from the worker progress dict."""
    return {k: v for k, v in progress.items() if not k.startswith("_")}


def _make_migration_id() -> str:
    """Generate a sortable migration identifier ``migration_YYYYMMDD_HHMMSS``."""
    return f"migration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/availability")
async def get_availability(
    worker: "MigrationWorker" = Depends(get_migration_worker),
) -> dict[str, Any]:
    """Return whether a SQLite→PostgreSQL migration is currently possible.

    Always returns 200; the response body indicates eligibility so the
    frontend can render the right UI state (no migration button, error
    reasons shown, etc.).
    """
    availability = worker.is_migration_available()
    return _availability_to_response(availability)


@router.post(
    "/start",
    status_code=202,
    responses={
        202: {"description": "Migration started"},
        400: {"description": "Migration prerequisites not met"},
        409: {"description": "Migration is already running"},
    },
)
async def start_migration(
    background_tasks: BackgroundTasks,
    worker: "MigrationWorker" = Depends(get_migration_worker),
) -> dict[str, Any]:
    """Kick off a migration in the background.

    Returns 202 with a ``migration_id`` so the client can correlate SSE
    events. The actual orchestration happens on a background task so the
    HTTP request returns quickly.
    """
    migration_id = _make_migration_id()

    # Validate preconditions synchronously so the client gets an
    # immediate 400/409 instead of an async failure on the SSE stream.
    if worker.get_status()["status"] == MigrationState.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail="Migration is already running",
        )

    availability = worker.is_migration_available()
    if not availability.get("can_migrate"):
        reasons = "; ".join(availability.get("reasons", [])) or "unknown"
        raise HTTPException(
            status_code=400,
            detail=f"Migration not available: {reasons}",
        )

    async def _run() -> None:
        try:
            await worker.start()
        except Exception:
            # Worker already updates its own progress / SSE stream on
            # failure. Log here so we have a server-side breadcrumb.
            logger.exception("Background migration %s failed", migration_id)

    background_tasks.add_task(_run)

    return {
        "migration_id": migration_id,
        "status": MigrationState.RUNNING.value,
        "message": "Migration started successfully",
    }


@router.get("/status")
async def get_status(
    worker: "MigrationWorker" = Depends(get_migration_worker),
) -> dict[str, Any]:
    """Return the latest :class:`MigrationProgress` snapshot."""
    progress = worker.get_status()
    return _strip_internal_fields(progress)


@router.post(
    "/cancel",
    responses={
        200: {"description": "Cancellation requested"},
        409: {"description": "No migration is currently running"},
    },
)
async def cancel_migration(
    worker: "MigrationWorker" = Depends(get_migration_worker),
) -> dict[str, Any]:
    """Request cooperative cancellation of a running migration.

    The worker flips state to ``CANCELLED`` cooperatively; this endpoint
    returns 200 as soon as the cancel signal has been accepted.
    """
    try:
        await worker.cancel()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "status": MigrationState.CANCELLED.value,
        "message": "Migration cancellation requested",
    }


@router.get("/events")
async def migration_events(
    request: Request,
    worker: "MigrationWorker" = Depends(get_migration_worker),
) -> EventSourceResponse:
    """SSE stream of migration events.

    Each event from the worker is forwarded verbatim, with ``data``
    JSON-encoded for the wire. The stream also emits a ``keepalive``
    comment every 15s so proxies don't drop the connection during long
    silent phases (e.g. while a large table is being copied).
    """
    _ = request  # keep reference to silence unused-arg warnings in some linters

    async def event_generator() -> Any:
        queue = worker.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keepalive — sse_starlette will also emit its own
                    # ping at SSE_PING_INTERVAL, but a fast-path
                    # comment here keeps the event loop snappy during
                    # the long table-copy phase.
                    yield {"event": "keepalive", "data": ""}
                    continue

                # Worker events are shaped like
                #   {"event": "<type>", "data": {...}}
                # Re-emit with `data` JSON-encoded for the SSE wire.
                event_type = event.get("event", "message")
                event_data = event.get("data", {})
                yield {
                    "event": event_type,
                    "data": json.dumps(event_data),
                }
        finally:
            worker.unsubscribe(queue)

    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)


__all__ = ["router", "get_migration_worker"]
