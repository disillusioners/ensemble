"""Memoir Store v2 — Persona HTTP Surface (Human Lane Gate).

Phase 5 of Memoir Store v2 (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase5-plan.md``; flags V2-37 /
B1 / Leader ruling 2026-11-09).

This router exposes exactly three endpoints under ``/api/memoir/persona``:
1. ``GET /api/memoir/persona/pending`` (scope prompts:read) — list pending
   versions (status == 'pending_approval' only).
2. ``POST /api/memoir/persona/{version_id}/approve`` (scope prompts:approve)
   — human session approval (transitions to approved, moves pointer row).
3. ``POST /api/memoir/persona/{version_id}/reject`` (scope prompts:approve)
   — human session rejection (transitions to rejected).

Strict scope discipline:
* Human-only approve/reject is enforced inside the repo and mapped to 403
  ``HUMAN_LANE_REQUIRED`` (bearer tokens holding prompts:approve are still
  refused — DD-9).
* Auth-OFF posture: when ``auth_config`` is absent, endpoints return 503
  ``AUTH_NOT_CONFIGURED``.
* Zero create, edit, or delete endpoints exist (strict LIST/APPROVE/REJECT).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from daemon.auth.decorators import require_scope
from daemon.repositories.memoir.repository import (
    MemoirPersonaRepository,
    MemoirTransitionError,
)
from daemon.repositories.prompt_version.repository import (
    actor_from_request,
)
from daemon.services.prompt_version_service import HumanLaneRequiredError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memoir/persona", tags=["memoir"])

_SCOPE_READ = "prompts:read"
_SCOPE_APPROVE = "prompts:approve"

_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
_ERR_HUMAN_LANE_REQUIRED = "HUMAN_LANE_REQUIRED"
_ERR_VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
_ERR_INVALID_TRANSITION = "INVALID_TRANSITION"

_PATH_PARAM_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Return an HTTPException with typed {error, message} body."""
    return HTTPException(
        status_code=status_code,
        detail={"error": error, "message": message},
    )


def _require_auth_on(request: Request) -> None:
    """When auth_config is absent, return 503 AUTH_NOT_CONFIGURED."""
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(503, _ERR_AUTH_NOT_CONFIGURED, "Auth not configured (fail-closed)")


def _require_uuid_like(value: str) -> None:
    """URL-segment allowlist for version_id."""
    if not _PATH_PARAM_RE.match(value):
        raise _err(
            422,
            "INVALID_PATH_PARAM",
            f"version_id must match {_PATH_PARAM_RE.pattern!r}; got {value!r}",
        )


def _get_persona_repo(request: Request) -> MemoirPersonaRepository:
    """Reach MemoirPersonaRepository via manager."""
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise _err(503, "MEMOIR_UNAVAILABLE", "Manager not configured")
    if hasattr(manager, "_memoir_persona_repo") and manager._memoir_persona_repo is not None:
        return manager._memoir_persona_repo
    engine = getattr(manager, "engine", None)
    if engine is None:
        raise _err(503, "MEMOIR_UNAVAILABLE", "Database engine unavailable")
    return MemoirPersonaRepository(engine)


# ── Response Models ─────────────────────────────────────────────────────────


class MemoirPendingVersionResponse(BaseModel):
    id: str
    version: int
    body: str
    source: str
    submitted_at: Optional[str] = None


class MemoirPersonaVersionResponse(BaseModel):
    id: str
    agent_id: str
    version: int
    body: str
    status: str
    source: str
    submitted_at: Optional[str] = None
    approved_at: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/pending", response_model=list[MemoirPendingVersionResponse])
@require_scope(_SCOPE_READ)
async def list_pending_versions(
    request: Request,
) -> list[MemoirPendingVersionResponse]:
    """List pending persona versions (status == 'pending_approval' only)."""
    _require_auth_on(request)
    repo = _get_persona_repo(request)
    rows = await asyncio.to_thread(repo.list_pending, agent_id="ari")
    return [
        MemoirPendingVersionResponse(
            id=r.id,
            version=r.version,
            body=r.body,
            source=r.source,
            submitted_at=r.submitted_at,
        )
        for r in rows
    ]


@router.post("/{version_id}/approve", response_model=MemoirPersonaVersionResponse)
@require_scope(_SCOPE_APPROVE)
async def approve_persona_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> MemoirPersonaVersionResponse:
    """Approve a pending persona version. HUMAN session lane only (D2 / DD-9).

    Bearer + scope still returns 403 HUMAN_LANE_REQUIRED.
    Session lane with prompts:approve scope passes the gate.
    """
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    repo = _get_persona_repo(request)
    try:
        row = await asyncio.to_thread(repo.approve, version_id, actor)
    except HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Approve requires session lane (D2)")
    except MemoirTransitionError as exc:
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except ValueError as exc:
        raise _err(404, _ERR_VERSION_NOT_FOUND, str(exc))

    return MemoirPersonaVersionResponse(
        id=row.id,
        agent_id=row.agent_id,
        version=row.version,
        body=row.body,
        status=row.status,
        source=row.source,
        submitted_at=row.submitted_at,
        approved_at=row.approved_at,
    )


@router.post("/{version_id}/reject", response_model=MemoirPersonaVersionResponse)
@require_scope(_SCOPE_APPROVE)
async def reject_persona_version(
    version_id: str = Path(...),
    request: Request = ...,
) -> MemoirPersonaVersionResponse:
    """Reject a pending persona version. HUMAN session lane only (D2 / DD-9)."""
    _require_auth_on(request)
    _require_uuid_like(version_id)
    actor = actor_from_request(request)
    repo = _get_persona_repo(request)
    try:
        row = await asyncio.to_thread(repo.reject, version_id, actor)
    except HumanLaneRequiredError:
        raise _err(403, _ERR_HUMAN_LANE_REQUIRED, "Reject requires session lane (D2)")
    except MemoirTransitionError as exc:
        raise _err(409, _ERR_INVALID_TRANSITION, str(exc))
    except ValueError as exc:
        raise _err(404, _ERR_VERSION_NOT_FOUND, str(exc))

    return MemoirPersonaVersionResponse(
        id=row.id,
        agent_id=row.agent_id,
        version=row.version,
        body=row.body,
        status=row.status,
        source=row.source,
        submitted_at=row.submitted_at,
        approved_at=row.approved_at,
    )
