# Phase 1: Database Schema

## Objective

Create the new `task` and `event` tables, enhance the `instance` table with new columns (status values, version, waiting_for, metadata), and create the corresponding SQLModel models and repositories. **Zero changes to existing code** — pure additive schema.

## Coupling

- **Depends on**: None
- **Coupling type**: independent (root phase)
- **Shared files with other phases**: None (this phase is pure addition)
- **Shared APIs/interfaces**: None
- **Why this coupling**: This phase only adds new tables and columns. Existing code continues to work unchanged. The interfaces this creates (Task/Event models, repositories) are consumed by later phases but don't affect existing code.

## Context

### What Exists Today

| Table | Model File | Key Fields |
|-------|-----------|-----------|
| `instances` | `daemon/repositories/instance/models.py` | instance_id, agent_id, status, parent_id, instance_metadata |
| `message_queue` | `daemon/repositories/message_queue/models.py` | message_id, instance_id, content, status, retry_count |
| `instance_hierarchy` | Junction table | parent_id, child_id |

### What We Add

<!-- FIX: C5 — new tables need CREATE TABLE migrations, not ALTER TABLE -->
| Table | Purpose |
|-------|---------|
| `task` | Explicit tasks for process_message / send_report / cleanup |
| `event` | SSE events persisted to DB for cursor-based delivery |

<!-- FIX: W1 — SQLite type mappings: JSONB → TEXT with JSON column type, TEXT[] → TEXT DEFAULT '[]', SERIAL → INTEGER PRIMARY KEY AUTOINCREMENT -->
### What We Enhance

| Table | New Columns |
|-------|------------|
| `instances` | status values (add `waiting_children`), version (INTEGER), children (TEXT DEFAULT '[]', denormalized cache from instance_hierarchy), waiting_for (INTEGER), last_activity_at (TEXT/ISO timestamp) |
| `message_queue` | type (TEXT), priority changes, processing_task_id (TEXT), reuse existing `processing_started_at` instead of new `started_at` <!-- FIX: W4 --> |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create task table schema | SQLModel model for task table with all fields per design doc | `daemon/repositories/task/models.py` (new) |
| 2 | Create task repository | CRUD + atomic claim (UPDATE ... RETURNING pattern for SQLite) | `daemon/repositories/task/repository.py` (new) |
| 3 | Create task repository tests | Test atomic claim, concurrent claiming, task lifecycle | `tests/message_queue_redesign/test_task_repository.py` (new) |
| 4 | Create event table schema | SQLModel model for event table (no `delivered` boolean — use cursor-based delivery via `Last-Event-ID`) <!-- FIX: C2 --> | `daemon/repositories/event/models.py` (new) |
| 5 | Create event repository | CRUD + get_events_since() for cursor-based delivery + periodic cleanup <!-- FIX: C2 --> | `daemon/repositories/event/repository.py` (new) |
| 6 | Create event repository tests | Test event creation, delivery tracking, ordering | `tests/message_queue_redesign/test_event_repository.py` (new) |
| 7 | Enhance instance model | Add status values (keep existing PAUSED, QUEUED, ERROR; add WAITING_CHILDREN, FAILED), version, children (denormalized cache), waiting_for, last_activity_at <!-- FIX: C4 W6 --> | `daemon/repositories/instance/models.py` (modify) |
| 8 | Create task table migration | CREATE TABLE migration for new task table <!-- FIX: C5 --> | `daemon/migrations/versions/202604XX_000001_create_task_table.sql` (new) |
| 9 | Create event table migration | CREATE TABLE migration for new event table (no delivered/delivered_at columns) <!-- FIX: C5 C2 --> | `daemon/migrations/versions/202604XX_000002_create_event_table.sql` (new) |
| 10 | Create instance enhancement migration | ALTER TABLE instances: add columns (status already TEXT so new values work), version, children, waiting_for, last_activity_at | `daemon/migrations/versions/202604XX_000003_enhance_instance_for_worker_pool.sql` (new) |
| 11 | Create message enhancement migration | ALTER TABLE message_queue: add type, processing_task_id; reuse existing processing_started_at <!-- FIX: W4 --> | `daemon/migrations/versions/202604XX_000004_enhance_message_for_worker_pool.sql` (new) |
| 12 | Verify migrations | Run migrations, verify schema in test DB | Manual verification |

## Key Files

### New Files

| File | Purpose |
|------|---------|
| `daemon/repositories/task/__init__.py` | Module exports |
| `daemon/repositories/task/models.py` | Task SQLModel with id, type, instance_id, message_id, status, worker_id, result, error, timestamps |
| `daemon/repositories/task/repository.py` | TaskRepository with atomic claim, poll, complete, fail, recovery queries |
| `daemon/repositories/event/__init__.py` | Module exports |
| `daemon/repositories/event/models.py` | Event SQLModel with id (INTEGER PRIMARY KEY AUTOINCREMENT), instance_id, message_id, type, data (TEXT/JSON), created_at <!-- FIX: C2 W1 — no delivered/delivered_at, use AUTOINCREMENT not SERIAL --> |
| `daemon/repositories/event/repository.py` | EventRepository with create, get_events_since (cursor-based), get_by_instance, cleanup_old |
| `tests/message_queue_redesign/__init__.py` | Test package |
| `tests/message_queue_redesign/conftest.py` | Shared fixtures for new tests |
| `tests/message_queue_redesign/test_task_repository.py` | Task repository unit tests |
| `tests/message_queue_redesign/test_event_repository.py` | Event repository unit tests |

### Modified Files

| File | Changes |
|------|---------|
| `daemon/repositories/instance/models.py` | Add new status values, version, children, waiting_for, last_activity_at fields |
| `daemon/repositories/message_queue/models.py` | Add type, processing_task_id, started_at fields |
| `daemon/repositories/__init__.py` | Export new repositories |

### Migration Files

<!-- FIX: C5 — proper naming convention (YYYYMMDD_NNNNNN), CREATE TABLE for new tables -->
| File | Changes |
|------|---------|
| `daemon/migrations/versions/202604XX_000001_create_task_table.sql` | CREATE TABLE IF NOT EXISTS task with all columns, indexes |
| `daemon/migrations/versions/202604XX_000002_create_event_table.sql` | CREATE TABLE IF NOT EXISTS event (no delivered/delivered_at columns) with indexes |
| `daemon/migrations/versions/202604XX_000003_enhance_instance_for_worker_pool.sql` | ALTER TABLE instances: ADD COLUMN version, children (TEXT DEFAULT '[]'), waiting_for, last_activity_at |
| `daemon/migrations/versions/202604XX_000004_enhance_message_for_worker_pool.sql` | ALTER TABLE message_queue: ADD COLUMN type, processing_task_id |

## Constraints

1. **Backward compatibility**: Existing code must continue working without modification
2. **SQLite compatibility**: All queries must work on SQLite (no PostgreSQL-specific features). Use `TEXT` with `JSON` column type instead of `JSONB`, `TEXT DEFAULT '[]'` instead of `TEXT[]`, `INTEGER PRIMARY KEY AUTOINCREMENT` instead of `SERIAL` <!-- FIX: W1 -->
3. **Idempotent migrations**: Migrations must handle re-running gracefully
4. **No functional changes**: This phase only changes schema, not behavior

## Task Repository Design

### Atomic Claim (SQLite-compatible)

Since SQLite doesn't support `FOR UPDATE SKIP LOCKED`, we use the atomic UPDATE-RETURNING pattern:

```python
def claim_pending_task(self, worker_id: str, task_type: str | None = None) -> Task | None:
    """Atomically claim the next pending task using UPDATE-RETURNING."""
    
    # Step 1: BEGIN IMMEDIATE to acquire write lock
    # Step 2: Find and claim in single UPDATE
    # Step 3: COMMIT
    
    with SQLModelSession(self.engine) as session:
        # Use raw SQL for RETURNING (SQLAlchemy SQLModel doesn't support RETURNING well)
        stmt = text("""
            UPDATE task
            SET status = 'running', 
                worker_id = :worker_id,
                started_at = :started_at
            WHERE id = (
                SELECT id FROM task
                WHERE status = 'pending'
                AND (:task_type IS NULL OR type = :task_type)
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *
        """)
        
        result = session.exec(stmt, params={
            "worker_id": worker_id,
            "task_type": task_type,
            "started_at": datetime.now(timezone.utc)
        })
        
        row = result.fetchone()
        session.commit()
        
        if row is None:
            return None
        
        return self._row_to_task(row)
```

### Key Queries

| Query | Purpose |
|-------|---------|
| `claim_pending_task()` | Atomic claim for any pending task |
| `claim_task_for_message()` | Claim specific task for a message |
| `complete_task()` | Mark task as completed with result |
| `fail_task()` | Mark task as failed with error |
| `find_stale_running_tasks()` | Find tasks running beyond threshold (configurable, default 15 min) (crash recovery) <!-- FIX: W5 --> |
| `get_pending_count()` | Count pending tasks for monitoring |

## Event Repository Design

### Event Types (from design doc)

| Type | Purpose |
|------|---------|
| `message_received` | New message enqueued |
| `processing_started` | Worker picked up message |
| `processing_completed` | Message processed successfully |
| `processing_failed` | Message processing failed |
| `child_completed` | Child instance completed |
| `child_failed` | Child instance failed |
| `instance_completed` | All work done for instance |
| `error` | Error event |

### Key Queries

<!-- FIX: C2 — cursor-based delivery, no delivered boolean -->
| Query | Purpose |
|-------|---------|
| `create_event()` | Insert new event, return auto-generated id |
| `get_events_since(instance_id, after_event_id)` | Get events after cursor position (for SSE delivery and reconnection) |
| `get_by_instance()` | Get all events for an instance |
| `cleanup_old(max_age_hours)` | Delete events older than N hours (configurable, default 24h) |

## Instance Model Enhancement

<!-- FIX: C4 — complete status enum including existing values -->
### New and Existing Status Values

| Status | Source | Meaning |
|--------|--------|---------|
| `idle` | Existing | Instance exists, no current work |
| `running` | Existing | Actively processing a message |
| `paused` | Existing (keep) | Instance paused by user or system |
| `queued` | Existing (keep) | Instance waiting in job queue |
| `waiting_children` | **NEW** | Parent waiting for child completion reports |
| `completed` | Existing | All work done, can be terminated |
| `error` | Existing (keep) | Processing failed (backward compatible) |
| `failed` | **NEW** | Task-level failure (distinct from instance ERROR) |
| `terminated` | Existing | Manually terminated |

**Important**: Keep all existing status values (`PAUSED`, `QUEUED`, `ERROR`) — they are used by the Job Queue and other subsystems. Add `WAITING_CHILDREN` and `FAILED` as new values only.

<!-- FIX: W6 — junction table is canonical, children is denormalized cache -->
### New Fields

| Field | Type | Purpose |
|-------|------|---------|
| `children` | TEXT DEFAULT '[]' | Denormalized cache of child instance IDs; **instance_hierarchy junction table is the canonical source** — updated via application-level hook on spawn/complete |
| `waiting_for` | INTEGER DEFAULT 0 | Count of pending children (increments on spawn, decrements on complete) |
| `version` | INTEGER DEFAULT 1 | Optimistic locking version |
| `last_activity_at` | TEXT (ISO timestamp) | For watchdog timeout detection |

## Migration Testing

The migrations must be:
1. **Idempotent**: Can run multiple times without error
2. **Backward compatible**: Existing code unaffected
3. **Reversible**: DOWN migrations undo the changes

Test scenarios:
- Fresh database: migration creates all new columns
- Existing database with data: migration adds columns, preserves data
- Re-run migration: gracefully skips already-existing columns
- Rollback migration: restores original schema

## Deliverables

- [ ] `daemon/repositories/task/models.py` created with Task model
- [ ] `daemon/repositories/task/repository.py` created with TaskRepository
- [ ] `daemon/repositories/event/models.py` created with Event model (no delivered boolean) <!-- FIX: C2 -->
- [ ] `daemon/repositories/event/repository.py` created with EventRepository (cursor-based) <!-- FIX: C2 -->
- [ ] `daemon/repositories/instance/models.py` enhanced with new fields (keeping existing PAUSED, QUEUED, ERROR statuses) <!-- FIX: C4 -->
- [ ] `daemon/repositories/message_queue/models.py` enhanced with new fields (reusing processing_started_at) <!-- FIX: W4 -->
- [ ] CREATE TABLE migrations for task and event tables <!-- FIX: C5 -->
- [ ] ALTER TABLE migrations for instance and message_queue tables
- [ ] Unit tests pass for all new repositories
- [ ] Existing tests still pass (no regression)
- [ ] Manual verification: new tables exist, old tables unchanged
