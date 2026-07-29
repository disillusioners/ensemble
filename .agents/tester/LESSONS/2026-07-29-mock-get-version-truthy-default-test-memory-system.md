# Quick Fix: MagicMock.get_version() truthy-default in test_memory_system.py

Date: 2026-07-29
Branch: `bugfix/deferred-version-tag-fixes`
Commit: `7a8641e4`
Found by: Worker (pack-core-S6, instance 53cf90fc)

## Root Cause
`access_memory.py:48` uses the pattern:
```python
registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)
```

6 test mocks in `tests/test_memory_system.py` only stubbed `get_resolved`. But `MagicMock.get_version()` returns a truthy MagicMock by default — this short-circuits the `or` fallback, returning a MagicMock `.path` → "Access denied" on every memory read.

## The Pattern (5th occurrence!)
This is the **same mock gotcha** seen in:
1. `cf30fcd7` — tool_filter tests (test_tool_filter.py)
2. `d392b73c` — registry tests (8 mock sites across 4 test files)
3. `5b1cca86` — instance_messaging tests (18 mock sites)
4. Prior C1 fix work — version_tag_tool_resolution tests
5. **This fix** — test_memory_system.py (6 mock sites)

## Fix
Added `mock_registry.get_version.return_value = None` to all 6 mock setup locations in `tests/test_memory_system.py`.

## Prevention Rule
**Whenever a test mocks a registry and the code under test uses `registry.get_version(...) or registry.get_resolved(...)`, the mock MUST explicitly set `get_version.return_value = None`** to exercise the fallback path. Otherwise the truthy MagicMock default silently bypasses the fallback.

## Before/After
- Before: 6 tests in test_memory_system.py failed (access denied) — would have been NEW failures
- After: All 6 pass, core_unit_test back to PASS by baseline (697 passed, 41 pre-existing, 0 NEW)
