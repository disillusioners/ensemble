# Phase 2 Worker Pool Testing - Key Patterns

**Date:** 2026-04-09
**Project:** agents-ensemble
**Branch:** feature/message-queue-redesign

## Key Testing Patterns Discovered

### 1. Phase 2 Test Structure
The `tests/message_queue_redesign/` directory uses its own `conftest.py` that provides:
- In-memory SQLite database for testing
- `TaskRepository` and `EventRepository` instances
- `StaleTaskRecovery` test helper
- `WorkerPool` test setup with configurable worker count

### 2. Critical Tests Verified
- **Concurrent claim behavior**: `test_task_repository.py` has atomic claim tests with explicit transactions
- **Worker pool lifecycle**: `test_worker_pool.py` tests start, task processing, and graceful shutdown
- **Stale task recovery**: `test_stale_task_recovery.py` tests old started_at detection and reset

### 3. Key Classes Under Test
- `TaskRepository`: CRUD operations, atomic task claiming
- `EventRepository`: Event logging and message linking
- `WorkerPool`: Worker lifecycle, task distribution
- `StaleTaskRecovery`: Detection and reset of stale tasks

### 4. SQLAlchemy Deprecation Warnings
- 17 warnings about datetime adapter and non-blocking operations
- These are expected and don't affect functionality
- Not failures, just informational warnings

### 5. InstanceWatchdog Runtime Errors (Not Test Failures)
After test completion, the InstanceWatchdog logs errors about `message_queue` table not existing.
- This is expected behavior
- The tests use a test database without the legacy table
- Not actual test failures

## Related Files
- Full test report: `.agents/tester/RESULTS/2026-04-09-phase2-worker-pool-tests.md`
- Test files: `tests/message_queue_redesign/`
