# Phase 4: API & Schema — Expose History via REST API

## Objective
Add REST API endpoints for project history so external consumers (dashboards, CLI tools, web UIs) can query and manage project history programmatically.

## Coupling
- **Depends on**: Phase 1 (Data Layer — repository methods)
- **Coupling type**: loose
- **Shared files with other phases**:
  - `daemon/routers/schemas.py` — new response/request schemas (no shared files)
  - `daemon/routers/projects.py` (or similar) — new routes (imports repository from Phase 1)
- **Shared APIs/interfaces**: Repository methods from Phase 1
- **Why this coupling**: API layer only consumes the repository interface; no implementation details shared.

## Context
- Existing project routes in `daemon/routers/` — need to verify exact file
- Response schemas in `daemon/routers/schemas.py` with Pydantic-style models
- Existing patterns: `ProjectResponse`, `ProjectListResponse`, `ProjectNotFoundResponse`
- Routes typically use FastAPI router with dependency injection for store

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create history request/response schemas | `ProjectHistoryEntryResponse`, `ProjectHistoryAddRequest`, `ProjectHistoryListResponse`, `ProjectHistorySearchResponse`. Follow existing schema patterns. Use `entry_metadata` (not `metadata`) consistently. | `daemon/routers/schemas.py` |
| 2 | Add history endpoints to project router | `GET /projects/{project_id}/history` (list with paging + entry_type filter), `POST /projects/{project_id}/history` (add), `GET /projects/{project_id}/history/search?q=...` (search), `DELETE /projects/{project_id}/history/{entry_id}` (delete with project ownership validation). | `daemon/routers/projects.py` (or new router file) |
| 3 | Add history to `ProjectResponse` (optional) | Consider adding `recent_history` field to ProjectResponse so API consumers get recent history when fetching a project. | `daemon/routers/schemas.py` |
| 4 | Add API endpoint tests | Test all 4 endpoints: list (with paging + entry_type filter), add, search, delete. Test error cases: 404 for missing project/entry, 400 for invalid entry_type, 404 for wrong project_id on delete. | `tests/` |

## Key Files
- `daemon/routers/schemas.py` — New schemas
- `daemon/routers/projects.py` (verify filename) — New routes

## Detailed Implementation Notes

### Schemas (`schemas.py`)
```python
class ProjectHistoryEntryResponse(BaseModel):
    id: str
    project_id: str
    entry_type: str
    summary: str
    details: str | None = None
    recorded_by_agent: str | None = None
    recorded_by_instance: str | None = None
    entry_metadata: dict | None = None
    created_at: str | None = None

class ProjectHistoryListResponse(BaseModel):
    entries: list[ProjectHistoryEntryResponse]
    total: int
    limit: int
    offset: int

class ProjectHistoryAddRequest(BaseModel):
    entry_type: str
    summary: str
    details: str | None = None
    entry_metadata: dict | None = None

class ProjectHistorySearchResponse(BaseModel):
    entries: list[ProjectHistoryEntryResponse]
    total: int
    limit: int
    offset: int
    query: str
```

### API Endpoints
Follow existing project router patterns. Use dependency injection for the store.

```python
# GET /projects/{project_id}/history
# Query params: limit (default 20), offset (default 0), entry_type (optional filter)

# POST /projects/{project_id}/history
# Body: ProjectHistoryAddRequest

# GET /projects/{project_id}/history/search
# Query params: q (required), limit (default 20), offset (default 0)

# DELETE /projects/{project_id}/history/{entry_id}
# Validates entry belongs to project before deletion
```

## Constraints
- Follow existing router patterns (FastAPI, dependency injection)
- Endpoints should be consistent with existing project API style
- Error handling: 404 for missing project/entry, 400 for invalid entry_type
- Consider authentication if existing endpoints require it
- DELETE endpoint uses `DELETE /projects/{project_id}/history/{entry_id}` (nested under project, not flat)
- Use `entry_metadata` (not `metadata`) in request/response schemas
- Search response includes `limit` and `offset` for consistency with list response
- List endpoint supports optional `entry_type` query parameter for filtering

## Testing Strategy
Add tests covering the API layer:
- **GET /history:** returns entries with paging, supports entry_type filter, 404 for missing project
- **POST /history:** creates entry, 400 for invalid entry_type, 404 for missing project
- **GET /history/search:** matches query, returns consistent format with limit/offset, 404 for missing project
- **DELETE /history/{entry_id}:** deletes entry, 404 for missing entry, 404 for wrong project_id
- **Integration:** full flow — add → list → search → delete

## Deliverables
- [ ] History request/response schemas (with `entry_metadata` naming)
- [ ] 4 API endpoints (list with filter, add, search, delete with project validation)
- [ ] Optional: `recent_history` field on `ProjectResponse`
- [ ] API endpoint tests
