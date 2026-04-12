# SSE Message Unification — Test Fixes Required

## Date: 2026-04-12

## Issue
When running the full test suite on the `feature/sse-message-unification` branch, 4 existing test files failed due to API changes from the feature.

## Root Causes

### 1. New InstanceStatus enum value
- `tests/test_models.py` expected 6 statuses, now there are 7
- Fix: Updated expected count to 7

### 2. terminate_instance() became async
- `tests/test_manager.py` called `terminate_instance()` without `await`
- `tests/test_api.py` used `Mock` instead of `AsyncMock`
- Fix: Added `@pytest.mark.asyncio` decorator and `await`, switched to `AsyncMock`

### 3. Async mock alist() iterator
- `tests/test_persistence.py` mocked `alist()` returning coroutine instead of async iterator
- Fix: Created `EmptyAsyncIterator` class for proper async iteration

## Lesson
When adding new enum values or changing method signatures (sync→async), existing tests that assert on counts or call patterns will break. Always run the FULL test suite, not just new tests.

## Commit
`7f39b28` — test: fix async mock and status count issues
