# Test Report: Instance List Scroll Fix
Date: 2026-05-25
Branch: feature/instance-list-scroll-fix
Commits: 06464dc, 5ec6cd2, b1e3cd4 (test)

## Summary
- **Total Tests**: 679 (661 existing + 18 new)
- **Passed**: 679
- **Failed**: 0
- **Errors**: 0
- **Skipped**: 0
- **Quick Fixes Applied**: 0
- **ensure.md**: ✅ PASS

## What Was Tested

### Changes on this branch:
1. **Scroll position preservation** — saves `scrollTop` before data refresh, restores via `requestAnimationFrame` after load. Listener cleanup in `ngOnDestroy`.
2. **Polling interval** — changed from 10s to 60s in `instance.service.ts`
3. **Manual refresh button** — added to instance list with spinning icon during load
4. **input()/output() restoration** — commit 5ec6cd2 restored accidentally removed declarations

### Test Coverage Analysis

#### Gaps Identified and Fixed (18 new tests):

**`instance-list.component.spec.ts` — 17 new tests:**
- `isRefreshing signal` (3 tests): default state, true during refresh, false after completion
- `onRefresh` (5 tests): isRefreshing before load, saveScrollPosition call, loadInstances call, reset after load, error handling
- `saveScrollPosition` (2 tests): saves scrollTop, handles undefined container
- `scrollHandler` (3 tests): tracks scrollTop, sets/clears isScrolledByUser
- `ngOnDestroy` (2 tests): removes listener, handles null container
- `scroll restoration effect` (2 tests): restores via requestAnimationFrame, skips when scrollTop is 0

**`instance.service.spec.ts` — 1 new test:**
- `POLLING_INTERVAL` (1 test): verifies value is 60000ms

## Unit Test Results
- **Frontend Unit Tests**: ✅ 679/679 PASS (661 existing + 18 new)
- **Pre-existing e2e suite issue**: 2 e2e test suites have Playwright/Jest config issue (unrelated to this branch)

## ensure.md Validation
- ✅ dev.sh ran stable for 30 seconds (exit code 124 = timeout killed it)
- All services initialized: API on port 8079, RAG, MCP, worker pool, job recovery

## Commits
- `b1e3cd4` — test: add tests for scroll preservation and refresh button

## Overall Status: ✅ READY
- All 679 frontend tests pass (0 regressions, 18 new tests added)
- dev.sh stable for 30s
- No quick fixes needed
