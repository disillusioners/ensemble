# Phase D — Dependency Bus & Cleanup: Task Plan

## Branch: `feature/decouple-phase-d`

## Reference
- Plan: `docs/plans/decouple-execution-plan.md` (Phase D notes in §13 — "deferred to follow-up")
- Review: `docs/plans/decouple-review.md` §2.5 (irreversible column drop), §3.2 (in-flight migration), §7.2 (two-step column drop), §7.4 (drain approach)
- Leader deliverables: D1-D16

## Key Architecture Decisions

1. **D2 creates a NEW table** (`dependency_watchers`) → use standard `.sql` migration + SQLModel class. PostgreSQL: `create_all()` picks it up because it's a brand-new table, NOT a column addition. SQLite: migration runner runs the .sql.
2. **D10 drops columns** → IRREVERSIBLE. Use a `.sql` migration guarded by a Python check on `USE_DEPENDENCY_BUS=ON`. For PG, the migration runner NO-OPs .sql; so we add an `_ensure_postgres_drop_legacy_columns()` hook in manager.py that only executes when the flag is ON.
3. **In-flight migration**: Document the "drain in-flight jobs before flipping flag" approach (reviewer §7.4). The flag is OFF by default, so this is a manual operation at cutover, not a code path.
4. **CM stays as shadow**: D8 keeps CM class for shadow validation (one more release). CM hooks run in parallel under flag ON.
5. **FollowUp is NEW**: The plan references `FollowUp` and `Outcome` types that don't exist in the codebase. We define them in `dependency_bus.py`.

## Task Plan (16 deliverables → 7 tasks)

### Batch 1: Foundation (no dependencies, can run in parallel)

**Task A: D2+D3 — Bus migration + model + config flag**
- Create `daemon/migrations/versions/20260621_000001_create_dependency_watchers.sql` (new table)
- Create `daemon/repositories/dependency_bus/models.py` (SQLModel) + `repository.py` (with WriteGuardSession pattern)
- Add `use_dependency_bus` flag to `daemon/config.py` JobSystemConfig (default False)
- Wire the new model so `create_all()` registers it
- **Files:** daemon/migrations/versions/20260621_000001_*.sql, daemon/repositories/dependency_bus/*, daemon/config.py
- **Parallel:** Yes (Batch 1)

**Task B: D1 — DependencyBus service class**
- Create `daemon/services/dependency_bus.py` with `DependencyBus` class
- API: `watch()`, `emit_terminal()`, `pending_watchers()`
- Define `FollowUp` and `Outcome` dataclasses
- Uses the repository from Task A (depends on Task A)
- Internal in-memory cache layered over DB (for hot-path performance) + crash-survivable
- `emit_terminal` atomically transitions PENDING → FIRED and enqueues FollowUps one at a time (backpressure)
- Cancellation path for parent termination (marks PENDING → CANCELLED)
- **Files:** daemon/services/dependency_bus.py
- **Parallel:** No — depends on Task A's repository (Batch 2)

### Batch 2: Wiring (depends on Batch 1)

**Task C: D4+D5+D6+D7 — Wire bus into send_message + task_processor + structured logging**
- `daemon/tools/instance.py` send_message: under `USE_DEPENDENCY_BUS=ON`, write `dependency_watchers` row (skip `notify_corr_register`)
- `daemon/services/task_processor.py` MessageTaskProcessor / `daemon/services/child_reports.py`: on terminal event, call `bus.emit_terminal()` (skip `notify_corr_resolve`)
- Add `completion_delivery_path=cm|bus` to every relevant structured log line
- Initialize bus singleton in `daemon/api.py` alongside CM init
- Generation counter + post-commit re-arm must work on bus path
- **Files:** daemon/tools/instance.py, daemon/services/task_processor.py, daemon/services/child_reports.py, daemon/services/error_reporting.py, daemon/api.py
- **Parallel:** No — depends on Task B (Batch 3)

### Batch 3: Tests (depends on Batch 2)

**Task D: D9 — Dependency bus test pack (~30 tests)**
- `tests/test_dependency_bus.py` (unit, no daemon)
- Bus watcher semantics: 1 parent, 3 children, all complete → follow-up enqueued exactly once
- `waiting_for` double-decrement impossible (bus has no counter)
- Bus survives restart (write watcher, clear in-memory state, emit terminal → fires)
- Bus cancellation (terminate parent → CANCELLED, does not enqueue)
- Bus backpressure (10,000 watchers → one-at-a-time emit)
- Shadow-equivalence: for every CM unit fixture, assert `USE_DEPENDENCY_BUS=ON` identical to OFF
- **Files:** tests/test_dependency_bus.py, tests/postgres/test_dependency_bus_pg.py
- **Parallel:** No — depends on Task C (Batch 4)

### Batch 4: Flag flip + legacy drop (depends on Batch 3 passing)

**Task E: D8 — Flip flag default + remove CM from hot path**
- Verify D9 passes (shadow-equivalence green)
- Set `use_dependency_bus` default to ON in config.py
- Remove `notify_corr_register`/`notify_corr_resolve` calls from hot path (send_message, child_reports, error_reporting)
- Keep CM class for shadow validation only
- **Files:** daemon/config.py, daemon/tools/instance.py, daemon/services/child_reports.py, daemon/services/error_reporting.py
- **Parallel:** No (Batch 5)

**Task F: D10 — Drop legacy columns migration (IRREVERSIBLE, gated)**
- Create `daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql`
- Add `_ensure_postgres_drop_legacy_columns()` in `daemon/manager.py` — ONLY runs when `use_dependency_bus=ON`
- Drops: `Instance.waiting_for`, `Instance.children`, `instance_hierarchy` table
- Document data loss in migration header + docstring
- Update SQLModel models to remove the dropped columns (with backward-compat note)
- **Files:** daemon/migrations/versions/20260621_000002_*.sql, daemon/manager.py, daemon/repositories/instance/models.py, daemon/repositories/instance_hierarchy/* (delete)
- **Parallel:** Yes with Task G (Batch 5)

### Batch 5: MESSAGE dispatch removal + docs (depends on Batch 4)

**Task G: D11+D12+D13 — Drop MESSAGE dispatch**
- `daemon/services/job_processor.py`: remove `job_type='message'` branch (lines 864-973 dispatch decision + 543-646 orphan guard cleanup)
- Delete `daemon/services/message_job_handler.py`
- Move any cross-instance handoff to `job_feedback_observer.py`
- `daemon/services/job_queue_service.py`: remove MESSAGE-specific helpers
- Update `tests/test_dispatcher_path_invariants.py` allow-list
- **Files:** daemon/services/job_processor.py, daemon/services/message_job_handler.py (DELETE), daemon/services/job_queue_service.py, daemon/services/job_feedback_observer.py, tests/test_dispatcher_path_invariants.py
- **Parallel:** Yes with Task F (Batch 5)

**Task H: D14+D15+D16 — Docs + changelog**
- Update `docs/architecture/message-processing-and-correlation.md`
- Update `docs/architecture/job-task-pause-resume.md`
- Update `docs/architecture.md` with one-page summary
- `CHANGELOG.md` entry
- **Files:** docs/architecture/*.md, docs/architecture.md, CHANGELOG.md
- **Parallel:** Yes (Batch 6, after Batch 5)

## Execution Order
```
Batch 1: Task A (migration+model+config)
    ↓
Batch 2: Task B (bus service)              [depends on A]
    ↓
Batch 3: Task C (wiring)                    [depends on B]
    ↓
Batch 4: Task D (tests)                     [depends on C]
    ↓
Batch 5: Task E (flag flip) → Task F (drop cols) ‖ Task G (drop MESSAGE dispatch)
    ↓
Batch 6: Task H (docs)
    ↓
Final: Comprehensive review → fix loop → commit per deliverable
```

## Confidence: HIGH for sequential execution (each task strictly depends on the previous)
