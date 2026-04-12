# Quick Fixes: SSE Message Unification Testing (2026-04-12)

## Quick Fixes Applied

### 1. Persistence Tests - Async Mock alist() (commit 7f39b28)
**Problem**: `tests/test_persistence.py` had 9 failing tests
- Async mock `alist()` returned a coroutine instead of an async iterator
- `AsyncMock()` objects don't implement the async iteration protocol properly

**Fix**: Created `EmptyAsyncIterator` class:
```python
class EmptyAsyncIterator:
    def __aiter__(self):
        return self
    async def __anext__(self):
        raise StopAsyncIteration
```

**Files modified**: `tests/test_persistence.py`
**Tests affected**: 9 tests
**Root cause**: Pre-existing issue (not SSE-related)

### 2. Status Count Test (commit 7f39b28)
**Problem**: `tests/test_models.py::test_job_statuses`
- Expected 6 statuses, but 7 exist in `InstanceStatus` enum

**Fix**: Updated expected count from 6 to 7
**Files modified**: `tests/test_models.py`
**Root cause**: Pre-existing issue (not SSE-related)

### 3. Manager Tests - Async/Await (commit 7f39b28)
**Problem**: `tests/test_manager.py`
- `terminate_instance` is async but tests weren't awaiting it
- Missing `@pytest.mark.asyncio` decorator

**Fix**: Added `@pytest.mark.asyncio` and `await` for async calls
**Files modified**: `tests/test_manager.py`
**Root cause**: Pre-existing issue (not SSE-related)

### 4. API Tests - Mock vs AsyncMock (commit 7f39b28)
**Problem**: `tests/test_api.py`
- `terminate_instance` is async but mocked with `Mock` instead of `AsyncMock`

**Fix**: Changed `Mock` to `AsyncMock`
**Files modified**: `tests/test_api.py`
**Root cause**: Pre-existing issue (not SSE-related)

## Lesson Learned
These test failures were pre-existing and unrelated to the SSE message unification feature. They were likely introduced by async refactoring in earlier commits. The SSE implementation itself is clean and all 24 mock tests pass without issues.
