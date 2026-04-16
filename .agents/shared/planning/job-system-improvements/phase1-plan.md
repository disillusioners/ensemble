# Phase 1: Foundation — State Machine & Persistent Locks

## Objective

Formalize the job state machine with validated transitions, fix the `cancel_job()` repository bypass, migrate `JobLockManager` from in-memory dict to database-backed persistence, and add all new model fields in a single migration. This is the foundation all other improvements build upon.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `models.py` (all phases use fields added here), `job_queue_service.py` (all phases add methods)
- **Shared APIs/interfaces**: `JobStatus` enum, `StateMachine` class, `JobLockManager.acquire/release` interface
- **Why this coupling**: State machine is the single source of truth for transitions. Persistent locks are needed by recovery (Phase 2). DLQ (Phase 3) adds a new state. Event dispatch (Phase 4) triggers on transitions.

## Context

The current system uses ad-hoc state validation — each method in `JobQueueService` and `JobRepository` independently checks `job.status == X` before transitioning. This is fragile: adding a new state (e.g., DEAD_LETTER) requires updating every method.

Additionally, the service layer's `cancel_job()` bypasses the repository for PROCESSING jobs (calls `self._repository.update()` directly because `cancel_job()` in the repo only handles PENDING). The state machine must capture **both** paths as legitimate transitions.

The in-memory `JobLockManager` loses all lock state on daemon restart, causing queue capacity to be misreported.

## Tasks

### Task 1: Implement Formal State Machine

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | Define transition table | Create `JobStateMachine` class with explicit `(from_state, to_state)` map. Include all current transitions (including PROCESSING→CANCELLED abort path) + placeholders for future states. | `daemon/services/job_state_machine.py` (NEW) |
| 1.2 | Add transition validation | `can_transition(from_state, to_state) -> bool` for pre-checking. The real enforcement happens in the repository's atomic methods. | `daemon/services/job_state_machine.py` (NEW) |
| 1.3 | Add `atomic_transition()` to JobRepository | Central method for all state transitions: `UPDATE job_queue_items SET status=?, **updates WHERE job_id=? AND status=?` with rowcount verification. Raises `InvalidTransitionError` on rowcount=0 (stale state / concurrent modification). This replaces all read-then-write patterns. | `daemon/repositories/job_queue/repository.py` |
| 1.4 | Fix `cancel_job()` repository bypass | Add `PROCESSING → CANCELLED` as a valid transition in the state machine. Refactor repository `cancel_job()` to accept both PENDING and PROCESSING as valid source states, eliminating the service-layer bypass. Both paths use `atomic_transition()`. | `daemon/repositories/job_queue/repository.py`, `daemon/services/job_queue_service.py` |
| 1.5 | Integrate into JobQueueService | Replace ad-hoc `if job.status != X` checks with calls to repository's `atomic_transition()`. The state machine's `can_transition()` is used for pre-validation (e.g., returning 409 to API callers) but the authoritative check is the atomic UPDATE. Methods: `start_job`, `complete_job`, `fail_job`, `cancel_job`. | `daemon/services/job_queue_service.py` |
| 1.6 | Integrate into JobRepository | `start_job_atomic()` already uses atomic pattern — keep it. `cancel_job()` should accept PROCESSING source state and use `atomic_transition()`. All other transition methods (`complete_job`, `fail_job`) migrated to `atomic_transition()`. | `daemon/repositories/job_queue/repository.py` |
| 1.7 | Add transition logging | Log every state transition with job_id, from_status, to_status, timestamp. Done inside `atomic_transition()` after successful rowcount check. | `daemon/repositories/job_queue/repository.py` |

**Transition table:**

```python
TRANSITIONS = {
    # Current transitions
    (None, PENDING): "create",
    (PENDING, PROCESSING): "start",
    (PROCESSING, COMPLETED): "complete",
    (PROCESSING, FAILED): "fail",
    (PENDING, CANCELLED): "cancel",
    (PROCESSING, CANCELLED): "abort",        # Was a service bypass — now formalized
    (FAILED, PENDING): "retry",               # Both manual retry API and auto-retry
    # Future transitions (Phase 3)
    (FAILED, DEAD_LETTER): "dead_letter",
    (DEAD_LETTER, PENDING): "replay",
}
```

> **No TIMED_OUT state.** Tasks already handle timeout. See ADR-009.

### Task 2: Persist Locks to Database

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | Add `job_locks` table | New SQLModel table: `lock_id` (PK), `project_id`, `queue_id`, `job_id`, `instance_id`, `acquired_at`. | `daemon/repositories/job_queue/models.py` |
| 2.2 | Create LockRepository | CRUD for `job_locks`: `acquire()`, `release()`, `release_by_instance()`, `get_active_locks()`, `find_stale_locks()`. | `daemon/repositories/job_queue/lock_repository.py` (NEW) |
| 2.3 | Refactor JobLockManager | Replace in-memory dict with LockRepository calls. Keep `asyncio.Lock` for serialization but persist every acquire/release to DB. | `daemon/services/job_lock_manager.py` |
| 2.4 | Startup lock reconciliation | On startup, load all locks from DB. If any lock's `instance_id` points to a non-existent or completed instance, release it. | `daemon/services/job_lock_manager.py` |

**Lock table schema:**

```python
class JobLock(SQLModel, table=True):
    __tablename__ = "job_locks"
    lock_id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    project_id: str = Field(index=True)
    queue_id: str = Field(index=True)
    job_id: str = Field(index=True)
    instance_id: Optional[str] = Field(default=None, index=True)
    acquired_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
```

### Task 3: Add New Fields to JobItem and JobQueue Models

All new fields are added in Phase 1 per ADR-005. Fields are inert until later phases activate them.

#### JobItem — New Fields

| # | Sub-task | Field | Type | Default | Activated By | Key Files |
|---|----------|-------|------|---------|-------------|-----------|
| 3.1 | `retry_count` | `int` | `0` | Phase 3 | `daemon/repositories/job_queue/models.py` |
| 3.2 | `max_retries` | `Optional[int]` | `None` | Phase 3 | `daemon/repositories/job_queue/models.py` |
| 3.3 | `idempotency_key` | `Optional[str]` | `None` | Phase 4 | `daemon/repositories/job_queue/models.py` |
| 3.4 | `failed_at` | `Optional[str]` | `None` | Phase 3 | `daemon/repositories/job_queue/models.py` |
| 3.5 | `next_retry_at` | `Optional[str]` | `None` | Phase 3 | `daemon/repositories/job_queue/models.py` |

> **Removed `last_heartbeat_at`:** No job-level heartbeat (W1). Tasks track activity via `started_at`.
> **Removed `max_duration_seconds`:** No job-level timeout (ADR-009). Tasks handle timeout.

#### JobQueue — New Fields

| # | Sub-task | Field | Type | Default | Activated By | Key Files |
|---|----------|-------|------|---------|-------------|-----------|
| 3.6 | `default_max_retries` | `Optional[int]` | `None` | Phase 3 | `daemon/repositories/job_queue/models.py` |

### Task 4: Migration

All schema changes are combined into a **single migration** per ADR-005.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | Migration file | Create `YYYYMMDD_HHMMSS_add_job_system_improvements.sql` in `daemon/migrations/versions/`. Follow existing `MigrationRunner` convention (`daemon/migrations/runner.py`). | `daemon/migrations/versions/` |
| 4.2 | UP: Create `job_locks` table | `CREATE TABLE job_locks (...)` with indexes on `project_id`, `queue_id`, `instance_id`. | Migration file |
| 4.3 | UP: Add JobItem columns | One `ALTER TABLE` per column (SQLite requirement). | Migration file |
| 4.4 | UP: Add JobQueue columns | `ALTER TABLE job_queues ADD COLUMN default_max_retries INTEGER DEFAULT NULL`. | Migration file |
| 4.5 | UP: Add partial unique index | `CREATE UNIQUE INDEX idx_job_idempotency ON job_queue_items(idempotency_key) WHERE idempotency_key IS NOT NULL`. | Migration file |
| 4.6 | DOWN: Reverse all changes | Drop `job_locks` table, drop index. Note SQLite `DROP COLUMN` limitations for older versions. | Migration file |

> **Migration system:** Uses `MigrationRunner` at `daemon/migrations/runner.py`. Migrations auto-applied on startup at `daemon/manager.py:354-359`. File naming: `YYYYMMDD_HHMMSS_description.sql` with `-- UP` / `-- DOWN` sections.

### Task 5: Configuration Updates

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 5.1 | Add job system config section | New `JobSystemConfig` with: `default_max_retries` (3), `retry_backoff_base_seconds` (60), `retry_backoff_max_seconds` (3600), `dlq_enabled` (True), `event_dispatch_enabled` (True), `observer_health_check_interval_seconds` (300). | `daemon/config.py` |
| 5.2 | Add to config.yaml schema | Document new section with sensible defaults. | `config.yaml` (reference) |
| 5.3 | Wire config in api.py | Load `JobSystemConfig` and pass to relevant services. | `daemon/api.py` |

## Key Files

| File | Role |
|------|------|
| `daemon/services/job_state_machine.py` (NEW) | State machine definition and validation |
| `daemon/repositories/job_queue/models.py` | Extended JobItem + JobQueue models, new JobLock model |
| `daemon/repositories/job_queue/lock_repository.py` (NEW) | Lock persistence layer |
| `daemon/services/job_lock_manager.py` | Refactored to use DB-backed locks |
| `daemon/services/job_queue_service.py` | Uses state machine for transitions; cancel_job bypass removed |
| `daemon/repositories/job_queue/repository.py` | Uses state machine in atomic operations; cancel_job handles PROCESSING |
| `daemon/config.py` | New JobSystemConfig |
| `daemon/api.py` | Wire new config and lock reconciliation |
| `daemon/migrations/versions/YYYYMMDD_HHMMSS_add_job_system_improvements.sql` (NEW) | Single migration for all schema changes |

## Constraints

- **No breaking API changes.** All new fields are optional with defaults.
- **Migration must be backward compatible.** Existing jobs continue to work.
- **State machine must be extensible.** Future phases add states without refactoring the core.
- **Lock manager interface unchanged.** `acquire_queue_lock()`, `release_queue_lock()`, `release_by_instance()` signatures stay the same — only internal implementation changes.
- **cancel_job bypass eliminated.** Repository `cancel_job()` will handle both PENDING and PROCESSING source states via the state machine.
- **All state transitions use `atomic_transition()`.** No read-then-write. The `UPDATE ... WHERE status = ?` pattern is the single source of truth.

## Deliverables

- [ ] `JobStateMachine` class with full transition table and validation
- [ ] `JobLock` model and `LockRepository` for persistent locks
- [ ] Refactored `JobLockManager` using DB-backed locks
- [ ] Startup lock reconciliation (orphaned locks cleaned)
- [ ] Extended `JobItem` model with all new fields (backward compatible)
- [ ] Extended `JobQueue` model with `default_max_retries`
- [ ] `JobSystemConfig` in config system
- [ ] Single database migration for all schema changes
- [ ] `cancel_job()` repository bypass eliminated
- [ ] `atomic_transition()` method in JobRepository (single-statement state changes with rowcount verification)
- [ ] All existing tests pass
