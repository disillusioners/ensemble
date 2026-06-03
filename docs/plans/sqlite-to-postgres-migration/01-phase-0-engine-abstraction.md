# Phase 0: Engine Access Abstraction

> **Effort**: 1-2 hours
> **Priority**: P0 BLOCKER
> **Risk**: Low (mechanical refactor)

## Goal

Eliminate direct `manager._engine` access in 6 locations by exposing `manager.engine` as a public property. This unblocks all subsequent phases by making service code database-agnostic.

## Why This Phase First

Without this change, every service that uses `manager._engine` would need its own database-routing logic. By exposing a single public property, we centralize the abstraction at the manager level, and all services automatically benefit when the engine points at PostgreSQL.

## Changes

### 1. Expose Public Engine Property

**File**: `daemon/manager.py`

**Before**:
```python
class InstanceManager:
    def __init__(self):
        self._engine: Engine | None = None
    
    async def initialize(self):
        # ... engine creation ...
        self._engine = create_engine_from_config(...)
```

**After**:
```python
class InstanceManager:
    def __init__(self):
        self._engine: Engine | None = None
    
    @property
    def engine(self) -> Engine:
        """Public engine accessor. Returns the active database engine.
        
        This is the single source of truth for database access. Services
        should use this property instead of accessing _engine directly.
        """
        if self._engine is None:
            raise RuntimeError("Manager not initialized. Call initialize() first.")
        return self._engine
    
    async def initialize(self):
        # ... engine creation ...
        self._engine = create_engine_from_config(...)
```

### 2. Update Direct Access Sites

Replace `manager._engine` with `manager.engine` in 6 locations:

#### 2.1. `daemon/services/instance_messaging.py`

**Lines**: 594, 1187

**Before**:
```python
async def some_method(self, ...):
    with Session(self._manager._engine) as session:
        # ... cross-table writes ...
```

**After**:
```python
async def some_method(self, ...):
    with Session(self._manager.engine) as session:
        # ... cross-table writes ...
```

#### 2.2. `daemon/services/child_reports.py`

**Line**: 591

```python
# Before
with Session(self._manager._engine) as session:

# After
with Session(self._manager.engine) as session:
```

#### 2.3. `daemon/services/instance_lifecycle.py`

**Line**: 339

```python
# Before
with Session(self._manager._engine) as session:

# After
with Session(self._manager.engine) as session:
```

#### 2.4. `daemon/services/error_reporting.py`

**Line**: 159

```python
# Before
with Session(self._manager._engine) as session:

# After
with Session(self._manager.engine) as session:
```

#### 2.5. `daemon/tools/instance.py`

**Line**: 484

```python
# Before
with Session(manager._engine) as session:

# After
with Session(manager.engine) as session:
```

## Why Property, Not Public Attribute

Using `@property` provides:
1. **Initialization guard**: Raises if accessed before `initialize()` is called
2. **Future extensibility**: Can add lazy loading, swapping, or proxy logic without changing call sites
3. **Read-only semantics**: Callers can't accidentally reassign `manager.engine`

## Testing

### Unit Test: Property Access Guard

```python
# tests/unit/test_manager_engine_property.py
import pytest
from daemon.manager import InstanceManager

def test_engine_property_raises_before_init():
    manager = InstanceManager()
    with pytest.raises(RuntimeError, match="Manager not initialized"):
        _ = manager.engine
```

### Integration Test: Engine Routing

```python
# tests/integration/test_engine_routing.py
import pytest
from daemon.manager import InstanceManager

@pytest.mark.asyncio
async def test_engine_points_to_sqlite_by_default():
    manager = InstanceManager()
    await manager.initialize()
    assert manager.engine.dialect.name == "sqlite"
    await manager.shutdown()

@pytest.mark.asyncio
async def test_engine_points_to_postgres_when_configured(tmp_path):
    # Setup config with PostgreSQL
    # ... (requires Phase 1 config system) ...
    manager = InstanceManager()
    await manager.initialize()
    assert manager.engine.dialect.name == "postgresql"
    await manager.shutdown()
```

### Regression Test: No Direct `_engine` Access

```python
# tests/unit/test_no_direct_engine_access.py
import subprocess

def test_no_direct_engine_access_in_services():
    """Ensure no service accesses _engine directly."""
    result = subprocess.run(
        ["grep", "-rn", "manager._engine", "daemon/"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Found direct _engine access:\n{result.stdout}\n"
        "Use manager.engine instead."
    )
```

## Acceptance Criteria

- [ ] `manager.engine` property added with initialization guard
- [ ] All 6 direct `_engine` access sites updated to use `manager.engine`
- [ ] `grep "manager._engine" daemon/` returns no results
- [ ] Unit test for property guard passes
- [ ] Regression test for no direct access passes
- [ ] All existing tests pass (no regressions)
- [ ] No new imports required
- [ ] No public API changes (additive only)

## Rollback Plan

If issues arise:
1. Revert all 6 file changes
2. Remove `engine` property
3. Existing `manager._engine` access works as before

No data migration needed—this is a pure refactor.

## Estimated Diff Size

- 1 file modified: `daemon/manager.py` (+15 lines for property)
- 5 files modified: services (1 line each)
- 1 file modified: tools (1 line)

**Total**: 7 files, ~20 lines changed

## Next Phase

[Phase 1: Config System](./02-phase-1-config-system.md)
