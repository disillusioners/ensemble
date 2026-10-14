# Phase 4 Manager Decomposition — Key Learnings

## Date: 2026-04-23

## What We Did
Decomposed the 2985-line `InstanceManager` god class into 7 focused services using a facade pattern.

## Key Learnings

### 1. Service State Access Pattern (CRITICAL)
When tests override `manager._instance_repository = mock_repo` after construction, services that captured the repository in their `__init__` get a stale reference. 

**Solution**: Services should access state through `self._manager._instance_repository` (via the facade) instead of storing their own copy. This way test mocks work correctly.

### 2. Facade Size Reality
The plan estimated ~600 lines for the facade, but reality is ~1473 lines because:
- `__init__` is ~200 lines (creates all services + internal state)
- Each delegation method has a docstring (~5-10 lines each)
- Compaction logic stayed inline (~120 lines)
- Module-level functions (~100 lines)
- Inner classes (ActivityCallbackHandler, CancellationCallbackHandler) (~130 lines)
- Worker pool setup/teardown (~100 lines)
- Source management methods (~100 lines)
- Shutdown/cleanup logic (~100 lines)

### 3. Test Modifications Are Sometimes Necessary
For a "no logic changes" refactoring, some test modifications are unavoidable:
- Tests that mock internal attributes (e.g., `_event_bus`) need updating when the target changes (e.g., `_events_service`)
- Tests that call moved utility functions need import path updates
- These are NOT logic changes — they're structural alignment

### 4. Fallback Code Smells
Fix sessions tend to add "fallback" or "inline" methods when tests fail. This is wrong — the correct fix is ensuring services are always properly initialized. Watch for `hasattr(self, '_service')` patterns — they indicate incomplete initialization.

### 5. Incremental Extraction Order Matters
Leaf services (no dependencies) should be extracted first:
1. EventPublisher, TitleGeneration (leaf)
2. Cancellation, ErrorReporting (mid)
3. ChildReports (mid)
4. InstanceMessaging (complex, depends on many)
5. InstanceLifecycle (depends on Cancellation)

### 6. Fuzzy Matching Relocation
Moving utility functions to `utils.py` requires a re-export in the original module for backward compatibility:
```python
from daemon.utils import find_near_instance as find_near_instance  # noqa: F401
```
