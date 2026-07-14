# Phase 2: Backend API Layer

## Objective

Build the `/api/skill-bank` REST router exposing full CRUD. **No service layer** — the router accesses the repository directly via `manager._skill_bank_repo`, exactly matching the `mcp_servers.py` pattern. Validation is done via Pydantic `Field(min_length=1)` on request schemas. All write endpoints guard with `is_write_paused`. Register the router in the daemon.

## Coupling

- **Depends on:** Phase 1 (imports `SkillBankItem` model; accesses `manager._skill_bank_repo` wired in Phase 1)
- **Coupling type:** **tight** — Phase 2 router imports the model class from Phase 1 and accesses the repository instance wired into the manager by Phase 1.
- **Shared files with other phases:** None directly (new files only), but imports Phase 1's `models.py`
- **Shared APIs/interfaces:** The REST API contract (`/api/skill-bank/*`) is the interface Phase 3 depends on (loose coupling).
- **Why:** Router depends on repository; both in the same vertical slice.

## Context

- **Previous phase completed:** `skill_bank` table + `SkillBankItem` model + `SkillBankRepository` + factory + `__init__.py` registration + manager wiring.
- **No service layer** (per Approver decision): The router accesses `manager._skill_bank_repo` directly. Input validation is handled by Pydantic schema constraints (`Field(min_length=1)`). This is simpler and consistent with the `mcp_servers.py` reference pattern.
- **Pattern to follow:** The `mcp_servers.py` router — `request.app.state.manager._skill_bank_repo` access, `asyncio.to_thread` bridging, `is_write_paused` guard on writes, Pydantic response models.

### Pattern References (verified from source)

- **Router shape:** `daemon/routers/mcp_servers.py` — `router = APIRouter(prefix="/skill-bank", tags=["skill-bank"])`, `_get_manager(request)` helper, endpoints use `await asyncio.to_thread(manager._skill_bank_repo.method, ...)`.
- **is_write_paused guard:** `daemon/routers/mcp_servers.py` lines 283, 348, 481, 568, 612 — `if manager.is_write_paused: raise HTTPException(status_code=503, detail="Writes are paused for database migration")` on ALL write endpoints.
- **Response models:** Pydantic `BaseModel` schemas defined in the router file.
- **Request validation:** Pydantic `Field(min_length=1)` on required string fields — FastAPI auto-validates and returns 422 on violation. No manual `ValueError` handling needed.
- **Router registration:** `daemon/routers/__init__.py` import + `__all__`; `daemon/api.py` import + `include_router()`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define Pydantic schemas | Request/response models: `SkillBankItemCreate`, `SkillBankItemUpdate`, `SkillBankItemResponse`, `SkillBankItemListResponse`. Use `Field(min_length=1)` on `name` and `content` in `SkillBankItemCreate` for validation (replaces service-layer validation). | `daemon/routers/skill_bank.py` (inline, same file) |
| 2 | Create API router | `APIRouter(prefix="/skill-bank", tags=["skill-bank"])`. Endpoints: `GET ""`, `POST ""` (201), `GET "/{item_id}"`, `PUT "/{item_id}"`, `DELETE "/{item_id}"`. Use `_get_manager(request)` + `asyncio.to_thread`. **Add `is_write_paused` guard to POST/PUT/DELETE.** 404 for missing items. | `daemon/routers/skill_bank.py` |
| 3 | Register router | Import `skill_bank_router` in `daemon/routers/__init__.py` + add to `__all__`. Import + `include_router(skill_bank_router)` in `daemon/api.py`. | `daemon/routers/__init__.py`, `daemon/api.py` |
| 4 | Write API tests | Integration tests: create → get → list → update → delete. Cover 404 cases. Test with `TestClient` against SQLite. | `tests/` (new test file) |

## Key Files

- `daemon/routers/skill_bank.py` — **NEW** router + schemas
- `daemon/routers/__init__.py` — Add import + `__all__`
- `daemon/api.py` — Add import + `include_router`

> **Note:** No `daemon/services/skill_bank_service.py` — service layer dropped per Approver decision.

## Detailed Specs

### Pydantic Schemas (Task 1) — inline in router file

```python
from pydantic import BaseModel, Field


class SkillBankItemCreate(BaseModel):
    """Create request — validation via Pydantic Field constraints."""
    name: str = Field(min_length=1, description="Skill name (required, non-empty)")
    content: str = Field(min_length=1, description="Skill body content (required, non-empty)")
    project_id: str | None = None
    description: str = ""
    category: str = "workflow"


class SkillBankItemUpdate(BaseModel):
    """Update request — all fields optional. Only non-None fields are applied."""
    name: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    description: str | None = None
    category: str | None = None
    project_id: str | None = None


class SkillBankItemResponse(BaseModel):
    """Response shape for a single skill bank item."""
    id: str
    project_id: str | None = None
    name: str
    description: str = ""
    content: str
    category: str = "workflow"
    created_at: str
    updated_at: str


class SkillBankItemListResponse(BaseModel):
    """Response shape for the list endpoint."""
    items: list[SkillBankItemResponse]
    total: int
```

**Validation note:** `Field(min_length=1)` on `name` and `content` means FastAPI automatically rejects empty strings with HTTP 422. No manual `ValueError` / `raise HTTPException(400)` needed — Pydantic handles it before the endpoint body runs. This replaces the service-layer validation that was in the original plan.

### API Router (Task 2)

```python
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
```

### Router Registration (Task 3)

**`daemon/routers/__init__.py`:**
```python
from .skill_bank import router as skill_bank_router
# ... add to __all__:
    "skill_bank_router",
```

**`daemon/api.py`:**
```python
# In the import block (~line 65):
    skill_bank_router,    # /api/skill-bank (Skill Bank CRUD)

# In the include_router block (~line 1346):
    api_router.include_router(skill_bank_router)   # /api/skill-bank
```

## API Contract Summary (for Phase 3 reference)

| Method | Path | Body | Response | Status |
|--------|------|------|----------|--------|
| GET | `/api/skill-bank` | — | `{items: [...], total: N}` | 200 |
| GET | `/api/skill-bank?project_id=X&category=Y&limit=100&offset=0` | — | `{items: [...], total: N}` | 200 |
| POST | `/api/skill-bank` | `{name, content, project_id?, description?, category?}` | `{id, project_id, name, description, content, category, created_at, updated_at}` | 201 / 422 (empty name/content) / 503 (write paused) |
| GET | `/api/skill-bank/{id}` | — | `{id, ...}` | 200 / 404 |
| PUT | `/api/skill-bank/{id}` | `{name?, content?, description?, category?, project_id?}` | `{id, ...}` | 200 / 400 (no fields) / 404 / 503 (write paused) |
| DELETE | `/api/skill-bank/{id}` | — | `{deleted: true}` | 200 / 404 / 503 (write paused) |

## Constraints

- **No service layer** — the router accesses `manager._skill_bank_repo` directly. Do NOT create `daemon/services/skill_bank_service.py`.
- **NOT gated** — the router must work regardless of `config.skill_evolution`.
- **No imports from skill evolution** — `skill_bank.py` must not import `skill_store_service`, `skill_search_service`, etc.
- **Sync repo → async bridge** — all repo calls must use `await asyncio.to_thread(...)`.
- **is_write_paused guard** — ALL write endpoints (POST, PUT, DELETE) must check `manager.is_write_paused` and return 503 when paused. Matches `mcp_servers.py` pattern.
- **Request validation** — use `Field(min_length=1)` on `name` and `content` in Pydantic schemas. FastAPI returns 422 automatically — no manual validation needed.
- **Response shapes** — return flat objects (no `{"skill": {...}}` envelope). Matches the existing skills API convention and keeps the frontend clean.
- **404 for missing** — `get`/`update`/`delete` return 404 when the item doesn't exist.

## Deliverables

- [ ] `/api/skill-bank` router with full CRUD in `daemon/routers/skill_bank.py` (NO service layer)
- [ ] Pydantic schemas with `Field(min_length=1)` validation on name/content
- [ ] `is_write_paused` guard on all write endpoints (POST/PUT/DELETE)
- [ ] Router registered in `daemon/routers/__init__.py` + `daemon/api.py`
- [ ] API integration tests pass (create → get → list → update → delete + 404s + 422 validation + 503 pause)
- [ ] Feature works without `skill_evolution` config enabled
- [ ] No `SkillBankService` class exists
