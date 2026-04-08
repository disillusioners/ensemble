# Review: Phase 1 DB Schema & Migration for Named Per-Project Job Queues

**Date:** 2026-04-08
**Branch:** feature/job-queue-management
**Files:** models.py, migration SQL, schemas.py

## Key Findings
- 3 critical issues: queue_id missing from API, CHECK constraint gap, timestamp format mismatch
- 9 warnings: index naming inconsistency, constraint gaps, validation edge cases
- Branch uses `CREATE TABLE IF NOT EXISTS` which won't enforce CHECK on existing tables
- Migration seeds with `datetime('now')` but model uses `datetime.utcnow().isoformat()` — different formats
- `idx_` vs `ix_` prefix naming inconsistency for job_queue_items indexes

## Lessons
- Always cross-check model indexes against prior migration indexes for naming conflicts
- SQLModel's `ge`/`le` constraints do NOT generate SQLite CHECK constraints
- `create_all()` runs before migrations — any CHECK/UNIQUE constraints must survive both paths
- SQLite `datetime('now')` format ≠ Python `datetime.utcnow().isoformat()` format
