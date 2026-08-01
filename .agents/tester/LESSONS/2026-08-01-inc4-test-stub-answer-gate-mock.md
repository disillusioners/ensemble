# Lesson: Test Stub Mock Missing `find_suspended_turn_for_answer` (Inc 4 Cluster 1)

**Date:** 2026-08-01
**Branch:** `latest`
**Commits:** `0a0d7a5` + `e1f973fd`

## Root Cause

When Inc 4 added the new answer-gate selector `find_suspended_turn_for_answer` to `TaskRepository` and wired it into `resume_processing_job` (before the existing `find_paused_or_cancellable_turn` selector), the 4 unit test files that mock `TaskRepository` were not updated.

A bare `MagicMock()` returns a truthy `MagicMock` for any unconfigured method. So `resume_processing_job` always entered the `answer_gate_existing_turn` branch instead of the intended test path. Tests then failed in two ways:
1. **Path A (assertion mismatch):** Code returned `status="resuming"` from `_schedule_explicit_handle_resume` but tests expected `None` or `status="silent_resume"`
2. **Path B (AttributeError):** `test_child_resume.py` lacked `_request_registry` on its manager stubs (the other 3 files had it)

Additionally, 4 tests in `test_child_resume.py` and `test_resume_child_notification.py` were **obsolete** — they tested the pre-Inc-4 child fallback that called `enqueue_message(source="cascade_resume")`. Inc 4 intentionally removed that fallback (§9.4); the new "absent handle" outcome is `resume_processing_job` returning `None` without calling `enqueue_message`.

## Fix

1. Add `repo.find_suspended_turn_for_answer = MagicMock(return_value=None)` to all 4 mock stubs
2. Add `_request_registry = ActiveRequestRegistry()` to `test_child_resume.py` fixtures (matching the other 3 files)
3. Update 4 obsolete tests to assert `enqueue_message.assert_not_called()` + `result is None`

## Pattern: New Selector → Update All Mocks

When adding a new repository method that is called early in a routing path (before other selectors), ALL test mocks must configure it to return `None` (or an appropriate value). A bare `MagicMock()` is truthy and will redirect execution into the wrong branch.

**Checklist for future increments:**
```bash
# After adding a new repository method used in routing:
grep -rn "mock_task_repository\|MockTaskRepository\|MagicMock.*task_repo" tests/ --include="*.py"
# For each file found, verify the new method is explicitly stubbed
```

## Pattern: Obsolete Tests After Fallback Removal

When removing a fallback code path (e.g., enqueue_message for absent-handle case), tests asserting the old behavior become obsolete. They must be updated to match the new behavior. The production callers (3 in `instances.py`, 1 in `messages.py`) already handle `None` gracefully.
