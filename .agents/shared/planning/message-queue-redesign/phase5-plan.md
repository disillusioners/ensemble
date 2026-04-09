# Phase 5: Remove Old Code

## Objective

Clean up all deprecated code after the migration is validated. Remove the old consumer pattern, in-memory state, InputMessageQueue, InstanceWatchdog, InstanceCircuitBreaker, and the old EventBroadcaster. Simplify InstanceManager to only orchestrate graph execution.

## Coupling

- **Depends on**: Phase 3 (message flow migration), Phase 4 (SSE migration)
- **Coupling type**: tight (removes code that Phases 3 and 4 replaced)
- **Shared files with other phases**: `daemon/manager.py`, `daemon/queue.py`, `daemon/events.py`
- **Why this coupling**: Can only remove old code after new code is proven stable

## Context

### What Gets Removed

| Component | Location | Replaced By |
|-----------|----------|-------------|
| `InputMessageQueue` | `daemon/queue.py` | WorkerPool + TaskRepository |
| `InstanceWatchdog` | `daemon/queue.py` | WorkerPool stale task recovery |
| `InstanceCircuitBreaker` | `daemon/queue.py` | DB-based failure tracking |
| `EventBroadcaster` | `daemon/events.py` | `DBEventBroadcaster` |
| `_instance_queues` | `daemon/manager.py` | Task table |
| `_consumer_tasks` | `daemon/manager.py` | WorkerPool |
| `_processing` | `daemon/manager.py` | Message status in DB |
| `_instance_consumer()` | `daemon/manager.py` | TaskProcessor |
| `_process_queue()` | `daemon/manager.py` | ProcessMessageProcessor |
| `_ensure_consumer()` | `daemon/manager.py` | WorkerPool (no consumers needed) |
| `_start_consumer()` | `daemon/manager.py` | WorkerPool |
| `_signal_consumer()` | `daemon/manager.py` | DB polling (no signals needed) |
| Old `Event` dataclass | `daemon/events.py` | DB Event model |
| Feature flag | `daemon/config.py` | Removed (worker pool always on) |

### What Gets Simplified

| Component | Change |
|-----------|--------|
| `InstanceManager` | Remove 6+ methods, 4 in-memory data structures |
| `daemon/manager.py` | Reduce from ~1200 lines to ~600 lines |
| `daemon/api.py` | Remove old SSE path, old consumer management |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Remove InputMessageQueue | Delete class, update all imports | `daemon/queue.py` (modify → simplify or delete) |
| 2 | Remove InstanceWatchdog | Delete class, WorkerPool handles stale tasks | `daemon/queue.py` (modify) |
| 3 | Remove InstanceCircuitBreaker | Delete class, DB tracks failures | `daemon/queue.py` (modify) |
| 4 | Remove old EventBroadcaster | Delete class, DBEventBroadcaster replaces | `daemon/events.py` (delete or simplify) |
| 5 | Clean up InstanceManager | Remove old consumer methods, in-memory state, feature flag | `daemon/manager.py` (modify) |
| 6 | Clean up API | Remove old SSE path, consumer endpoints | `daemon/api.py` (modify) |
| 7 | Remove feature flag | Delete `use_worker_pool` config option | `daemon/config.py` (modify) |
| 8 | Clean up old tests | Remove tests for deleted components | `tests/` (modify) |
| 9 | Add deprecation migration | Optional: rename `message_queue` → `message` table | `daemon/migrations/versions/` (new) |
| 10 | Final regression testing | Full test suite passes, manual E2E verification | Manual |

## Key Files

### Deleted / Gutted

| File | Action |
|------|--------|
| `daemon/queue.py` | Delete entirely (all 3 classes replaced) |
| `daemon/events.py` | Delete `EventBroadcaster` class (may keep `EventPriority` enum if still used) |

### Modified Files

| File | Changes |
|------|---------|
| `daemon/manager.py` | Remove old consumer methods, in-memory state, circuit breaker integration |
| `daemon/api.py` | Remove old SSE path, cleanup imports |
| `daemon/config.py` | Remove `use_worker_pool` feature flag |
| `daemon/dispatcher.py` | Finalize new event subscription |
| `daemon/sources/dispatcher.py` | Remove EventBroadcaster import, use DBEventBroadcaster <!-- FIX: W8 --> |

### Test Changes

| File | Action |
|------|--------|
| `tests/queue/` or tests referencing `InputMessageQueue` | Delete or update |
| `tests/events/` or tests referencing `EventBroadcaster` | Delete or update |
| All existing test files | Verify they still pass |

## Constraints

1. **Only remove after validation**: Both Phase 3 and Phase 4 must be stable
2. **No functional changes**: This phase only removes dead code
3. **Full regression suite**: Every test must pass
4. **Clean imports**: No dangling imports or references

## Removal Checklist

### Code Removal Verification

```bash
# Verify no references to removed classes
grep -r "InputMessageQueue" daemon/ --include="*.py"
grep -r "InstanceWatchdog" daemon/ --include="*.py"
grep -r "InstanceCircuitBreaker" daemon/ --include="*.py"
grep -r "_instance_queues" daemon/ --include="*.py"
grep -r "_consumer_tasks" daemon/ --include="*.py"
grep -r "_processing" daemon/ --include="*.py"
grep -r "use_worker_pool" daemon/ --include="*.py"
grep -r "EventBroadcaster" daemon/ --include="*.py"
```

All should return zero results after cleanup.

<!-- FIX: W8 — additional import sites that need cleanup -->
### Additional Import Sites to Check

These files import or reference components being removed and must be updated:

| File | References to Clean |
|------|---------------------|
| `daemon/sources/dispatcher.py:9` | EventBroadcaster import |
| `tests/integration/test_sse_streaming.py:16` | EventBroadcaster import |
| `tests/integration/test_streaming_performance.py` | EventBroadcaster / InputMessageQueue references |
| `tests/integration/test_streaming_errors.py:16` | EventBroadcaster import |

### Optional: Table Rename

After validation, optionally rename `message_queue` → `message`:

```sql
-- UP
ALTER TABLE message_queue RENAME TO message;

-- DOWN
ALTER TABLE message RENAME TO message_queue;
```

**This is optional** and should only be done if the rename doesn't break too many imports. The `message_queue` name works fine.

## Testing Strategy

### Regression Tests

| Test | Scenario |
|------|----------|
| `test_no_old_code_references` | No imports of removed classes |
| `test_full_message_flow` | End-to-end message processing works |
| `test_child_instance_flow` | Spawn + completion + report works |
| `test_sse_delivery` | SSE events delivered correctly |
| `test_restart_recovery` | App restart doesn't lose state |
| `test_terminate_instance` | Instance termination works |
| `test_shutdown` | Graceful shutdown works |
| `test_all_existing_tests_pass` | Complete test suite green |

### Manual Verification

1. Start daemon, create instance, send message, verify response
2. Create child instance, verify completion report
3. Restart daemon during processing, verify recovery
4. Connect SSE client, verify events
5. Disconnect SSE client, reconnect, verify missed events replayed

## Deliverables

- [ ] `daemon/queue.py` removed (InputMessageQueue, Watchdog, CircuitBreaker)
- [ ] `daemon/events.py` cleaned up (old EventBroadcaster removed)
- [ ] `daemon/manager.py` simplified (no old consumer code)
- [ ] `daemon/api.py` cleaned up (no old SSE path)
- [ ] Feature flag removed
- [ ] No dangling imports or references
- [ ] All tests pass
- [ ] Manual E2E verification complete
- [ ] Optional: table rename migration
