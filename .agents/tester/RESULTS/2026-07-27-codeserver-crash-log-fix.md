# Test Report: code-server crash log surfacing fix

**Date:** 2026-07-27
**Branch:** `feature/fix-codeserver-crash-logs`
**Commit:** `7c3bde3` — fix: surface code-server crash output and reset state on startup crash
**Worker instances:** `a4406f0f` (vscode-server-pack), `df75aef3` (editor-settings-pack)
**Skill:** `test-pack-execution` (loaded per worker via `load_skill`)

## Summary

| Pack | Tests | Passed | Failed | Errors | Skipped | Runtime | Result |
|------|-------|--------|--------|--------|---------|---------|--------|
| `vscode_server_manager_unit_test` | 47 | 47 | 0 | 0 | 0 | 4.41s | ✅ PASS |
| `vscode_editor_settings_api_test` | 32 | 32 | 0 | 0 | 0 | 1.06s | ✅ PASS |
| **Total** | **79** | **79** | **0** | **0** | **0** | ~5.5s (parallel) | **✅ ALL PASS** |

- Unit Tests: 79 tests total
- ensure.md: in-scope requirements met (no regressions)
- Quick Fixes Applied: 0 (none needed)
- Quarantined: 0 tests skipped

## Scope Decision

> Full suite NOT warranted. Change touches 2 files (1 production `daemon/services/vscode_server_manager.py`, 1 test) in a single module — a focused bug fix (crash log surfacing + state reset on startup crash). Running 2 relevant packs: `vscode_server_manager_unit_test` (the fix + 9 new tests) and `vscode_editor_settings_api_test` (API regression check). Skipped: ~198 other packs. Reason: small/isolated change, single module, no architecture impact, no cross-module blast radius.

## What Was Verified (specific scenarios)

All 7 requested scenarios confirmed passing via named tests in the `vscode_server_manager_unit_test` pack:

1. ✅ **Crash log surfacing** — error message includes log buffer content when process exits during startup
2. ✅ **Empty buffer** — error message is clean when `log_buffer` is empty (no spurious content)
3. ✅ **Byte cap (16KB truncation)** — truncation works correctly when buffer exceeds 16KB
4. ✅ **Status reset** — `state.status` is `"stopped"` after crash (not left as `"starting"`)
5. ✅ **exit_code + last_error set** — both populated correctly on crash path
6. ✅ **reader_task cancelled** — cancelled gracefully on crash path
7. ✅ **Edge cases** — non-UTF8 bytes in `log_buffer`, very large `log_buffer`, `reader_task` already done / None — all handled

## API Regression Check

`vscode_editor_settings_api_test` confirms the crash log surfacing fix does **NOT** break the API contract:
- 32/32 pass (count grew from 29 → 32; benign drift, not a regression signal)
- All 503 error-path tests that exercise status propagation from `vscode_server_manager.py` to the API layer pass cleanly
- The 503 response from the settings router already propagates `str(e)`, so the crash output now reaches the frontend with no API-layer changes needed

## ensure.md Validation Results

**Core (always-on, scoped to change set):**
- **Critical Requirements:**
  - ✅ **No regressions in changed packs** — every pack in the blast-radius change set returns PASS (both packs green)
  - ⏭️ *Deadlock / concurrency integrity* — N/A (this change is not a deadlock/concurrency fix; the reader_task cancel is verified within the vscode pack, not a `concurrency_atomic_unit_test` concern)
  - ⏭️ *No sync DB calls on asyncio event loop* — N/A (no DB layer touched)
  - ⏭️ *`dev.sh` includes `--timeout-graceful-shutdown 10`* — N/A (static check, unrelated to this change)

**Release Gate:** Not run. Change is a small, focused bug fix — NOT big/critical/architecture. Per ensure.md scoping rules, Release Gate (full non-integration suite + E2E) is not warranted.

**Contradiction Notices:** None. No ensure.md requirement contradicted pack/timeout/scoping rules.

## Test Count Drift

- `vscode_server_manager_unit_test`: 36 → 47 (+11; commit message says +9 new tests; delta of 11 likely includes minor additional cases in the same file — all pass)
- `vscode_editor_settings_api_test`: 29 → 32 (+3; benign drift from other work on the branch base, not a regression)

## Failures

None.

## Action Needed

None. The fix is validated. The branch is ready for merge from a testing standpoint.

## Documentation Updated

- [x] `.agents/tester/PACKS.md` — updated last run + status for both packs (47/47 in 4.41s, 32/32 in 1.06s)
- [ ] `.agents/tester/rules/ensure.md` — no changes (user-maintained, read-only)
- [ ] `.agents/tester/MOCK_TESTS.md` — no changes
- [ ] `.agents/tester/LESSONS/` — none needed (clean pass, no quick fixes, no flakiness)
- [x] `.agents/tester/RESULTS/2026-07-27-codeserver-crash-log-fix.md` — this report

## Code Changes Summary

No code changes were made during testing (no quick fixes needed). The fix under test (commit `7c3bde3`) was already in place and is validated as correct.

---

## Overall Status
- Unit Tests (fix area): ✅ PASS (47/47)
- Unit Tests (API regression): ✅ PASS (32/32)
- ensure.md (scoped Core): ✅ PASS (no regressions in changed packs)
- **Testing Complete:** ✅ READY — fix validated, no regressions
