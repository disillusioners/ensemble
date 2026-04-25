# Plan Overview: Schedule Feature Improvement

## Objective
Fix correctness bugs (last_run_at, race conditions, missing indexes), refactor duplicated code (god method, magic constants), and add comprehensive test coverage for the schedule feature — addressing 15 issues across 4 priority levels.

## Scope Assessment
**LARGE** — 15 issues across 7+ files (scheduler.py, schedules.py, models.py, repository.py, schedule.py, constants.py, tests), requiring data model changes, core engine refactoring, API fixes, and ~40 new tests. Estimated 24 hours total.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Backend**: Python 3.11+, FastAPI, SQLModel, aiosqlite
- **Frontend**: Angular 21 (already expects `last_run_at` in schedule responses — no frontend changes needed)

### Critical Root Cause Discovery
The `last_run_at` bug (#1) is an **API read gap** — execution recording already works. The callback chain flows:
```
SchedulerAdapter._execution_callback()
  → SourceRegistry.execution_callback()         [registry.py:300-314]
    → _safe_sync_callback()                      [registry.py:278-298]
      → repo.record_execution_start()            [registry.py:286-290]
      → repo.record_execution_complete()          [registry.py:292-296]
```
The `ScheduleExecution` table IS being populated. The bug is that `GET /schedules` never passes `last_run_at` to `ScheduleInfo`, and `PUT /schedules/{id}` hardcodes `last_run_at=None`. The fix is to **read** the existing data via `get_latest_execution()`.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Data Layer Foundation | Add indexes, enum, fix race condition, standardize timezones | None | — (root) | 4h |
| 2 | Scheduler Core Refactor | Extract constants, refactor god method, unify execution paths, remove dead code | Phase 1 | tight | 8h |
| 3 | API Layer — Fix `last_run_at` | Read existing execution records in API endpoints | Phase 2 | loose | 3h |
| 4 | Comprehensive Testing | Cover untested endpoints, error paths, edge cases | Phases 1–3 | loose | 6h |
| 5 | DI Cleanup (Optional) | Extract Protocol interfaces for testability | Phase 4 | loose | 2h |

### Coupling Assessment

| Phase Pair | Coupling | Shared Files | Rationale |
|------------|----------|-------------|-----------|
| 1 → 2 | **tight** | `models.py` (enum), `repository.py` (atomic update) | Phase 2 uses `ExecutionStatus` enum from Phase 1, and calls refactored repo methods |
| 2 → 3 | **loose** | `schedules.py` (API reads) | Phase 3 only reads from DB via existing repo methods — no shared files with Phase 2's refactored `scheduler.py` |
| 3 → 4 | **loose** | All scheduler files | Phase 4 tests the interfaces/behaviors established in Phases 1–3; can pipeline review of Phase 3 with Phase 4 test writing |
| 4 → 5 | **loose** | `scheduler.py` | Phase 5 changes type hints to Protocol classes; no behavioral changes, tests already exist |

### Scheduling Recommendation
- **Phases 1–2**: Strictly sequential (tight coupling)
- **Phase 3**: Can start after Phase 1 (loose with Phase 2 — only reads DB via existing repo methods)
- **Phase 4**: Start test scaffolding immediately after Phase 3 review, write tests as Phase 3 completes
- **Phase 5**: Can be deferred — low priority, no user-facing impact

## Architectural Decisions

### Decision 1: ScheduleExecution Status Enum
- **Create `ExecutionStatus` enum** but keep DB column as `str`
- Valid values: `triggered`, `completed`, `failed`, `skipped`, `queued`
- Validation at repository/adapter layer
- Backward compatible — no migration for column type

### Decision 2: store_responses — REMOVE
- Remove `_store_responses` flag, `_store_response()` method, and the check in `send()`
- **Rationale**: Flag exposed to users but does nothing — misleading. No clear requirements. Response logging already exists. Can re-add with proper spec later.

### Decision 3: Run Counter Atomic Update
- Use SQL `JSON_SET` for atomic increment in a single statement
- Replaces non-atomic read-modify-write at `repository.py:97-126`
- **Fundamental shift**: This REPLACES the Python dict manipulation path entirely with SQL-side logic. The old Python read-modify-write codepath is removed, not preserved alongside.

### Decision 4: Constants Extraction
- Extract all magic numbers to `daemon/constants.py` (or scheduler-specific constants module)
- Constants: semaphore timeouts, grace period, retry delay, drain check interval

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| God method refactor introduces behavioral regression | High | Medium | Refactor incrementally with test verification at each step |
| DB index migration fails on existing data | High | Low | Use ALTER TABLE only; test on fresh DB first |
| Run counter atomic update changes behavior under concurrent load | Medium | Low | Test with concurrent triggers; SELECT FOR UPDATE as fallback |
| `last_run_at` N+1 query in GET list endpoint | Medium | Medium | Batch query for all schedule IDs or add dedicated repo method |

## Success Criteria

- [ ] `last_run_at` populated correctly in all schedule API responses (GET list, PUT update)
- [ ] Run counter is atomic — no lost increments under concurrent triggers
- [ ] DB indexes exist on `triggered_at` and `(schedule_id, status)`
- [ ] `store_responses` flag and `_store_response()` removed
- [ ] `_emit_scheduled_message()` refactored — no single method > 60 lines
- [ ] Duplicated logic between scheduled and manual triggers unified via shared helper
- [ ] Manual triggers preserved as immediate execution (intentional design — not queued)
- [ ] Semaphore timeout configurable via constant (default ≥ 1s for scheduled triggers)
- [ ] All 3 untested API endpoints have test coverage (PUT, POST start, POST stop)
- [ ] Error path tests: queue failure, semaphore timeout, concurrent triggers
- [ ] Edge case tests: DST transitions, run counter persistence
- [ ] All existing scheduler tests pass with zero regression

## Tracking
- **Created**: 2026-04-25
- **Last Updated**: 2026-04-25 (rev 2 — critical review fixes applied)
- **Status**: draft
