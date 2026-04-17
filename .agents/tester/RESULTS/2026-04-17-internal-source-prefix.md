# Test Report: Internal Source Prefix Fix

**Date:** 2026-04-17
**Branch:** fix/internal-source-prefix
**Sessions:** verify-dispatcher, run-unit-tests, ensure-md

## Summary
- **Unit Tests:** ✅ PASS — 1688 tests (14 skipped, 0 failed)
- **Code Verification:** ✅ PASS — Dispatcher logic correct, consistency fixed
- **ensure.md:** ✅ PASS — dev.sh runs for 30s without crash
- **Quick Fixes Applied:** 1 commit (5 remaining old source IDs in manager.py)

## Code Verification Results

### Part 1: Dispatcher Logic ✅
- `dispatch_completed()` in `daemon/sources/dispatcher.py:78-159`
- Internal prefix check at **lines 131-133** happens BEFORE adapter lookup (line 136)
- Only DEBUG log emitted for internal sources — no ERROR log
- Logic is correct: `source_id.startswith("internal_")` → skip adapter lookup

### Part 2: Consistency — Fixed ✅
Found 5 remaining old source IDs in `manager.py` that were missed:

| Location | Old → New |
|----------|-----------|
| `manager.py:884` | `"report:"` → `"internal_report:"` |
| `manager.py:888` | `"error_report:"` → `"internal_error_report:"` |
| `manager.py:892` | `"agent:"` → `"internal_agent:"` |
| `manager.py:1043` | `"report:"` → `"internal_report:"` |
| `manager.py:1044` | `"error_report:"` → `"internal_error_report:"` |

**Fix committed** by opencode session.

### Part 3: New Source IDs ✅
11 occurrences now correctly using `internal_agent:`, `internal_report:`, `internal_error_report:` throughout `manager.py` and `tools/instance.py`.

## Unit Test Results

| Pack | Status | Tests Run | Tests Passed | Tests Failed |
|------|--------|-----------|--------------|--------------|
| sources_unit_test | ✅ PASS | 110 | 110 | 0 |
| core_unit_test | ✅ PASS | 569 | 569 | 0 |
| compaction_unit_test | ✅ PASS | 171 | 171 | 0 |
| job_queue_unit_test | ✅ PASS | 852 | 838 + 14 skipped | 0 |

**Total: 1688 tests, 1688 passed (14 skipped), 0 failed**

## ensure.md Validation ✅
- `dev.sh` ran for full 30 seconds without crash
- Exit code: 124 (timeout killed it → server ran fine)
- Clean startup and shutdown

## Quick Fixes Applied
- **verify-dispatcher session**: Fixed 5 remaining old source IDs in `manager.py`
  - Root cause: Initial rename missed `manager.py` lines 884, 888, 892, 1043, 1044
  - Fix: Updated to `internal_report:`, `internal_error_report:`, `internal_agent:`
  - Committed with appropriate message

## Overall Status: ✅ READY FOR MERGE

All verifications pass:
1. ✅ Core logic — `internal_` prefix check before adapter lookup
2. ✅ Consistency — All source IDs now use `internal_` prefix (after quick fix)
3. ✅ No regressions — All 1688 unit tests pass
4. ✅ ensure.md — Server runs cleanly
