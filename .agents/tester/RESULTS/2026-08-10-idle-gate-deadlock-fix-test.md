# Test Report: Idle-Gate Deadlock Fix
Date: 2026-08-10
Branch: `fix/idle-gate-deadlock`
Instance IDs: a6406793 (jobqueue), fc238ddb (concurrency), cd507190 (msg-regression), c6129424 (msg-routing), 0034b5ba (e2e-create), d09f3bc0 (e2e-run), 884b792c (ensure-static)

## Summary
- **Total tests: 1,640** | **Passed: 1,540** | **Failed: 0** | **Skipped: 113** (mostly PG-only on SQLite)
- **New tests: 37** (23 idle_gate_deadlock_fix + 14 E2E integration) — **ALL PASS**
- **Quick fixes applied: 2** (test-code only)
- **Quarantined: 0**
- **Overall Status: ✅ READY**

## Scope Decision
> Change touches 4 files across 3 modules (instance_messaging, task repository, job_queue repository) — a focused concurrency/deadlock fix, NOT a cross-module architecture change. Full suite NOT warranted. Scoped to 5 directly-affected packs + E2E creation. Release Gate NOT RUN (not architecture change). Running: job_queue_unit_test, concurrency_atomic_unit_test, instance_messaging_regression_test, instance_messaging_queue_routing_unit_test, idle_gate_e2e_integration_test. Skipped: all other packs (no changed files in those modules).

## Test Pack Results

| Pack | Tests | Result | Runtime | Notes |
|------|-------|--------|---------|-------|
| job_queue_unit_test | 1491 pass, 39 skip | ✅ PASS | ~23s | 23/23 new idle-gate tests pass; 0 regressions |
| concurrency_atomic_unit_test | 91 pass, 74 skip | ✅ PASS | ~8s | Quick fix 1c65fe76 (test-only): cross-system guard test aligned to post-self-deadlock behavior |
| instance_messaging_regression_test | 28 pass | ✅ PASS | 1.68s | Injection hooks intact |
| instance_messaging_queue_routing_unit_test | 16 pass | ✅ PASS | 1.13s | Queue routing unchanged |
| idle_gate_e2e_integration_test (NEW) | 14 pass | ✅ PASS | 0.11s | Quick fix f60ddfd6: bool() coercion for SQLite int→bool |

## E2E Integration Test (Key Deliverable)

**File:** `tests/job_queue/test_idle_gate_e2e_integration.py` (14 tests, 6 classes, commit f60ddfd6)

### Scenarios Verified:
1. **Defer queue flag propagation** (2 tests): Enqueue to defer queue → Task gets `is_deferred=True`, `is_background=False`; caller flags bypassed
2. **Background queue flag propagation** (2 tests): Enqueue to background queue → Task gets `is_background=True`, `is_deferred=False`; caller flags bypassed
3. **Normal queue passthrough** (1 parametrized ×2): FIFO/parallel queue → caller flags pass through unchanged
4. **Idle-gate deadlock scenario** (2 tests): Defer queue's own PENDING task with queued JobItem does NOT count as active non-deferred work; sibling PROCESSING task DOES count — **deadlock broken**
5. **Edge cases** (3 tests): No JobItem → counts normally; active JobItem → counts normally; queued JobItem + PENDING → excluded from idle-gate count
6. **Migration** (3 tests): File exists, applies cleanly on fresh SQLite, idempotent re-apply

### Migration Verified:
- File: `daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql`
- Applies cleanly on SQLite (tested in E2E)
- Idempotent re-apply confirmed

## Edge Case Testing (per task requirements)
All 4 edge cases from the task spec are covered in the E2E test file:
- ✅ Task with no linked JobItem → counts as active work normally
- ✅ Task with JobItem at `active` admission_state → counts normally
- ✅ Task with JobItem at `queued` admission_state + pending status → EXCLUDED from idle-gate count
- ✅ Normal (non-defer, non-background) queue behavior unchanged

## ensure.md Validation Results (blast-radius scoped: Core only)

### Critical Requirements: 4/4 passed
- ✅ No regressions in changed packs — all 5 packs PASS
- ✅ Deadlock / concurrency integrity — `concurrency_atomic_unit_test` PASS (91 pass, 74 skip, 0 fail)
- ✅ No sync DB calls on asyncio event loop — covered by concurrency pack (thread-identity tests)
- ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` — static check PASS (line 102)

### Important Requirements: 1/1 passed
- ✅ All callers of converted async functions properly await — static check PASS (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats` all properly awaited in changed files)

### Nice-to-have: 1/1 passed
- ✅ No dead code from the fix — static check PASS (`_derive_task_flags_from_queue_type()` defined and used; no orphaned functions)

### Release Gate: NOT RUN
Not warranted — focused concurrency fix, not cross-module architecture change.

## Quick Fixes Applied

1. **concurrency_atomic_unit_test — cross-system guard test alignment** (commit `1c65fe76`)
   - File: `tests/test_report_lane_phase2.py:504`
   - Root cause: Test ported from `tests/message_queue_redesign/test_task_repository.py` during branch development but missed the self-deadlock-fix exclusion (commit `338a72b0`) that landed ~1 day later. The cross-system guard now excludes the candidate's own row, so the test's manual alignment of `work_id == job_id` became a false positive.
   - Fix: Insert a PAUSED sibling Task instead of aligning the candidate's own work_id. PAUSED is in the cross-system guard's in-flight set but NOT in the per-instance guard's RUNNING-only set, isolating the cross-system guard correctly. +64/-12 lines, test-only.

2. **idle_gate_e2e_integration_test — SQLite bool coercion** (commit `f60ddfd6`)
   - File: `tests/job_queue/test_idle_gate_e2e_integration.py`
   - Root cause: SQLite stores BOOLEAN columns as INTEGER (0/1). Python `is True`/`is False` are identity-strict checks that fail on `1`/`0`. 8/14 tests initially failed.
   - Fix: Added `bool()` coercion after every raw SQL row unpack. Production code already coerces internally (which is why the 23 unit tests in test_idle_gate_deadlock_fix.py don't hit this — they use repository-layer access patterns). Test-only, <20 lines.

## Documentation Updated
- [x] PACKS.md — updated job_queue, concurrency, instance_messaging (×2) pack entries; added idle_gate_e2e_integration_test pack; updated summary count (248→249); added history entry
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no changes
- [x] RESULTS/2026-08-10-idle-gate-deadlock-fix-test.md — this report
- [x] LESSONS/2026-08-10-idle-gate-deadlock-quick-fixes.md — quick fix documentation

## Code Changes Summary (test-code only, all committed)
- `tests/test_report_lane_phase2.py` — cross-system guard test aligned to post-self-deadlock behavior (commit 1c65fe76)
- `tests/job_queue/test_idle_gate_e2e_integration.py` — NEW: 14 E2E integration tests (commit f60ddfd6)
- `test/packs/idle_gate_e2e_integration_test.sh` — NEW: pack script (commit f60ddfd6)
- Commit: f60ddfd6, 1c65fe76

---

### Overall Status
- **Unit/Regression Tests: ✅ PASS** (1,640 total, 0 failures)
- **E2E Integration Test: ✅ PASS** (14/14, deadlock scenario verified)
- **ensure.md: ✅ PASS** (4/4 Critical, 1/1 Important, 1/1 Nice-to-have)
- **Migration: ✅ PASS** (clean apply on SQLite, idempotent re-apply)
- **Testing Complete: ✅ READY**
