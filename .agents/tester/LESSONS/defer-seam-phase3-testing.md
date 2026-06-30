# Phase 3 Defer Seam Bugfix — Testing Lessons

## Date: 2026-06-30
## Branch: feature/defer-seam-bugfix
## Commits: 1a2a2353, 6a8e99be

---

## Bug Coverage: 9/9 PASS (80 tests)

All 9 bugs (F2, F5, F6, F8, F10, F12, F13, F14, F15) verified via pre-written tests.

### Test File Mapping

| Bug(s) | Test File | Test Classes |
|--------|-----------|-------------|
| F2 | test_seam_invariants.py | TestDeferIdleGateActiveNonDeferredWork, TestDeferIdleGateReleasesAfterIdle, TestDeferQueueJobSpawnsDeferredTask, TestF2MaintenanceIsIdleJobItemGate |
| F5/F10 | test_seam_invariants.py | TestPeriodicDriftReconciler |
| F5/F10 (recovery) | test_job_recovery_service.py | TestJobRecoveryStartup, TestJobRecoveryServiceHelpers, TestFailOrphanedJobLockOrdering |
| F6 | test_seam_invariants.py | TestF6WatcherMigratesOnRetry |
| F8 | test_seam_invariants.py | TestF8SecondDeferIdleGateObserverPath |
| F12 | test_seam_invariants.py | TestF12StalePendingCancelledOnRetry |
| F13 | test_observer_hardening_f13_f14_f15.py | TestF13ExactJobResolution |
| F14 | test_observer_hardening_f13_f14_f15.py | TestF14BusGateSeesPendingTasks |
| F15 | test_observer_hardening_f13_f14_f15.py | TestF15DeferredFinalizeToctouGuard |

---

## Key Architectural Patterns Verified

### C1 Ordering (Finalize-First)
- `test_reconciler_c1_ordering_survives_finalize_failure` proves: finalize FIRST, then cancel. If finalize fails, state remains recoverable. If cancel-first were used and finalize failed, task=CANCELLED would be invisible to all reconciler patterns → unrecoverable wedge.

### F13 Three-Layer Defense
Exact job_id resolution + counting gate + TOCTOU guard = comprehensive premature finalization prevention:
- F13: `get_active_by_instance(instance_id, job_id=specific_id)` returns exact job, not freshest-by-created_at
- F14: Counts PENDING tasks not in dependency_watchers, defers if >0 (dual-layer: sync helper + WriteGuardSession)
- F15: `expected_job_id` in deferred finalize prevents finalizing a NEW JobItem created during sleep window

### F6 Atomic Watcher Migration
- Watcher rows migrate from parent `work_id` to child `work_id` in the SAME transaction as retry INSERT
- `notify_work_watchers(child_work_id)` correctly invokes migrated watchers

### F12 Cancel-Before-Start Ordering
- `cancel_pending_tasks_for_instance` runs BEFORE `start_job` during retry
- Only PENDING tasks cancelled, RUNNING siblings untouched

---

## Known Flaky Test

`test_atomic_retry_concurrent_calls_only_one_succeeds` in `test_job_retry_engine.py` is a timing-sensitive concurrency stress test. Fails ~once in full-suite context, passes consistently in isolation. Pre-existing, not Phase 3 related.

---

## Regression: Zero New Failures

Full `tests/job_queue/` suite: 1314 passed, 38 skipped, 0 failures.
Cross-cutting observer/concurrency: 74 passed, 48 skipped (pre-existing skips), 0 failures.

Phase 1 (F1, F3, F4, F7) + Phase 2 (review fixes) + Phase 3 (F2, F5, F6, F8, F10, F12, F13, F14, F15) all pass together.
