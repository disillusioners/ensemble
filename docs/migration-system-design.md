# Database Migration System Architecture

## Overview

This document defines the architecture for a file-based database migration system supporting versioned, reversible schema changes for the agents-ensemble project.

### Goals

- **Version Control**: Track applied migrations with timestamps
- **Reversibility**: Support up/down migrations for rollback capability
- **Automation**: Auto-detect and apply pending migrations on startup
- **Safety**: Transaction-wrapped migrations with clear error handling
- **Simplicity**: SQL-based migrations (no ORM migration DSL to learn)

### Non-Goals

- Multi-database support (SQLite only for now)
- Automatic migration generation from model changes
- Parallel migration execution

---

## Architecture Overview

```
daemon/
├── migrations/
│   ├── __init__.py
│   ├── runner.py           # MigrationRunner class
│   ├── models.py           # SchemaMigration SQLModel
│   └── versions/           # Migration files
│       ├── 20250326_120000_initial_schema.sql
│       ├── 20250326_130000_add_job_queue_paused.sql
│       └── ...
└── repositories/
    └── factory.py          # Integration point
```

### Components

| Component | Responsibility |
|-----------|---------------|
| `SchemaMigration` | SQLModel for tracking applied migrations |
| `MigrationRunner` | Discovers, orders, and executes migrations |
| `versions/*.sql` | Individual migration files with up/down sections |
| `factory.py` | Integration point calling `MigrationRunner` |

---

## Database Schema

### `schema_migrations` Table

```sql
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,          -- Migration version (timestamp)
    name TEXT NOT NULL,                -- Human-readable name
    applied_at TEXT NOT NULL,          -- ISO 8601 timestamp
    execution_time_ms INTEGER,         -- Duration in milliseconds
    checksum TEXT                      -- SHA-256 of migration content
);
```

### SQLModel Definition

```python
# daemon/migrations/models.py
from datetime import datetime
from sqlmodel import Field, SQLModel


class SchemaMigration(SQLModel, table=True):
    """Tracks applied database migrations."""
    
    __tablename__ = "schema_migrations"
    
    version: str = Field(primary_key=True, description="Migration version (YYYYMMDD_HHMMSS)")
    name: str = Field(description="Human-readable migration name")
    applied_at: str = Field(description="ISO 8601 timestamp when applied")
    execution_time_ms: int | None = Field(default=None, description="Execution duration in ms")
    checksum: str | None = Field(default=None, description="SHA-256 hash of migration content")
```

---

## Migration File Format

### File Naming Convention

```
{timestamp}_{description}.sql

Where:
- timestamp: YYYYMMDD_HHMMSS format
- description: lowercase_with_underscores, brief description
```

### Examples

```
migrations/versions/
├── 20250326_120000_initial_schema.sql
├── 20250326_130000_add_job_queue_paused_to_projects.sql
├── 20250327_090000_add_creator_agent_id_to_projects.sql
├── 20250328_140000_add_agent_id_to_sessions.sql
└── 20250329_100000_add_message_priority.sql
```

### File Structure

Each migration file contains both `-- UP` and `-- DOWN` sections:

```sql
-- Migration: add_job_queue_paused_to_projects
-- Created: 2025-03-26
-- Author: system

-- UP
ALTER TABLE projects ADD COLUMN job_queue_paused BOOLEAN DEFAULT 0;

-- DOWN
-- SQLite doesn't support DROP COLUMN, so we recreate the table
CREATE TABLE projects_backup AS SELECT 
    id, name, description, created_at, updated_at, settings
FROM projects;
DROP TABLE projects;
ALTER TABLE projects_backup RENAME TO projects;
```

#### Section Markers

| Marker | Required | Description |
|--------|----------|-------------|
| `-- UP` | Yes | SQL to apply the migration |
| `-- DOWN` | Yes | SQL to reverse the migration |
| `-- Migration:` | No | Human-readable name |
| `-- Created:` | No | Creation date |
| `-- Author:` | No | Author information |

#### Parsing Rules

1. Content before `-- UP` is treated as header/comments
2. Content between `-- UP` and `-- DOWN` is the up migration
3. Content after `-- DOWN` is the down migration
4. Trailing whitespace is stripped from each section

---

## Module Design (Final Implementation)

### `daemon/migrations/runner.py` (Key Methods)

```python
class MigrationRunner:
    """Discovers and executes database migrations."""
    
    def __init__(self, engine: Engine, migrations_dir: Path | None = None):
        self.engine = engine
        self.migrations_dir = migrations_dir or Path(__file__).parent / "versions"
    
    def ensure_migrations_table(self) -> None:
        """Create the schema_migrations table if it doesn't exist.
        
        Uses raw SQL to avoid conflicts with SQLModel metadata.
        """
        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
            )
            if not result.fetchone():
                conn.execute(text("""
                    CREATE TABLE schema_migrations (
                        version TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL,
                        execution_time_ms INTEGER,
                        checksum TEXT
                    )
                """))
                conn.commit()
    
    def get_applied_versions(self) -> set[str]:
        """Get set of applied migration versions."""
        with Session(self.engine) as session:
            migrations = session.exec(select(SchemaMigration)).all()
            return {m.version for m in migrations}
    
    def discover_migrations(self) -> list[MigrationFile]:
        """Discover and parse all migration files, sorted by version."""
        migrations = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            try:
                migration = MigrationFile.parse(path)
                migrations.append(migration)
            except ValueError as e:
                logger.warning(f"Skipping invalid migration file {path.name}: {e}")
        return sorted(migrations, key=lambda m: m.version)
    
    def apply_migration(self, migration: MigrationFile) -> float:
        """Apply a single migration within a transaction.
        
        Gracefully handles 'duplicate column name' and 'no such table' errors.
        """
        start_time = time.perf_counter()
        
        with self.engine.begin() as conn:
            statements = [s.strip() for s in migration.up_sql.split(";") if s.strip()]
            for stmt in statements:
                if stmt:
                    try:
                        conn.execute(text(stmt))
                    except Exception as stmt_err:
                        err_str = str(stmt_err).lower()
                        if "duplicate column name" in err_str:
                            logger.warning(f"Column already exists, skipping")
                        elif "no such table" in err_str:
                            logger.warning(f"Table doesn't exist yet, skipping")
                        else:
                            raise
            
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            record = SchemaMigration(
                version=migration.version,
                name=migration.name,
                applied_at=datetime.now(timezone.utc).isoformat(),
                execution_time_ms=execution_time_ms,
                checksum=migration.checksum,
            )
            session = Session(bind=conn)
            session.add(record)
            session.commit()
        
        return execution_time_ms
    
    def run_pending_migrations(self) -> list[str]:
        """Apply all pending migrations. Returns list of applied versions."""
        self.ensure_migrations_table()
        pending = self.get_pending_migrations()
        
        if not pending:
            return []
        
        applied = []
        for migration in pending:
            self.apply_migration(migration)
            applied.append(migration.version)
        
        return applied
    
    def get_migration_status(self) -> dict[str, object]:
        """Get migration status."""
        self.ensure_migrations_table()
        applied = sorted(self.get_applied_versions())
        all_migrations = self.discover_migrations()
        pending = [m.version for m in self.get_pending_migrations()]
        
        return {
            "applied": applied,
            "pending": pending,
            "total_discovered": len(all_migrations),
            "last_applied": applied[-1] if applied else None,
        }
```

### `daemon/migrations/__init__.py`

```python
"""Database migration system."""

from .models import SchemaMigration
from .runner import MigrationError, MigrationFile, MigrationRunner

__all__ = [
    "MigrationRunner",
    "MigrationFile",
    "MigrationError",
    "SchemaMigration",
]
```

---

## Integration Points

### Repository Factory Integration

Modify `daemon/repositories/factory.py`:

```python
from daemon.migrations import MigrationRunner

def run_migrations(engine: Engine) -> None:
    """Run database migrations.
    
    This replaces the previous inline migration approach with
    the new file-based migration system.
    """
    runner = MigrationRunner(engine)
    try:
        applied = runner.run_pending_migrations()
        if applied:
            logger.info(f"Applied {len(applied)} migrations: {applied}")
    except MigrationError as e:
        logger.error(f"Migration failed: {e}")
        raise
```

### Startup Integration

In `daemon/manager.py`:

```python
from daemon.migrations import MigrationRunner

class SessionManager:
    def _initialize_database(self) -> None:
        """Initialize database with schema and migrations."""
        # Create tables from SQLModel metadata
        SQLModel.metadata.create_all(self.engine)
        
        # Run any pending migrations
        runner = MigrationRunner(self.engine)
        runner.run_pending_migrations()
```

---

## CLI Commands

Add migration management commands to the API:

```python
# daemon/api.py

@app.get("/api/migrations/status")
async def get_migration_status() -> dict:
    """Get the current migration status."""
    runner = MigrationRunner(get_engine())
    return runner.get_migration_status()


@app.post("/api/migrations/rollback/{version}")
async def rollback_migration(version: str) -> dict:
    """Rollback a specific migration."""
    runner = MigrationRunner(get_engine())
    try:
        execution_time = runner.rollback_migration(version)
        return {"status": "success", "version": version, "execution_time_ms": execution_time}
    except MigrationError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

---

## Usage Examples

### Creating a New Migration

1. **Create the file**:
   ```bash
   # Generate timestamped filename
   touch daemon/migrations/versions/$(date +%Y%m%d_%H%M%S)_add_new_column.sql
   ```

2. **Write the migration**:
   ```sql
   -- Migration: add_new_column
   -- Created: 2025-03-26
   
   -- UP
   ALTER TABLE sessions ADD COLUMN metadata TEXT;
   
   -- DOWN
   -- Note: SQLite doesn't support DROP COLUMN in older versions
   -- For full rollback, recreate table without the column
   ```

3. **Restart the server** - migrations auto-apply on startup

### Manual Migration Commands

```bash
# Check status via API
curl http://localhost:8000/api/migrations/status

# Rollback specific migration
curl -X POST http://localhost:8000/api/migrations/rollback/20250326_130000
```

### Programmatic Usage

```python
from sqlalchemy import Engine
from daemon.migrations import MigrationRunner

# Initialize runner
runner = MigrationRunner(engine)

# Check status
status = runner.get_migration_status()
print(f"Applied: {status['applied']}")
print(f"Pending: {status['pending']}")

# Apply pending migrations
applied = runner.run_pending_migrations()
print(f"Applied {len(applied)} migrations")

# Rollback a migration
runner.rollback_migration("20250326_130000")
```

---

## Error Handling

### Migration Failure

When a migration fails:

1. **Transaction rolls back** - no partial state
2. **Error logged** with full context
3. **Subsequent migrations don't run** - maintains consistency
4. **System continues** with previous schema

```python
try:
    runner.run_pending_migrations()
except MigrationError as e:
    logger.error(f"Migration failed: {e}")
    # System continues with existing schema
    # Admin must fix migration and retry
```

### Checksum Validation

If an applied migration file is modified:

```python
# Detect modified migration
applied = session.exec(
    SchemaMigration.select().where(SchemaMigration.version == version)
).first()

current_checksum = migration.checksum
if applied and applied.checksum != current_checksum:
    logger.warning(
        f"Migration {version} has been modified since application. "
        f"Stored checksum: {applied.checksum}, Current: {current_checksum}"
    )
```

---

## Best Practices

### 1. Idempotent Migrations

Make migrations safe to re-run:

```sql
-- UP
-- Check if column exists before adding
SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions';
-- Application code handles the conditional
```

### 2. Small, Focused Migrations

One logical change per migration:

```sql
-- GOOD: Single responsibility
-- UP
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;

-- BAD: Multiple changes
-- UP
ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN tags TEXT;
CREATE INDEX idx_priority ON sessions(priority);
```

### 3. SQLite Limitations

Handle SQLite's constraints:

```sql
-- DOWN for ALTER TABLE ADD COLUMN
-- SQLite doesn't support DROP COLUMN (pre-3.35.0)
-- Must recreate table:

-- DOWN
BEGIN;
CREATE TABLE sessions_backup AS SELECT 
    id, name, created_at, updated_at  -- exclude new column
FROM sessions;
DROP TABLE sessions;
ALTER TABLE sessions_backup RENAME TO sessions;
COMMIT;
```

### 4. Data Migrations

For data transformations:

```sql
-- UP
-- Add column first
ALTER TABLE sessions ADD COLUMN agent_id TEXT;

-- Populate from existing data
UPDATE sessions 
SET agent_id = SUBSTR(agent_dir, INSTR(agent_dir, '/') + 1)
WHERE agent_dir IS NOT NULL;
```

### 5. Never Modify Applied Migrations

Once a migration is applied to production:
- **Do not edit** the file
- **Create a new migration** to fix issues
- **Use rollback** only in development

---

## Migration from Current System

### Migration File Format (Final)

The final implementation uses a simpler file format with `-- UP` and `-- DOWN` section markers:

```sql
-- Migration: add job_queue_paused to projects
-- Created: 2024-01-01 (retrospective)
-- Author: system
-- Description: Add job_queue_paused column to projects table

-- UP
ALTER TABLE projects ADD COLUMN job_queue_paused BOOLEAN DEFAULT 0;

-- DOWN
-- SQLite does not support DROP COLUMN
-- Migration is not reversible in SQLite
```

### Section Markers (Final)

| Marker | Required | Description |
|--------|----------|-------------|
| `-- UP` | **Yes** | SQL to apply the migration |
| `-- DOWN` | Recommended | SQL to rollback (or note why not reversible) |
| `-- Migration:` | Optional | Human-readable name |
| `-- Created:` | Optional | Creation date |
| `-- Author:` | Optional | Author information |
| `-- Description:` | Optional | Detailed description |

### Parsing Rules (Final)

1. Content before `-- UP` is treated as header/comments
2. Content between `-- UP` and `-- DOWN` is the up migration
3. Content after `-- DOWN` is the down migration
4. Multiple SQL statements separated by semicolons
5. Trailing whitespace stripped from each section

### Step 1: Create Initial Schema Migration

Extract current schema to baseline migration:

```sql
-- daemon/migrations/versions/20250326_120000_initial_schema.sql

-- UP
-- This is the baseline - all tables already exist
-- Empty UP section since we're capturing existing state

-- DOWN
-- Would drop all tables - rarely needed for initial schema
```

### Step 2: Convert Existing Migrations

Move inline migrations from `factory.py` to files:

| Current | New File |
|---------|----------|
| `job_queue_paused` column | `20250326_130000_add_job_queue_paused.sql` |
| `creator_agent_id` column | `20250327_090000_add_creator_agent_id.sql` |
| `agent_id` column | `20250328_140000_add_agent_id.sql` |

### Step 3: Mark Existing Migrations as Applied

For existing databases:

```python
# One-time script to populate schema_migrations
from daemon.migrations import MigrationRunner, SchemaMigration
from sqlmodel import Session

def bootstrap_migrations(engine):
    runner = MigrationRunner(engine)
    runner.ensure_migrations_table()
    
    # Mark all discovered migrations as applied
    with Session(engine) as session:
        for migration in runner.discover_migrations():
            record = SchemaMigration(
                version=migration.version,
                name=migration.name,
                applied_at="2025-03-26T00:00:00+00:00",  # Bootstrap timestamp
                checksum=migration.checksum,
            )
            session.add(record)
        session.commit()
```

---

## Configuration

Add to `config.yaml`:

```yaml
migrations:
  enabled: true
  auto_apply: true           # Apply pending migrations on startup
  directory: daemon/migrations/versions
  stop_on_error: true        # Halt on migration failure
```

---

## Implementation Notes

### Differences from Initial Design

The production implementation has these differences from the initial design:

1. **Graceful Error Handling**: The runner catches `duplicate column name` and `no such table` errors internally rather than failing:
   ```python
   # Split on semicolons and execute each statement
   statements = [s.strip() for s in migration.up_sql.split(";") if s.strip()]
   for stmt in statements:
       try:
           conn.execute(text(stmt))
       except Exception as stmt_err:
           err_str = str(stmt_err).lower()
           if "duplicate column name" in err_str:
               logger.warning(f"Migration {version}: column already exists, skipping")
           elif "no such table" in err_str:
               logger.warning(f"Migration {version}: table doesn't exist yet, skipping")
           else:
               raise
   ```

2. **Raw SQL Table Creation**: Uses raw SQL instead of `SQLModel.metadata.create_all()`:
   ```python
   def ensure_migrations_table(self) -> None:
       with self.engine.connect() as conn:
           result = conn.execute(
               text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
           )
           if not result.fetchone():
               conn.execute(text("""CREATE TABLE schema_migrations (...)"""))
               conn.commit()
   ```

3. **Checksum as Property**: Checksum is computed on-demand as a property:
   ```python
   @property
   def checksum(self) -> str:
       content = self.path.read_text()
       return hashlib.sha256(content.encode()).hexdigest()
   ```

### Dual-Layer Migration System

The system uses two migration approaches:

| Layer | Location | Purpose |
|-------|----------|---------|
| File-based | `daemon/migrations/runner.py` | Versioned, reversible schema changes |
| Inline | `daemon/repositories/factory.py` | Legacy column additions (agent_id) |

Both run on startup for backward compatibility.

### Startup Integration

```python
# daemon/manager.py (line 279)
from .migrations.runner import MigrationRunner

migration_runner = MigrationRunner(self._engine)
applied = migration_runner.run_pending_migrations()
if applied:
    logger.info(f"Applied {len(applied)} migrations: {applied}")
```

### Existing Migration Files

```
daemon/migrations/versions/
├── 20240101_000001_add_job_queue_paused.sql
├── 20240102_000002_add_creator_agent_id.sql
├── 20240103_000003_add_agent_id_sessions.sql
├── 20240104_000004_add_agent_id_session_mappings.sql
├── 20240105_000005_add_agent_id_jobqueue.sql
├── 20240106_000006_add_agent_id_job_queue_items.sql
└── 20250326_000000_initial_schema.sql
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/migrations/status` | GET | Get applied/pending migrations |
| `/api/migrations/rollback/{version}` | POST | Rollback a specific migration |

---

## Summary

| Aspect | Decision |
|--------|----------|
| **Version Tracking** | `schema_migrations` table with version, timestamp, checksum |
| **File Format** | SQL files with `-- UP` and `-- DOWN` sections |
| **Naming** | `{YYYYMMDD_HHMMSS}_{description}.sql` |
| **Execution** | Transaction-wrapped, sequential, graceful error handling |
| **Rollback** | Supported via DOWN sections (SQLite limitations apply) |
| **Auto-apply** | On startup via `run_pending_migrations()` |
| **Integration** | `daemon/manager.py` calls `MigrationRunner.run_pending_migrations()` |
