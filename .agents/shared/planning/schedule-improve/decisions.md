# Architectural Decisions: Schedule Feature Improvement

## Decision 1: ExecutionStatus Enum — String in DB, Enum in Code

**Context**: `ScheduleExecution.status` is currently `str` with no validation. Arbitrary values can be inserted.

**Decision**: Create `ExecutionStatus` enum in `models.py` with values `triggered`, `completed`, `failed`, `skipped`, `queued`. Keep the DB column as `str` type. Add validation at the repository/adapter layer.

**Alternatives Considered**:
1. **Full migration to enum column**: Rejected — requires DB migration, breaks backward compatibility with existing data, adds complexity for marginal benefit.
2. **String literal type hints only**: Rejected — no runtime validation, doesn't prevent invalid values.

**Trade-offs**:
- ✅ Type safety in code (IDE support, autocomplete)
- ✅ Runtime validation prevents invalid status values
- ✅ No DB migration needed
- ❌ Requires discipline to use enum rather than raw strings

---

## Decision 2: store_responses — REMOVE

**Context**: `store_responses` config flag exists in scheduler config. `_store_response()` method is an empty TODO stub. `send()` calls it conditionally but nothing happens.

**Decision**: Remove the flag, the method, and the conditional call. Keep the logging in `send()`.

**Alternatives Considered**:
1. **Implement response storage to DB**: Rejected — no clear requirements for what "store" means. DB? Webhook? File? How long to retain? Who consumes it?
2. **Implement minimal version (log only)**: Rejected — logging already exists.
3. **Keep flag but add deprecation warning**: Rejected — prolongs confusion, the flag was never functional.

**Trade-offs**:
- ✅ Removes misleading config option
- ✅ Cleaner codebase
- ❌ Breaking change if anyone checks this config (unlikely since it does nothing)
- ✅ Can re-add with proper spec when requirements are clear

---

## Decision 3: Atomic Run Counter via SQL JSON_SET

**Context**: `increment_scheduler_run_counter()` at `repository.py:97-126` uses non-atomic read-modify-write. Counter stored in `SourceConfig.config["_run_counter"]` JSON column.

**Decision**: Replace with single atomic SQL statement using `JSON_SET`/`JSON_EXTRACT`.

```sql
UPDATE source_configs 
SET config = JSON_SET(config, '$._run_counter', 
    COALESCE(CAST(JSON_EXTRACT(config, '$._run_counter') AS INTEGER), 0) + 1),
    updated_at = ?
WHERE source_id = ?
```

**Fundamental shift**: This is NOT a parallel path added alongside the Python dict manipulation. The entire Python read-modify-write codepath (lines 110–123) is **replaced** with the SQL statement. No Python dict access for the counter remains — the JSON column is only mutated via SQL.

**Alternatives Considered**:
1. **SELECT FOR UPDATE (pessimistic locking)**: Viable but requires transaction management. SQLite support for row-level locking is limited.
2. **Separate counter column**: Cleaner but requires migration, changes model. The counter is schedule-specific metadata that belongs in config.
3. **Application-level locking**: asyncio Lock would work for single-process but not multi-process deployments.

**Trade-offs**:
- ✅ Atomic — no lost increments
- ✅ Single SQL statement — no transaction management
- ✅ Works with SQLite (project uses SQLite exclusively)
- ❌ SQLite-specific JSON functions (not portable to PostgreSQL)
- ❌ Slightly harder to test — must verify SQL execution rather than Python dict mutation

---

## Decision 4: Semaphore Timeout Increase (100ms → 1s)

**Context**: Scheduled trigger semaphore timeout is 100ms at `scheduler.py:590`. Under load, legitimate executions are skipped. Manual trigger uses 10s timeout.

**Decision**: Increase scheduled timeout to 1.0s and extract as configurable constant `SCHEDULER_SEMAPHORE_TIMEOUT_S`. Keep manual at 10s.

**Rationale**:
- 100ms is too aggressive — a single slow DB query or GC pause causes missed triggers
- 1s still prevents runaway scheduling while allowing legitimate concurrent executions
- Manual triggers (user-initiated) should tolerate longer waits (10s is fine)
- Both values become constants for easy tuning per deployment

**Trade-offs**:
- ✅ Fewer missed triggers under load
- ✅ Configurable via constants
- ❌ Slight behavioral change — scheduled triggers wait longer before skipping
- ✅ Skipped triggers are logged, so impact is observable

---

## Decision 5: Execution Recording — Keep in Registry Callback

**Context**: Execution recording already works via a callback chain:
```
SchedulerAdapter._execution_callback()
  → SourceRegistry.execution_callback()         [registry.py:300-314]
    → _safe_sync_callback()                      [registry.py:278-298]
      → repo.record_execution_start()            [registry.py:286-290]
      → repo.record_execution_complete()          [registry.py:292-296]
```
The `ScheduleExecution` table IS being populated correctly. The `last_run_at` bug is that API endpoints don't **read** this data — not that it isn't written.

**Decision**: Keep execution recording in the registry callback. Do NOT add duplicate recording calls in the adapter.

**Rationale**:
- The registry callback pattern works — recording happens for every execution status transition
- Adding recording in the adapter would create **duplicate records** (one from callback, one from adapter)
- The registry is the correct orchestration layer for cross-cutting concerns like DB recording
- The adapter's `execution_callback` is the hook the registry uses — it's already connected

**What to fix instead**: Only the API read path — `GET /schedules` and `PUT /schedules/{id}` need to call `get_latest_execution()` to read the data that's already there.

**Trade-offs**:
- ✅ No duplicate execution records
- ✅ Clean separation: adapter emits callback → registry records to DB → API reads from DB
- ✅ No changes to the working recording pipeline
- ❌ The callback chain is indirect (adapter → registry → repo) — could benefit from documentation
