# Phase 4: Comprehensive Testing

## Objective
Add comprehensive test coverage for all untested API endpoints, error paths, and edge cases. This phase verifies all changes from Phases 1–3 and closes the test coverage gaps identified during exploration.

## Coupling
- **Depends on**: Phases 1–3 (tests verify all changes)
- **Coupling type**: loose
- **Shared files with other phases**: `tests/test_scheduler_api.py`, `tests/test_scheduler_adapter.py`
- **Shared APIs**: All scheduler public methods and API endpoints
- **Why**: Tests verify the interfaces and behaviors established in Phases 1–3; can pipeline (start test scaffolding during Phase 3 review)

## Context
**Issues addressed**: #10 (untested API endpoints), #11 (missing error path tests), #12 (missing edge case tests)

**Current test coverage**:
- ~185 tests across 4 files (~2800 lines total)
- **NO tests for**: PUT `/schedules/{id}`, POST `/schedules/{id}/start`, POST `/schedules/{id}/stop`
- **NO tests for**: semaphore timeout, concurrent triggers, queue failure paths
- **NO DST tests**
- Reusable fixtures: `mock_on_message`, `mock_execution_callback`, `mock_source_repo`, `make_config()`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4.1 | **Refactor shared test fixtures** (prerequisite) | Extract common fixtures to conftest or shared module **before writing API tests**: `create_mock_scheduler()`, `make_config()` with sensible defaults, `mock_source_repo` with DB recording methods, `mock_job_queue_service`, mock API client helpers. Standardize across test files. **This unblocks Tasks 4.2–4.4** which need API test fixtures that currently only exist in adapter test files. | `tests/conftest.py`, `tests/test_scheduler_adapter.py` |
| 4.2 | PUT `/schedules/{id}` endpoint tests | Test cases: (1) successful update with name change, (2) partial config update merges with existing, (3) instance_mode validation, (4) max_concurrent enforcement for reuse_instance, (5) 404 for non-existent schedule, (6) 400 for non-scheduler source type, (7) last_run_at populated after update. ~7 tests. **Requires fixtures from Task 4.1.** | `tests/test_scheduler_api.py`, `daemon/routers/schedules.py:72-151` |
| 4.3 | POST `/schedules/{id}/start` endpoint tests | Test cases: (1) successful start returns running status, (2) 404 for non-existent schedule, (3) 400 for non-scheduler source, (4) adapter start failure → appropriate error, (5) starting already-running schedule (idempotent). ~5 tests. **Requires fixtures from Task 4.1.** | `tests/test_scheduler_api.py`, `daemon/routers/schedules.py:227-272` |
| 4.4 | POST `/schedules/{id}/stop` endpoint tests | Test cases: (1) successful stop returns stopped status, (2) 404 for non-existent schedule, (3) 400 for non-scheduler source, (4) adapter stop failure → appropriate error, (5) stopping already-stopped schedule (idempotent). ~5 tests. **Requires fixtures from Task 4.1.** | `tests/test_scheduler_api.py`, `daemon/routers/schedules.py:275-318` |
| 4.5 | Semaphore timeout tests | Test cases: (1) scheduled execution skipped when max concurrent reached, (2) timeout logs warning message, (3) execution_callback called with `status="skipped"`, (4) semaphore released after skip, (5) manual trigger uses longer timeout. ~5 tests. | `tests/test_scheduler_adapter.py`, `daemon/sources/adapters/scheduler.py` |
| 4.6 | Concurrent trigger tests | Test cases: (1) multiple triggers within short window all complete, (2) run counter increments correctly under concurrency, (3) no missed triggers within max_concurrent limit, (4) excess triggers properly skipped. ~4 tests. | `tests/test_scheduler_adapter.py` |
| 4.7 | Queue failure tests | Test cases: (1) job queue service raises exception → execution marked failed, (2) execution_callback called with `status="failed"`, (3) error logged with details, (4) graceful recovery — next trigger works. ~4 tests. | `tests/test_scheduler_adapter.py` |
| 4.8 | DST transition tests | Test cases: (1) schedule crossing DST boundary triggers at correct local time, (2) next_run_at calculation correct during spring-forward, (3) next_run_at correct during fall-back. ~3 tests. | `tests/test_scheduler_adapter.py` |
| 4.9 | Run counter persistence tests | Test cases: (1) run counter persists after scheduler restart, (2) atomic under concurrent access, (3) initialized to 1 if not present in config, (4) accurate across multiple scheduled + manual runs. ~4 tests. | `tests/test_scheduler_adapter.py`, `daemon/repositories/source/repository.py` |
| 4.10 | `last_run_at` integration tests | Test cases: (1) last_run_at populated after first execution, (2) reflects most recent execution (not first), (3) None for schedule with no executions, (4) GET /schedules list includes last_run_at. ~4 tests. | `tests/test_scheduler_api.py` |

## Key Files
- `tests/conftest.py` — Shared fixtures (created/updated first)
- `tests/test_scheduler_api.py` — API endpoint tests (new: ~21 tests)
- `tests/test_scheduler_adapter.py` — Adapter unit tests (new: ~20 tests)

## Test Count Summary

| Category | New Tests | Priority |
|----------|-----------|----------|
| Fixture refactoring (prerequisite) | — | High (blocks API tests) |
| Untested API endpoints (PUT, start, stop) | 17 | High |
| Error paths (semaphore, queue, concurrent) | 13 | High |
| Edge cases (DST, run counter) | 7 | Medium |
| Integration (last_run_at) | 4 | High |
| **Total** | **~41** | — |

## Constraints
- All new tests must pass deterministically (no flaky tests)
- All existing tests must continue to pass
- Use async/await properly (existing pattern: `@pytest.mark.asyncio`)
- Mock external dependencies (repo, job queue, instance repo) — don't hit real DB in unit tests
- For integration tests requiring DB, use the existing `mock_manager` fixture pattern
- Task 4.1 (fixture refactoring) MUST complete before Tasks 4.2–4.4 (API endpoint tests)

## Deliverables
- [ ] Shared fixtures refactored to `conftest.py` (Task 4.1)
- [ ] PUT /schedules/{id} tests (~7 test cases)
- [ ] POST /schedules/{id}/start tests (~5 test cases)
- [ ] POST /schedules/{id}/stop tests (~5 test cases)
- [ ] Semaphore timeout tests (~5 test cases)
- [ ] Concurrent trigger tests (~4 test cases)
- [ ] Queue failure tests (~4 test cases)
- [ ] DST transition tests (~3 test cases)
- [ ] Run counter persistence tests (~4 test cases)
- [ ] last_run_at integration tests (~4 test cases)
- [ ] All ~226 tests pass (185 existing + 41 new)
