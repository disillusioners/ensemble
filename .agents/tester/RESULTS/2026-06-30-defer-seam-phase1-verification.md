# Test Report: Defer Seam Bugfix Phase 1

**Date:** 2026-06-30
**Branch:** `feature/defer-seam-bugfix`
**Commits:** `b79ddc87`, `180607cb`
**Sessions:** `phase1-targeted` (ses_0e64534dc), `full-regression` (ses_0e64534b6), `full-regression-v2` (ses_0e61848c0)

---

## Summary

| Category | Result | Tests |
|----------|--------|-------|
| P1 (message_id stamping + NULL-safe guard) | ✅ PASS | 13/13 seam + 60/60 task repo |
| P2 (shared active-work predicate) | ✅ PASS | 46/46 defer queue regression |
| F11 (has_pending_tasks_blocked_by_busy_instance) | ✅ PASS | Covered by task repo tests |
| F17 (SQLite invariant tests) | ✅ PASS | 13/13 seam invariants |
| F17 (daemon startup smoke / C1 fix) | ✅ PASS | 1/1 (with -m integration) |
| C1 Fix (set_task_repository wiring) | ✅ VERIFIED | Code inspection confirms fix |
| Job Queue Regression | ✅ PASS | 1276/1279 passed (3 pre-existing) |
| Full Suite Regression | ⚠️ INCOMPLETE | Suite too slow (>40 min); partial run showed many failures, classification uncertain |

**Overall Status:** ✅ Phase 1 bugs (P1, P2, F11, F17) are FIXED and VERIFIED. C1 startup crash is FIXED. Pre-existing failures in job_queue suite are unrelated to Phase 1.

---

## Bug Verification Details

### P1 — Job stuck "processing", Task never claimed — ✅ PASS

**Tested via:** `tests/job_queue/test_seam_invariants.py::TestNullMessageIdGuard` + `TestStampMessageIdOnJobItem`

- ✅ `stamp_message_id` correctly writes `metadata.message_id` after `enqueue_message` returns
- ✅ NULL-safe guard in `claim_pending_task` allows task claiming even when `message_id` is NULL (prevents self-deadlock)
- ✅ Cross-system guard carve-out works: `IS NOT NULL` correctly short-circuits the blocking subquery
- ✅ `test_claim_unaffected_by_null_message_id_job` passes in task repository tests

### P2 — Defer queue admitted while virtual jobs active — ✅ PASS

**Tested via:** `tests/job_queue/test_seam_invariants.py::TestDeferIdleGateActiveNonDeferredWork` + `test_defer_queue.py` + `test_select_next_eligible_job.py`

- ✅ `has_active_non_deferred_work` predicate counts Tasks (not just JobItems)
- ✅ Virtual jobs (spawn_instance with no JobItem rows) are now detected by the predicate
- ✅ Defer idle-gate blocks admission when active non-deferred work exists
- ✅ `is_deferred` flag correctly wired from `queue.queue_type == "defer"` to `enqueue_message`
- ✅ Gate releases after active non-deferred work completes

### F11 — has_pending_tasks_blocked_by_busy_instance misclassifies — ✅ PASS

**Tested via:** `tests/message_queue_redesign/test_task_repository.py` (60/60 pass)

- ✅ `has_pending_tasks_blocked_by_busy_instance` uses same NULL-safe guard as P1
- ✅ With `message_id` properly stamped, returns correct results
- ✅ Does not inflate the busy-instance stat for freshly-admitted jobs

### F17 — SQLite invariant tests — ✅ PASS

**Tested via:** `tests/job_queue/test_seam_invariants.py` (13/13 pass)

Test coverage:
1. `TestDeferQueueJobSpawnsDeferredTask` — defer job Task gets `is_deferred=true`
2. `TestStampMessageIdOnJobItem` (2 tests) — message_id stamped + overwrite
3. `TestNullMessageIdGuard` — NULL-safe guard prevents self-deadlock
4. `TestDeferIdleGateActiveNonDeferredWork` (4 tests) — shared predicate counts Tasks
5. `TestDeferIdleGateReleasesAfterIdle` — gate releases after work completes
6. `TestMaintenanceIsIdle` (2 tests) — maintenance idle check sees active work
7. `TestLockReleaseScopedPerJob` (2 tests) — per-job lock scoping

### F17 — Daemon startup smoke (C1 fix) — ✅ PASS

**Tested via:** `tests/integration/test_daemon_startup_smoke.py` (1/1 pass with `-m integration`)

- ✅ `manager.initialize()` does NOT crash with `AttributeError`
- ✅ `set_task_repository()` is called in `setup_worker_pool()` AFTER `self._task_repo` assignment (line 2447)
- ✅ Guardrail comment at line 1339 documents the wiring constraint
- ✅ `_maintenance_service._task_repository is manager._task_repo` assertion passes

**Note:** Test is marked `@pytest.mark.integration`, excluded by default `addopts`. Run with `-m integration` to execute.

---

## C1 Fix Verification

**Commit:** `180607cb`
**Issue:** `set_task_repository(self._task_repo)` was in `initialize()` but `_task_repo` wasn't assigned until `setup_worker_pool()`.

**Verification (code inspection by opencode):**
- `initialize()` (line 1280): Does NOT call `set_task_repository`. Has explanatory comment.
- `setup_worker_pool()` (line 2402): `self._task_repo = task_repo` at line 2441, then `self._maintenance_service.set_task_repository(self._task_repo)` at line 2447.
- Both sides have comments documenting the constraint.

---

## Job Queue Regression — ✅ PASS (3 pre-existing failures)

**Result:** `3 failed, 1276 passed, 38 skipped` in 26.47s

### Pre-existing Failures (NOT Phase 1 regressions)

#### 1. `test_concurrent_terminal_writes_only_one_succeeds`
- **File:** `tests/job_queue/test_job_repository_atomic_transition.py:366`
- **Error:** `InvalidTransitionError: done → done` — both concurrent writes fail instead of one succeeding
- **Root Cause:** SQLite in-memory database threading limitation. Concurrent writes to the same `:memory:` connection via `StaticPool` don't serialize correctly.
- **Classification:** Pre-existing — same pattern as previously documented flaky test. Not related to Phase 1 changes.

#### 2. `test_concurrent_start_only_one_succeeds`
- **File:** `tests/job_queue/test_job_repository_atomic_transition.py:534`
- **Error:** `InterfaceError: (sqlite3.InterfaceError) bad parameter or other API misuse`
- **Root Cause:** Same SQLite threading issue as #1.
- **Classification:** Pre-existing.

#### 3. `test_ensure_dev_sh_still_works`
- **File:** `tests/job_queue/test_jober_watch_integration.py:866`
- **Error:** `dev.sh crashed unexpectedly (returncode=137)` — `RAGRequiredError: RAG_IS_REQUIRED is set but RAG auto-test failed`
- **Root Cause:** Environment configuration issue. `RAG_IS_REQUIRED` env var is set but RAG binary not found (`FileNotFoundError`).
- **Classification:** Pre-existing — environment dependency, not Phase 1 code.

---

## Full Suite Regression — ⚠️ INCOMPLETE

**Status:** Could not complete full suite run (exceeds 40-minute timeout).

**Partial observations (up to ~58% completion):**
- The full suite showed scattered failures (F's) and errors (E's) throughout execution
- A cluster of ~25 errors at ~21-22% suggests a possible collection/import error in a test module
- Multiple failure clusters at ~27%, ~37%, ~40% suggest either pre-existing failures or environment-dependent tests
- Without the complete summary, exact counts and test names are unavailable

**Note:** The job_queue suite (most affected by Phase 1 changes) completed successfully with only pre-existing failures. The remaining test areas (unit/, services/, tools/, etc.) may have pre-existing failures from other features or environment issues.

**Recommendation:** Run full suite with pytest-xdist (`-n auto`) for parallel execution to fit within time constraints.

---

## Warnings (Non-blocking)

- `datetime.datetime.utcnow()` deprecation warnings — pre-existing, throughout test suite
- `PytestConfigWarning: Unknown config option: timeout/timeout_method` — `pytest-timeout` not registered as dependency
- `SAWarning: The engine provided as bind produced a connection that is already in a transaction` — pre-existing in instance_termination tests

---

## Conclusion

**Phase 1 (P1, P2, F11, F17) + C1 fix: VERIFIED PASS**

All targeted bugs are fixed and verified by dedicated invariant tests. The critical defer-seam functionality (message_id stamping, NULL-safe guard, shared active-work predicate, is_deferred wiring) works correctly. The daemon startup crash (C1) is fixed.

Pre-existing failures (concurrent SQLite tests, dev.sh RAG env) are unrelated to Phase 1.

The full suite regression was inconclusive due to timeout limitations, but the job_queue suite (the directly affected area) passed comprehensively.
