# Phase 1: Enum & State Machine

## Objective

Add the `PAUSED` state to both `JobStatus` and `TaskStatus` enums and update all state-transition validation to accept the new state. **No database migration is needed** — statuses are VARCHAR/TEXT columns with app-level validation, so adding `PAUSED = "paused"` requires only Python enum additions.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/repositories/job_queue/models.py`, `daemon/repositories/task/models.py`
- **Shared APIs/interfaces**: `JobStatus`, `TaskStatus` enums (consumed by Phases 2, 3, 4, 5)
- **Why this coupling**: All subsequent phases import and use the PAUSED state added here

## Context

- This is the foundational phase — everything else builds on these enum changes
- **No DB schema change needed** (reviewer S1): `PAUSED` is a new enum VALUE, not a new column. Statuses are VARCHAR/TEXT with app-level validation (no native PG enums). Adding `PAUSED = "paused"` requires zero `.sql` migration files and zero `_ensure_postgres_columns()` changes.
- The real work is finding ALL status enumeration sites (switch statements, validation maps, transition guards) and ensuring they handle PAUSED.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add PAUSED to JobStatus enum | Add `PAUSED = "paused"` to the `JobStatus` enum. | `daemon/repositories/job_queue/models.py:21` |
| 2 | Add PAUSED to TaskStatus enum | Add `PAUSED = "paused"` to the `TaskStatus` enum. | `daemon/repositories/task/models.py:36` |
| 3 | **Add transition pairs to TRANSITIONS dict in `job_state_machine.py`** (BLOCKER — approver B1) | This is the **authoritative enforcement gate** for all job state transitions. `JobRepository.atomic_transition()` at `repository.py:664` calls `job_state_machine.validate_transition()` BEFORE any UPDATE — if the pair isn't in the TRANSITIONS dict, it throws `InvalidTransitionError`. Without these pairs, **every pause/resume would throw on first execution**. Add these pairs to the dict at lines 20-41: `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)` (Decision 10). | **`daemon/services/job_state_machine.py:20-41`** (PRIMARY), `validate_transition` at line 76, `daemon/repositories/job_queue/repository.py:664` (caller) |
| 4 | Update secondary job transition maps | Update any secondary transition validation/mapping in `job_queue_service.py` and `repository.py` to be consistent with the new transitions. These are secondary to the PRIMARY enforcement gate in `job_state_machine.py` (Task 3). Add `PROCESSING → PAUSED`, `PAUSED → PROCESSING`, `PAUSED → CANCELLED`. | `daemon/services/job_queue_service.py`, `daemon/repositories/job_queue/repository.py` |
| 5 | Update TaskStatus state transitions | Find the state-transition validation for tasks. Add PAUSED to allowed transitions: `RUNNING → PAUSED`, `PAUSED → PENDING`, `PAUSED → CANCELLED`. Verify whether tasks have a similar enforcement gate like `job_state_machine.py` — if so, add the pairs there first. | `daemon/repositories/task/repository.py` |
| 6 | ~~SQLite migration file~~ | ~~Create `.sql` migration~~ — **NOT NEEDED** (S1). Enum values are app-level VARCHAR, no DDL change required. | ~~`daemon/migrations/`~~ |
| 7 | ~~PostgreSQL: _ensure_postgres_columns~~ | ~~Update for new columns~~ — **NOT NEEDED** (S1). No new columns. Only Python enum additions. | ~~`daemon/manager.py:1587-1759`~~ |
| 8 | **Audit ALL status enumeration sites** (includes `job_state_machine.py`) | This is the real work of Phase 1. Search the entire codebase for every place that enumerates or switches on JobStatus/TaskStatus values. **Critical file**: `job_state_machine.py` — verify the TRANSITIONS dict from Task 3 is the only gate, and no other code path bypasses it. Common patterns: `if status == JobStatus.COMPLETED`, `status not in (...)`, validation maps, SSE event mappings, UI status display. Ensure each handles PAUSED correctly. | **`daemon/services/job_state_machine.py`** (critical), codebase-wide search for `JobStatus` and `TaskStatus` references |
| 9 | Update `claim_pending_task` serialization guard | Review the per-instance serialization guard (Guard 1, repository.py:315-318). It currently checks `status='running'`. A PAUSED task should NOT block a sibling — but since paused instances are filtered by the pause gate (Guard 2), this is likely a no-op change. Verify with explicit test in Phase 5. | `daemon/repositories/task/repository.py:315-318` |
| 10 | Update `has_inflight_task` | Review `has_inflight_task` (repository.py:149-190). PAUSED tasks should NOT count as "in-flight" — PAUSED means paused, not active. The current query checks `status IN ('pending', 'running')` which already excludes PAUSED. Verify this is correct. | `daemon/repositories/task/repository.py:149-190` |
| 11 | Update DemandState mapping | Find where `DemandState` (COMPLETED, FAILED, CANCELLED) maps to `JobStatus`. Ensure no PAUSED demand state is needed (pause is not a demand — it's a user action). | `daemon/services/job_queue_service.py` |
| 12 | Write unit tests for new states | Test that PAUSED state is accepted in transitions via `job_state_machine.validate_transition()`, rejected for invalid transitions (e.g., `COMPLETED → PAUSED`), and properly stored/retrieved from DB on both SQLite and PostgreSQL. Include test for `PAUSED → CANCELLED` (Decision 10). | `tests/unit/test_paused_state.py` (new) |

## Key Files

- **`daemon/services/job_state_machine.py`** — **TRANSITIONS dict (lines 20-41)** — the authoritative enforcement gate. `validate_transition()` at line 76 throws `InvalidTransitionError` if pair not in dict. `JobRepository.atomic_transition()` at `repository.py:664` calls this before any UPDATE. **CRITICAL: must add PAUSED transition pairs here or every pause/resume throws.**
- `daemon/repositories/job_queue/models.py` — JobStatus enum definition (line 21)
- `daemon/repositories/task/models.py` — TaskStatus enum definition (line 36)
- `daemon/repositories/instance/models.py` — InstanceStatus (already has PAUSED at line 20, reference)
- `daemon/services/job_queue_service.py` — Job state machine transitions, `complete_job()` at line 1335
- `daemon/repositories/job_queue/repository.py` — Job repository with `atomic_transition` at line 664 (calls `job_state_machine.validate_transition`)
- `daemon/repositories/task/repository.py` — Task repository, `claim_pending_task` at line 196, `has_inflight_task` at line 149, serialization guard at line 315-318
- `daemon/services/job_feedback_observer.py` — `_get_processing_job_for_instance` at line 518 (only returns PROCESSING — verify PAUSED excluded correctly)

## Constraints

- Enum changes are additive — don't remove or reorder existing values
- Both `JobStatus` and `TaskStatus` are VARCHAR/TEXT columns with app-level validation (no native PG enums)
- **No `.sql` migration files needed** — no DDL change required
- **No `_ensure_postgres_columns()` changes needed** — no new columns

## Deliverables

- [ ] `JobStatus.PAUSED` added to enum
- [ ] `TaskStatus.PAUSED` added to enum
- [ ] **Transition pairs added to TRANSITIONS dict in `job_state_machine.py`** — `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)` (BLOCKER B1)
- [ ] ALL status enumeration sites audited and handle PAUSED correctly (including `job_state_machine.py` verification)
- [ ] `claim_pending_task` serialization guard reviewed for PAUSED (likely no change)
- [ ] `has_inflight_task` reviewed for PAUSED semantics (already excludes PAUSED)
- [ ] Unit tests for new state transitions passing on both SQLite and PostgreSQL
- [ ] No existing tests broken by enum addition

## Notes

- **Simplified from original plan** (reviewer S1): Removed tasks for SQLite migration and `_ensure_postgres_columns()` — they are not needed. The real work shifted to Task 7: auditing all status enumeration sites.
- Enum additions are low-risk since they're additive
- The main risk is missing a place that enumerates all states (switch statements, validation maps, SSE mappings)
- Search for all references to `JobStatus` and `TaskStatus` to find enumeration sites
- The `DemandState` enum is separate (in-memory demand states) and does NOT need a PAUSED value
