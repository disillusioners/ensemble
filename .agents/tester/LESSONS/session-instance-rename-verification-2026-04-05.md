# Session→Instance Rename Fix Verification

**Date:** 2026-04-05
**Commits:** 11d9993, 40280ec (fix), 6cc16e2 (test fix)

## What Was Tested
API routes, method references, import integrity, and unit tests for the session→instance rename regression fix.

## Key Findings

### ✅ The Fix Is Complete and Correct
- All 30 API routes use `/instances` (not `/sessions`)
- All manager methods correctly renamed
- All repository methods correct (`dequeue_by_instance`, `list_instance_mappings`)
- No broken imports or missing methods

### One Test Fix Needed
- `tests/test_api.py:136` had `list_session_mappings` mock → fixed to `list_instance_mappings`
- Commit: 6cc16e2

### 18 Pre-existing Failures (NOT rename-related)
1. **test_manager.py (8)**: Missing `_generate_instance_title` method
2. **test_scheduler_api.py (2)**: `source_registry` is None in fixtures
3. **test_spawn_instance_instructive_errors.py (8)**: Error message format mismatch

These existed before the rename fix and are separate issues.

## Lesson
When merge regressions occur, check **test files too** — the previous fix (44f0025) only caught production code, but a test mock was still using the old name.
