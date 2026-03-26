# Database Migration System

A file-based database migration system for versioned, reversible schema changes. Migrations are SQL files that auto-apply on startup.

## Overview

The migration system ensures your database schema stays synchronized across:
- Fresh installations
- Existing deployments
- Multiple developers

### Key Features

- **Version Tracking**: Each migration has a timestamp version
- **Reversibility**: Support for UP (apply) and DOWN (rollback) sections
- **Auto-Apply**: Pending migrations run automatically on startup
- **Checksum Validation**: Detects modified migration files
- **Transaction Safety**: Each migration runs in its own transaction

## Quick Start

```bash
# 1. Create a new migration file
touch daemon/migrations/versions/$(date +%Y%m%d_%H%M%S)_add_feature.sql

# 2. Edit the migration file with your SQL
# (see Migration Format below)

# 3. Restart the server - migrations auto-apply
./dev.sh

# 4. Check migration status
curl http://localhost:8079/api/migrations/status
```

## Migration File Format

### File Naming Convention

```
{YYYYMMDD}_{HHMMSS}_{description}.sql
```

**Examples:**
```
20250326_120000_add_user_email.sql
20250327_090000_create_sessions_index.sql
20250328_140000_add_priority_column.sql
```

### File Structure

```sql
-- Migration: add user email
-- Created: 2026-03-26
-- Author: your_name
-- Description: Adds email column to users table

-- UP
ALTER TABLE users ADD COLUMN email TEXT;

-- DOWN
-- SQLite does not support DROP COLUMN
-- This migration is not reversible in SQLite
```

### Sections

| Section | Required | Description |
|---------|----------|-------------|
| `-- UP` | **Yes** | SQL to apply the migration |
| `-- DOWN` | Recommended | SQL to rollback the migration |

#### Header Comments (Optional)

| Comment | Format | Purpose |
|---------|--------|---------|
| Migration name | `-- Migration: name` | Human-readable identifier |
| Created date | `-- Created: YYYY-MM-DD` | When the migration was created |
| Author | `-- Author: name` | Who created the migration |
| Description | `-- Description: text` | What the migration does |

### Parsing Rules

1. Content before `-- UP` is treated as header/comments
2. Content between `-- UP` and `-- DOWN` is the UP migration
3. Content after `-- DOWN` is the DOWN migration
4. Multiple SQL statements are separated by semicolons
5. Trailing whitespace is stripped from each section

## Creating a New Migration

### Step 1: Generate the Filename

Use the current timestamp:

```bash
# Bash
date +%Y%m%d_%H%M%S

# Example output: 20260326_143052
```

### Step 2: Create the File

```bash
touch daemon/migrations/versions/20260326_143052_add_user_preferences.sql
```

### Step 3: Write the Migration

```sql
-- Migration: add user preferences
-- Created: 2026-03-26
-- Author: developer
-- Description: Adds preferences JSON column to users for storing user settings

-- UP
ALTER TABLE users ADD COLUMN preferences TEXT DEFAULT '{}';

-- DOWN
-- SQLite does not support DROP COLUMN
-- This migration is not directly reversible
```

### Step 4: Restart the Server

```bash
./dev.sh
```

The server logs will show:
```
INFO - Applying migration: 20260326_143052 - add user preferences
INFO - Completed migration 20260326_143052 in 5ms
```

## Migration Examples

### Adding a Column

```sql
-- Migration: add priority to sessions
-- Created: 2026-03-26

-- UP
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;

-- DOWN
-- SQLite does not support DROP COLUMN
-- Migration is not reversible
```

### Adding a Column with Data Backfill

```sql
-- Migration: populate agent_id from agent_dir
-- Created: 2024-01-03

-- UP
ALTER TABLE sessions ADD COLUMN agent_id TEXT;

-- Backfill existing rows
UPDATE sessions 
SET agent_id = REPLACE(agent_dir, 'agents/', '')
WHERE agent_dir IS NOT NULL;

-- DOWN
-- Data backfill cannot be reversed
```

### Creating an Index

```sql
-- Migration: add index on priority
-- Created: 2026-03-26

-- UP
CREATE INDEX IF NOT EXISTS idx_sessions_priority ON sessions(priority);

-- DOWN
DROP INDEX IF EXISTS idx_sessions_priority;
```

### Baseline Migration (Empty UP)

```sql
-- Migration: initial schema baseline
-- Created: 2025-03-26
-- Description: Baseline migration capturing initial schema state

-- UP
-- Tables are created via SQLModel.metadata.create_all()
-- This migration records the baseline state

-- DOWN
DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS projects;
```

## SQLite-Specific Considerations

### No DROP COLUMN (Pre-3.35.0)

SQLite versions before 3.35.0 don't support `DROP COLUMN`. For rollback:

**Option 1: No-op rollback**
```sql
-- DOWN
-- Migration is not reversible in SQLite
```

**Option 2: Recreate table (SQLite 3.35.0+)**
```sql
-- DOWN
BEGIN;
CREATE TABLE sessions_backup AS SELECT 
    id, name, created_at  -- exclude the new column
FROM sessions;
DROP TABLE sessions;
ALTER TABLE sessions_backup RENAME TO sessions;
COMMIT;
```

### SQLite-Safe Patterns

```sql
-- Good: ADD COLUMN with default (always safe)
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;

-- Good: CREATE INDEX (reversible)
CREATE INDEX IF NOT EXISTS idx_priority ON sessions(priority);

-- Good: Check column exists (idempotent)
-- The runner handles "duplicate column name" errors gracefully
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS email TEXT;
```

## Auto-Migration on Startup

Migrations run automatically when the server starts:

```
┌─────────────────┐
│  Server Start   │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│ SQLModel.metadata.create_all() │
│ (Creates tables)                │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ MigrationRunner.run_pending()  │
│                                 │
│ 1. ensure_migrations_table()   │
│ 2. discover_migrations()        │
│ 3. filter pending               │
│ 4. apply each in order          │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Server Ready                    │
└─────────────────────────────────┘
```

### Integration Points

**`daemon/manager.py`** (line 279):
```python
from .migrations.runner import MigrationRunner

migration_runner = MigrationRunner(self._engine)
applied = migration_runner.run_pending_migrations()
if applied:
    logger.info(f"Applied {len(applied)} migrations: {applied}")
```

## Checking Migration Status

### Via API

```bash
curl http://localhost:8079/api/migrations/status
```

Response:
```json
{
  "applied": ["20240101_000001", "20240102_000002", "20250326_000000"],
  "pending": ["20260326_143052"],
  "total_discovered": 7,
  "last_applied": "20260326_143052"
}
```

### Via Python

```python
from daemon.migrations import MigrationRunner

runner = MigrationRunner(engine)
status = runner.get_migration_status()

print(f"Applied: {status['applied']}")
print(f"Pending: {status['pending']}")
print(f"Total: {status['total_discovered']}")
```

### Via Database

```sql
SELECT * FROM schema_migrations ORDER BY applied_at DESC;
```

## Rolling Back Migrations

### Via API

```bash
curl -X POST http://localhost:8079/api/migrations/rollback/20240101_000001
```

### Via Python

```python
runner = MigrationRunner(engine)
runner.rollback_migration("20240101_000001")
```

### Requirements for Rollback

1. Migration must have a `-- DOWN` section
2. DOWN section must be non-empty
3. Migration must not have data that can't be recovered

### Rollback Limitations

- **SQLite**: Can't drop columns added by UP
- **Data Loss**: Backfilled data is lost on rollback
- **Dependencies**: Other migrations may depend on the schema

## Best Practices

### 1. Small, Focused Migrations

**Good**: One logical change per migration
```sql
-- UP
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;
```

**Avoid**: Multiple unrelated changes
```sql
-- UP
-- Bad: mixing schema and index changes
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN tags TEXT;
CREATE INDEX idx_priority ON sessions(priority);
```

### 2. Idempotent Operations

The migration runner handles these gracefully:
- `duplicate column name` errors
- `no such table` errors (table created by SQLModel)

### 3. Document Non-Reversible Migrations

```sql
-- DOWN
-- SQLite does not support DROP COLUMN
-- This migration is NOT REVERSIBLE
-- Data backfilled in UP cannot be recovered
```

### 4. Test Migrations

1. Apply migration: `./dev.sh`
2. Verify changes: Check table schema
3. Rollback (if possible): API call
4. Verify rollback: Check table schema
5. Re-apply: Restart server

### 5. Never Modify Applied Migrations

Once a migration is applied:
- **Do not edit** the migration file
- **Create a new migration** to fix issues
- **Use checksum** to detect modifications

## Troubleshooting

### Migration Skipped

**Symptom**: Migration file exists but doesn't appear in `schema_migrations`

**Cause**: Column/table already exists (handled gracefully)

**Solution**: This is normal - the migration runner skips statements that fail with "duplicate column name" or "no such table"

### Migration Fails

**Symptom**: Server won't start, error in logs

**Cause**: Invalid SQL syntax or constraint violation

**Solution**:
1. Check the migration file syntax
2. Verify SQL works in SQLite directly
3. Check for conflicts with existing schema

### Duplicate Version Error

**Symptom**: `UNIQUE constraint failed: schema_migrations.version`

**Cause**: Migration with same version already applied

**Solution**: Use a unique timestamp for new migrations

### Checksum Mismatch Warning

**Symptom**: Log shows migration checksum mismatch

**Cause**: Migration file was modified after application

**Solution**:
1. Never modify applied migrations
2. Create a new migration for fixes
3. Investigate unauthorized changes

### "No such table" Warning

**Symptom**: Log shows `table doesn't exist yet, skipping`

**Cause**: Table created by SQLModel but not yet in database

**Solution**: This is normal - the migration runner handles this case. The SQLModel `create_all()` will create the table.

## Programmatic Usage

### Apply Pending Migrations

```python
from daemon.migrations import MigrationRunner

runner = MigrationRunner(engine)
applied = runner.run_pending_migrations()
print(f"Applied {len(applied)} migrations")
```

### Get Migration Status

```python
runner = MigrationRunner(engine)
status = runner.get_migration_status()

print(f"Applied: {status['applied']}")
print(f"Pending: {status['pending']}")
print(f"Total: {status['total_discovered']}")
print(f"Last: {status['last_applied']}")
```

### Rollback a Migration

```python
runner = MigrationRunner(engine)
execution_time = runner.rollback_migration("20240101_000001")
print(f"Rolled back in {execution_time}ms")
```

### Discover All Migrations

```python
runner = MigrationRunner(engine)
migrations = runner.discover_migrations()

for m in migrations:
    print(f"{m.version}: {m.name}")
    print(f"  UP: {m.up_sql[:50]}...")
    print(f"  DOWN: {m.down_sql[:50]}...")
```

## File Structure

```
daemon/migrations/
├── __init__.py           # Public API
├── models.py             # SchemaMigration model
├── runner.py             # MigrationRunner class
└── versions/             # Migration files
    ├── 20240101_000001_add_job_queue_paused.sql
    ├── 20240102_000002_add_creator_agent_id.sql
    ├── 20240103_000003_add_agent_id_sessions.sql
    ├── 20240104_000004_add_agent_id_session_mappings.sql
    ├── 20240105_000005_add_agent_id_jobqueue.sql
    ├── 20240106_000006_add_agent_id_job_queue_items.sql
    └── 20250326_000000_initial_schema.sql
```

## API Reference

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/migrations/status` | Get migration status |
| POST | `/api/migrations/rollback/{version}` | Rollback a migration |

### Status Response

```json
{
  "applied": ["20240101_000001", "20240102_000002"],
  "pending": ["20260326_143052"],
  "total_discovered": 3,
  "last_applied": "20240102_000002"
}
```

### Python API

```python
from daemon.migrations import MigrationRunner, MigrationFile, MigrationError

# Initialize
runner = MigrationRunner(engine)

# Apply pending migrations
runner.run_pending_migrations()

# Check status
status = runner.get_migration_status()

# Rollback
runner.rollback_migration(version)

# Discover
migrations = runner.discover_migrations()
```

## See Also

- [Migration System Design](../docs/migration-system-design.md) - Architecture details
- [Job Queue Documentation](../docs/features/job-queue.md) - Queue system using migrations
- [Repository Factory](../daemon/repositories/factory.py) - Integration point
