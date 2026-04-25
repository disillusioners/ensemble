# Phase 2: Scheduler Core Refactor

## Objective
Refactor the scheduler adapter to eliminate code duplication, reduce method complexity (254-line god method → focused methods), extract magic constants, unify shared execution logic, and remove dead code (`store_responses`). This is the highest-effort phase.

## Coupling
- **Depends on**: Phase 1 (uses `ExecutionStatus` enum, atomic counter)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/sources/adapters/scheduler.py`
- **Shared APIs**: `_emit_scheduled_message()`, `_execute_trigger()`, `send()`, new helper methods
- **Why**: Phase 3 will read execution data from DB in API endpoints (no changes to scheduler.py needed from Phase 3)

## Context
**Issues addressed**: #4 (semaphore timeout), #6 (duplicated logic), #7 (god method), #8 (store_responses), #13 (magic constants)

**Current state**:
- `_emit_scheduled_message()` = 254 lines (lines 573–827) — god method with 4 sub-phases
- `_execute_trigger()` = 158 lines (lines 829–987) — ~80% code duplication with `_emit_scheduled_message()`
- Manual triggers always use immediate execution (deliberate — immediate feedback, simpler debugging)
- Scheduled triggers route through job queue when `project_id` configured (different use case)
- Semaphore timeout: 100ms (scheduled), 10s (manual) — hardcoded
- `_store_response()` is empty TODO, `store_responses` flag does nothing
- 7+ magic constants scattered throughout

**Target state**:
- Shared `_execute_run()` helper for both scheduled and manual paths
- `_emit_scheduled_message()` → < 50 lines (orchestration only)
- `_execute_trigger()` → < 30 lines (delegates to shared helper)
- All constants in `daemon/constants.py`
- `store_responses` removed entirely

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 2.1 | Extract magic constants | Create scheduler constants in `daemon/constants.py`: `SCHEDULER_SEMAPHORE_TIMEOUT_S=1.0` (raised from 0.1s), `SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S=10.0`, `SCHEDULER_GRACE_PERIOD_S=30.0`, `SCHEDULER_ERROR_RETRY_S=5.0`, `SCHEDULER_DRAIN_CHECK_S=0.5`, `SCHEDULER_DEFAULT_MAX_CONCURRENT=1`, `SCHEDULER_DEFAULT_PRIORITY=5`. Update all references in `scheduler.py`. | `daemon/constants.py`, `daemon/sources/adapters/scheduler.py:590,843,288,445,302,107,113` |
| 2.2 | Create `_acquire_execution_slot()` helper | Extract semaphore acquisition logic (lines 581–608 for scheduled, 835–855 for manual) into a single method. Parameters: `timeout: float`, `execution_id: str`. Returns `(acquired: bool)`. Handles logging and skip callback on timeout. | `daemon/sources/adapters/scheduler.py` |
| 2.3 | Create `_execute_run()` shared helper | Extract the inner `execute()` coroutine common to both paths. This is the core shared logic (~80% overlap). Parameters: `execution_id`, `trigger_type: str` (`"scheduled"` or `"manual"`). Handles: run counter increment, message formatting, metadata construction, error handling, semaphore release. **Queue routing is NOT shared** — scheduled triggers route through job queue when `project_id` configured; manual triggers always use immediate execution (deliberate design for immediate user feedback). The helper accepts a parameter to control this. | `daemon/sources/adapters/scheduler.py` |
| 2.4 | Refactor `_emit_scheduled_message()` | Reduce from 254 lines to < 50 lines. New structure: (1) acquire slot with `SCHEDULER_SEMAPHORE_TIMEOUT_S`, (2) check instance active for reuse_instance, (3) await `_execute_run(execution_id, "scheduled")`, (4) cleanup. | `daemon/sources/adapters/scheduler.py:573-827` |
| 2.5 | Refactor `_execute_trigger()` | Reduce from 158 lines to < 30 lines. New structure: (1) acquire slot with `SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S`, (2) await `_execute_run(execution_id, "manual")`, (3) cleanup. Manual triggers continue to use **immediate execution only** (no job queue routing) — this is intentional for immediate user feedback and simpler debugging. | `daemon/sources/adapters/scheduler.py:829-987` |
| 2.6 | Remove `store_responses` dead code | Remove: `self._store_responses` initialization (line 131), `_store_response()` method (lines 331–340), conditional call in `send()` (lines 326–327). Keep the logging in `send()`. | `daemon/sources/adapters/scheduler.py:126-131, 304-340` |
| 2.7 | Update `_format_continuation_message()` for reuse | Ensure this method (lines 487–512) works correctly with both trigger types. Currently only used by `_emit_scheduled_message` — after refactoring, `_execute_run()` uses it for both. | `daemon/sources/adapters/scheduler.py:487-512` |
| 2.8 | Verify all existing tests pass | Run the full test suite. Fix any breakage from refactoring. The refactoring should be behavioral-equivalent — same inputs produce same outputs. | `tests/test_scheduler_adapter.py`, `tests/test_scheduler_instance_mode.py`, `tests/test_retry_scheduler.py` |

## Key Files
- `daemon/sources/adapters/scheduler.py` — Main target (987 → ~550 lines)
- `daemon/constants.py` — New/updated constants
- `tests/test_scheduler_adapter.py` — Verify existing tests pass

## Refactoring Map

```
BEFORE (scheduler.py):
├── _emit_scheduled_message()  [254 lines] ──┐
│   ├── semaphore acquire (100ms)            │ ~80% duplicate
│   ├── instance check                       │
│   └── inner execute() coroutine            │
├── _execute_trigger()          [158 lines] ──┘
│   ├── semaphore acquire (10s)              
│   └── inline execution logic               
└── _store_response()           [TODO stub]

AFTER:
├── _acquire_execution_slot(timeout, exec_id)  [~20 lines]
├── _execute_run(exec_id, trigger_type)        [~100 lines, shared]
│   ├── shared: counter, formatting, metadata, error handling
│   └── split: job queue (scheduled only) vs immediate (manual only)
├── _emit_scheduled_message()                  [<50 lines, orchestration]
├── _execute_trigger()                         [<30 lines, orchestration]
└── [removed] _store_response()
```

## Constraints
- Must not change public API behavior — same inputs produce same outputs
- Must maintain backward compatibility with existing schedules
- Manual triggers MUST use immediate execution (no job queue) — this is deliberate design for immediate user feedback
- Semaphore timeout increase (100ms → 1s) is intentional behavioral change — documented in constants
- Refactored code must pass all ~185 existing tests

## Deliverables
- [ ] Magic constants extracted to `daemon/constants.py`
- [ ] `_acquire_execution_slot()` helper created
- [ ] `_execute_run()` shared execution helper created
- [ ] `_emit_scheduled_message()` reduced to < 50 lines
- [ ] `_execute_trigger()` reduced to < 30 lines (immediate execution preserved)
- [ ] `store_responses` flag and `_store_response()` removed
- [ ] Semaphore timeout raised to 1.0s (configurable via constant)
- [ ] All ~185 existing scheduler tests pass
