# Recurring Pattern: get_version() mock-gap in version-tag resolution tests

**Date:** 2026-07-29
**Branch:** bugfix/version-tag-tool-resolution
**Commits affected:** 09d146c9, 9c2d95cc
**Commits (fixes):** cf30fcd7, 5b1cca86, d392b73c

## Problem (Recurring — 4 occurrences across 2 commits)

The version-tag tool resolution fix introduced the pattern `registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)` in many source files. When tests use a `MagicMock()` for the registry, `get_version()` returns an auto-created MagicMock (truthy), so the `or` short-circuits and `get_resolved()` (the properly-mocked method) is never called. This causes the configured mock data to be silently bypassed.

## Occurrences

| Commit | Source file changed | Test file affected | # mock sites | Fix commit |
|--------|-------------------|-------------------|-------------|------------|
| 09d146c9 | daemon/tools/instance.py (_apply_tool_filter) | tests/test_tool_filter.py | 12 | cf30fcd7 |
| 9c2d95cc | daemon/services/instance_messaging.py (S2/C1) | tests/services/test_instance_messaging_*.py | 18 | 5b1cca86 |
| 9c2d95cc | daemon/tools/access_memory.py + inner_soul.py (W1) | tests/unit/tools/test_inner_soul_*.py + test_memory_edge_cases.py | 8 | d392b73c |
| 09d146c9 | daemon/tools/instance.py (_check_team_membership) | tests/test_spawn_team_members.py | — | Already patched by developer |

## Root Cause

`MagicMock` auto-creates attributes and return values on access. Any method called on a MagicMock returns a new truthy MagicMock by default. The `or` fallback pattern means:
```python
agent_meta = registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)
```
Under MagicMock: `get_version()` returns `MagicMock()` (truthy) → `or` short-circuits → `get_resolved()` never called → the test's configured return value is ignored.

## Fix Pattern

Add `mock_registry.get_version.return_value = None` to every mock registry setup so the code falls through to the properly-mocked `get_resolved()`:
```python
mock_registry = MagicMock()
mock_registry.get_version.return_value = None  # CRITICAL: forces fallback to get_resolved()
mock_registry.get_resolved.return_value = configured_agent_meta
```

## Lesson / Prevention

**When a function gains a new lookup path via `or`-fallback, ALL test mocks must stub the new method to return `None` explicitly.** The developer who wrote the C1 fix patched 5 test files but missed test_tool_filter.py. The W1/W2 commit added 3 more source files but no test fixes at all — the tester caught these.

**Systematic check:** After adding `get_version() or get_resolved()` to ANY source file, grep all test files that mock `get_resolved` for that module and add `get_version.return_value = None`.
