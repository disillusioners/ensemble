"""Plane project sync HTTP surface — Phase 3.

Phase 2 closed the auth-seam (X-Api-Key + is_available gate). Phase 3
closes the retry-seam: ``POST /api/plane/sync/{project_id}`` is a manual
re-sync trigger that complements the periodic
:class:`PlaneSyncWatchdogService`. The watchdog drives the steady-state
recovery for ``error`` / ``drift`` rows; the endpoint gives operators a
synchronous escape hatch to:

  * Force a re-sync outside the 5-minute cadence (e.g. immediately
    after a manual Plane-side edit).
  * Reset ``plane_attempt_count`` for a project that hit the quarantine
    ceiling (the watchdog stops at ``max_attempts`` — a fresh POST
    resets the counter via the bump-on-error / reset-on-success
    invariant: a manual force-sync that succeeds clears the counter).

Endpoint contract (Phase 3 Step 1):

  POST /api/plane/sync/{project_id}
  Body: empty (no fields; reserved for future flags)
  Responses:
    200 — sync attempted; response body carries the resulting state
          (linked / drift / error / syncing / not_found / disabled).
    404 — project_id does not exist.
    409 — a sync is already in flight (``plane_sync_state="syncing"``);
          the response body also carries the in-flight status so the
          caller can poll.
    503 — PLANE_API_KEY / PLANE_BASE_URL not configured; the endpoint
          refuses with a clear disabled reason rather than marking
          projects error. Distinct from the sync service's
          ``status="disabled"`` so the client can branch on HTTP code.

Re-entrancy: the endpoint NEVER spawns concurrent syncs for the same
project. The :meth:`PlaneSyncService.claim_sync_slot` CAS owns the
race; the 409 path is the contract-level expression of that guard.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.repositories import SQLModelProjectRepository
from daemon.services.plane_sync_service import PlaneSyncService
from .schemas import (
    PlaneProjectSyncErrorResponse,
    PlaneProjectSyncRequest,
    PlaneProjectSyncResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plane", tags=["plane"])


# Module-level repo handle — set via ``set_project_repository`` from
# the daemon lifespan (mirrors the projects/settings/queues router
# pattern). ``None`` = the lifespan has not wired the repo yet, which
# is a real boot-time error (503 — never 500).
_project_repo: SQLModelProjectRepository | None = None


def get_project_repository() -> SQLModelProjectRepository:
    """Dependency that returns the wired ``SQLModelProjectRepository``.

    Raises 503 (not 500) when the lifespan has not yet injected the
    repo — this is a contract-level error, not a server fault, and
    clients can interpret 503 as "try again after boot".
    """
    if _project_repo is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Project repository not initialized"},
        )
    return _project_repo


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    """Inject the project repository from the daemon lifespan."""
    global _project_repo
    _project_repo = repo


@router.post(
    "/sync/{project_id}",
    response_model=PlaneProjectSyncResponse,
    status_code=200,
    responses={
        200: {"description": "Sync attempted — body carries the resulting state"},
        404: {"model": PlaneProjectSyncErrorResponse, "description": "Project not found"},
        409: {"model": PlaneProjectSyncErrorResponse, "description": "A sync is already in flight for this project"},
        503: {"model": PlaneProjectSyncErrorResponse, "description": "Plane integration not configured (PLANE_API_KEY not set)"},
    },
)
async def sync_project(
    project_id: str,
    body: PlaneProjectSyncRequest | None = None,  # noqa: ARG001 — reserved for future flags
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> PlaneProjectSyncResponse:
    """Manually re-sync an Ensemble project to Plane.

    The endpoint:

    1. Returns 503 with a disabled reason when the integration is not
       configured (``PLANE_API_KEY`` absent / empty). The endpoint
       deliberately does NOT mark the project ``error`` here — the
       absence of the integration is a configuration question, not a
       sync failure.
    2. Returns 404 when the project does not exist (no row to mark).
    3. Returns 409 when ``plane_sync_state`` is already ``"syncing"``
       (the watchdog / a concurrent operator call owns the slot). The
       response body's ``status`` field echoes ``"syncing"`` so the
       caller can poll.
    4. Otherwise calls :meth:`PlaneSyncService.sync_project` and maps
       the structured result to the response shape.

    The endpoint is intentionally synchronous from the caller's
    perspective: ``sync_project`` itself is async; we ``await`` it
    directly. The shared module-level ``_plane_sync_executor`` is
    NOT used here — the HTTP request handler is already on a running
    loop, so we let the coroutine drive natively.
    """
    # Feature gate: 503 with a clear disabled reason when the
    # integration is off. We deliberately do NOT mutate project state
    # here (no ``error`` marking) — the watchdog's no-key boot log
    # line carries the same message.
    if not PlaneSyncService.is_available():
        raise HTTPException(
            status_code=503,
            detail=PlaneProjectSyncErrorResponse(
                error="plane_disabled",
                project_id=project_id,
                message="Plane integration not configured "
                "(PLANE_API_KEY not set). The watchdog will re-drive "
                "this project once the integration is enabled.",
            ).model_dump(),
        )

    # Existence check — return 404 cleanly. Avoids paying for a
    # ``claim_sync_slot`` write on a non-existent project.
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=PlaneProjectSyncErrorResponse(
                error="project_not_found",
                project_id=project_id,
                message=f"Project {project_id} does not exist",
            ).model_dump(),
        )

    # Re-entrancy guard: 409 when the slot is already held. The body's
    # ``status`` echoes ``"syncing"`` so the client knows the row is
    # NOT in a fresh state — they should poll rather than re-POST.
    service = PlaneSyncService(repo)
    if not service.claim_sync_slot(project_id):
        raise HTTPException(
            status_code=409,
            detail=PlaneProjectSyncErrorResponse(
                error="sync_in_flight",
                project_id=project_id,
                message="A sync is already in flight for this project. "
                "Poll the project's plane_sync_state metadata or retry "
                "after the in-flight attempt completes.",
            ).model_dump(),
        )

    # We have the slot — call sync_project with claim_slot=False so it
    # does not double-claim (the slot is already in "syncing").
    try:
        result = await service.sync_project(project_id, claim_slot=False)
    except Exception as exc:  # noqa: BLE001 — sync_project is contractually never-raises
        # Defensive: sync_project must not raise, but a belt-and-braces
        # catch protects the HTTP path from bubbling a 500.
        logger.warning(
            "Plane sync endpoint: sync_project raised for %s (should "
            "not happen — sync_project is never-raise): %s",
            project_id,
            exc,
        )
        result = {
            "status": "error",
            "action": None,
            "message": f"Internal error: {exc}",
        }

    return PlaneProjectSyncResponse(
        project_id=project_id,
        status=result.get("status", "error"),
        action=result.get("action"),
        plane_project_id=result.get("plane_project_id"),
        synced_at=result.get("synced_at"),
        attempt=result.get("attempt"),
        message=result.get("message"),
    )
