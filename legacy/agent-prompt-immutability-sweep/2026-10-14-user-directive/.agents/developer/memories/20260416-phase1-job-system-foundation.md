# Phase 1 Implementation — Job System Improvements

## Date: 2026-04-16

## What was implemented
Phase 1: Foundation — State Machine & Persistent Locks for agents-ensemble job queue system.

## Key Architecture Decisions

### State Machine
- `daemon/services/job_state_machine.py` — lightweight custom class, no library dependency
- 9 transitions defined with string-based keys (not enum values) to avoid circular imports
- `InvalidTransitionError` for atomic transition failures
- `job_state_machine` singleton for convenience

### atomic_transition() Pattern
- Uses SQLModel session pattern (NOT `session.exec(update(...))`) — the codebase uses direct attribute assignment + commit
- SELECT + validate + UPDATE in single session
- Lazy import of state machine to avoid circular deps
- Logs every transition with job_id, from/to status, transition name

### cancel_job() Fix
- Repository now handles both PENDING→CANCELLED and PROCESSING→CANCELLED
- Service no longer bypasses repository with `update()` calls
- Both paths go through `atomic_transition()`

### Lock Persistence
- `JobLock` SQLModel table + `LockRepository` for DB persistence
- `JobLockManager` keeps in-memory cache AND persists to DB
- `reconcile_locks()` for startup cleanup of orphaned locks
- `LockRepository` is Optional — backward compatible

### Config
- `JobSystemConfig` with Pydantic BaseSettings pattern
- **Important:** Must add new config sections to BOTH the Config class AND `load_config()` function
- Bug found in review: `load_config()` didn't include `job_system` in config_dict

## Files Changed
- NEW: `daemon/services/job_state_machine.py`
- NEW: `daemon/repositories/job_queue/lock_repository.py`
- NEW: `daemon/migrations/versions/20260420_000001_add_job_system_improvements.sql`
- NEW: `tests/job_queue/test_state_machine.py`
- NEW: `tests/job_queue/test_atomic_transition.py`
- NEW: `tests/job_queue/test_lock_repository.py`
- MOD: `daemon/repositories/job_queue/models.py`
- MOD: `daemon/repositories/job_queue/repository.py`
- MOD: `daemon/repositories/job_queue/__init__.py`
- MOD: `daemon/services/job_queue_service.py`
- MOD: `daemon/services/job_lock_manager.py`
- MOD: `daemon/config.py`
- MOD: `daemon/api.py`

## Commit: 7d7ba1d (feature/job-system-improvements branch)

## Lessons Learned
1. **Circular imports are a real concern** — state machine uses string literals instead of importing JobStatus enum
2. **Config wiring has TWO places** — must add to Config class AND load_config() dict builder
3. **Repository pattern** — codebase uses session.get() + attribute assignment + commit, NOT session.exec(update(...))
4. **Review caught a real bug** — config.py missing job_system section was caught by cross-verification
5. **Review false alarm** — reviewer claimed cancel_job had wrong from_status but it was correct; always verify
