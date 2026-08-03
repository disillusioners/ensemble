"""REST API router for Project Blueprints — user-facing CRUD.

Mounted under /api/projects/{project_id}/blueprints. Per the architecture
review (B2 fix): ALL endpoints consume ``manager._blueprint_repo`` for
CRUD operations. The ``manager._blueprint_matcher`` is MATCH-ONLY and is
NOT consumed by this router — do not add CRUD methods to it, do not call
it from here.

No service layer — the router accesses ``manager._blueprint_repo``
directly, matching the ``daemon/routers/skill_bank.py`` pattern.
Input validation is handled by Pydantic ``Field(min_length=1)`` on the
request schemas; the ``is_write_paused`` manager check is applied to
every write endpoint (POST, PUT, DELETE).

DI pattern: ``_get_manager(request) → manager._blueprint_repo``,
sync calls bridged with ``asyncio.to_thread``. All endpoints are
scoped by ``project_id`` path param — a security requirement. A
blueprint belonging to project A is invisible (404) to project B.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/blueprints", tags=["blueprints"])


# ─── Pydantic schemas ──────────────────────────────────────────────────────────


class BlueprintCreate(BaseModel):
    """Create request — validation via Pydantic Field constraints.

    ``project_id`` comes from the URL path, NOT the body.
    """

    slug: str = Field(min_length=1, description="Unique slug within the project")
    name: str = Field(min_length=1, description="Display name")
    kind: str = Field(default="area", description="'core' | 'area'")
    content: str = Field(min_length=1, description="Blueprint body content")
    tags: list[dict] | None = None
    file_refs: list[str] | None = None


class BlueprintUpdate(BaseModel):
    """Update request — all fields optional. Only non-None fields are applied."""

    name: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    tags: list[dict] | None = None
    file_refs: list[str] | None = None
    status: str | None = None


class BlueprintResponse(BaseModel):
    """Response shape for a single blueprint.

    Mirrors the fields returned by ``Blueprint.to_dict()``.
    """

    id: str
    project_id: str
    slug: str
    name: str
    kind: str
    content: str
    status: str
    tags: list[dict]
    file_refs: list[str]
    version: int
    embedding_model: str | None = None
    source: str
    created_at: str
    updated_at: str
    last_reviewed_at: str | None = None
    is_active: bool


class BlueprintRevisionResponse(BaseModel):
    """Response shape for a single blueprint revision.

    Mirrors the fields returned by ``BlueprintRevision.to_dict()``.
    """

    id: str
    blueprint_id: str
    version: int
    content_snapshot: str
    source: str
    reason: str | None = None
    created_at: str


class BlueprintListResponse(BaseModel):
    """Response shape for the list endpoint."""

    items: list[BlueprintResponse]
    total: int


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _blueprint_to_response(bp: Any) -> BlueprintResponse | None:
    """Convert a Blueprint model instance (or dict) to a Pydantic response."""
    if bp is None:
        return None
    d = bp.to_dict() if hasattr(bp, "to_dict") else bp
    return BlueprintResponse(**d)


def _revision_to_response(rev: Any) -> BlueprintRevisionResponse:
    """Convert a BlueprintRevision model instance (or dict) to a Pydantic response."""
    d = rev.to_dict() if hasattr(rev, "to_dict") else rev
    return BlueprintRevisionResponse(**d)


def _check_project_ownership(bp: Any, project_id: str) -> None:
    """Verify blueprint belongs to the project.

    Returns 404 (not 403) on mismatch so cross-project existence is not
    leaked. Matches the security pattern documented in the task spec.
    """
    if bp is None or bp.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=f"Blueprint not found in project '{project_id}'",
        )


def _check_write_paused(manager: Any) -> None:
    """503 if writes are paused (database migration in progress)."""
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )


# ─── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=BlueprintListResponse)
async def list_blueprints(
    request: Request,
    project_id: str,
    kind: str | None = Query(
        default=None,
        description="Filter by 'core' | 'area'",
    ),
    status: str | None = Query(
        default=None,
        description="Filter by 'published' | 'draft'",
    ),
):
    """List active blueprints for a project.

    ``kind`` is pushed to the repo. ``status`` is filtered client-side
    because the repo's ``list_by_project`` only supports ``kind`` +
    ``active_only``. ``active_only`` is hardcoded to True (soft-deleted
    blueprints are invisible here).
    """
    manager = _get_manager(request)
    repo = manager._blueprint_repo
    items = await asyncio.to_thread(
        repo.list_by_project, project_id, kind=kind, active_only=True
    )
    if status is not None:
        items = [b for b in items if b.status == status]
    return BlueprintListResponse(
        items=[_blueprint_to_response(b) for b in items],
        total=len(items),
    )


@router.get("/{blueprint_id}", response_model=BlueprintResponse)
async def get_blueprint(
    request: Request,
    project_id: str,
    blueprint_id: str,
):
    """Get a single blueprint by ID.

    Returns 404 if the blueprint does not exist OR belongs to a different
    project — never leaks cross-project existence.
    """
    manager = _get_manager(request)
    repo = manager._blueprint_repo
    bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    _check_project_ownership(bp, project_id)
    return _blueprint_to_response(bp)


@router.post("", response_model=BlueprintResponse, status_code=201)
async def create_blueprint(
    request: Request,
    project_id: str,
    body: BlueprintCreate,
):
    """Create a new blueprint.

    Validation: slug, name, content must be non-empty (Pydantic
    ``Field(min_length=1)`` → 422 on empty string). ``project_id``
    comes from the URL path, NOT the body.
    """
    manager = _get_manager(request)
    _check_write_paused(manager)
    repo = manager._blueprint_repo
    created = await asyncio.to_thread(
        repo.create,
        project_id=project_id,
        slug=body.slug,
        name=body.name,
        kind=body.kind,
        content=body.content,
        tags=body.tags if body.tags is not None else [],
        file_refs=body.file_refs if body.file_refs is not None else [],
    )
    return _blueprint_to_response(created)


# ─── Admin: blueprint initialization trigger ──────────────────────────────────


@router.post("/initialize", response_model=dict, status_code=202)
async def initialize_project_blueprints(
    request: Request,
    project_id: str,
):
    """Trigger blueprint initialization for a project.

    Spawns a blueprinter agent on the system_background_queue to bootstrap
    the blueprint corpus (core.md + area blueprints). Returns 409 if a
    core.md blueprint already exists. The initialization runs asynchronously
    — this endpoint returns immediately with 202 Accepted.
    """
    manager = _get_manager(request)

    # Guard: refuse to re-initialize when a core blueprint already exists.
    existing_core = await asyncio.to_thread(
        manager._blueprint_repo.get_core, project_id
    )
    if existing_core is not None:
        raise HTTPException(
            status_code=409,
            detail="Blueprints already initialized",
        )

    job_service = getattr(manager, "_job_queue_service", None)
    if job_service is None:
        raise HTTPException(
            status_code=503,
            detail="JobQueueService not available",
        )

    bg_queue = await asyncio.to_thread(
        job_service._queue_repo.get_by_name,
        project_id,
        "system_background_queue",
    )
    if bg_queue is None:
        raise HTTPException(
            status_code=404,
            detail="system_background_queue not found for project",
        )

    job = await job_service.enqueue(
        agent_id="blueprinter",
        message=(
            f"Initialize project blueprints for project {project_id}.\n\n"
            "Steps:\n"
            "1. Create a `core` blueprint from the project's critical notes, "
            "context.md, and project metadata\n"
            "2. Scan the project directory structure and identify major modules\n"
            "3. For each major module, create an `area` blueprint with overview-level "
            "content and file references\n"
            "4. Generate trigger queries for each blueprint\n"
            "5. Rate-limit yourself: if you hit the rate limit, schedule the "
            "remaining work for later\n\n"
            "Use the blueprint_create tool to create each blueprint. Use filesystem "
            "tools to read project structure. You may spawn worker agents for deep "
            "codebase analysis if needed."
        ),
        source="admin-endpoint",
        project_id=project_id,
        priority=9,  # lowest priority — pure background
        queue_id=bg_queue.queue_id,
        metadata={"trigger": "initialize", "source": "admin-endpoint"},
    )
    return {"job_id": job.job_id, "status": "enqueued"}


# ─── Admin: external-cron blueprint scan trigger (§4.6 Option B) ──────────────


@router.post("/scan", response_model=dict)
async def trigger_blueprint_scan(
    request: Request,
    project_id: str,
):
    """Trigger an immediate blueprinter daily scan for a project.

    Intended for external cron (e.g., systemd timer, GitHub Actions schedule).
    Dispatches on system_background_queue. Requires that the queue exists
    for the project (provisioned automatically on project creation).
    """
    manager = _get_manager(request)
    job_service = getattr(manager, "_job_queue_service", None)
    if job_service is None:
        raise HTTPException(
            status_code=503,
            detail="JobQueueService not available",
        )

    bg_queue = await asyncio.to_thread(
        job_service._queue_repo.get_by_name,
        project_id,
        "system_background_queue",
    )
    if bg_queue is None:
        raise HTTPException(
            status_code=404,
            detail="system_background_queue not found for project",
        )

    job = await job_service.enqueue(
        agent_id="blueprinter",
        message=(
            "Daily blueprint scan (external trigger).\n\n"
            f"Project: {project_id}\n\n"
            "Perform a full drift scan. Review core.md first, then area "
            "blueprints. Respect the rate limit."
        ),
        source="admin-endpoint",
        project_id=project_id,
        priority=9,  # lowest priority — pure background
        queue_id=bg_queue.queue_id,
        metadata={"trigger": "daily-scan", "source": "admin-endpoint"},
    )
    return {"job_id": job.job_id, "status": "enqueued"}


@router.put("/{blueprint_id}", response_model=BlueprintResponse)
async def update_blueprint(
    request: Request,
    project_id: str,
    blueprint_id: str,
    body: BlueprintUpdate,
):
    """Update a blueprint. Only non-None fields are applied.

    Fetches the blueprint first to verify it belongs to ``project_id``
    (security). The repo's ``update`` auto-bumps the version when
    ``content``, ``file_refs``, or ``tags`` change.
    """
    manager = _get_manager(request)
    _check_write_paused(manager)
    repo = manager._blueprint_repo
    # Verify ownership BEFORE mutating.
    bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    _check_project_ownership(bp, project_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = await asyncio.to_thread(repo.update, blueprint_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return _blueprint_to_response(updated)


@router.delete("/{blueprint_id}")
async def delete_blueprint(
    request: Request,
    project_id: str,
    blueprint_id: str,
):
    """Soft-delete a blueprint (sets ``is_active=False``).

    Fetches the blueprint first to verify it belongs to ``project_id``.
    """
    manager = _get_manager(request)
    _check_write_paused(manager)
    repo = manager._blueprint_repo
    # Verify ownership BEFORE deleting.
    bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    _check_project_ownership(bp, project_id)
    deleted = await asyncio.to_thread(repo.soft_delete, blueprint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    return {"deleted": True}


@router.get(
    "/{blueprint_id}/revisions",
    response_model=list[BlueprintRevisionResponse],
)
async def list_blueprint_revisions(
    request: Request,
    project_id: str,
    blueprint_id: str,
):
    """List revision history for a blueprint (newest first).

    Verifies the blueprint belongs to ``project_id`` first.
    """
    manager = _get_manager(request)
    repo = manager._blueprint_repo
    bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
    _check_project_ownership(bp, project_id)
    revisions = await asyncio.to_thread(repo.list_revisions, blueprint_id)
    return [_revision_to_response(r) for r in revisions]
