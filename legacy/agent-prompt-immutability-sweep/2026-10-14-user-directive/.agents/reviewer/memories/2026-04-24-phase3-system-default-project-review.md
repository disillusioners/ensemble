# Phase 3 System Default Project Review — Deep-Review Findings

## Date: 2026-04-24
## Scope: Phase 3 — Migration + Orphan Removal (commit be754a0)

## Key Findings

### 🔴 CRITICAL #1: DispatchEventBus Event-Driven Dispatch Broken
- Old code: `_global_event` was ALWAYS set alongside per-project event. Processor waited on global event.
- Phase 3: Removed `_global_event`. `wait_for_job(None)` now does `asyncio.sleep(timeout)` + returns False.
- Producer sends `notify_new_job(real_uuid)`, consumer waits on `None` — events never connect.
- **Impact**: All jobs have worst-case 30s latency. `_jobs_dispatched_immediately` counter always 0.
- **Fix**: Restore global event in `notify_new_job()` or change processor to iterate per-project events.

### 🔴 CRITICAL #2: `assert` for Production Guards
- `dead_letter_service.py:113,187` and `job_queue_service.py:259` use `assert`
- Python strips assert with `python -O`
- **Mitigated**: Project doesn't use `-O` in production. But should use `raise ValueError()` for safety.
- **Deploy-order risk**: If code deployed before migration, stale NULL jobs crash the process.

### 🟡 WARNINGS
- DOWN migration is irreversible (intentional, documented in SQL comments)
- Verification queries commented out
- Race window during migration (mitigated by app-layer normalization)
- `find_retryable_jobs` still accepts `None` (dead code in retry_scheduler.py filter)

### Migration SQL Assessment
- Idempotent ✅ (INSERT OR IGNORE, UPDATE WHERE IS NULL)
- Handles NULL, empty string ✅
- UUID deterministic and matches codebase ✅ (verified uuid5 output)
- WHERE clauses don't affect valid rows ✅

### Test Coverage
- 16 migration tests — comprehensive backfill verification
- 6 DLQ normalization tests — cover the fix
- Missing: DOWN migration tests, race condition tests, assertion path tests
