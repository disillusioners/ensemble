"""Crash-recovery endpoints (pause-report-recovery Phase 2, task 2.5).

Phase 2 task 2.5: the ``recover_report_delivery`` action runs the
periodic :class:`ReportDeliveryRecoveryService` on-demand and
returns structured per-row results. The endpoint is the operator-
facing seam for forcing a recovery sweep without waiting for the
periodic interval.

Mirrors the existing ``POST /api/jobs/cleanup`` pattern
(``daemon/routers/jobs_management.py`` — synchronous service call,
structured response) so the operator UX stays uniform across the
cleanup / recovery surfaces.

Bound to ``manager.app`` via :func:`register_recovery_routes` — the
lifespan calls this once after the manager is wired.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recovery", tags=["recovery"])


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


@router.post(
    "/recover_report_delivery",
    responses={
        200: {
            "description": (
                "On-demand report-delivery recovery sweep completed. "
                "Per-lane counts in the response body."
            ),
        },
        503: {"description": "Recovery service disabled by config."},
    },
)
async def recover_report_delivery(request: Request) -> dict[str, Any]:
    """Run the ``ReportDeliveryRecoveryService`` once on demand.

    Phase 2 task 2.5. Forces a single sweep pass and returns
    structured per-lane results — operators can verify the sweep
    recovered / skipped / disposed each candidate row. Same shape
    the periodic sweep logs internally.

    The endpoint is synchronous (calls ``recover_now`` which is
    sync — the DB queries do not need an event loop). The
    ``asyncio.to_thread`` wrapper keeps the event loop free.

    Returns:
        Structured per-lane result + total_recovered. See
        :meth:`ReportDeliveryRecoveryService.recover_now` for the
        field shape.

    Raises:
        HTTPException: 503 if the recovery service is disabled
            (``report_delivery_recovery_enabled=False``) or not
            wired (test doubles / partial initialization).
    """
    manager = _get_manager(request)
    if getattr(manager, "is_write_paused", False):
        raise HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )
    service = getattr(manager, "_report_recovery", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "ReportDeliveryRecoveryService is not wired "
                "(disabled by config or partial init)"
            ),
        )
    try:
        result = await asyncio.to_thread(service.recover_now)
    except Exception as exc:
        logger.error(
            f"recover_report_delivery failed: {type(exc).__name__}: {exc}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Recovery sweep failed: {exc}",
        )
    return result.to_dict()
