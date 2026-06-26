# Phase 0: Acceptance Test (Red)

## Objective

Write the `test_06f500af_bug_class_eliminated` E2E test FIRST — before any implementation changes. This test will **fail** until Phases 1 and 2 land, then turn **green** as the fixes are applied. This is the test-first approach that validates the entire migration's core hypothesis: a parent instance must not be permanently stranded in `waiting_children` after a child task is cancelled-and-retried or crashes without sending a terminal notification.

## Coupling

- **Depends on**: None (this is the starting phase)
- **Coupling type**: loose — Phase 1 and Phase 2 make the test pass
- **Shared files with other phases**: none (new test file)
- **Shared APIs/interfaces**: tests DependencyBus + StaleTaskRecovery + WorkerPool integration
- **Why this coupling**: The test encodes the acceptance criteria. It fails initially, then each phase that lands makes incremental progress toward green.

## Context

### The 06f500af Bug Class

Instance `06f500af` was stuck in `status=waiting_children` because:
1. A child task (task 4464) was force-cancelled by `StaleTaskRecovery` and a retry (task 4466) was scheduled
2. The DependencyBus had a PENDING watcher keyed on `source_task_id=4464`
3. No code path notified the bus that task 4464 reached a terminal event
4. The watcher stayed PENDING forever → `count_pending_for_target(06f500af) > 0` → parent never completed

**Two layers of fix** (validated by this test):
- **Phase 1**: Startup sweep for orphan PENDING watchers (the immediate fix)
- **Phase 2**: D13 structural elimination (single work record per message → no dual-record divergence)

### Test Design

The test simulates the EXACT failure scenario:
1. Spawn a parent (leader) instance
2. Parent spawns a child instance → bus watcher registered on child's task_id
3. Simulate child crash: kill the child's worker WITHOUT sending a terminal notification (no `emit_terminal`, no `_process_child_completion`)
4. Restart the daemon (triggers `bus.start()` → startup sweep)
5. Assert: the orphan PENDING watcher is cancelled
6. Assert: parent transitions OUT of `waiting_children` to `completed` (or appropriate next state)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **0.1** | Create E2E test file | Create `tests/e2e/test_06f500af_bug_class_eliminated.py`. This test will be marked `@pytest.mark.e2e` and `@pytest.mark.postgres` (requires real daemon + DB). | `tests/e2e/test_06f500af_bug_class_eliminated.py` (new) |
| **0.2** | Implement test — orphan watcher startup sweep | Test scenario: (1) Spawn parent + child with a bus watcher, (2) Simulate child crash (delete the child's task row or mark it terminal WITHOUT calling `bus.emit_terminal`), (3) Call `bus.start()` (or simulate daemon restart), (4) Assert the orphan watcher transitions to CANCELLED, (5) Assert `bus.count_pending_for_target(parent_id) == 0`. | `tests/e2e/test_06f500af_bug_class_eliminated.py` |
| **0.3** | Implement test — paused task exempt | Same setup but with the child task in PAUSED status. Assert the watcher stays PENDING (not cancelled) after the sweep. | `tests/e2e/test_06f500af_bug_class_eliminated.py` |
| **0.4** | Implement test — single work record invariant (D13) | After `enqueue_message`, assert exactly one `task` row exists and zero `job_queue_items` rows with `job_type="message"`. This will fail until Phase 2 (D13) lands. | `tests/e2e/test_06f500af_bug_class_eliminated.py` |
| **0.5** | Mark all tests as expected-to-fail initially | Use `@pytest.mark.xfail(reason="Phase 1: startup sweep not yet implemented")` and `@pytest.mark.xfail(reason="Phase 2: D13 not yet implemented")` so the test suite stays green while the implementation phases land. Remove the `xfail` markers as each phase completes. | `tests/e2e/test_06f500af_bug_class_eliminated.py` |

## Key Files

- `tests/e2e/test_06f500af_bug_class_eliminated.py` (new) — the acceptance test

## Constraints

- **Test must run against PostgreSQL** — the primary dev/test DB. Use `@pytest.mark.postgres`.
- **Test must simulate a REAL crash scenario** — not a mocked bus. The test should exercise the actual `bus.start()` → `_sweep_orphan_watchers()` path.
- **xfail markers**: Initially all tests should be marked `xfail` so CI stays green. As each phase lands, remove the corresponding `xfail` markers.
- **Test independence**: The test must not depend on specific task IDs or instance IDs — generate them dynamically.

## Deliverables

- [ ] `test_06f500af_bug_class_eliminated.py` created with 3 test scenarios
- [ ] All 3 tests marked `xfail` with phase-specific reasons
- [ ] Test file documented with the 06f500af bug class context
- [ ] Test suite still passes (xfail tests don't break CI)
