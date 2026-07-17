"""REST API router for the Skill Bank — user-facing CRUD.

Mounted under /api/skill-bank. Isolated from the skill evolution
system: NOT gated by config.skill_evolution.

No service layer — the router accesses manager._skill_bank_repo
directly, matching the daemon/routers/mcp_servers.py pattern.
Input validation is handled by Pydantic Field(min_length=1) on
the request schemas.

DI pattern: _get_manager(request) → manager._skill_bank_repo,
sync calls bridged with asyncio.to_thread.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/skill-bank", tags=["skill-bank"])


class SkillBankItemCreate(BaseModel):
    """Create request — validation via Pydantic Field constraints."""
    name: str = Field(min_length=1, description="Skill name (required, non-empty)")
    content: str = Field(min_length=1, description="Skill body content (required, non-empty)")
    project_id: str | None = None
    description: str = ""
    category: str = "workflow"
    # Phase 2 (skill evolution): optional template metadata.
    # ``template_version`` defaults to ``'1.0.0'``; callers creating a
    # new revision should pass the bumped semver. ``agent_id`` scopes
    # the template to one agent (NULL = generic/shared). ``auto_load``
    # is the source-of-truth flag from the skill-set.yaml (legacy .md) definition.
    template_version: str = "1.0.0"
    agent_id: str | None = None
    auto_load: bool = False


class SkillBankItemUpdate(BaseModel):
    """Update request — all fields optional. Only non-None fields are applied."""
    name: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    description: str | None = None
    category: str | None = None
    project_id: str | None = None
    # Phase 2 (skill evolution): optional template metadata updates.
    # All optional — clients can selectively bump ``template_version``
    # when refreshing a stale bank copy.
    template_version: str | None = None
    agent_id: str | None = None
    auto_load: bool | None = None


class SkillBankItemResponse(BaseModel):
    """Response shape for a single skill bank item."""
    id: str
    project_id: str | None = None
    name: str
    description: str = ""
    content: str
    category: str = "workflow"
    # Phase 2 (skill evolution): template metadata returned to clients.
    template_version: str = "1.0.0"
    agent_id: str | None = None
    auto_load: bool = False
    created_at: str
    updated_at: str


class SkillBankItemListResponse(BaseModel):
    """Response shape for the list endpoint."""
    items: list[SkillBankItemResponse]
    total: int


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state."""
    return request.app.state.manager


def _item_to_response(item: Any) -> SkillBankItemResponse | None:
    """Convert a SkillBankItem model instance to a Pydantic response."""
    if item is None:
        return None
    d = item.to_dict() if hasattr(item, "to_dict") else item
    return SkillBankItemResponse(**d)


@router.get("", response_model=SkillBankItemListResponse)
async def list_items(
    request: Request,
    project_id: str | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List skill bank items with optional filters."""
    manager = _get_manager(request)
    repo = manager._skill_bank_repo
    items = await asyncio.to_thread(
        repo.list_items, project_id, category, limit, offset
    )
    total = await asyncio.to_thread(repo.count, project_id, category)
    return SkillBankItemListResponse(
        items=[_item_to_response(i) for i in items], total=total
    )


@router.post("", response_model=SkillBankItemResponse, status_code=201)
async def create_item(item: SkillBankItemCreate, request: Request):
    """Create a new skill bank item.

    Validation: name and content must be non-empty (Pydantic
    Field(min_length=1) → 422 on empty string).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    repo = manager._skill_bank_repo
    created = await asyncio.to_thread(
        repo.create,
        name=item.name,
        content=item.content,
        project_id=item.project_id,
        description=item.description,
        category=item.category,
        template_version=item.template_version,
        agent_id=item.agent_id,
        auto_load=item.auto_load,
    )
    return _item_to_response(created)


@router.get("/{item_id}", response_model=SkillBankItemResponse)
async def get_item(item_id: str, request: Request):
    """Get a single skill bank item by ID."""
    manager = _get_manager(request)
    item = await asyncio.to_thread(manager._skill_bank_repo.get, item_id)
    resp = _item_to_response(item)
    if resp is None:
        raise HTTPException(status_code=404, detail="Skill bank item not found")
    return resp


@router.put("/{item_id}", response_model=SkillBankItemResponse)
async def update_item(item_id: str, data: SkillBankItemUpdate, request: Request):
    """Update a skill bank item. Only non-None fields are applied."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = await asyncio.to_thread(
        manager._skill_bank_repo.update, item_id, **fields
    )
    resp = _item_to_response(updated)
    if resp is None:
        raise HTTPException(status_code=404, detail="Skill bank item not found")
    return resp


@router.delete("/{item_id}")
async def delete_item(item_id: str, request: Request):
    """Delete a skill bank item (hard delete)."""
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    deleted = await asyncio.to_thread(manager._skill_bank_repo.delete, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill bank item not found")
    return {"deleted": True}
