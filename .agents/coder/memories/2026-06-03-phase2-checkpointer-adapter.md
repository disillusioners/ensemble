# Phase 2 Implementation — CheckpointerAdapter + PostgreSQL Drivers

## Date: 2026-06-03
## Commit: 8c76247

## What Was Done

Phase 2 of SQLite → PostgreSQL migration: PostgreSQL driver support, CheckpointerAdapter abstraction, and maintenance service refactoring.

### Key Files Created
- `daemon/checkpoint_adapter.py` — CheckpointerAdapter ABC + SQLite/PG implementations (416 lines)

### Key Files Modified
- `daemon/persistence.py` — PostgreSQL checkpointer creation with lazy imports
- `daemon/services/maintenance.py` — All .conn/.lock access replaced with adapter calls
- `daemon/manager.py` — Adapter wiring into init/shutdown lifecycle
- `pyproject.toml` — Optional [postgres] dependency group
- 3 service files — Removed dangling CheckpointSaver imports

## Critical Bug Found in Review

The PG adapter's `delete_checkpoints_excluding()` and `delete_writes_excluding()` used `= ANY($3::text[])` instead of `NOT (checkpoint_id = ANY($3::text[]))`. This would have deleted the KEEP ids instead of the OLD ones on PostgreSQL. Caught by code review, not by tests (because no PG adapter tests exist yet).

**Lesson**: The `= ANY()` vs `NOT (= ANY())` is the PG equivalent of `IN` vs `NOT IN`. Easy to get wrong.

## Checkpoint Serialization Compatibility

- SQLite: msgpack BLOB for checkpoint/metadata, `type='msgpack'`
- PostgreSQL: JSONB for checkpoint/metadata, `type=NULL` (JsonPlusSerializer)
- Migration path: `serde.loads_typed((type, blob))` → Python dict → `json.dumps()` → JSONB
- `checkpoint_writes` table has different column layouts (SQLite: value/checkpoint_id/task_id, PG: blob/version)
- Documented in `.agents/shared/planning/sqlite-to-postgres-migration/checkpoint-compatibility.md`

## Test Infrastructure
- PostgreSQL 14 running locally via Homebrew
- Database: `ensemble_test`, User: `ensemble`, Password: `ensemble_dev`
- Connection strings: `postgresql+psycopg://` (SQLAlchemy) and `postgresql://` (asyncpg)

## Test Results
- 2,420+ unit tests passing
- 21/21 Phase 2 verification tests passing (PG connectivity, schema creation, checkpoint compat)
- 1 pre-existing failure (unrelated to Phase 2)

## Open Items for Later Phases
- No direct unit tests for adapter SQL (I1 from review)
- _prune_thread_checkpoints is no longer atomic (W1 from review)
- Pool sizing hardcoded (W4 from review)
