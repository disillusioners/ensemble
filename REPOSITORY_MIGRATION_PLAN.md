# Repository Layer Migration Plan

## Executive Summary

✅ **MIGRATION COMPLETE** - All 4 phases of the repository layer migration have been successfully completed.

The migration from direct SQLite database access to the repository layer pattern is now complete. All session, message queue, and source operations now use the repository pattern via `SQLModel` repositories.

---

## Migration Status

### ✅ Completed Migrations

| Repository | Status | Files Modified |
|------------|--------|----------------|
| `SQLModelSessionRepository` | ✅ Complete | `daemon/manager.py` |
| `SQLModelMessageQueueRepository` | ✅ Complete | `daemon/manager.py`, `daemon/repositories/message_queue/` |
| `SQLModelSourceRepository` | ✅ Complete | `daemon/api.py`, `daemon/sources/{mapper,cleanup,registry}.py` |
| `SQLModelProjectRepository` | ✅ Complete | Already in use |

### ⚠️ Temporary (Kept for LangGraph, Table Creation & Watchdog)

| File | Status | Purpose |
|------|--------|---------|
| `daemon/persistence.py` | ⚠️ Keep | `init_database()` for table creation, `get_checkpointer()` and `get_session_messages()` for LangGraph |
| `daemon/queue.py` | ⚠️ Keep | `SessionWatchdog` needs `InputMessageQueue` for stuck message recovery |
| `daemon/sources/persistence.py` | ⚠️ Keep | Backward compatibility with existing tests |

---

## Changes Summary

### Phase 1: Session Repository ✅
- Added `_session_repository` to `SessionManager` using `create_session_repository()`
- Replaced `get_session_metadata()` → `_session_repository.get()`
- Replaced `save_session_metadata()` → `_session_repository.create()`
- Replaced `update_session_title()` → `_session_repository.update_title()`
- Replaced `list_all_sessions()` → `_session_repository.list()` (with dict conversion)
- Replaced `delete_all_sessions()` → `_session_repository.delete_all()`

### Phase 2: Message Queue Repository ✅
- Added `_queue_repository` to `SessionManager` using `create_message_queue_repository()`
- Updated queue operations: `enqueue`, `complete`, `fail`, `retry`, `is_empty`
- Added `update_activity()`, `get_status()`, `is_empty()` methods to repository
- Added `RETRYING` status and `last_activity_at` field to `MessageQueue` model
- Kept `InputMessageQueue` for session-specific dequeue (temporary)

### Phase 3: Source Repository ✅
- Added `_source_repository` to `SessionManager` using `create_source_repository()`
- Updated all API endpoints to use repository pattern
- Updated `SessionMapper`, `SourceCleanup`, `SourceRegistry` to use repository

### Phase 4: Test Updates ✅
- Fixed `tests/test_persistence.py` - removed non-existent `create_checkpointer` import
- Updated tests to handle tuple return from `list_all_sessions()`
- Added `mock_session_repository` fixture to test files

---

## Current Import Structure

```
daemon/
├── manager.py
│   ├── from .persistence import (init_database, get_checkpointer, get_session_messages, update_session_status)
│   ├── from .queue import (InputMessageQueue, SessionWatchdog, SessionCircuitBreaker, QueuedMessage)
│   └── from .repositories import (
│         SQLModelSessionRepository,
│         SQLModelProjectRepository,
│         SQLModelSourceRepository,
│         SQLModelMessageQueueRepository,
│         create_project_repository,
│         create_session_repository,
│         create_source_repository,
│         create_message_queue_repository,
│       )
│
├── persistence.py (KEPT - init_database, get_checkpointer, get_session_messages)
├── queue.py (KEPT - InputMessageQueue for session-specific dequeue)
│
├── sources/
│   └── persistence.py (KEPT - backward compatibility with tests)
│
└── repositories/
    ├── __init__.py (ALL EXPORTS)
    ├── session/repository.py ✅
    ├── message_queue/repository.py ✅
    ├── source/repository.py ✅
    └── project/repository.py ✅
```

---

## Future Improvements

### Priority 1: Complete Repository Adoption ✅ DONE

**Completed Changes:**
- Added `dequeue_by_session(session_id)` method to `SQLModelMessageQueueRepository`
- Added `get_stats(session_id)` method to `SQLModelMessageQueueRepository`
- Updated `manager.py` to use `_queue_repository.dequeue_by_session()` instead of `InputMessageQueue.dequeue()`
- Updated `manager.py` to use `_queue_repository.get_stats()` instead of `InputMessageQueue.get_stats()`
- Updated `manager.py` to use `msg.message_metadata` instead of `msg.metadata`
- Updated `SessionWatchdog` to accept `queue_repository` instead of `queue` and `conn`
- Added repository methods:
  - `find_stuck_messages()` - find stuck processing messages
  - `fail_stuck_message()` - mark stuck message as permanently failed
  - `schedule_retry_for_stuck()` - schedule retry for a stuck message
  - `find_retry_ready_messages()` - find retry-ready messages
  - `move_retry_ready_to_ready()` - move retry-ready messages back to ready status
- Added deprecation warning to `InputMessageQueue` class

- **Remaining:** `InputMessageQueue` still used by `SessionWatchdog` (now accepts repository) and backward compatibility with tests

- **Future:** Create repository-based watchdog, remove `InputMessageQueue` entirely

### Priority 2: LangGraph Integration

| Task | Description | Effort |
|------|-------------|--------|
| SQLModel checkpointer | Create SQLModel-based checkpointer for LangGraph | High |
| Remove `get_checkpointer` | Migrate to SQLModel-based checkpointer | High |
| Remove `get_session_messages` | Use repository for message retrieval | Medium |

### Priority 3: Database Initialization

| Task | Description | Effort |
|------|-------------|--------|
| SQLModel metadata | Use `SQLModel.metadata.create_all()` for table creation | Medium |
| Migration system | Add Alembic or similar for schema migrations | High |
| Remove `init_database` | Delete after SQLModel migration complete | Low |

---

## Decision Log

### Decision 1: Keep `init_database()` ✅
**Decision:** Keep `init_database()` in `daemon/persistence.py` for table creation
- **Rationale:** Minimal change, guaranteed schema compatibility
- **Future:** Migrate to SQLModel metadata creation as follow-up task

### Decision 2: Keep `self.conn` ✅
**Decision:** Keep `self.conn` for `init_database()` and LangGraph checkpointer
- **Rationale:** Required for database initialization and LangGraph integration
- **Future:** Remove after LangGraph checkpointer migration

### Decision 3: Keep `InputMessageQueue` ✅ → ⚠️ Partially Resolved
**Decision:** Keep `InputMessageQueue` for session-specific dequeue operations
- **Rationale:** Repository didn't support session-specific dequeue yet
- **Status:** ✅ **RESOLVED** - Added `dequeue_by_session()` to repository
- **Remaining:** `SessionWatchdog` still uses `InputMessageQueue` for stuck message recovery
- **Future:** Refactor `SessionWatchdog` to use repository pattern

---

## Testing

All tests pass after migration:
```bash
# Core tests
pytest tests/test_persistence.py tests/test_manager.py tests/test_session_title.py -v

# Source tests
pytest tests/test_sources*.py -v

# API tests
pytest tests/test_api.py -v
```

---

## Rollback Plan

If issues arise:
1. **Revert commits** - Each phase was a separate set of changes
2. **Database compatibility** - SQLModel and sqlite3 coexist; schema remains compatible
3. **Legacy files kept** - `persistence.py`, `queue.py`, `sources/persistence.py` still exist
