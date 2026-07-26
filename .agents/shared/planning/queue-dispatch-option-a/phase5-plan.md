# Phase 5: Test & Contract — Update Tests + API Audit

## Objective

Update the ~20+ tests that assert the D13 invariant (which we are reversing), add new tests asserting Option-A behavior, and audit all 5 callers of `enqueue_message_job` for the `message_id` contract change. This phase must land with Phases 1-4 as a single atomic unit — the system is broken with half-updated tests.

## Coupling

- **Depends on**: Phase 1-4 (all behavioral changes must be live)
- **Coupling type**: **tight**
- **Shared files with other phases**: test files across `tests/`
- **Shared APIs/interfaces**: `AsyncMessageResult` return type (if Phase 3 changed it), HTTP response models
- **Why this coupling**: Tests assert the invariants. They must be updated to match the new behavior exactly when Phases 1-4 land.

## Context

- **Previous phases completed**: Phases 1-4 — full Option-A behavioral changes live
- **Key decisions**:
  - **D13 invariant tests are EXPECTED to break** — they assert the very behavior we are reversing. Convert them to assert the NEW invariant.
  - **New tests assert Option-A guarantees**: concurrency enforcement, instance reuse, no double-dispatch, startup safety, PG trigger enforcement.
  - **Test DB**: PostgreSQL is mandatory (🟡 critical constraint). The PG trigger tests CANNOT run on SQLite.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update D13 guard rejection test | `tests/test_dispatcher_path_equivalence.py:445-490` (`TestEnqueueJobRejectsMessage`) currently asserts `enqueue(job_type='message')` raises ValueError. Flip it: assert it SUCCEEDS and creates a QUEUED JobItem. Rename the test class to `TestEnqueueJobAcceptsMessage`. | `tests/test_dispatcher_path_equivalence.py` |
| 2 | Update bug-class-elimination PG test | `tests/postgres/test_06f500af_bug_class_eliminated_pg.py:40-695` asserts 0 `job_type='message'` JobItem rows and enqueue rejection. Update: message JobItems now exist and are created via standard path. The "bug class" being eliminated was ghost mirror creation — update the test to verify NO ghost/duplicate creation (still valid) but ALLOW legitimate message JobItems. | `tests/postgres/test_06f500af_bug_class_eliminated_pg.py` |
| 3 | Update repository filter tests | `tests/job_queue/test_job_repository_phase1.py` tests filter behavior for `job_type='message'`. Update: filters are removed, so `list_pending_*` now INCLUDES message jobs. `find_jobs_by_instance` (line 998) is parameterized — verify it still works for both types. | `tests/job_queue/test_job_repository_phase1.py` |
| 4 | Update observer filter tests | `tests/job_queue/test_job_feedback_observer.py:1513-1732` (Fix 4 regression tests) assert observers exclude message JobItems. Update: observers now handle message JobItems through the standard finalization path (no exclusion). | `tests/job_queue/test_job_feedback_observer.py` |
| 5 | Update job processor tests | `tests/job_queue/test_job_processor.py:60-1064` has tests using `job_type='message'` for status guard paths. Update for message-aware dispatch (instance reuse, no duplicate spawn). | `tests/job_queue/test_job_processor.py` |
| 6 | Update termination/cleanup tests | `tests/job_queue/test_instance_termination_job_cleanup.py:508-1431` tests D13 elimination of message JobItem creation. Update for Option-A where message JobItems ARE created (authoritatively) and cleaned up normally. | `tests/job_queue/test_instance_termination_job_cleanup.py` |
| 7 | Update task repository tests | `tests/message_queue_redesign/test_task_repository.py:410-1123` uses `job_type='message'` extensively. Update for new behavior. | `tests/message_queue_redesign/test_task_repository.py` |

### New Tests to Add

| # | Test | Description | Key Assertions |
|---|------|-------------|----------------|
| 8 | Concurrency enforcement test | N messages to DIFFERENT instances in a `concurrency_limit=1` FIFO queue → only 1 runs at a time across ALL instances | Slot locking serializes; ExecutionGate (per-instance) does NOT prevent cross-instance parallelism, but queue concurrency DOES |
| 9 | Instance reuse test | Message to an EXISTING (IDLE/COMPLETED) instance → same `instance_id` reused | No new Instance row created; `spawn_instance` returns the existing id |
| 10 | No-double-dispatch test | Single message → exactly 1 Task created (via JobProcessor), not 2 | JobProcessor creates the Task once; no inline Task creation in producer |
| 11 | Startup safety test | Daemon restart with in-flight (QUEUED/ACTIVE) message jobs → jobs survive | Startup migration does NOT cancel them; jobs resume processing |
| 12 | PG trigger enforcement test | Message job admission → `job_locks` row required (PG only) | Without a lock row, admission ABORTS with constraint violation; with a lock row, it succeeds |
| 13 | Recursion safety test | JobProcessor processing a job → its internal `enqueue_message` call does NOT re-enter the queue | Internal `enqueue_message` stays Task-only; no infinite loop |
| 14 | Dispatch bus notification test | After `enqueue_message_job`, `dispatch_bus.notify_new_job` fires exactly once | JobProcessor poll loop wakes via `wait_for_job` (asyncio.Event channel) |

### API Contract Audit

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 15 | Audit HTTP POST /messages response | `daemon/routers/messages.py:321` returns result to client. Verify `message_id` is available (if Phase 3 used option (c) — pre-generation — it is; if (b), update response model). | `daemon/routers/messages.py`, frontend API client |
| 16 | Audit scheduler correlation | `daemon/sources/adapters/scheduler.py:762` — verify what it reads from result. | `daemon/sources/adapters/scheduler.py` |
| 17 | Audit external source registry | `daemon/sources/registry.py:827` — verify correlation needs. | `daemon/sources/registry.py` |
| 18 | Audit `job_continue` tool | `daemon/tools/job_queue.py:749` — verify agent-facing result shape. | `daemon/tools/job_queue.py` |

## Key Files

- `tests/test_dispatcher_path_equivalence.py`
- `tests/postgres/test_06f500af_bug_class_eliminated_pg.py`
- `tests/job_queue/test_job_repository_phase1.py`
- `tests/job_queue/test_job_feedback_observer.py`
- `tests/job_queue/test_job_processor.py`
- `tests/job_queue/test_instance_termination_job_cleanup.py`
- `tests/message_queue_redesign/test_task_repository.py`

## Test DB Requirement

🟡 **MANDATORY**: All tests must pass against **PostgreSQL**, not just SQLite. Key reasons:

1. **PG trigger tests** (`trg_job_queue_items_active_lock_guard`) — SQLite has no equivalent
2. **Slot locking** uses `ON CONFLICT DO NOTHING` (PG) vs `INSERT OR IGNORE` (SQLite) — behavior must match
3. **Critical constraint** (from project notes): "PostgreSQL is the PRIMARY dev/test DB. Run tests against PostgreSQL, not just SQLite."

Test command (per dev env): run the full suite against the PostgreSQL test DB, not the SQLite default.

## Constraints

- **All Phase 1-4 changes must be live** before running these tests — they assert the new behavior.
- **Do not delete tests** — convert them. A deleted test is a lost regression guard. The D13 tests become "verify D13 is reversed" tests.
- **Frontend impact**: if `message_id` is no longer immediate (option b), the frontend chat UI may need a loading state. Coordinate with frontend if option (b) is chosen. (Option (c) — pre-generation — avoids this.)

## Deliverables

- [ ] All ~20+ D13-invariant tests updated to assert Option-A behavior
- [ ] 7 new tests added (concurrency, reuse, no-double-dispatch, startup, PG trigger, recursion, dispatch bus)
- [ ] All 5 callers audited for `message_id` contract change
- [ ] Full test suite passes against PostgreSQL (🟡 mandatory)
- [ ] No regressions outside D13-specific tests

## Notes

- This phase is the largest by test count but the smallest by code complexity — it's mechanical test updates plus targeted new tests.
- The "test churn" is a FEATURE, not a bug: these tests documented D13, and now they document Option-A. The conversion is the migration's acceptance criteria.
- **Estimated test files touched**: 7 existing + ~3 new test files = 10 files.
- Consider creating a dedicated test file `tests/job_queue/test_option_a_message_dispatch.py` for the 7 new tests (cleaner than scattering them).
