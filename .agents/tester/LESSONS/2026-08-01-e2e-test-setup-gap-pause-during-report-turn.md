# E2E Test Setup Gap: `test_pause_during_report_turn_then_resume`

## Date: 2026-08-01
## Context: Phase 1 Bug A Deadlock Fix (`feature/fix-pause-report-turn-orphan`, commits `76c19ce2` + `b2083ff4`)

## Problem

The E2E test `test_pause_during_report_turn_then_resume` (tests/e2e/test_e2e_workflows.py:1936) fails **consistently (5/5)** with `status: None`. Root cause is a **test setup gap, not a production bug**.

## Root Cause

Two distinct issues in the test:

### 1. No active-orphan state constructed
The test's docstring describes the Bug A scenario (ask_questions pauses mid-process_report turn, orphaning the active JobItem). However, the test implementation:
- Calls `_wait_for_completion(leader_id)` which polls until terminal status
- The leader reaches `status=completed` naturally
- **No `_pause_instance` call** — the test never produces a PAUSED leader
- The test's own docstring (line 1978) acknowledges needing "a deterministic hook inside `ask_questions`" but doesn't implement it

### 2. Wrong assertion key
The test asserts `result.get("status")` on the **router envelope**. The resume API response (`daemon/routers/instances.py:596-602`) returns:
```python
{"resumed": True, "resumed_ids": [], "skipped_ids": [...], "target_id": ..., "resume_results": {}}
```
There is **no top-level `status` key**. Per-instance status lives at `result["resume_results"][leader_id]["status"]`. The canonical assertion pattern is in `tests/test_api.py:622-626`.

## Why Production Code Is Correct

- `InstanceLifecycleService.resume_instance_cascade` correctly filters on `status=PAUSED` only (line 1964–1968) — a `completed` leader should be skipped
- `InstanceManager.resume_processing_job` and `TaskRepository.find_resume_root_candidate_by_active_job` (the fix in `76c19ce2`) are only called when `resumed_ids` is non-empty
- Unit tests that call these functions directly (with properly seeded state) all pass (28/28)
- The daemon log confirms zero `[RESUME]` log lines — `resume_processing_job` was never called

## Code Path (for reference)
- Resume API: `POST /api/instances/{id}/resume` → `daemon/routers/instances.py:552`
- Cascade gate: `daemon/services/instance_lifecycle.py:1964-1968` (`meta.status != PAUSED → skip`)
- Manager fallback (the fix): `daemon/manager.py:4884-4919` (only reached when cascade returns resumed_ids)
- Repository primitive: `daemon/repositories/task/repository.py:246-370`

## Suggested Fix (test-only, not production)

1. **Fix assertion** (`test_e2e_workflows.py:2028`):
   ```python
   # Instead of:
   assert result.get("status") in ("queued", "resuming", ...)
   # Use:
   job_result = result["resume_results"].get(leader_id, {})
   assert job_result.get("status") in ("queued", "resuming", ...)
   ```

2. **Construct active-orphan state** (`test_e2e_workflows.py:1987-2005`):
   - Either implement the "deterministic hook inside ask_questions" the docstring describes
   - Or seed the active-orphan DB state directly via a test fixture
   - The leader must be PAUSED (not completed) for the resume cascade to process it

## Impact
- **Production safety:** ✅ Safe to merge — fix is correct, 294 tests validate it
- **Test coverage gap:** The Bug A scenario is NOT covered by E2E (only unit-level)
- **Recommendation:** Merge the fix; fix the E2E test as a follow-up (test-only change)
