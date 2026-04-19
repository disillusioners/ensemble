# Lesson: Hard Delete Method Rename Pattern

**Date:** 2026-04-22
**Feature:** Job Soft Delete
**Context:** When `delete()` was renamed to `hard_delete()`, several test files still called the old method name.

## What Happened

The soft-delete feature renamed three repository methods:
- `delete()` → `hard_delete()`
- `delete_completed()` → `hard_delete_completed()`
- `delete_by_project()` → `hard_delete_by_project()`

The conftest.py fixture cleanup and two test files (`test_task_queue_repository.py`, `test_task_queue_integration.py`) still used the old names, causing `AttributeError` at runtime.

## Root Cause

When renaming public API methods, test files that call those methods need to be updated too. The implementation commits didn't include test file updates.

## Pattern to Follow

When renaming methods:
1. Grep all test files for the old method name
2. Update conftest.py fixtures (especially cleanup)
3. Update all test classes that call the method
4. Run full suite to catch any missed references

## Files Affected
- `tests/job_queue/conftest.py` — fixture cleanup
- `tests/job_queue/test_task_queue_repository.py` — TestRepositoryDelete class
- `tests/job_queue/test_task_queue_integration.py` — test_recovery_completed_job_cleanup
