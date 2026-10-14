# Phase 1: Project History Data Layer — Implementation Notes

## What Was Implemented
Phase 1 of the `project_history` feature — the data layer.

### Files Changed/Created
1. **`daemon/repositories/project/models.py`** — Added `HistoryEntryType` enum (8 values) and `ProjectHistoryEntry` SQLModel with `to_dict()`
2. **`daemon/repositories/project/repository.py`** — Added 6 methods to `SQLModelProjectRepository`: add, get, delete, list, search, get_recent
3. **`daemon/repositories/project/__init__.py`** — Updated exports
4. **`daemon/migrations/versions/20260521_000001_add_project_history_table.sql`** — New migration
5. **`tests/test_project_history.py`** — 23 unit tests

### Key Decisions
- Field names: `recorded_by_agent`, `recorded_by_instance`, `entry_metadata` (not `metadata`)
- Method params: `source_agent`, `source_instance_id` map to model fields `recorded_by_agent`, `recorded_by_instance`
- All methods return dicts (not model objects) via `.to_dict()`
- `datetime.now(timezone.utc)` for timestamps (NOT deprecated `utcnow`)
- NULL-safe search uses `func.coalesce(ProjectHistoryEntry.details, "").ilike()`

### Test Results
- 23 new tests all pass
- 4,343 existing tests pass; 23 pre-existing failures unrelated to changes

### Commit
`c569dcc` — `feat: add project history data layer (Phase 1)`
