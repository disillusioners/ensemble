"""Maintenance Console HTTP surface — Section 1 (Checkpoint Cleanup).

AM-1 + AM-13 + AM-17 — five FROZEN endpoints under the
``/api/maintenance/checkpoint-cleanup`` prefix, registered in
``daemon/api.py`` BEFORE the SPA catch-all (the established router
seam at ``daemon/api.py:2687-2726``).

Endpoints::

    GET  /api/maintenance/checkpoint-cleanup/availability
    GET  /api/maintenance/checkpoint-cleanup/status
    POST /api/maintenance/checkpoint-cleanup/dry-run
    POST /api/maintenance/checkpoint-cleanup/execute
    GET  /api/maintenance/checkpoint-cleanup/runs/{run_id}

Gate ordering (every endpoint's dependency chain): INV-10/AM-1
``require_trusted_origin`` FIRST → [AM-13] kill-switch check →
``get_maintenance_api_service`` (not_initialized) → service gates.
A request whose Origin fails the guard gets 403
``origin_not_trusted`` without inspecting any other gate.
``/availability`` is EXEMPT from the Origin guard AND from the
kill-switch (the FE gear-menu probe must see disabled state
cleanly; it is non-destructive).

Error code literals: 404 code = ``not_found`` everywhere (C-1 v3
fix pass); 409 body nested under ``details`` everywhere (C-2 v3
fix pass). No ``idempotency_key`` field anywhere (AM-17 DROPPED).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.constants import MAINTENANCE_ENDPOINTS_ENABLED
from daemon.routers.maintenance_origin_guard import (
    require_trusted_origin as _require_origin,
)
from daemon.routers.schemas import (
    CheckpointCleanupAvailabilityResponse,
    CheckpointCleanupDryRunResponse,
    CheckpointCleanupErrorResponse,
    CheckpointCleanupExecuteRequest,
    CheckpointCleanupExecuteResponse,
    CheckpointCleanupRunResponse,
    CheckpointCleanupStatusResponse,
)
from daemon.services.maintenance_api_service import (
    MaintenanceApiService,
    MaintenanceError,
    RequesterInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance/checkpoint-cleanup", tags=["maintenance"])


# ── Dependency: service resolution ─────────────────────────────────────────────


def get_maintenance_api_service(request: Request) -> MaintenanceApiService:
    """Resolve the wired :class:`MaintenanceApiService` from ``app.state``.

    Raises 503 (not 500) when the lifespan has not yet injected the
    service — this is a contract-level error, not a server fault;
    clients can interpret 503 as "try again after boot" (matches
    ``plane.py``'s precedent and the migration router's 503 on
    unwired worker).
    """
    svc = getattr(request.app.state, "maintenance_api_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "not_initialized",
                "message": "Maintenance API service not initialized",
                "state": "subsystem_disabled",
            },
        )
    return svc


# ── Dependency: kill-switch ─────────────────────────────────────────────────────


def _check_kill_switch() -> None:
    """[AM-13] Boot-read kill-switch — endpoints 2–5 only.

    ``/availability`` is NEVER 503'd by the kill-switch (it returns
    200 ``state:"kill_switched"`` instead — rendered inside the
    availability endpoint below). The switch is BOOT-READ (the
    constant is evaluated once at import; the operator restarts the
    daemon after editing the env). One INFO line is logged at boot
    when OFF (emitted in the api.py lifespan — see T8's boot block;
    the PlaneSyncWatchdog no-key precedent).
    """
    if not MAINTENANCE_ENDPOINTS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "maintenance_disabled",
                "message": "Maintenance endpoints are disabled (MAINTENANCE_ENDPOINTS_ENABLED=0)",
            },
        )


# ── Dependency chain composition ────────────────────────────────────────────────
#
# Gate ordering (INV-10 / AM-1 / AM-13), realized via FastAPI's
# dependency resolution order on each guarded route:
#
#   1. ``require_trusted_origin`` — decorator-level
#      ``dependencies=[Depends(_require_origin)]`` (decorator
#      dependencies run BEFORE the endpoint's parameter dependencies);
#   2. ``_check_kill_switch`` — first parameter dependency;
#   3. ``get_maintenance_api_service`` — second parameter dependency
#      (503 not_initialized);
#   4. service gates.
#
# A request whose Origin fails the guard therefore 403s without the
# kill-switch or service state ever being consulted. The full order
# (Origin → kill-switch → service gates) is PINNED over HTTP by case
# 28 — ``TestRouterGates.test_gate_order_first_failure_wins`` in
# tests/unit/services/test_maintenance_checkpoint_cleanup_service.py
# (plus the integration twin ``test_origin_guard_precedes_kill_switch``).


# ── /availability (EXEMPT from Origin guard AND kill-switch 503) ───────────────


@router.get(
    "/availability",
    response_model=CheckpointCleanupAvailabilityResponse,
    status_code=200,
    responses={
        503: {
            "model": CheckpointCleanupErrorResponse,
            "description": "MaintenanceService not initialized",
        },
    },
)
async def get_availability(
    svc: MaintenanceApiService = Depends(get_maintenance_api_service),
) -> CheckpointCleanupAvailabilityResponse:
    """Probe whether the Maintenance gear-menu entry should render.

    Always returns 200; the body indicates eligibility. The kill-switch
    gate (``MAINTENANCE_ENDPOINTS_ENABLED=0``) renders
    ``state:"kill_switched"`` at 200 here (NOT 503) — the FE gear
    probe needs to see disabled state cleanly. Backend gate: PG-only
    blob prune → SQLite renders ``state:"backend_unsupported"``.
    """
    result = await _call_service(svc, "availability")
    if not MAINTENANCE_ENDPOINTS_ENABLED:
        # AM-13 — the switch gates the API surface only; the
        # availability probe renders the kill-switched state cleanly
        # (200, ``eligible:false``, diagnostic reason string).
        result = {
            "eligible": False,
            "backend": result.get("backend", "postgres"),
            "state": "kill_switched",
            "reason": "MAINTENANCE_ENDPOINTS_ENABLED=0",
        }
    return result


# ── /status (Origin guarded, kill-switched) ─────────────────────────────────────


@router.get(
    "/status",
    response_model=CheckpointCleanupStatusResponse,
    status_code=200,
    dependencies=[Depends(_require_origin)],
    responses={
        200: {"description": "Current config + last-run summary + in-flight state"},
        403: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Origin guard refused (origin_not_trusted)",
        },
        503: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Maintenance endpoints disabled (maintenance_disabled) or service not yet initialized (not_initialized)",
        },
    },
)
async def get_status(
    _kill_switch: None = Depends(_check_kill_switch),
    svc: MaintenanceApiService = Depends(get_maintenance_api_service),
) -> CheckpointCleanupStatusResponse:
    """Current config (LIVE env) + last-run summary + in-flight state.

    Gate order — Origin guard (applied via ``Depends(_require_origin)``
    at the router-decorator level; runs BEFORE ``_check_kill_switch``
    which runs BEFORE ``get_maintenance_api_service``) → kill-switch
    → service. ``last_run`` is the latest ``succeeded|failed`` row of
    ``kind ∈ {auto, manual_execute}`` (manual_dry_run NEVER surfaces —
    AM-9); ``in_flight`` is any ``running`` row.
    """
    return await _call_service(svc, "status")


# ── /dry-run ────────────────────────────────────────────────────────────────────


@router.post(
    "/dry-run",
    response_model=CheckpointCleanupDryRunResponse,
    status_code=200,
    dependencies=[Depends(_require_origin)],
    responses={
        200: {"description": "Dry-run result (would-delete counts/bytes/skipped)"},
        403: {"model": CheckpointCleanupErrorResponse, "description": "Origin guard refused"},
        409: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Another run is in flight (run_in_flight; details.run_id for 409-adoption)",
        },
        503: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Endpoints disabled / service not initialized / SQLite backend",
        },
    },
)
async def post_dry_run(
    request: Request,
    _kill_switch: None = Depends(_check_kill_switch),
    svc: MaintenanceApiService = Depends(get_maintenance_api_service),
) -> CheckpointCleanupDryRunResponse:
    """Compute would-delete counts/bytes/skipped (no writes).

    The dry-run takes the SAME single-flight gate as execute (AM-4):
    a kind=manual_dry_run run row holds the gate for the duration of
    the scan. ``fresh_until`` is computed from
    ``MAINTENANCE_DRY_RUN_FRESH_SECONDS`` (default 300s).
    """
    requester = _extract_requester(request)
    payload: dict[str, Any] = {}  # dry-run reserves no body; plane.py pattern
    # The service accepts ``dry_run(requester)`` with no payload.
    return await _call_service(
        svc, "dry_run", requester=requester
    )


# ── /execute ────────────────────────────────────────────────────────────────────


@router.post(
    "/execute",
    response_model=CheckpointCleanupExecuteResponse,
    status_code=202,
    dependencies=[Depends(_require_origin)],
    responses={
        202: {"description": "Run started (poll /runs/{run_id})"},
        400: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Validation failure (confirm_required | dry_run_required | dry_run_stale | byte_count_mismatch)",
        },
        403: {"model": CheckpointCleanupErrorResponse, "description": "Origin guard refused"},
        404: {
            "model": CheckpointCleanupErrorResponse,
            "description": "dry_run_run_id not found (not_found)",
        },
        409: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Another run is in flight (run_in_flight; details.run_id for 409-adoption)",
        },
        503: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Endpoints disabled / service not initialized / SQLite backend",
        },
    },
)
async def post_execute(
    request: Request,
    body: CheckpointCleanupExecuteRequest,
    _kill_switch: None = Depends(_check_kill_switch),
    svc: MaintenanceApiService = Depends(get_maintenance_api_service),
) -> CheckpointCleanupExecuteResponse:
    """Start a destructive run; returns ``run_id`` for polling.

    Validation chain (in the Contract v3 order, first failure raises):
    1. backend_unsupported (PG-only)
    2. confirm_required (payload.confirm is not True)
    3. dry_run_required (payload.dry_run_run_id absent)
    4. not_found (dry_run_run_id not in maintenance_runs)
    5. dry_run_stale (age > MAINTENANCE_DRY_RUN_FRESH_SECONDS)
    6. byte_count_mismatch (echoed expected_bytes != stored)
    7. single-flight gate → 409 run_in_flight on conflict

    On success: 202 with ``{run_id, status, started_at, advisory,
    expected_duration_ms_hint}``. Both ``advisory`` and
    ``expected_duration_ms_hint`` are ALWAYS present (AM-12).
    """
    requester = _extract_requester(request)
    return await _call_service(
        svc, "execute", payload=body, requester=requester
    )


# ── /runs/{run_id} ─────────────────────────────────────────────────────────────


@router.get(
    "/runs/{run_id}",
    response_model=CheckpointCleanupRunResponse,
    status_code=200,
    dependencies=[Depends(_require_origin)],
    responses={
        200: {"description": "Run found"},
        403: {"model": CheckpointCleanupErrorResponse, "description": "Origin guard refused"},
        404: {
            "model": CheckpointCleanupErrorResponse,
            "description": "run_id not found (not_found)",
        },
        503: {
            "model": CheckpointCleanupErrorResponse,
            "description": "Endpoints disabled / service not initialized",
        },
    },
)
async def get_run(
    run_id: str,
    _kill_switch: None = Depends(_check_kill_switch),
    svc: MaintenanceApiService = Depends(get_maintenance_api_service),
) -> CheckpointCleanupRunResponse:
    """Poll a run's progress/result (404 when unknown).

    404 code literal is ``not_found`` (C-1 v3 fix pass — unified
    across the surface).
    """
    return await _call_service(svc, "get_run", run_id=run_id)


# ── helpers ────────────────────────────────────────────────────────────────────


async def _call_service(
    svc: MaintenanceApiService, method: str, **kwargs: Any
) -> Any:
    """Invoke a service method and translate ``MaintenanceError`` → HTTPException.

    The structured dict body (``detail={"error", "message", "details"}``)
    is the A-8 RATIFIED shape binding for all 5 endpoints. Per [C-2, v3
    fix pass] the per-code extras nest UNDER ``details`` — including the
    409-adoption payload (``details.run_id`` / ``details.started_at``).
    FastAPI wraps the dict under its own ``detail`` key on the wire
    (``{"detail": {"error": ..., "message": ..., "details": {...}}``),
    matching the plane.py precedent this pattern was ratified from.

    [tidier fix pass] Unexpected non-``MaintenanceError`` raises are
    caught, logged with ``logger.exception`` (full traceback), and
    re-surfaced as a CONTRACT-SHAPED 500 (``{"error": "internal_error",
    "message": ..., "details": {}}`` per A-8) — an unexpected raise
    must never break the wire shape silently (FastAPI's default 500
    body is plain-text ``Internal Server Error``).
    """
    try:
        result = await getattr(svc, method)(**kwargs)
        return result
    except MaintenanceError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "error": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )
    except Exception:
        logger.exception(
            "maintenance endpoint dispatch raised unexpectedly "
            "(service method=%s)",
            method,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Unexpected server error",
                "details": {},
            },
        )


def _extract_requester(request: Request) -> RequesterInfo:
    """Build the per-request forensics payload from the request scope.

    AM-15 — ``{peer_ip, user_agent, origin}`` stamped onto manual
    run rows. Never attribution (no session ids exist).
    """
    peer_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    origin = request.headers.get("origin")
    return RequesterInfo(
        peer_ip=peer_ip, user_agent=user_agent, origin=origin
    )


# Boot-log line for the kill-switch OFF state (T7 / [AM-13]) is
# emitted in the api.py lifespan boot block (one INFO line when
# ``MAINTENANCE_ENDPOINTS_ENABLED`` is False at boot) — import-time
# logging is unreliable (logger config may not be attached yet).


__all__ = ["router", "get_maintenance_api_service"]
