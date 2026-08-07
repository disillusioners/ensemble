# Quick Fix: _LoopCleanupStub Missing _deferred_watchover_terminate

**Date**: 2026-08-05
**File**: `tests/test_loop_breaker_integration.py`
**Commit**: `0fbb4457`
**Instance**: dc1ff8bd

## Issue
The `_LoopCleanupStub` in `_make_manager_with_loop_breaker_surface()` mirrored the question-pause deferred marker (`_deferred_question_pause = set()`) but was missing its watchover equivalent. The real `InstanceManager._cleanup_instance_state` (bound to the stub via `__get__`) calls `self._deferred_watchover_terminate.discard(instance_id)` at `daemon/manager.py:2990`, causing an `AttributeError` when the test exercises the cleanup path.

## Root Cause
The stub was created before the watchover feature added the `_deferred_watchover_terminate` set to `InstanceManager.__init__`. When watchover Phase 1 added the new deferred marker + cleanup discard, the test stub wasn't updated to match.

## Fix
Added 1 line after `stub._deferred_question_pause = set()`:
```python
stub._deferred_watchover_terminate = set()
```

## Pattern to Follow
When adding a new `_deferred_*` set to `InstanceManager.__init__` that `_cleanup_instance_state` touches, update ALL test stubs that bind `_cleanup_instance_state`:
- `tests/test_loop_breaker_integration.py::_make_manager_with_loop_breaker_surface()`
- `tests/test_gii_throttle.py::_make_manager_with_cleanup_surface()` (if it exists)
- Any other stub following the same pattern

## Verification
- `test_cleanup_instance_state_clears_loop_breaker`: PASS
- Full loop breaker pack: 17/17 PASS in 1.90s
