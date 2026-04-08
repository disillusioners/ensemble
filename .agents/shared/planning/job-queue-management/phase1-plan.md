# Phase 1: DB Schema & Migration

## Objective
Create the `job_queues` table and add `queue_id` foreign key to `job_queue_items`. Since we're clearing all existing job data, the migration is straightforward: DELETE jobs → add column → seed system queues.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: 
  - `daemon/repositories/job_queue/models.py` — shared with Phases 2, 3
  - `daemon/migrations/versions/*.sql` — consumed at startup by migration runner
- **Shared APIs/interfaces**: `JobQueue` SQLModel class, `QueueType` enum
- **Why this coupling**: Data model is the foundation for all backend logic

## Context
- Current schema has `job_queue_items` table with `project_id` (nullable) and no queue concept
- Projects have `job_queue_paused` boolean field (single on/off)
- SQLite database with WAL mode, foreign keys enabled
- Migrations are SQL files with `-- UP` / `-- DOWN` sections, named `YYYYMMDD_HHMMSS_name.sql`
- MigrationRunner applies pending migrations at startup, idempotency via error suppression for "already exists"
- **Migration strategy:** Since we're not using the job system, we DELETE all existing jobs. No data preservation needed.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create `QueueType` enum** | `FIFO` and `PARALLEL` enum values | `daemon/repositories/job_queue/models.py` |
| 2 | **Create `JobQueue` SQLModel** | New table `job_queues` with fields: `queue_id` (PK, UUID), `project_id` (FK → projects), `queue_name` (str, max 100), `queue_name_lower` (str, max 100, for case-insensitive uniqueness), `queue_type` (QueueType enum), `concurrency_limit` (int, default 1), `is_paused` (bool, default False), `is_system` (bool, default False), `description` (optional str), `created_at` (str ISO), `updated_at` (str ISO). Unique constraint on `(project_id, queue_name_lower)`. CHECK constraint on `queue_type`. Index on `project_id`. | `daemon/repositories/job_queue/models.py` |
| 3 | **Add `queue_id` to `JobItem`** | Add `queue_id: Optional[str] = Field(default=None, foreign_key="job_queues.queue_id"))` to `JobItem`. Add index on `queue_id`. | `daemon/repositories/job_queue/models.py` |
| 4 | **Write migration SQL (UP)** | Create `YYYYMMDD_HHMMSS_add_job_queues_table.sql`. See detailed SQL below — includes: (a) DELETE job_queue_items (clean slate), (b) CREATE TABLE with CHECK constraint, (c) ALTER TABLE to add column, (d) INSERT system queue seeding, (e) CREATE INDEX | `daemon/migrations/versions/YYYYMMDD_HHMMSS_add_job_queues_table.sql` |
| 5 | **Write migration SQL (DOWN)** | DROP TABLE `job_queues` (SQLite limitation: column `queue_id` cannot be dropped from `job_queue_items`, document this). | Same file |
| 6 | **Create Pydantic response models** | `JobQueueResponse`, `JobQueueCreateRequest`, `JobQueueUpdateRequest` Pydantic models for API layer. Include `queue_name` normalization validator. | `daemon/routers/schemas.py` |

## Key Files
- `daemon/repositories/job_queue/models.py` — Add `QueueType`, `JobQueue` model, modify `JobItem`
- `daemon/migrations/versions/YYYYMMDD_HHMMSS_add_job_queues_table.sql` — New migration
- `daemon/routers/schemas.py` — Pydantic request/response models

## Detailed Schema Design

### `job_queues` table
```sql
CREATE TABLE IF NOT EXISTS job_queues (
    queue_id          TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(project_id),
    queue_name        TEXT NOT NULL,
    queue_name_lower  TEXT NOT NULL,
    queue_type        TEXT NOT NULL DEFAULT 'fifo'
                      CHECK(queue_type IN ('fifo', 'parallel')),
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_paused         BOOLEAN NOT NULL DEFAULT 0,
    is_system         BOOLEAN NOT NULL DEFAULT 0,
    description       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(project_id, queue_name_lower)
);

CREATE INDEX IF NOT EXISTS idx_job_queues_project ON job_queues(project_id);
```

### Migration UP — Full SQL
```sql
-- Migration: add_job_queues_table
-- Created: 2026-04-09
-- Description: Add named per-project job queues with system queue seeding
-- Strategy: DELETE all existing jobs (we are not using the job system)

-- STEP 1: DELETE all existing jobs (clean migration)
DELETE FROM job_queue_items;

-- STEP 2: Create the job_queues table
CREATE TABLE IF NOT EXISTS job_queues (
    queue_id          TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(project_id),
    queue_name        TEXT NOT NULL,
    queue_name_lower  TEXT NOT NULL,
    queue_type        TEXT NOT NULL DEFAULT 'fifo'
                      CHECK(queue_type IN ('fifo', 'parallel')),
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_paused         BOOLEAN NOT NULL DEFAULT 0,
    is_system         BOOLEAN NOT NULL DEFAULT 0,
    description       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(project_id, queue_name_lower)
);

CREATE INDEX IF NOT EXISTS idx_job_queues_project ON job_queues(project_id);

-- STEP 3: Add queue_id column to job_queue_items
ALTER TABLE job_queue_items ADD COLUMN queue_id TEXT REFERENCES job_queues(queue_id);
CREATE INDEX IF NOT EXISTS idx_job_queue_items_queue ON job_queue_items(queue_id);

-- STEP 4: Seed system queues for all existing projects
INSERT INTO job_queues (
    queue_id, project_id, queue_name, queue_name_lower,
    queue_type, concurrency_limit, is_paused, is_system,
    description, created_at, updated_at
)
SELECT 
    'sys-fifo-' || project_id,
    project_id,
    'system_fifo_queue',
    'system_fifo_queue',
    'fifo',
    1,
    0,
    1,
    'System FIFO queue - default, one job at a time',
    datetime('now'),
    datetime('now')
FROM projects;

INSERT INTO job_queues (
    queue_id, project_id, queue_name, queue_name_lower,
    queue_type, concurrency_limit, is_paused, is_system,
    description, created_at, updated_at
)
SELECT 
    'sys-parallel-' || project_id,
    project_id,
    'system_parallel_queue',
    'system_parallel_queue',
    'parallel',
    3,  -- default concurrency
    0,
    1,
    'System parallel queue - configurable concurrency',
    datetime('now'),
    datetime('now')
FROM projects;

-- DOWN
DROP TABLE IF EXISTS job_queues;
```

## Constraints
- SQLite: no native ALTER TABLE DROP COLUMN (queue_id on job_queue_items will remain even in DOWN migration)
- Migration strategy: DELETE all existing jobs — we are not using the job system
- `queue_name_lower` provides case-insensitive uniqueness at DB level (addresses W1)
- CHECK constraint on `queue_type` enforces valid values at DB level (addresses S1)
- Application-level validation enforces `concurrency_limit=1` for FIFO queues (addresses S2)
- Queue name reserved words: "system_fifo_queue" and "system_parallel_queue" cannot be used for custom queues

## Deliverables
- [ ] `QueueType` enum defined
- [ ] `JobQueue` SQLModel created with proper table, indexes, constraints, validators
- [ ] `queue_name_lower` column for case-insensitive uniqueness (W1)
- [ ] CHECK constraint on `queue_type` (S1)
- [ ] FIFO concurrency_limit validation (S2)
- [ ] `JobItem` model updated with `queue_id` FK
- [ ] Migration SQL file with DELETE + system queue seeding
- [ ] Pydantic request/response models for queue CRUD with name normalization
