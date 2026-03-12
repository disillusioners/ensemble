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

### ⚠️ Temporary (Kept for LangGraph & Table Creation)

| File | Status | Purpose |
|------|--------|---------|
| `daemon/persistence.py` | ⚠️ Keep | `init_database()` for table creation, `get_checkpointer()` and `get_session_messages()` for LangGraph |
| `daemon/queue.py` | ⚠️ Keep | `InputMessageQueue` for session-specific dequeue operations |
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

### Priority 1: Complete Repository Adoption

| Task | Description | Effort |
|------|-------------|--------|
| Session-specific dequeue | Add `dequeue_by_session(session_id)` to `SQLModelMessageQueueRepository` | Low |
| Remove `InputMessageQueue` | Delete `daemon/queue.py` after adding session-specific dequeue | Low |
| Update test imports | Update tests to use repository pattern instead of direct persistence | Medium |

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

### Decision 3: Keep `InputMessageQueue` ✅
**Decision:** Keep `InputMessageQueue` for session-specific dequeue operations
- **Rationale:** Repository doesn't support session-specific dequeue yet
- **Future:** Add `dequeue_by_session()` method to repository

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
