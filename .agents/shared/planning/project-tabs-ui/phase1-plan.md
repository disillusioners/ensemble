# Phase 1: Backend — Schema & Migration

## Objective
Add a nullable `project_id` column to the `instances` SQLModel table, with a SQL migration that backfills values from the existing `metadata` JSON column where available. Also update serialization and creation models to include the new field.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/repositories/instance/models.py`, `daemon/repositories/instance/repository.py`, `daemon/models/instance.py`
- **Shared APIs/interfaces**: Instance model schema, InstanceInfo response, InstanceCreate request
- **Why this coupling**: Phase 2 queries the column and uses the models updated here

## Context
- Current instance model has NO `project_id` column
- `project_id` is stored in `metadata` JSON field (the actual SQLite column name is `metadata`, mapped via `sa_column=Column("metadata", JSON)` at `daemon/repositories/instance/models.py:58`)
- `Instance.to_dict()` at `daemon/repositories/instance/models.py:78-95` serializes fields explicitly — new columns are NOT automatically included
- `InstanceCreate` at `daemon/models/instance.py:19-38` is the API creation model — must accept `project_id`
- Database is SQLite with SQLModel (SQLAlchemy-based)
- Migration system uses `daemon/migrations/runner.py` (MigrationRunner) with SQL files in `daemon/migrations/versions/` using `-- UP` / `-- DOWN` sections
- Existing migration: `20260424_000001_backfill_null_project_ids.sql` for job_queue_items — use a DIFFERENT version number
- `spawn_instance` already validates and stores project_id in metadata — the new column duplicates this as a first-class field

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `project_id` column to Instance model | Nullable `str | None` field with `default=None`, `index=True`. Place alongside other columns in the model. | `daemon/repositories/instance/models.py` |
| 2 | Update `Instance.to_dict()` to include `project_id` | Add `"project_id": self.project_id` to the explicit serialization at lines 78-95. This method won't pick up new columns automatically. | `daemon/repositories/instance/models.py:78-95` |
| 3 | Create SQL migration file | File: `daemon/migrations/versions/20260514_000001_add_project_id_to_instances.sql`. Use `-- UP` / `-- DOWN` format consumed by MigrationRunner. | New file |
| 4 | Migration UP: ADD COLUMN + backfill | `ALTER TABLE instances ADD COLUMN project_id VARCHAR;` then backfill from `metadata` JSON: `UPDATE instances SET project_id = json_extract(metadata, '$.project_id') WHERE json_extract(metadata, '$.project_id') IS NOT NULL;` (column is `metadata`, NOT `instance_metadata`) | Migration SQL file |
| 5 | Migration DOWN: DROP COLUMN | `ALTER TABLE instances DROP COLUMN project_id;` (SQLite 3.35.0+ supports DROP COLUMN) | Migration SQL file |
| 6 | Add `project_id` to `InstanceCreate` model | Add optional `project_id: str | None = None` field so API consumers can specify project at instance creation | `daemon/models/instance.py:19-38` |
| 7 | Wire `InstanceCreate.project_id` into instance creation flow | When `spawn_instance` creates an instance, set the `project_id` column from `InstanceCreate.project_id` (in addition to storing in metadata) | Instance creation code path |
| 8 | Write unit tests | Test: migration idempotency, backfill from JSON, `to_dict()` includes `project_id`, `InstanceCreate` accepts `project_id` | `tests/` directory |

## Key Files
- `daemon/repositories/instance/models.py` — Instance SQLModel table + `to_dict()` method
- `daemon/models/instance.py` — API models: `InstanceInfo`, `InstanceCreate`
- `daemon/migrations/versions/20260514_000001_add_project_id_to_instances.sql` — New migration
- `daemon/migrations/runner.py` — Existing MigrationRunner (reference only, no changes needed)
- `daemon/repositories/instance/repository.py` — Instance CRUD (may need creation update)

## Implementation Notes

### ⚠️ CRITICAL: Column name is `metadata` (not `instance_metadata`)
The SQLModel model maps: `sa_column=Column("metadata", JSON)` at line 58. All SQL must reference `metadata`:
```sql
-- CORRECT
UPDATE instances SET project_id = json_extract(metadata, '$.project_id')
WHERE json_extract(metadata, '$.project_id') IS NOT NULL;

-- WRONG (old plan had this)
UPDATE instances SET project_id = json_extract(instance_metadata, '$.project_id') ...
```

### Migration File Format
```sql
-- UP
ALTER TABLE instances ADD COLUMN project_id VARCHAR;

-- Backfill from existing metadata JSON
UPDATE instances SET project_id = json_extract(metadata, '$.project_id')
WHERE json_extract(metadata, '$.project_id') IS NOT NULL;

-- DOWN
ALTER TABLE instances DROP COLUMN project_id;
```

### to_dict() Update
The method at `models.py:78-95` explicitly lists fields. Add `project_id`:
```python
def to_dict(self) -> dict:
    return {
        # ... existing fields ...
        "project_id": self.project_id,  # NEW
    }
```

### InstanceCreate Update
```python
class InstanceCreate(BaseModel):
    # ... existing fields ...
    project_id: str | None = None  # NEW
```

### Version Number
Use `20260514_000001` — the existing migration `20260424_000001` is for `job_queue_items`. This avoids version conflicts.

## Constraints
- Must be backward compatible — nullable column, no data loss
- Must work with SQLite (ADD COLUMN supported; DROP COLUMN requires SQLite 3.35.0+)
- Migration must be idempotent (safe to run multiple times via MigrationRunner)
- `to_dict()` must explicitly include new field — it does NOT auto-discover columns
- All references to the JSON metadata column must use `metadata` (the actual SQLite column name)

## Deliverables
- [ ] `project_id` column added to Instance model
- [ ] `Instance.to_dict()` returns `project_id`
- [ ] SQL migration file with UP (add + backfill) and DOWN (drop) sections
- [ ] `InstanceCreate` model accepts `project_id`
- [ ] Instance creation flow sets `project_id` column
- [ ] Unit tests for migration and serialization pass
- [ ] Existing tests still pass (no regressions)
