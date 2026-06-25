# Phase 4 Manager Decomposition Experience

## Key Learnings

### 1. Service state access through facade
When refactoring a god class into services + facade, services should NOT store copies of repositories/config. They should access through `self._manager.*`. Otherwise, tests that do `manager._instance_repository = mock` after construction will leave services with stale references.

**Pattern**:
```python
# WRONG - services store their own copy
class SomeService:
    def __init__(self, instance_repository, ...):
        self._instance_repository = instance_repository  # stale after test override!

# RIGHT - services access through facade
class SomeService:
    def __init__(self, manager):
        self._manager = manager
    def do_something(self):
        repo = self._manager._instance_repository  # always current
```

### 2. Test mock targets change with internal structure
Tests that use `patch.object(InstanceManager, '__init__', lambda self, config: None)` and then manually set internal state (like `manager._event_bus = Mock()`) need updating when internals change (now `manager._events_service = Mock()`). This is expected and acceptable.

### 3. Fallback/inline patterns are code smells
During debugging, it's tempting to add fallback patterns like:
```python
if hasattr(self, '_lifecycle_service'):
    return await self._lifecycle_service.spawn_instance(...)
else:
    return await self._spawn_instance_inline(...)
```
These should be removed before committing. They indicate incomplete extraction.

### 4. Facade line count
Expected ~600 lines but got 1473. The extra lines come from:
- Detailed docstrings on delegation methods (necessary for API documentation)
- `__init__` at 196 lines (initializes many repos + services)
- `_maybe_compact_context` (89 lines) - should be in compaction service but left in facade
- Delegation methods are verbose because they repeat parameter lists

### 5. Circular import prevention
Moving `InstanceManager` import to `TYPE_CHECKING` in `daemon/services/job_processor.py` was necessary to prevent circular imports between `daemon.manager` and `daemon.services.*`.

## Architecture
- 7 new service files in `daemon/services/`
- InstanceManager is facade, services access state through `self._manager.*`
- Module-level functions + inner classes stay in manager.py for backward compat
- Fuzzy matching moved to `daemon/utils.py` with re-export

## Stats
- Original: 2985 lines in manager.py
- After: 1473 lines in manager.py + 2733 lines across 7 services = 4206 total
- Tests: 2800 pass, 0 fail, 27 skip
