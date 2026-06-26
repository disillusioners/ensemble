# Phase 1: Orphan Watcher Defense-in-Depth

## Objective

Close the remaining gap in the 06f500af bug class by adding a startup sweep for orphan PENDING watchers and verifying that permanent-fail paths properly notify the DependencyBus. The `cancel_for_source` method and retry-path notifications already exist and are wired — this phase adds the safety net.

## Coupling

- **Depends on**: Phase 0 (acceptance test written first)
- **Coupling type**: loose — Phase 0's test goes green when this phase lands
- **Shared files with other phases**: none (only touches `dependency_bus.py` `start()` method, the watcher repository, and `stale_task_recovery.py` permanent-fail paths)
- **Shared APIs/interfaces**: none
- **Why this coupling**: Defense-in-depth bug fix is orthogonal to the D11+D13 coupling elimination

## Context

- The `cancel_for_source` method **already exists** at `dependency_bus.py:885-997` and is **already wired** into all retry-scheduled paths via `_notify_bus_of_cancel_and_retry` (`stale_task_recovery.py:490-543`) and `_cancel_bus_watchers_for_task` (`worker_pool.py:463-496`).
- The gap: `start()` (`dependency_bus.py:999-1047`) only warms the cache + recovers FIRED-unsent rows. It does NOT sweep for PENDING watchers whose `source_task_id` no longer corresponds to an active task.
- The permanent-fail paths (4 sites in `stale_task_recovery.py`) call `_on_task_permanently_failed` → `manager._send_error_report`, but the exploration did not confirm whether `_send_error_report` calls `bus.emit_terminal`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **1.0** | Implement `fetch_all_pending()` on the watcher repository | **W1**: Before building the sweep, add a method to `DependencyWatcherRepository` that returns ALL PENDING watchers (not filtered by source or target). This is the query primitive the sweep needs. Verify it works on both SQLite and PostgreSQL. Pattern: matches existing `fetch_pending_for_source` (line 143) and `fetch_pending_for_target` (line 171) but without the source/target WHERE clause. | `daemon/repositories/dependency_bus/repository.py` |
| **1.1** | Verify permanent-fail bus coverage | Read `daemon/manager.py` — find `_send_error_report` and confirm it calls `bus.emit_terminal(task_id, status="error")`. If it does NOT, add an explicit `bus.cancel_for_source(task_id)` call in the permanent-fail branches of `stale_task_recovery.py` (lines ~240-251, ~292-303, ~384-395, ~440-451). | `daemon/manager.py`, `daemon/services/stale_task_recovery.py` |
| **1.2** | Implement `_sweep_orphan_watchers()` — atomic UPDATE | **W2**: New method on DependencyBus. Uses a **single atomic conditional UPDATE** (not read-then-update) to avoid the race window between reading PENDING rows and transitioning them. SQL pattern: `UPDATE dependency_watchers SET state='cancelled', fired_at=:now WHERE state='pending' AND source_task_id NOT IN (SELECT id FROM task WHERE status IN ('running','pending','paused'))`. This is a single-statement operation — no TOCTOU window. Use `sa_update` or raw `text()` via `asyncio.to_thread`. Log how many rows affected. | `daemon/services/dependency_bus.py` |
| **1.3** | Wire `_sweep_orphan_watchers()` into `start()` | Call `_sweep_orphan_watchers()` in `DependencyBus.start()` after `_warm_cache()` + `_recover_fired_unsent()`. The sweep must run BEFORE the bus starts processing new events so orphaned watchers don't block completion during the startup window. | `daemon/services/dependency_bus.py:999-1047` |
| **1.4** | Regression test — paused tasks exempt | Write test: create a PAUSED instance with a PENDING watcher, run `bus.start()` (or call `_sweep_orphan_watchers` directly), assert the watcher stays PENDING. This is critical — paused tasks MUST NOT have their watchers cancelled. **Also remove xfail from Phase 0 test 0.3** if the sweep makes it pass. | `tests/unit/test_dependency_bus.py` |
| **1.5** | Regression test — orphan watcher cancelled | Write test: create a PENDING watcher for a `source_task_id` that is no longer active (task completed or was deleted), run sweep, assert watcher transitions to CANCELLED. **Also remove xfail from Phase 0 test 0.2** if the sweep makes it pass. | `tests/unit/test_dependency_bus.py` |
| **1.6** | Regression test — permanent-fail cancels watchers | If Task 1.1 found a gap, write test that verifies: after `force_cancel_and_schedule_retry` exhausts retries (permanent fail), the bus watchers for that task_id are CANCELLED (via emit_terminal or cancel_for_source). | `tests/unit/test_stale_task_recovery.py` |

## Key Files

- `daemon/repositories/dependency_bus/repository.py` — new `fetch_all_pending()` method (W1)
- `daemon/services/dependency_bus.py` — `start()` method (line ~999), new `_sweep_orphan_watchers()` method (W2 atomic)
- `daemon/services/stale_task_recovery.py` — permanent-fail paths (lines ~240, ~292, ~384, ~440)
- `daemon/manager.py` — `_send_error_report` method (verify bus integration)
- `tests/unit/test_dependency_bus.py` — regression tests
- `tests/unit/test_stale_task_recovery.py` — permanent-fail regression test

## Constraints

- **PostgreSQL is primary dev/test DB** — the sweep query must work on both SQLite and PostgreSQL. Use parameterized queries via the repository pattern or raw `text()` with bound params.
- **Paused tasks must NOT have watchers cancelled** — the sweep must check task status and exclude `paused`.
- The sweep must be idempotent — safe to run multiple times (if daemon restarts repeatedly).
- **W2 — Atomic sweep**: Use a conditional UPDATE, NOT a read-then-update loop. The atomic `UPDATE ... WHERE ... NOT IN (SELECT ...)` pattern eliminates the TOCTOU race between reading PENDING rows and transitioning them. If a concurrent `emit_terminal` fires between the read and the update, the guarded `WHERE state='pending'` clause in `transition_state` would fail — but with a single atomic UPDATE, the DB engine handles the race internally.
- The sweep must not hold a long-running lock that blocks `emit_terminal` calls.

## Deliverables

- [ ] `fetch_all_pending()` method on `DependencyWatcherRepository` (W1)
- [ ] `_sweep_orphan_watchers()` method on DependencyBus using atomic UPDATE (W2)
- [ ] Sweep wired into `start()` after cache warm + FIRED recovery
- [ ] Permanent-fail paths verified/fixed for bus coverage
- [ ] 3 regression tests (paused exempt, orphan cancelled, permanent-fail)
- [ ] Phase 0 acceptance test xfails removed for tasks 0.2, 0.3
- [ ] All existing tests pass
