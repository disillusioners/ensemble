## Test Report: Child-Parent Source Propagation Fix
Date: 2026-04-17
Sessions: propagation-test, propagation-regression, ensure-propagation

### Summary
- **New Tests Added**: 7 (all PASS)
- **Total Progressive Dispatch Tests**: 32 (all PASS)
- **Existing Tests**: 704 passed across sources + core packs (0 failures)
- **Quick Fixes Applied**: 1 (narrowed internal source skip to only report/error_report)
- **Commits**: `020e60f` (coder's fix), `21ad4e1` (tests + dispatcher narrowing fix)
- **ensure.md**: PASS (dev.sh runs clean for 30s)

### Bug Fixed
After a child agent completed and reported back to parent, the parent's subsequent AI messages were not sent to Telegram. The original source (`telegram:123`) was lost because `internal_agent:` sources were incorrectly being skipped along with `internal_report:` and `internal_error_report:`.

### Regression Test Results

| Pack | Result | Passed | Failed | Skipped |
|------|--------|--------|--------|---------|
| sources_unit_test | ✅ PASS | 125 | 0 | 0 |
| core_unit_test | ✅ PASS | 579 | 0 | 0 |
| progressive_dispatch | ✅ PASS | 32 | 0 | 0 |

### New Tests Added to `tests/test_progressive_dispatch.py`

| # | Test | Description | Result |
|---|------|-------------|--------|
| 25 | `test_dispatch_message_internal_agent_dispatches_normally` | `internal_agent:*` dispatches normally, NOT skipped | ✅ PASS |
| 26 | `test_internal_report_skips_and_retrieves_original_source` | `internal_report:*` skips AND triggers source recovery | ✅ PASS |
| 27 | `test_internal_error_report_skips_and_retrieves_original_source` | Same for error reports | ✅ PASS |
| 28 | `test_manager_warns_when_original_source_not_found` | Warning logged when `original_source` missing | ✅ PASS |
| 29 | `test_full_chain_external_msg_to_telegram_after_child_completion` | Full chain: external → store → child inherits → report → telegram | ✅ PASS |
| 30 | `test_source_inheritance_grandchild_from_grandparent` | Multi-level spawning chain propagates source | ✅ PASS |
| 31 | `test_write_once_guard_persists_through_multiple_external_messages` | First source is sticky across messages | ✅ PASS |

### Quick Fix Applied

**File**: `daemon/sources/dispatcher.py`
**Issue**: The dispatcher was skipping ALL `internal_*` sources, but the fix requires only `internal_report` and `internal_error_report` to be skipped (NOT `internal_agent`). This was the root cause of the bug.
**Fix**: Changed from `source_id.startswith("internal_")` to `source_id in ("internal_report", "internal_error_report")` in both `dispatch_message()` and `dispatch_completed()`.
**Commit**: `21ad4e1`

### Test Focus Verification

| Focus Area | Verified | Tests |
|------------|----------|-------|
| `internal_agent:` dispatches normally (not skipped) | ✅ | #25 |
| `internal_report:` triggers source recovery from metadata | ✅ | #26 |
| `internal_error_report:` triggers source recovery from metadata | ✅ | #27 |
| Source inheritance: child gets parent's `original_source` | ✅ | #29, #30 |
| Write-once guard: `original_source` not overwritten | ✅ | #31 |
| Warning log when `original_source` not found | ✅ | #28 |
| Full chain: external → spawn → child report → telegram dispatch | ✅ | #29 |
| Narrowing correct: `internal_agent:` handled differently | ✅ | #25 |

### ensure.md Validation
- ✅ dev.sh runs for 30 seconds without crashing
- Server starts cleanly on http://127.0.0.1:8079
- All services initialized, graceful shutdown

### Overall Status
- Regression Tests: ✅ PASS (704 tests, 0 failures)
- Progressive Dispatch Tests: ✅ PASS (32/32)
- ensure.md: ✅ PASS
- **Testing Complete**: ✅ READY — Fix verified, no regressions
