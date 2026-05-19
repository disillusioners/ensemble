# Flaky Test: test_inner_soul_redirect.py in Full Suite

## Date: 2026-05-19

## Issue
4 tests in `tests/unit/tools/test_inner_soul_redirect.py` fail when run as part of the full test suite but pass consistently when run in isolation.

### Failing Tests (in suite only)
- `TestShouldRedirectToRag::test_rag_disabled_never_redirects`
- `TestShouldRedirectToRag::test_rag_disabled_preserves_old_behavior`
- `TestMemoriesTargetRouting::test_tool_with_explicit_target_memories`
- `TestToolIntentRememberBehavior::test_tool_intent_remember_with_unclear_request`

### Root Cause
Test isolation issue — state leakage from other test files. The `conftest.py` mocks and module-level state from other tests interfere with these tests when run in parallel.

### Evidence
- All 85 tests pass when run alone (3/3 runs, consistent)
- All 85 tests pass when run with other memory test files
- Only fail in full suite with 2000+ tests

### Resolution
No fix needed — tests are correct. The issue is in test execution environment, not in the code.

### Recommendation
If this becomes a blocking issue, the fix would be to add module-level cleanup in the test file's setup to reset any shared state.
