# Test Report: Instance Refresh Filter Fix
Date: 2026-05-26
Scope: Frontend-only fix — instance refresh button respects project tab filter

## Summary
- **Frontend Unit Tests**: 723/723 PASS (18 suites, 3.58s)
- **Backend Tests**: SKIPPED (frontend-only change, no backend files modified)
- **ensure.md**: PASS — dev.sh stable for 30s (exit 124, no errors)
- **Quick Fixes Applied**: 0
- **Overall Status**: ✅ READY

## Changes Under Test
1. `frontend/src/app/services/instance.service.ts` — `currentProjectId` now has public getter (was private field only)
2. `frontend/src/app/components/instance-list/instance-list.component.ts` — `onRefresh()` now passes `this.instanceService.currentProjectId ?? undefined` to `loadInstances()`

## Test Scope
- **Ran**: `frontend_unit_test` (723 tests, 18 suites)
- **Skipped**: All backend packs (core_unit_test, api_unit_test, job_queue_unit_test, etc.) — no backend files changed

## Frontend Unit Test Results
| Metric | Result |
|--------|--------|
| Test Suites | 18 passed, 18 total |
| Tests | 723 passed, 723 total |
| Failed | 0 |
| Errors | 0 |
| Time | 3.58s |

### Key Specs Verified
- ✅ `instance.service.spec.ts` — all tests pass (965 lines)
- ✅ `instance-list.component.spec.ts` — all tests pass (627 lines)

## ensure.md Validation
- ✅ dev.sh ran stably for 30 seconds (exit code 124 = timeout killed)
- No errors during startup or runtime
- Clean graceful shutdown after timeout

## Conclusion
The fix is minimal and correct — 0 regressions, all frontend tests pass, backend stable.
