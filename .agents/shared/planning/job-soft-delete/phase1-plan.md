# Phase 1: BE — Database & Model Layer

## Objective
Add `deleted_at` nullable timestamp column to `job_queue_items` table and update the `JobItem` SQLModel to include the field.

## Coupling
- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `daemon/repositories/job_queue/models.py`
- **Shared APIs/interfaces**: `JobItem` model, `to_dict()` method
- **Why this coupling**: All subsequent phases depend on the model definition.

## Context
- The project uses a custom SQL migration runner with `-- UP` / `-- DOWN` sections in `.sql` files.
- The `JobItem` model is a SQLModel (Pydantic + SQLAlchemy hybrid) with a `to_dict()` method for serialization.
- Latest migration version: `20260421_000001`. Use `20260422_000001` for this migration.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create SQL migration file | Add `deleted_at TEXT DEFAULT NULL` column to `job_queue_items`. Include `-- DOWN` to drop the column. | `daemon/migrations/versions/20260422_000001_add_job_soft_delete.sql` |
| 2 | Update `JobItem` model | Add `deleted_at: Optional[str] = None` field to the `JobItem` class. Place it near other timestamp fields (after `cancelled_at`). | `daemon/repositories/job_queue/models.py` |
| 3 | Update `JobItem.to_dict()` | Add `"deleted_at": self.deleted_at` to the dict output. | `daemon/repositories/job_queue/models.py` |
| 4 | Update `__init__.py` exports | No new exports needed — `JobItem` is already exported. Verify no changes needed. | `daemon/repositories/job_queue/__init__.py` |

## Key Files
- `daemon/repositories/job_queue/models.py` — JobItem model definition (add field + update to_dict)
- `daemon/migrations/versions/20260422_000001_add_job_soft_delete.sql` — New migration file

## Constraints
- Migration must be idempotent (handle case where column already exists, e.g. `ALTER TABLE ... ADD COLUMN` in SQLite is naturally idempotent if wrapped with error handling)
- SQLite doesn't support `IF NOT EXISTS` for `ALTER TABLE ADD COLUMN` — the migration runner handles `duplicate column name` errors gracefully already
- Use ISO-8601 string format for timestamps (consistent with other timestamp fields in the project)

## Migration SQL (Reference)

```sql
-- Migration: add soft delete to jobs
-- Created: 2026-04-19
-- Description: Add deleted_at column to job_queue_items for soft delete support.

-- UP
ALTER TABLE job_queue_items ADD COLUMN deleted_at TEXT DEFAULT NULL;

-- Create index for efficient filtering of non-deleted jobs
CREATE INDEX IF NOT EXISTS idx_job_queue_deleted_at ON job_queue_items(deleted_at);

-- DOWN
DROP INDEX IF EXISTS idx_job_queue_deleted_at;
-- Note: SQLite doesn't support DROP COLUMN before 3.35.0
-- For older SQLite, the column will remain but be unused
```

## Deliverables
- [ ] Migration file created with UP and DOWN sections
- [ ] `JobItem` model has `deleted_at` field
- [ ] `to_dict()` includes `deleted_at`
- [ ] Migration applies cleanly on existing database
