# Scheduling Feature Deep-Dive — 2025-04-25

## Architecture Overview
- Schedules are stored as `source_configs` rows with `source_type = "scheduler"`
- Core adapter: `daemon/sources/adapters/scheduler.py` (987 lines)
- Tables: `source_configs`, `schedule_executions`
- Frontend: Angular components under `web/src/app/features/schedules/`
- Two execution paths: via JobQueue (if project_id set) or direct instance message

## Key Files
- `daemon/sources/adapters/scheduler.py` — SchedulerAdapter (cron/interval/one-time)
- `daemon/routers/schedules.py` — REST API endpoints
- `daemon/models/schedule.py` — Pydantic response models
- `daemon/repositories/source/models.py` — SQLModel table definitions
- `daemon/repositories/source/repository.py` — DB operations
- `daemon/sources/registry.py` — Adapter lifecycle management

## Known Issues
1. Missing indexes on `schedule_executions` (triggered_at, status, completed_at)
2. Run counter race condition (non-atomic read-modify-write)
3. `_emit_scheduled_message()` is a 254-line god method
4. `last_run_at` always returns None (never populated)
5. `store_responses` flag exists but not implemented
6. No enum validation for execution status (plain str)
7. Semaphore timeout 0.1s too aggressive under load
8. 3 API endpoints untested (PUT update, POST start, POST stop)

## Test Coverage: ~6.5/10
- 4 test files, ~220+ test functions
- Adapter and instance mode well-tested
- API integration tests missing 3 endpoints
- Error path coverage weak (4/10)
- DST/concurrency edge cases untested
