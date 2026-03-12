# Repository Layer Migration Plan

## Executive Summary

This document outlines the plan to complete the migration from direct SQLite database access to the repository layer pattern. The refactoring was partially completed but left several legacy files and broken imports.

---

## Current State

### ✅ Completed
| Component | Status | Notes |
|-----------|--------|-------|
| `SQLModelProjectRepository` | ✅ Complete | Fully implemented and in use |
| Project repository tests | ✅ Fixed | Tests updated to use new repository |
| Backup file cleanup | ✅ Done | `project_store.py.bak` removed |
| `SQLModelSessionRepository` | ✅ Complete | Session operations migrated in manager.py |
| `SQLModelMessageQueueRepository` | ✅ Complete | Queue operations migrated in manager.py |
| `SQLModelSourceRepository` | ✅ Complete | Source operations migrated in api.py and sources/ |

### ⚠️ Temporary (kept for LangGraph & table creation)
| File | Lines | Status | Description |
|------|-------|--------|-------------|
| `daemon/persistence.py` | 773 | ⚠️ Temporary | `init_database()` for table creation, `get_checkpointer()` and `get_session_messages()` for LangGraph |
| `daemon/queue.py` | 599 | ⚠️ Temporary | `InputMessageQueue` kept for session-specific dequeue operations |
| `daemon/sources/persistence.py` | 356 | ⚠️ Temporary | Still imported by tests only |

### ✅ Repository Implementations Available
| Repository | File | Status |
|------------|------|--------|
| `SQLModelSessionRepository` | `daemon/repositories/session/repository.py` | ✅ In use |
| `SQLModelMessageQueueRepository` | `daemon/repositories/message_queue/repository.py` | ✅ In use |
| `SQLModelSourceRepository` | `daemon/repositories/source/repository.py` | ✅ In use |
| `SQLModelProjectRepository` | `daemon/repositories/project/repository.py` | ✅ In use |

---

## Files to Migrate

### 1. `daemon/persistence.py` → `SQLModelSessionRepository`

**Current Usage:**
```python
# daemon/manager.py:21
from .persistence import (
    init_database,
    save_session_metadata,
    update_session_status,
    update_session_title,
    get_session_metadata,
    ...
)
```

**Functions to Replace:**
| Old Function | New Repository Method |
|--------------|----------------------|
| `save_session_metadata()` | `session_repo.create()` |
| `update_session_status()` | `session_repo.update_status()` |
| `update_session_title()` | `session_repo.update_title()` |
| `get_session_metadata()` | `session_repo.get()` |
| `list_sessions()` | `session_repo.list()` |
| `delete_session()` | `session_repo.delete()` |
| `get_root_session()` | `session_repo.get()` + traverse hierarchy |
| `get_session_hierarchy()` | `session_repo.get_children()` |

**Special Consideration:**
- `init_database()` creates tables - needs to remain or be moved to SQLModel metadata creation
- `get_checkpointer()` creates LangGraph checkpointer - keep separate

---

### 2. `daemon/queue.py` → `SQLModelMessageQueueRepository`

**Current Usage:**
```python
# daemon/manager.py:263
self.queue = InputMessageQueue(self.conn)
```

**Methods to Replace:**
| Old Method | New Repository Method |
|------------|----------------------|
| `enqueue()` | `queue_repo.enqueue()` |
| `get()` | `queue_repo.get_next()` |
| `complete()` | `queue_repo.mark_completed()` |
| `fail()` | `queue_repo.mark_failed()` |
| `retry()` | `queue_repo.retry()` |

---

### 3. `daemon/sources/persistence.py` → `SQLModelSourceRepository`

**Current Usage:**
```python
# daemon/api.py (multiple endpoints)
from .sources.persistence import (
    save_source_config,
    get_source_config,
    list_source_configs,
    ...
)
```

**Functions to Replace:**
| Old Function | New Repository Method |
|--------------|----------------------|
| `save_source_config()` | `source_repo.create()` / `update()` |
| `get_source_config()` | `source_repo.get()` |
| `list_source_configs()` | `source_repo.list()` |
| `delete_source_config()` | `source_repo.delete()` |
| `update_source_status()` | `source_repo.update_status()` |

---

## Migration Steps

### Phase 1: Session Repository Migration ✅ COMPLETE

**Files modified:**
- [x] `daemon/manager.py` - Replaced persistence imports with repository
- [x] `daemon/repositories/__init__.py` - Export SessionRepository
- [x] `daemon/repositories/factory.py` - `create_session_repository()` works

**Steps completed:**
1. ✅ Export `SQLModelSessionRepository` from `daemon/repositories/__init__.py`
2. ✅ Added `_session_repository` to `SessionManager.__init__()`
3. ✅ Replaced all `persistence.*` calls with `self._session_repository.*`
4. ✅ Kept `init_database()` temporarily for table creation
5. ✅ Tests pass: `pytest tests/ -v`
6. ⏳️ Session-related code remains in `daemon/persistence.py` (kept for backward compatibility with tests)

---

### Phase 2: Message Queue Repository Migration ✅ COMPLETE

**Files modified:**
- [x] `daemon/manager.py` - Added `SQLModelMessageQueueRepository` alongside `InputMessageQueue`
- [x] `daemon/repositories/message_queue/models.py` - Added `RETRYING` status and `last_activity_at` field
- [x] `daemon/repositories/message_queue/repository.py` - Added `update_activity`, `get_status`, `is_empty` methods

**Steps completed:**
1. ✅ Export `SQLModelMessageQueueRepository` from `daemon/repositories/__init__.py`
2. ✅ Added `_queue_repository` to `SessionManager.__init__()`
3. ✅ Updated `enqueue_message`, queue operations to use repository methods
4. ✅ Kept `InputMessageQueue` for session-specific dequeue operations
5. ✅ Tests pass
6. ⏳️ `InputMessageQueue` kept temporarily for session-specific operations

---

### Phase 3: Source Repository Migration ✅ COMPLETE

**Files modified:**
- [x] `daemon/manager.py` - Added `_source_repository` using `create_source_repository()`
- [x] `daemon/api.py` - Updated all source endpoints to use repository pattern
- [x] `daemon/sources/mapper.py` - Updated `SessionMapper` to use repository
- [x] `daemon/sources/cleanup.py` - Updated `SourceCleanup` to use repository
- [x] `daemon/sources/registry.py` - Updated `SourceRegistry` to use repository

**Steps completed:**
1. ✅ Export `SQLModelSourceRepository` from `daemon/repositories/__init__.py`
2. ✅ Updated API endpoints to use repository pattern
3. ✅ Updated `SourceRegistry` to use repository
4. ✅ Tests pass
5. ⏳️ `daemon/sources/persistence.py` kept for backward compatibility with tests

---

### Phase 4: Final Cleanup ⏳️ IN PROGRESS

**Files to keep temporarily:**
- [x] `daemon/persistence.py` - Keep `init_database()`, `get_checkpointer()`, `get_session_messages()` for LangGraph
- [x] `daemon/queue.py` - Keep `InputMessageQueue` for session-specific dequeue
- [x] `daemon/sources/persistence.py` - Keep for backward compatibility with tests

**Files to update:**
- [ ] Remove `sqlite3` direct usage from `daemon/manager.py` (use repositories)
- [ ] Keep `self.conn` for `init_database()` and LangGraph
- [x] Update `daemon/repositories/__init__.py` with all exports

**Remaining tasks:**
- [ ] Update test files to use repository pattern instead of direct persistence imports
- [ ] Consider migrating `init_database()` to SQLModel metadata creation (follow-up task)
- [ ] Consider migrating LangGraph checkpointer to use SQLModel (follow-up task)

---

## Risk Assessment

### High Risk Areas
1. **Database initialization** - `init_database()` creates all tables; must ensure SQLModel creates same schema
2. **LangGraph checkpointer** - Uses `aiosqlite` directly; may need special handling
3. **Transaction management** - Repositories use SQLModel sessions; ensure proper commit/rollback

### Medium Risk Areas
1. **Concurrent access** - Current code uses WAL mode; ensure repositories maintain same behavior
2. **Error handling** - Direct sqlite3 errors vs SQLModel errors may differ
3. **Performance** - Repository pattern adds abstraction layer; benchmark if needed

### Low Risk Areas
1. **API contracts** - Repository methods mirror existing functions
2. **Tests** - Existing tests cover functionality

---

## Testing Strategy

### Before Migration
```bash
# Run all tests to establish baseline
pytest tests/ -v --tb=short
```

### After Each Phase
```bash
# Run relevant tests
pytest tests/test_session*.py -v      # Phase 1
pytest tests/test_queue*.py -v        # Phase 2
pytest tests/test_source*.py -v       # Phase 3
pytest tests/ -v                       # Phase 4 (full suite)
```

### Integration Testing
```bash
# Start daemon and test endpoints
python -m daemon.main &
curl http://localhost:8000/sessions
curl http://localhost:8000/sources
```

---

## Rollback Plan

If issues arise after migration:

1. **Revert commits** - Each phase should be a separate commit
2. **Restore deleted files** - Keep `.bak` copies until confirmed working
3. **Database compatibility** - SQLModel and sqlite3 can coexist; schema should remain compatible

---

## Timeline Estimate

| Phase | Estimated Time | Dependencies |
|-------|---------------|--------------|
| Phase 1: Session | 2-4 hours | None |
| Phase 2: Queue | 2-4 hours | Phase 1 |
| Phase 3: Source | 3-5 hours | None (parallel) |
| Phase 4: Cleanup | 1-2 hours | All phases |
| **Total** | **8-15 hours** | |

---

## Decision Points

### Question 1: Keep `init_database()` or migrate to SQLModel? ✅ DECIDED: Option A

**Decision:** Keep `init_database()` in `daemon/persistence.py` for table creation
- Pro: Minimal change, guaranteed schema compatibility
- Con: Keeps some legacy code

### Question 2: What to do with `self.conn` in SessionManager? ✅ DECIDED: Option A

**Decision:** Keep `self.conn` for `init_database()` and LangGraph checkpointer only
- Pro: Required for database initialization and LangGraph
- Con: None - this is the necessary dependency

---

## Next Steps

1. **Review this plan** with team
2. **Decide on approach** for decision points above
3. **Assign phases** to developers (can parallelize Phase 1 & 3)
4. **Create feature branches** for each phase
5. **Begin Phase 1** after approval

---

## Appendix: Current Import Structure

```
daemon/
├── manager.py
│   ├── from .persistence import init_database, save_session_metadata, ...
│   ├── from .queue import InputMessageQueue
│   └── from .repositories import SQLModelProjectRepository, create_project_repository
│
├── persistence.py (TO BE REMOVED)
│   └── Direct sqlite3 operations for sessions
│
├── queue.py (TO BE REMOVED)
│   └── Direct sqlite3 operations for message queue
│
├── sources/
│   └── persistence.py (TO BE REMOVED)
│       └── Direct sqlite3 operations for source configs
│
└── repositories/
    ├── __init__.py (NEEDS MORE EXPORTS)
    ├── session/repository.py ✅
    ├── message_queue/repository.py ✅
    ├── source/repository.py ✅
    └── project/repository.py ✅ (in use)
```

## Target Import Structure

```
daemon/
├── manager.py
│   └── from .repositories import (
│         SQLModelProjectRepository,
│         SQLModelSessionRepository,
│         SQLModelMessageQueueRepository,
│         create_project_repository,
│         create_session_repository,
│         create_message_queue_repository,
│       )
│
├── repositories/
│   ├── __init__.py (ALL EXPORTS)
│   ├── session/repository.py
│   ├── message_queue/repository.py
│   ├── source/repository.py
│   └── project/repository.py
│
└── (persistence.py, queue.py, sources/persistence.py removed)
```
