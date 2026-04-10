# Phase 1: Data Model & Migration

## Objective

Add retry tracking and cancellation fields to the Task model, add CANCELLED status to the enum, and create a database migration that safely adds new columns with defaults to the existing `task` table.

## Coupling

- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: `daemon/repositories/task/models.py` (shared with Phase 3)
- **Shared APIs/interfaces**: TaskStatus enum, Task model fields
- **Why this coupling**: Phase 3 (Repository) and Phase 4 (TaskProcessor) reference the new model fields and statuses

## Context

- Current Task model has: id, task_type, instance_id, message_id, status, worker_id, result, error, created_at, started_at, completed_at
- Current TaskStatus: PENDING, RUNNING, COMPLETED, FAILED (no CANCELLED)
- Migration system uses file-based SQL migrations in `daemon/migrations/versions/` with format `YYYYMMDD_HHMMSS_name.sql`
- MigrationRunner has idempotent error handling (skips "duplicate column", "already exists", etc.)
- SQLite is the database — ALTER TABLE ADD COLUMN supports defaults

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add CANCELLED to TaskStatus enum | Add `CANCELLED = "cancelled"` after FAILED | `daemon/repositories/task/models.py` |
| 2 | Add retry tracking fields to Task model | `retry_count: int = Field(default=0)`, `next_retry_at: Optional[datetime] = Field(default=None, index=True)` | `daemon/repositories/task/models.py` |
| 3 | Add cancellation fields to Task model | `cancel_requested: bool = Field(default=False)`, `cancel_requested_at: Optional[datetime] = Field(default=None)` | `daemon/repositories/task/models.py` |
| 4 | Add retry_scheduled guard field | `retry_scheduled: bool = Field(default=False)` — atomic guard against double-retry (S1) | `daemon/repositories/task/models.py` |
| 5 | Update _row_to_task() with new fields | <!-- FIX: C4 --> Add mapping for retry_count, next_retry_at, cancel_requested, cancel_requested_at, retry_scheduled with hasattr() guards | `daemon/repositories/task/repository.py` |
| 6 | Create SQL migration for new columns | ALTER TABLE task ADD COLUMN for each new field with defaults. Includes retry_scheduled column | `daemon/migrations/versions/20260415_000001_task_retry_cancel_fields.sql` |
| 7 | Update test fixtures | Update `tests/message_queue_redesign/conftest.py` if needed for new fields | `tests/message_queue_redesign/conftest.py` |
| 8 | Write model unit tests | Test Task creation with new fields, default values, status transitions | `tests/message_queue_redesign/test_task_models.py` (new) |

## Key Files

- `daemon/repositories/task/models.py` — Task model + TaskStatus/TaskType enums
- `daemon/migrations/versions/20260412_000001_create_task_table.sql` — Reference for existing schema
- `daemon/migrations/runner.py` — MigrationRunner.apply_migration() for migration pattern
- `tests/message_queue_redesign/conftest.py` — Test fixtures

## Detailed Implementation

### 1. TaskStatus Enum Update

```python
class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # NEW
```

### 2. Task Model New Fields

<!-- FIX: C4 — Update _row_to_task() to include new fields -->
<!-- FIX: S1 — Add retry_scheduled boolean guard column -->

Add after `worker_id` field, before `result`:

```python
# Retry tracking
retry_count: int = Field(default=0)
next_retry_at: Optional[datetime] = Field(default=None, index=True)

# Cancellation
cancel_requested: bool = Field(default=False)
cancel_requested_at: Optional[datetime] = Field(default=None)

# Retry guard (atomic flag to prevent double-retry)
retry_scheduled: bool = Field(default=False)
```

### 3. _row_to_task() Update

<!-- FIX: C4 — _row_to_task() must map all new fields or tasks will silently get defaults -->

The existing `_row_to_task()` in `daemon/repositories/task/repository.py` (lines 158-179) manually maps row columns. It MUST be updated to include the new fields, otherwise code paths using `_row_to_task()` will get wrong `retry_count=0` for retry tasks.

**Update `_row_to_task()` to include all new fields:**

```python
def _row_to_task(self, row) -> Task:
    """Convert a database row to a Task object."""
    return Task(
        id=row.id,
        task_type=row.task_type,
        instance_id=row.instance_id,
        message_id=row.message_id,
        status=row.status,
        worker_id=row.worker_id,
        retry_count=row.retry_count if hasattr(row, 'retry_count') else 0,
        next_retry_at=row.next_retry_at if hasattr(row, 'next_retry_at') else None,
        cancel_requested=row.cancel_requested if hasattr(row, 'cancel_requested') else False,
        cancel_requested_at=row.cancel_requested_at if hasattr(row, 'cancel_requested_at') else None,
        retry_scheduled=row.retry_scheduled if hasattr(row, 'retry_scheduled') else False,
        result=row.result,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )
```

> **Note**: The `hasattr()` guards provide backward compatibility during migration — if the code reads a row from a table that hasn't been migrated yet, it gracefully defaults instead of crashing. Once the migration is confirmed applied, these guards can be removed.

### 4. SQL Migration

```sql
-- Migration: add retry and cancellation fields to task
-- Created: 2026-04-15

-- UP
ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN next_retry_at TEXT;
ALTER TABLE task ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN cancel_requested_at TEXT;
-- FIX: S1 — retry_scheduled guard column prevents double-retry race condition
ALTER TABLE task ADD COLUMN retry_scheduled INTEGER NOT NULL DEFAULT 0;

-- Update status CHECK constraint to include 'cancelled'
-- SQLite doesn't support ALTER TABLE ... ALTER CONSTRAINT, so we recreate the table
-- Actually: the CHECK is just on INSERT/UPDATE, existing data is fine.
-- We'll use a simpler approach: just add columns. The SQLModel model handles validation.
-- If strict CHECK enforcement is needed, it requires table recreation (not worth the risk).

-- Create index for retry scheduling queries
CREATE INDEX IF NOT EXISTS idx_task_next_retry_at ON task(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_task_cancel_requested ON task(cancel_requested);
CREATE INDEX IF NOT EXISTS idx_task_retry_scheduled ON task(retry_scheduled);

-- DOWN
-- (Not supporting down migration for safety — columns with defaults are harmless)
```

**Important note on CHECK constraint**: SQLite doesn't support modifying CHECK constraints via ALTER TABLE. The existing `CHECK(status IN (...))` won't include 'cancelled'. Two options:
- **Option A (Recommended)**: Don't enforce via CHECK. Let the application layer (TaskStatus enum) handle validation. The column just stores TEXT.
- **Option B**: Recreate the table (risky, requires data copy). Not recommended.

The SQLModel model already uses `TaskStatus.CANCELLED.value` which maps to `"cancelled"` — the string is stored in the TEXT column. The CHECK constraint from the original migration only affects raw SQL INSERTs, not SQLModel operations.

## Constraints

- All new columns must have defaults (backward compatibility with existing rows)
- Migration must be idempotent (MigrationRunner handles "duplicate column" errors)
- Don't recreate the table — ALTER TABLE ADD COLUMN is safe
- Don't modify the existing CHECK constraint — use application-level validation instead

## Deliverables

- [ ] TaskStatus has CANCELLED value
- [ ] Task model has retry_count, next_retry_at, cancel_requested, cancel_requested_at, retry_scheduled
- [ ] _row_to_task() updated with all new fields and hasattr() guards <!-- FIX: C4 -->
- [ ] SQL migration file created with idempotent ADD COLUMN statements (including retry_scheduled)
- [ ] Indexes created for next_retry_at, cancel_requested, and retry_scheduled
- [ ] Unit tests pass for model with new fields
- [ ] Existing task tests still pass (backward compatibility)
