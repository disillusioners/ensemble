# Phase 2: Backend API + DB — Version Surfacing & Persistence

## Objective

Wire versioning through the API and database layers: add `version_tag` to request/response models, accept it in instance creation, persist it in a new `agent_tag` DB column, refactor the agents router to use the registry, and add the DB migration for both SQLite and PostgreSQL.

## Coupling

- **Depends on**: Phase 1 (registry methods `get_version`, `list_versions`, `list_all_grouped`)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/registry.py` (reads Phase 1 methods), `daemon/models/agent.py` + `daemon/models/instance.py` (Phase 3 frontend reads these API contracts)
- **Shared APIs/interfaces**: `GET /api/agents` response shape (new fields), `POST /api/instances` request body (new optional `version_tag` param)
- **Why this coupling**: Phase 2 calls registry methods from Phase 1; the API contract defined here is what Phase 3 consumes. Must wait for Phase 1 review.

## Context

- Previous phase delivered: Registry parses `[tag]`, exposes `get_version()`, `list_versions()`, `list_all_grouped()`
- Key decisions: D3 (version selection flow), D4 (`agent_tag` column name), D6 (unify agent listing), D7 (flat API response), D9 (migration approach)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `version_tag` + `available_versions` to `AgentInfo` model | New optional fields on the API response model. See decisions.md D7. | `daemon/models/agent.py:4-29` |
| 2 | Add `version_tag` to `InstanceCreate` model | New optional field `version_tag: str \| None = None`. Passed through to `spawn_instance()`. | `daemon/models/instance.py:12-32` |
| 3 | **Audit skip rules before refactor (C4)** | Compare `routers/agents.py` skip logic (skips ALL `_`-prefixed) vs `registry.discover()` skip logic (only skips `SKIP_DIRS` frozenset). Document differences. Ensure refactored router produces identical agent list for the 23 existing agents. | `daemon/routers/agents.py:30-69`, `daemon/registry.py:149-218` |
| 4 | Refactor `routers/agents.py:list_agents()` to use registry | Replace the independent FS scan with `registry.list_all_grouped()`. Apply `_`-prefix filter in the router (Option B from D12) to match current behavior. Populate `version_tag` and `available_versions`. | `daemon/routers/agents.py:30-69` |
| 5 | Add `version_tag` param to `spawn_instance()` method | New optional parameter. **C2 Fix**: explicit tag not found → ValueError. **Approver #4**: normalize path→base-id via `resolve_to_id()` BEFORE calling `get_version()`. **D15**: pass `version_tag` to `load_and_cache_prompt()` at line 1101. | `daemon/services/instance_lifecycle.py:1003-1101` |
| 6 | Add `version_tag` param to `spawn_instance_with_mcp()` | Thread the parameter from the router through to `spawn_instance()`. | `daemon/services/instance_lifecycle.py` (find `spawn_instance_with_mcp` signature) |
| 7 | **Add `version_tag` to manager wrapper (W6)** | `manager.py:4072` `spawn_instance()` delegation must explicitly forward `version_tag` parameter. | `daemon/manager.py:4072-4122` |
| 8 | Update `POST /instances` endpoint to pass `version_tag` | Read `instance_create.version_tag` and pass to `spawn_instance_with_mcp()`. | `daemon/routers/instances.py:222-276` |
| 9 | **Modify `_restore_instance()` for version-aware restore (C1 + D15 — CRITICAL)** | Change `registry.get_resolved()` to `registry.get_version()`. **D15**: pass `agent_tag` to `load_and_cache_prompt()` at line 2434. Both path AND cache key must be version-aware. | `daemon/services/instance_lifecycle.py:2402-2434` |
| 10 | Add `agent_tag` column to Instance SQLModel | New `agent_tag: str \| None`. **W9**: No index. **Non-blocking fix**: Must add to `to_dict()` — downstream consumers (`job_processor`, `child_reports`) use `to_dict()`, not just `InstanceInfo`. | `daemon/repositories/instance/models.py:47-95` |
| 11 | Create SQLite migration file | `20260724_000001_add_agent_tag_to_instances.sql` — `ALTER TABLE instances ADD COLUMN agent_tag VARCHAR`. **W9**: Column only, no index. | `daemon/migrations/versions/20260724_000001_add_agent_tag_to_instances.sql` (new) |
| 12 | Add PostgreSQL column to `_ensure_postgres_columns()` | Add `ALTER TABLE instances ADD COLUMN IF NOT EXISTS agent_tag VARCHAR`. **W9**: No index. | `daemon/manager.py:3010+` (append to `statements` list) |
| 13 | Update `_spawn_instance_db_sync()` to persist `agent_tag` | Pass `agent_tag` to the DB insert. Update the method signature and the `Instance(...)` constructor call. | `daemon/services/instance_lifecycle.py` (find `_spawn_instance_db_sync`) |
| 14 | **Add `agent_tag` to `InstanceInfo` model (REQUIRED — S9)** | Add `agent_tag: str \| None = Field(default=None)` to `InstanceInfo`. Update `POST /instances` response builder and `GET /instances` to include it. The frontend needs this for the version badge. | `daemon/models/instance.py:35-77`, `daemon/routers/instances.py:263-276` |
| 15 | Write Phase 2 integration tests | Tests for: API response includes version info, instance creation with version_tag, **invalid version_tag raises ValueError (C2)**, DB persistence of agent_tag, **restore loads correct version (C1)**, **PromptCache isolation (D15)**, backward compat (no version_tag → NULL). | `tests/test_agent_versioning_api.py` (new) |

## Key Files

- `daemon/models/agent.py` — `AgentInfo` model
- `daemon/models/instance.py` — `InstanceCreate`, `InstanceInfo` models
- `daemon/routers/agents.py` — `GET /api/agents` endpoint
- `daemon/routers/instances.py` — `POST /instances` + `GET /instances` endpoints
- `daemon/services/instance_lifecycle.py` — `spawn_instance()`, `spawn_instance_with_mcp()`, `_spawn_instance_db_sync()`, **`_restore_instance()` (C1+D15)**
- `daemon/loader.py` — **PromptCache + `load_and_cache_prompt` callers (D15 — Phase 1 modifies the signature, Phase 2 updates callers)**
- `daemon/repositories/instance/models.py` — `Instance` SQLModel table
- `daemon/migrations/versions/20260724_000001_add_agent_tag_to_instances.sql` — new migration
- `daemon/manager.py` — **`spawn_instance()` wrapper (W6)**, `_ensure_postgres_columns()` method

## Detailed Implementation Notes

### Task 1: AgentInfo Model Changes

```python
class AgentInfo(BaseModel):
    # ... existing fields ...
    version_tag: str | None = Field(
        default=None,
        description="Version tag for this agent entry (None = base). "
                    "Derived from directory suffix [tag]."
    )
    available_versions: list[str | None] = Field(
        default_factory=list,
        description="All available version tags for this agent_id. "
                    "None in the list means base version exists."
    )
```

### Task 3+4: Refactored list_agents() Using Registry (with C4 Skip-Rule Audit)

> **C4 Fix**: Before refactoring, audit skip rules. The router currently skips ALL `_`-prefixed dirs; the registry only skips `SKIP_DIRS` members. After refactor, apply `_`-prefix filter in the router (Option B from D12).

```python
@router.get("", response_model=AgentListResponse)
async def list_agents():
    """List all available agents with version information."""
    from daemon.registry import get_registry
    registry = get_registry()
    grouped = registry.list_all_grouped()  # dict[agent_id, list[AgentMetadata]]
    
    result = []
    for agent_id, versions in sorted(grouped.items()):
        # C4: Apply _ prefix filter to match pre-refactor router behavior
        if agent_id.startswith("_"):
            continue
        
        available_tags = sorted(
            [v.version_tag for v in versions],
            key=lambda t: (t is not None, t or "")
        )
        for meta in versions:
            result.append(AgentInfo(
                id=meta.id,
                name=meta.name,
                description=meta.description,
                icon=meta.icon,
                color=meta.color,
                version=meta.version,
                agent_dir=str(meta.path),
                system=meta.system,
                version_tag=meta.version_tag,
                available_versions=available_tags,
            ))
    
    return AgentListResponse(agents=result)
```

### Task 5: spawn_instance() Changes (C2 Fix + Approver Issue #4)

> **C2 Fix**: Explicit `version_tag` not found → `ValueError`.
> **Approver Issue #4**: Must normalize path→base-id BEFORE calling `get_version()`. Frontend sends `./agents/developer`, not `"developer"`.

```python
def spawn_instance(
    self,
    agent_id: str,
    instance_id: str | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    instance_name: str | None = None,
    invoked_as_tool: bool = False,
    model: str | None = None,
    version_tag: str | None = None,  # NEW
) -> tuple[str, str | None]:
    # ...
    registry = get_registry()
    
    # Step 1: Normalize agent_id — resolve paths/aliases to base agent_id
    # (Approver issue #4: frontend sends "./agents/developer", not "developer")
    resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id
    
    # Step 2: Version-aware resolution
    if version_tag is not None:
        # C2: Explicit version_tag must match exactly — no silent fallback
        metadata = registry.get_version(resolved_agent_id, version_tag)
        if metadata is None:
            available = registry.list_versions(resolved_agent_id)
            raise ValueError(
                f"Version tag '{version_tag}' not found for agent '{resolved_agent_id}'. "
                f"Available: {available}"
            )
    else:
        # No tag specified — use fallback chain (base → first tagged)
        metadata = registry.get_version(resolved_agent_id, None)
        if metadata is None:
            metadata = registry.get(resolved_agent_id)
    if metadata is None:
        raise ValueError(f"Agent not found: {agent_id}")
    
    resolved_agent_dir = str(metadata.path)
    # ... rest unchanged, but thread version_tag to _spawn_instance_db_sync ...
```

**Also update `load_and_cache_prompt` call at line 1101** (D15 PromptCache fix):

```python
# line 1101 — BEFORE:
system_prompt, token_count = load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names)

# line 1101 — AFTER (D15):
system_prompt, token_count = load_and_cache_prompt(
    resolved_agent_id, agent_path, prompt_cache, mcp_tool_names, version_tag
)
```

### Task 7: Manager Wrapper (W6)

> **W6 Fix**: `manager.py:4072` `spawn_instance()` must explicitly forward `version_tag`.

```python
# daemon/manager.py:4072
def spawn_instance(
    self,
    agent_id: str,
    instance_id: str | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    instance_name: str | None = None,
    invoked_as_tool: bool = False,
    model: str | None = None,
    version_tag: str | None = None,  # NEW — W6
) -> tuple[str, str | None]:
    # ... docstring ...
    return self._lifecycle_service.spawn_instance(
        agent_id=agent_id,
        instance_id=instance_id,
        parent_id=parent_id,
        project_id=project_id,
        instance_name=instance_name,
        invoked_as_tool=invoked_as_tool,
        model=model,
        version_tag=version_tag,  # NEW — W6
    )
```

### Task 9: _restore_instance() Version-Aware Restore (C1 — CRITICAL)

> **C1 Fix**: `_restore_instance()` at line 2425 currently uses `registry.get_resolved(meta.agent_id)` which returns the BASE version. On daemon restart, a `developer[v2]` instance would silently load the base developer prompt. This MUST use `registry.get_version()` with the stored `agent_tag`.
>
> **D15 Fix**: The `load_and_cache_prompt` call at line 2434 MUST also pass `version_tag` — otherwise the PromptCache returns the base prompt even when `agent_meta.path` is correct.

```python
# daemon/services/instance_lifecycle.py:2424-2434 — BEFORE:
registry = get_registry()
agent_meta = registry.get_resolved(meta.agent_id)  # Returns base — WRONG for tagged instances
if agent_meta is None:
    raise ValueError(f"Agent not found: {meta.agent_id}")
resolved_agent_id = meta.agent_id
# ...
system_prompt, token_count = load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names)

# AFTER (C1 + D15 fix):
registry = get_registry()
# Use get_version with stored agent_tag — falls back to base if agent_tag is None
agent_tag = getattr(meta, 'agent_tag', None)  # Read from DB row
agent_meta = registry.get_version(meta.agent_id, agent_tag)
if agent_meta is None:
    # Fallback: try legacy resolution for old instances without agent_tag
    agent_meta = registry.get_resolved(meta.agent_id)
if agent_meta is None:
    raise ValueError(f"Agent not found: {meta.agent_id} (tag: {agent_tag})")
resolved_agent_id = meta.agent_id

# D15: Pass version_tag to load_and_cache_prompt — prevents cache collision
system_prompt, token_count = load_and_cache_prompt(
    resolved_agent_id, agent_path, prompt_cache, mcp_tool_names, agent_tag
)
```

**Critical verification**: `agent_path = Path(agent_meta.path)` at line 2433 must resolve to the tagged directory (`agents/developer[v2]/`), not the base (`agents/developer/`). This ensures the correct soul.md, rule.md, workflow.md are loaded on restart. AND the `load_and_cache_prompt` call must pass `agent_tag` so the PromptCache doesn't return the base prompt.

### Task 10: Instance SQLModel Change

> **W9**: No `index=True` — queries filtering by `agent_tag` are rare.

```python
class Instance(SQLModel, table=True):
    # ... existing fields ...
    agent_tag: str | None = Field(
        default=None,
        sa_column=Column("agent_tag", String, nullable=True)
        # W9: No index — agent_tag filtering is rare
    )
    
    def to_dict(self):
        return {
            # ... existing fields ...
            "agent_tag": self.agent_tag,
        }
```

### Task 11: SQLite Migration

> **W9**: Column only, no index.

```sql
-- Migration: add agent_tag column to instances table
-- Created: 2026-07-24
-- Description: Add agent_tag column for agent version tagging

ALTER TABLE instances ADD COLUMN agent_tag VARCHAR;

-- DOWN
ALTER TABLE instances DROP COLUMN agent_tag;
```

### Task 12: PostgreSQL _ensure_postgres_columns() Addition

> **W9**: Column only, no index.

Add to the `statements` list in `_ensure_postgres_columns()`:

```python
# instances.agent_tag: agent version tag for directory-suffix versioning
"ALTER TABLE instances ADD COLUMN IF NOT EXISTS agent_tag VARCHAR",
```

## Constraints

- PostgreSQL is PRIMARY DB — migration must work on both SQLite and PostgreSQL.
- Use `_ensure_postgres_columns()` for new columns on existing PG tables (`.sql` migrations NO-OP on PG).
- No SQLite-only syntax (no `rowid`).
- The existing `version` Integer column on `instances` is for OPTIMISTIC LOCKING — do NOT touch it.
- `InstanceCreate.version_tag` must be optional (default None) for backward compatibility.
- **C1**: `_restore_instance()` MUST use `registry.get_version(meta.agent_id, meta.agent_tag)` — not `registry.get_resolved()`.
- **C2**: Explicit `version_tag` not found → `ValueError` (no silent fallback to base).
- **Approver #4**: `spawn_instance()` must normalize path→base-id (`resolve_to_id()`) BEFORE calling `get_version()`.
- **C4**: Router refactor must produce identical agent list for 23 existing agents. Audit skip rules first.
- **W6**: Manager `spawn_instance()` wrapper at `manager.py:4072` must forward `version_tag`.
- **D15**: Both spawn and restore must pass `version_tag` to `load_and_cache_prompt()` — PromptCache collision prevention.
- **W9**: No index on `agent_tag` column.
- **S9**: `agent_tag` in `InstanceInfo` is REQUIRED (not optional).
- **Non-blocking**: `Instance.to_dict()` must include `agent_tag` (downstream consumers like `job_processor`, `child_reports` use `to_dict()`).

## Deliverables

- [ ] `AgentInfo` has `version_tag` + `available_versions` fields
- [ ] `InstanceCreate` has optional `version_tag` field
- [ ] `InstanceInfo` has `agent_tag` field (REQUIRED — S9)
- [ ] **`Instance.to_dict()` includes `agent_tag`** (non-blocking fix)
- [ ] Skip-rule audit completed (C4)
- [ ] `GET /api/agents` returns version info (backed by registry, with `_`-prefix filter)
- [ ] `POST /api/instances` accepts and resolves `version_tag`
- [ ] **Invalid `version_tag` raises `ValueError` with available versions (C2)**
- [ ] **`spawn_instance()` normalizes path→base-id before `get_version()` (Approver #4)**
- [ ] `spawn_instance()` + `spawn_instance_with_mcp()` thread `version_tag`
- [ ] **Manager wrapper forwards `version_tag` (W6)**
- [ ] **`_restore_instance()` uses `get_version()` with stored `agent_tag` (C1)**
- [ ] **Both spawn + restore pass `version_tag` to `load_and_cache_prompt()` (D15)**
- [ ] Instance table has `agent_tag` column (SQLite + PostgreSQL, no index per W9)
- [ ] SQLite migration file created (column only)
- [ ] PostgreSQL `_ensure_postgres_columns()` updated (column only)
- [ ] `_spawn_instance_db_sync()` persists `agent_tag`
- [ ] Integration tests pass (including C1 restore test, C2 error test, PromptCache isolation)
