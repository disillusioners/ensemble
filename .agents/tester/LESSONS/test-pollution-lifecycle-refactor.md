# Test Pollution Amplification — instance_lifecycle Refactor

**Date:** 2026-06-19
**Branch:** feature/concurrency-fixes
**Severity:** MEDIUM (not a production bug, but causes full-suite failure count inflation)

## Symptom
`tests/test_manager.py` has **14 failures in isolation** but **~38 failures in full suite** on the feature branch. On `latest`, it has **13 failures in isolation** and **~15 in full suite**. The feature branch amplifies pre-existing test pollution by ~24 tests.

## Root Cause
Commit `0276e5b6` ("fix: phase 5 (H10) — terminate_instance single-transaction cascade") refactored `daemon/services/instance_lifecycle.py` (+1036/-262 lines):
- `spawn_instance` → `_spawn_instance_db_sync(self._manager.engine, ...)` → uses `WriteGuardSession(Session(engine))` directly
- `terminate_instance` → `_terminate_instance_db_sync(self._manager.engine, ...)` → uses `WriteGuardSession(Session(engine))` directly

The old tests mock `manager._instance_repository` to intercept DB calls. The new code **bypasses this mock** by going directly to `manager.engine`. When the full suite runs, earlier tests pollute the mock/DB state, and later tests fail because:
1. `terminate_instance` does raw SQL `session.get(Instance, instance_id)` against an empty `:memory:` DB → `OperationalError: no such table`
2. `spawn_instance` does inline metadata inheritance → `set_metadata()` is never called on the mock

## Correct Mock Pattern (from newer tests)
The branch's OWN test file `tests/services/test_instance_lifecycle_terminate.py:71-72` shows the correct pattern:
```python
manager.engine = MagicMock()
manager.write_guard = MagicMock()
```
The old test files (`test_manager.py`, `test_progressive_dispatch.py`) need updating to use this pattern.

## Impact on Merge Gate
- **Isolation delta (real regressions):** test_manager.py +1, test_progressive_dispatch.py +2 = **~3 real new failures**
- **Full-suite delta (pollution amplified):** **+12 net failures** (81 feature vs 69 latest with --tb=no)
- **0 production code regressions** — the lifecycle refactor is functionally correct

## Fix
Update ~3 test files to mock at the engine/write_guard level instead of the repository level:
- `tests/test_manager.py` (TestTerminateInstance::test_terminate_instance_success)
- `tests/test_progressive_dispatch.py` (source inheritance tests)
- Optionally: add migrations fixture to conftest.py to fix the 25 pre-existing `:memory:` DB failures

## Lesson
When refactoring code to bypass a repository layer (going direct to engine/session), ALL tests that mock the repository layer must be updated simultaneously. The correct mock pattern (`manager.engine = MagicMock()`) should be documented and enforced.
