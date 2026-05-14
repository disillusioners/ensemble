# Phase 2: Backend — API Filter (Full 4-Layer Stack)

## Objective
Add `project_id` query parameter support to the `GET /api/instances` endpoint by updating all 4 layers of the backend call chain: Router → Manager → LifecycleService → Repository.

## Coupling
- **Depends on**: Phase 1 (project_id column must exist in model + to_dict())
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/repositories/instance/repository.py`, `daemon/services/instance_lifecycle.py`, `daemon/manager.py`, `daemon/routers/instances.py`
- **Shared APIs/interfaces**: `GET /api/instances?project_id=xxx`
- **Why this coupling**: Directly queries the column created in Phase 1; all 4 layers must be consistent

## Context
- Phase 1 added `project_id` column to instances table
- The call chain is: **Router** (`daemon/routers/instances.py:108-119`) → **Manager** (`daemon/manager.py:1300`) → **LifecycleService** (`daemon/services/instance_lifecycle.py:467`) → **Repository** (`daemon/repositories/instance/repository.py`)
- **ALL 4 LAYERS** must be updated to pass `project_id` through
- Router at `instances.py:108-119` manually constructs `InstanceInfo` from dict fields — must explicitly extract and pass `project_id`
- Current `InstanceRepository.list()` accepts only `status`, `limit`, `offset`
- Current API endpoint `GET /api/instances` accepts only `limit`, `offset`
- Need to add `project_id` filter without breaking existing calls

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `project_id` param to Repository `list()` | Accept optional `project_id: str | None`; when provided, filter `WHERE project_id = :project_id` | `daemon/repositories/instance/repository.py` |
| 2 | Add `project_id` param to Repository `count()` | Same filter for correct pagination totals | `daemon/repositories/instance/repository.py` |
| 3 | Add `project_id` param to LifecycleService `list_instances()` | Accept and forward `project_id` parameter | `daemon/services/instance_lifecycle.py:467` |
| 4 | Add `project_id` param to Manager `list_instances()` | Accept and forward `project_id` parameter | `daemon/manager.py:1300` |
| 5 | Add `project_id` query param to Router endpoint | `GET /api/instances?project_id=xxx` — optional, defaults to None (show all) | `daemon/routers/instances.py` |
| 6 | Update Router's `InstanceInfo` construction | At `instances.py:108-119`, router manually builds `InstanceInfo` from dict. Must add `project_id=inst.get("project_id")` to the explicit field list | `daemon/routers/instances.py:108-119` |
| 7 | Write API tests | Test: no filter (all), filter by project_id, filter by nonexistent project_id (empty), combined with status filter | `tests/` directory |
| 8 | Verify backward compatibility | Ensure existing API calls without `project_id` still work identically | Manual + automated test |

## Key Files — The Full Call Chain
| Layer | File | Line | Method |
|-------|------|------|--------|
| Router | `daemon/routers/instances.py` | 108-119 | `list_instances()` endpoint |
| Manager | `daemon/manager.py` | ~1300 | `list_instances()` |
| LifecycleService | `daemon/services/instance_lifecycle.py` | ~467 | `list_instances()` |
| Repository | `daemon/repositories/instance/repository.py` | — | `list()` + `count()` |

## Implementation Notes

### Repository Layer
```python
# In InstanceRepository
async def list(self, status: str | None = None, project_id: str | None = None,
               limit: int = 50, offset: int = 0) -> list[Instance]:
    query = select(Instance)
    if status:
        query = query.where(Instance.status == status)
    if project_id is not None:
        query = query.where(Instance.project_id == project_id)
    query = query.offset(offset).limit(limit)
    # ... execute
```

### LifecycleService Layer
```python
# daemon/services/instance_lifecycle.py:467
async def list_instances(self, ..., project_id: str | None = None) -> ...:
    # Forward project_id to repository
    return await self.repo.list(project_id=project_id, ...)
```

### Manager Layer
```python
# daemon/manager.py:1300
async def list_instances(self, ..., project_id: str | None = None) -> ...:
    # Forward project_id to lifecycle service
    return await self.lifecycle_service.list_instances(project_id=project_id, ...)
```

### Router Layer — ⚠️ CRITICAL: Manual InstanceInfo Construction
The router at `instances.py:108-119` does NOT auto-map from dict. It constructs `InstanceInfo` field-by-field:
```python
# At daemon/routers/instances.py:108-119
InstanceInfo(
    # ... existing fields extracted from inst dict ...
    project_id=inst.get("project_id"),  # MUST ADD THIS EXPLICITLY
)
```

Without this explicit addition, `project_id` will be `None` in API responses even if the database has the value.

### Null Handling
Instances with `project_id = NULL` (no project) should:
- Appear when `project_id` param is not provided (All tab)
- NOT appear when filtering by a specific project_id
- This is the natural SQL behavior of `WHERE project_id = 'xxx'` (NULL != 'xxx')

## Constraints
- Must be backward compatible — omitting `project_id` returns all instances
- Must support combining with existing filters (status, limit, offset)
- No performance regression for unfiltered queries (column is indexed from Phase 1)
- **ALL 4 LAYERS must be updated** — missing any layer breaks the filter chain
- Router's manual `InstanceInfo` construction must explicitly include `project_id`

## Deliverables
- [ ] `GET /api/instances?project_id=xxx` returns filtered instances
- [ ] `GET /api/instances` (no filter) returns all instances as before
- [ ] All 4 layers pass `project_id` through correctly
- [ ] Router constructs `InstanceInfo` with `project_id` field
- [ ] Count respects project_id filter
- [ ] API tests pass
- [ ] Existing API tests still pass
