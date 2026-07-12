# Lesson: OpenCode Error Propagation Testing

**Date:** 2026-07-11
**Feature:** OpenCode Error Propagation Fix (feature/opencode-error-propagation)
**Commit:** 9dbfae2d

## What Was Tested

The fix adds `has_latest_error()` and `get_latest_error()` to `OpenCodeSessionManager` and makes `wait_for_result`/`get_status` check `_latest_response` for `{"error": "..."}` dicts to surface worker HTTP 500 errors to agents.

## Key Insights

### 1. Test Architecture Pattern — Mocking the Fire-and-Forget Pattern
The OpenCode session manager uses a fire-and-forget pattern: `submit_request()` queues the request and returns `status="ok"` immediately. The worker then attempts the HTTP call asynchronously. This means tests must mock at TWO levels:
- `_server_send_message` — the HTTP call layer (returns `OpenCodeResponse`)
- `mock_manager` / `mock_registry` — the session manager and registry

The error scenario requires constructing an `OpenCodeResponse` with `status="ok"` but `data.latest_response = {"error": "..."}` — this simulates the real-world case where the server accepted the request but the worker failed.

### 2. Fixture Pattern — Not All Fixtures Are in conftest.py
The `mock_manager` and `mock_registry` fixtures used in `tests/opencode/test_tools.py` are defined LOCALLY in that file, not in `tests/opencode/conftest.py`. When writing a new test file that needs these fixtures, either:
- Copy the fixtures verbatim into the new file (what the edge case tests did)
- Or move them to conftest.py for sharing

### 3. Edge Cases That Were Missing from the Original Commit
The original commit (9dbfae2d) added 12 tests but missed these edge cases:
- **Error → Recovery**: After an error, a new successful request should clear the error and return normally
- **Concurrent wait_for_result**: Two concurrent calls should BOTH get the error
- **Resume → Error**: Resume triggering a worker failure → wait_for_result surfaces error
- **get_status recovery**: Status shows [ERROR] then normal after recovery
- **No success tokens in error**: Error result must NOT contain [COMPLETED] or [RESUMED]

These were added in `tests/opencode/test_error_propagation_edge_cases.py` (commit c786373d).

### 4. Pre-existing Test Failures — Baseline Verification
The full non-integration test suite has 74 pre-existing failures + 5 SSE test hangs in unrelated modules. These are NOT caused by the opencode fix. Verification method:
- Check out the parent commit (`d75579f7`)
- Run the same failing tests
- Confirm they fail identically

This is critical for blast-radius assessment — without baseline verification, pre-existing failures could be falsely attributed to the new change.

### 5. Stale Test Expectations — A Common Quick Fix Pattern
Commit `bfda2a95` removed the `chart` innate skill from the tester agent, but `test_innate_skills_refactoring.py` still expected it. This is a common pattern when configuration changes outpace test updates. Quick fix: remove the stale expectation (3 lines, no source changes).

## Test Pack Created
- `test/packs/opencode_native_tools_unit_test.sh` — runs all `tests/opencode/` (excluding `test_integration.py`) with 180s internal timeout
- 481 tests, ~37s runtime
- Excludes `test_integration.py` (requires live OpenCode server, marked `@pytest.mark.integration`)

## Recommendations
1. **Always write edge case tests** for error handling — the happy path and basic error path are not enough
2. **Copy fixture patterns** from existing test files when creating new test files in the same directory
3. **Verify pre-existing failures** against baseline when running the full suite for ensure.md validation
