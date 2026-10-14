# Phase 3 Review Fixes - Job Queue System

## Date: 2026-04-17
## Branch: feature/job-system-improvements
## Commit: cc0e7e3

## What was done
Fixed 4 critical + 4 warning issues from Phase 3 code review.

### Critical Fixes:
- **C1**: `job_retry_engine.py` - Added session.rollback() in except block when move_to_dlq() fails
- **C2**: DLQ bulk cleanup now threads project_id through full chain (router → service → repository)
- **C3**: Pessimistic locking with `with_for_update=True` in `move_to_dlq()` + IntegrityError handling
- **C4**: `list_dlq()` now returns total count BEFORE pagination (not after)

### Warning Fixes:
- **W1**: Migration - added foreign key, unique index, metadata default `'{}'`
- **W2**: `with_for_update=True` in `replay_from_dlq()`
- **W3**: Index on `moved_to_dlq_at` for cleanup queries
- **W4**: fcntl.flock guard against duplicate scheduler instances

## Key patterns learned:
1. **TOCTOU prevention**: Always use `with_for_update=True` for state-changing operations
2. **IntegrityError handling**: In concurrent environments, catch IntegrityError, rollback, raise domain error
3. **Pagination counts**: Always compute total BEFORE applying offset/limit
4. **project_id scoping**: Always thread through the full chain, never assume single-tenant
5. **File-based locks**: fcntl.flock with LOCK_NB is simple and effective for process-level guards

## Files changed (21 files, +959/-140 lines):
- daemon/services/job_retry_engine.py
- daemon/services/dead_letter_service.py
- daemon/services/retry_scheduler.py
- daemon/routers/dlq.py
- daemon/repositories/job_queue/dead_letter_repository.py
- daemon/migrations/versions/20260421_000001_add_dead_letter_queue.sql
- Multiple test files

## Test results: 799 tests (785 passed, 14 skipped, 0 failures)
