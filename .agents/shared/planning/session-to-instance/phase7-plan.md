# Phase 7: SQL Migration & Config

## Objective
Create a new SQL migration to rename database tables and columns for existing deployments. Update `config.yaml` to use new key names. This phase ensures backward compatibility for existing databases.

## Context
- **Phase 2 completed**: Repository layer references new table names (`"instances"`, `"instance_mappings"`, `"instance_hierarchy"`)
- **Phase 3a completed**: Config access patterns renamed (`max_instances`, `instance_timeout_minutes`, etc.)
- Existing databases still have old table/column names — migration bridges the gap
- The message_queue table has a `session_id` column that also needs migration coverage

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create new SQL migration** | Create `migrations/versions/20260402_000001_rename_session_to_instance.sql` with ALTER TABLE statements for ALL tables/columns/indexes. See full list below. | New file: `migrations/versions/20260402_000001_rename_session_to_instance.sql` |
| 2 | **Create rollback migration (optional)** | Create `migrations/versions/20260402_000001_rename_session_to_instance_rollback.sql` that reverses all renames. | New file |
| 3 | **Update migration registry** | If there's a migration tracking file or registry, add the new migration to it. | `migrations/` |
| 4 | **Update config.yaml** | Rename keys: `max_sessions`→`max_instances`, `max_children_per_session`→`max_children_per_instance`, `session_timeout_minutes`→`instance_timeout_minutes`. Update `db_path` filename: `sessions.db`→`instances.db`. | `config.yaml` |
| 5 | **Update any config examples/docs** | If there are example configs or docs showing session config keys, update them. | `docs/`, `*.yaml.example` |

## Key Files
- New: `migrations/versions/20260402_000001_rename_session_to_instance.sql`
- `config.yaml` — configuration file
- Any migration tracking/registry files

## Migration Design — Complete ALTER TABLE List

### Table Renames
```sql
-- Core agent instance tables
ALTER TABLE sessions RENAME TO instances;
ALTER TABLE session_hierarchy RENAME TO instance_hierarchy;
ALTER TABLE session_mappings RENAME TO instance_mappings;
```

### Column Renames — instances (formerly sessions)
```sql
ALTER TABLE instances RENAME COLUMN session_id TO instance_id;
ALTER TABLE instances RENAME COLUMN session_metadata TO instance_metadata;
```

### Column Renames — instance_hierarchy (formerly session_hierarchy)
```sql
-- parent_id, child_id columns are generic names — no rename needed
-- Check if there are any session-named columns in this table
```

### Column Renames — instance_mappings (formerly session_mappings)
```sql
ALTER TABLE instance_mappings RENAME COLUMN agent_session_id TO agent_instance_id;
```

### Column Renames — other tables
```sql
-- schedule_executions
ALTER TABLE schedule_executions RENAME COLUMN session_id TO instance_id;

-- projects
ALTER TABLE projects RENAME COLUMN creator_session_id TO creator_instance_id;

-- job_queue_items
ALTER TABLE job_queue_items RENAME COLUMN session_id TO instance_id;

-- message_queue (IMPORTANT — added per reviewer feedback)
ALTER TABLE message_queue RENAME COLUMN session_id TO instance_id;
```

### Index Renames (SQLite: DROP + CREATE)
```sql
-- SQLite doesn't support ALTER INDEX RENAME
-- Drop old indexes and create new ones with instance naming
-- Check existing migration files for exact index names

-- sessions table indexes
DROP INDEX IF EXISTS ix_sessions_session_id;
CREATE INDEX ix_instances_instance_id ON instances(instance_id);

-- session_mappings indexes
DROP INDEX IF EXISTS ix_session_mappings_agent_session_id;
CREATE INDEX ix_instance_mappings_agent_instance_id ON instance_mappings(agent_instance_id);

-- message_queue indexes
DROP INDEX IF EXISTS ix_message_queue_session_id;
CREATE INDEX ix_message_queue_instance_id ON message_queue(instance_id);

-- job_queue_items indexes
DROP INDEX IF EXISTS ix_job_queue_items_session_id;
CREATE INDEX ix_job_queue_items_instance_id ON job_queue_items(instance_id);

-- Add any other session-named indexes found in initial migration
```

## Dependencies
- **Depends on Phase 2**: Repository layer defines the new table/column names that the migration must produce
- **Depends on Phase 3a**: Config key renames in config.py must match config.yaml changes

## Constraints
- **CRITICAL**: Do NOT modify the initial schema migration (`20250326_000000_initial_schema.sql`). It should remain as-is for historical accuracy.
- New databases (fresh installs) will use the new table names from Phase 1 model changes. Existing databases need this migration.
- SQLite has limited ALTER TABLE support — `ALTER TABLE ... RENAME COLUMN` is available in SQLite 3.25.0+ (2018). If the project targets older SQLite, may need table recreation pattern.
- Test migration on a copy of a real database before committing
- The `db_path` change from `sessions.db` to `instances.db` means existing DB files need to be renamed manually or handled by the migration

## Risk: SQLite Limitations
SQLite doesn't support all ALTER TABLE operations. For complex renames:
1. Check if `ALTER TABLE ... RENAME COLUMN` works for the SQLite version in use
2. If not, may need: create new table → copy data → drop old table → rename new table
3. Test with the project's SQLite version

## Verification
```bash
# 1. New migration file exists
ls migrations/versions/20260402_*rename_session_to_instance.sql

# 2. Migration does NOT modify initial schema
# (Check git diff — initial schema should be unchanged)

# 3. Migration covers ALL tables with session columns
grep -c "RENAME" migrations/versions/20260402_*rename_session_to_instance.sql
# Should have entries for: instances, instance_hierarchy, instance_mappings,
# schedule_executions, projects, job_queue_items, message_queue

# 4. Config updated
grep -E "max_sessions|session_timeout|sessions\.db" config.yaml  # should return 0

# 5. New config values present
grep -E "max_instances|instance_timeout|instances\.db" config.yaml
```

## Deliverables
- [ ] New SQL migration file created with ALL table/column/index renames (7 tables)
- [ ] Rollback migration (optional but recommended)
- [ ] `config.yaml` updated with new key names
- [ ] Initial schema migration unchanged
- [ ] Config examples/docs updated
- [ ] Grep verification passes
