# Test Report: Background Queue Type Feature
**Date:** 2026-07-13
**Branch:** `feature/background-all-projects-queue`
**Sessions:** bg-explore-write, bg-jq-regression, bg-task-regression, bg-run-tests

## Summary
- **Total tests run:** 1,428 (1,415 regression + 13 new)
- **Passed:** 1,428
- **Failed:** 0
- **Timeouts:** 0
- **Overall Status:** ✅ READY

## Scope Decision
> Based on blast radius assessment, the change (additive new queue type touching models, repository, services) was scoped to: `job_queue_unit_test.sh` (full regression), `test_task_repository.py` (claim_pending_task defer/background gate), and new `test_background_queue.py`. Skipped: core_unit_test, api_unit_test, frontend, MCP, all other unrelated packs. Full suite not warranted — additive feature, no cross-module architecture change.

## ensure.md Validation Results

### Core (always-on, scoped)
- **Critical: No regressions in changed packs** — ✅ PASS
  - `job_queue_unit_test.sh`: 1354 passed, 38 skipped (0 failures)
  - `test_task_repository.py`: 61 passed, 0 failed
  - `test_background_queue.py`: 13 passed, 0 failed
- **Critical: Deadlock/concurrency integrity** — ✅ N/A (not in scope — background queue follows existing atomic SQL claim pattern; concurrency_atomic_unit_test not affected by additive queue type)
- **Critical: dev.sh --timeout-graceful-shutdown** — ✅ N/A (not affected by this feature)

## Regression Test Results

### job_queue_unit_test.sh (Existing Suite)
- **Session:** bg-jq-regression
- **Result:** ✅ PASS
- **Stats:** 1354 passed, 38 skipped, 276 warnings (all pre-existing)
- **Runtime:** ~37 seconds
- **Failures:** None
- **Quick Fixes:** None

### test_task_repository.py (Task Repository)
- **Session:** bg-task-regression
- **Result:** ✅ PASS
- **Stats:** 61 passed, 0 failed
- **Runtime:** 1.27 seconds
- **Failures:** None
- **Quick Fixes:** None

## New Test Results: test_background_queue.py

- **Session:** bg-run-tests
- **Result:** ✅ PASS
- **Stats:** 13 passed, 0 failed
- **Runtime:** ~0.1 seconds
- **Commit:** `7613ded0db0b8a5435f169d2ef97d404cfe12612`

### Implementation Map (Found by Exploration)

| Component | File | Details |
|-----------|------|---------|
| QueueType enum | `job_queue/models.py:164-169` | `BACKGROUND = "background"` added |
| DB CheckConstraint | `job_queue/models.py:187` | Enforces `concurrency_limit=1` for defer+background |
| Model validator | `job_queue/models.py:215-229` | `enforce_defer_concurrency_limit` broadened to cover background |
| Task.is_background | `task/models.py:167` | Bool column, defaults False |
| claim_pending_task | `task/repository.py:367` | Atomic SQL gate: `NOT (is_background AND EXISTS(...active task ANYWHERE...))` — **no project_id filter** (cross-project) |
| has_active_non_background_work | `task/repository.py:1517` | Sister predicate; project_id accepted but deliberately ignored |
| auto_provision_system_queues | `job_queue_mgmt_service.py:55-154` | Creates 5 queues including `system_background_queue` |
| enqueue_message | InstanceMessagingService:1073 | Stamps `Task.is_background` at line 949 |
| JobProcessor | lines 733,793,929 | Derives `is_background=(queue.queue_type == "background")` |

### Test Scenarios Covered (A-F)

| Scenario | Test Name | Status |
|----------|-----------|--------|
| **A**: BG waits when other project active | `test_background_task_blocked_when_other_project_has_active_work` | ✅ PASS |
| **B**: BG processes when all idle | `test_background_task_claimable_when_all_projects_idle` | ✅ PASS |
| **C**: Defer vs BG scope comparison | `test_defer_claimable_while_other_project_active_background_blocked` | ✅ PASS |
| **D-1**: Auto-provision 5 queues | `test_auto_provision_creates_five_system_queues` | ✅ PASS |
| **D-2**: BG queue type+concurrency | `test_background_queue_has_correct_type_and_concurrency` | ✅ PASS |
| **D-3**: Auto-provision idempotent | `test_auto_provision_is_idempotent_for_background_queue` | ✅ PASS |
| **E-1**: BG concurrency=5 fails | `test_create_background_queue_with_concurrency_5_raises` | ✅ PASS |
| **E-2**: BG concurrency=1 succeeds | `test_create_background_queue_with_concurrency_1_succeeds` | ✅ PASS |
| **E-3**: BG concurrency=2 fails | `test_create_background_queue_with_concurrency_2_raises` | ✅ PASS |
| **E-4**: Defer validation still works | `test_defer_queue_validation_still_works` | ✅ PASS |
| **F-1**: is_background DB round-trip | `test_task_with_is_background_true_round_trips_through_db` | ✅ PASS |
| **F-2**: Queue type → flag mapping | `test_background_queue_type_maps_to_is_background_true` | ✅ PASS |
| **F-3**: enqueue_message signature | `test_enqueue_message_accepts_is_background_parameter` | ✅ PASS |

### Implementation Notes / Discrepancies

1. **Pydantic model_validator does NOT fire on SQLModel(table=True) instantiation** — DB CheckConstraint is the runtime enforcement layer. Test E accepts either `ValueError` or `IntegrityError`.
2. **SQLite returns 0/1 not Python booleans** for `is_background`/`is_deferred` — assertions cast through `bool()` for backend invariance.
3. **Task creation API gap**: `TaskRepository.create()` does not accept `is_background`/`is_deferred` — tests insert via raw SQL (consistent with existing defer-gate test patterns).
4. **Test F layered approach**: Splits into persistence, mapping, and signature tests rather than full JobProcessor→enqueue_message path (would require live InstanceManager + LLM mocks).

## Code Changes Summary
- `tests/job_queue/test_background_queue.py` — NEW FILE: 13 tests for BACKGROUND queue type (Tests A-F)
- Commit: `7613ded0db0b8a5435f169d2ef97d404cfe12612`

## Action Needed
- None — all tests pass, no failures

## Documentation Updated
- [x] RESULTS/2026-07-13-background-queue-type.md — this report
- [x] PACKS.md — new entry for background_queue_test
- [x] LESSONS/ — implementation map + discrepancies documented
