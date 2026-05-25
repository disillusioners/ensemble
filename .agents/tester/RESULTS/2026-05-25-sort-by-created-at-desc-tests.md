# Test Report: Instance Sort Fix — sortByCreatedAtDesc Utility Tests

**Date**: 2026-05-25
**Session**: ses_1a16dfe66ffe9FDy6Vx39MlN90 (tests) + ses_1a16aa330ffespT2sk2eYpaXLR (ensure.md)

## Summary
- **TypeScript Compilation**: ✅ PASS
- **New Tests**: 9/9 PASS
- **Full Frontend Suite**: 689/689 PASS (0 failures)
- **ensure.md**: ✅ PASS — dev.sh stable 30s+
- **Quick Fixes**: 3 (test helper alignment)
- **Commit**: bbe2da1 — `test: add sortByCreatedAtDesc unit tests`

---

## 1. TypeScript Compilation
- `npx tsc --noEmit` — **PASS** (no errors)

## 2. New Unit Tests Added

Added `describe('sortByCreatedAtDesc', ...)` to `frontend/src/app/services/instance.service.spec.ts`:

| # | Test Case | Result |
|---|-----------|--------|
| 1 | Sort instances by created_at descending (newest first) | ✅ PASS |
| 2 | Treat null created_at as oldest (sorted to end) | ✅ PASS |
| 3 | Treat undefined created_at as oldest (sorted to end) | ✅ PASS |
| 4 | Merge scenario: local SSE + API instances sorted by created_at desc | ✅ PASS |
| 5 | Pagination scenario: page 1 + page 2 sorted correctly | ✅ PASS |
| 6 | SSE out-of-order arrival: later instance sorts first | ✅ PASS |
| 7 | Does not mutate the original array | ✅ PASS |
| 8 | Handle empty array | ✅ PASS |
| 9 | Handle single element array | ✅ PASS |

## 3. Full Frontend Test Suite
- **Test Suites**: 18 passed
- **Tests**: 689 passed
- **Failures**: 0

## 4. Quick Fixes Applied
1. Added `sortByCreatedAtDesc` to `TestableInstanceService` in spec (mirrors actual service)
2. Updated `mergeInstances` in test helper to use `sortByCreatedAtDesc` (was missing sort call)
3. Fixed undefined `created_at` test by creating instances directly

## 5. ensure.md Validation
- dev.sh ran 30 seconds without crash — **PASS**
- Clean startup, no errors, graceful shutdown

---

## Overall Status: ✅ READY
- TypeScript compilation: PASS
- New sort tests: 9/9 PASS
- Regression: 689/689 PASS (0 failures)
- ensure.md: PASS
- Commit: bbe2da1
