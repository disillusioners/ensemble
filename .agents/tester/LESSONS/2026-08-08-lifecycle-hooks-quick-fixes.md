# LESSON: Quick fixes during Instance Lifecycle Hooks testing

**Date:** 2026-08-08
**Feature:** Instance Lifecycle Hooks
**Commit:** f69c6885 on branch `feature/instance-life-circle-hooks`

## Fixes Applied

### 1. `test_context_key.py:303` — `_restore_instance` async migration
- **Root cause:** `_restore_instance` was converted to `async` during the Instance Lifecycle Hooks feature. The test called it synchronously, so the coroutine was never awaited and the mock never fired.
- **Fix:** Wrapped the call with `asyncio.run(...)`.
- **Lesson:** When a production function is converted to async, ALL test call sites must be updated to `await` or `asyncio.run()`.

### 2-4. `test_context_injection.py` (3 tests) — Env var leak
- **Root cause:** Shell env var `HEURISTIC_MATCH_SHARED_MD_FILES_DEBUG=true` leaked into non-debug-path tests, causing them to assert debug output instead of production output.
- **Fix:** Added `monkeypatch.delenv("HEURISTIC_MATCH_SHARED_MD_FILES_DEBUG")` to 3 test functions (lines 906, 1104, 1133).
- **Lesson:** Tests sensitive to env vars should explicitly clean their environment via `monkeypatch.delenv()` to ensure isolation regardless of the shell state.

## Files Changed
- `tests/unit/test_context_key.py` — 1 asyncio.run() wrapper
- `tests/unit/services/test_context_injection.py` — 3 monkeypatch.delenv() calls
- 2 files changed, 10 insertions(+), 5 deletions(-)
