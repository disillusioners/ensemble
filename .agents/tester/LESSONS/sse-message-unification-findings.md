# SSE Message Unification — Test Findings

## Date: 2026-04-12
## Branch: feature/sse-message-unification

## Code Architecture
- **MessageService** is the single coordination point for message events
- **TaskProcessor** delegates to MessageService (no direct EventBus calls for these events)
- **Error isolation**: try/except around all EventBus calls — no exceptions propagate
- **UnifiedMessage model**: to_sse_data() omits None fields, to_api_response() includes all fields
- **Frontend**: msgIndex === -1 fallback handles new messages not yet in the list

## Pre-existing Test Issues Fixed (commit 7f39b28)
These were NOT caused by the SSE unification feature but were discovered during testing:

1. **persistence tests**: `alist()` async mock returned coroutine instead of async iterator
   - Fix: Created `EmptyAsyncIterator` class
2. **models test**: InstanceStatus enum got a new value (6 → 7 statuses)
   - Fix: Updated expected count
3. **manager/api tests**: `terminate_instance()` is async but tests weren't using AsyncMock/await
   - Fix: Added @pytest.mark.asyncio, await, AsyncMock

## Mock Test Design Pattern
- Mock EventBus directly (no real DB needed)
- Test error isolation by having EventBus.create_event raise exceptions
- Test duplicate prevention by verifying call counts on EventBus
- Test model serialization with None fields vs missing fields

## Quick Fixes
- Commit 7f39b28 contains all test fixes
- All fixes were pre-existing issues, not related to the feature
