# Lesson: Watchdog restart loop ignored user_stopped during inter-attempt backoff

**Date:** 2026-07-29
**Branch:** `feature/vscode-reliability-fixes`
**Commit:** `81e83473`
**Found by:** `vscode-mgr-unit` worker during pack execution

## Bug
The `VSCodeServerManager` watchdog restart loop (`_watchdog_loop`) had a `user_stopped` guard only at the outer `while`-loop level (line 965). The inner restart `for`-loop (iterating `VSCODE_RESTART_MAX_ATTEMPTS` times) did **not** re-check `state.user_stopped` between attempts.

**Impact:** If `stop()` was called during the exponential backoff sleep between restart attempts, the loop would still execute all 5 restart attempts — ignoring the user's explicit stop request. This directly violated the "user_stopped=True prevents restart" contract on a branch named `vscode-reliability-fixes`.

## Root Cause
The guard was placed at the loop level that checks "should we restart at all?" but not at the level that checks "should we continue restarting?" — a classic nested-loop guard omission.

## Fix
14 lines in `daemon/services/vscode_server_manager.py`:
- Added `user_stopped` guard at the top of the restart `for`-loop (mirrors the outer `while`-loop guard).
- Returns early with an info log when `user_stopped` is detected mid-backoff.

## How It Was Found
The worker wrote a test `test_watchdog_user_stopped_during_restart_backoff` that:
1. Triggers a crash
2. Lets the first restart attempt fail
3. Sets `user_stopped=True` during the backoff sleep
4. Asserts no further `start()` calls occur and status is not flipped to "crashed"

The test **failed** with 5 `start()` calls where 1 was expected — proving the bug. After the fix, all 61 tests pass.

## Pattern to Remember
**Nested-loop guards:** When a guard exists on an outer loop, always check if the inner loop also needs it. The restart loop is a `for` inside a `while` — both need the `user_stopped` check.

## Files Changed
- `daemon/services/vscode_server_manager.py` (+14 lines — source fix)
- `tests/unit/test_vscode_server_manager.py` (+100 lines — 2 new edge-case tests)
