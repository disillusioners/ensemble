# VSCode Crash Fix — E2E Edge Case: user_stopped Not Reset by stop()

**Date:** 2026-08-02
**Branch:** `feature/vscode-crash-fix`
**Commit:** `8d4c1b07` (E2E test pack)
**Severity:** 🟡 Pre-existing behavior (not a regression from this fix)

## Discovery

During browser automation E2E testing of the VSCode crash fix, an edge case surfaced in the interaction between `stop()` and `start()`:

1. Daemon starts code-server fresh via `manager.start()`
2. User calls `manager.stop()` → sets `user_stopped=True`
3. User calls `manager.start()` again → **short-circuits** because the `user_stopped` guard in `start()` (added in this branch) blocks it

The `user_stopped` flag is set by `stop()` but is NOT explicitly reset by `stop()`. It's designed to be reset by a successful `start()` — but the guard in `start()` returns early BEFORE reaching the reset point, creating a catch-22 for the direct `manager.start()` path.

## Root Cause

The `start()` method (line ~202) has this guard:
```python
if self.state.user_stopped or self.state.status == "stopping":
    return self.state
```

The `user_stopped` flag is only reset LATER in `start()` after a successful spawn. But since the guard returns early, the reset never executes, so `user_stopped` stays True across stop/start cycles when called directly on the manager.

## Why It's Pre-Existing

This is by design for the watchdog race protection (preventing restart→immediate-teardown cycles). The normal flow uses the API endpoint `POST /api/settings/vscode/start` which goes through a different code path that handles the reset. The direct `manager.start()` after `manager.stop()` was never the intended API contract.

## Workaround in E2E Test

The E2E test (`vscode_e2e_browser_test.py`) handles this by:
1. Using `POST /api/settings/vscode/stop` endpoint (not direct manager call)
2. Ordering crash-recovery scenario (3) BEFORE the stop scenario (2) to avoid the race

## Recommendation

This is a minor UX issue worth documenting for users who might call `manager.start()` directly after `stop()`. Not blocking for this fix. The API endpoints handle it correctly.

## Before/After

No code change — pre-existing behavior documented. The `start()` guard is correct for its primary purpose (watchdog race protection).
