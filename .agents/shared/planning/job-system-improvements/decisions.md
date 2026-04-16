# Architecture Decisions — Job System Improvements

## ADR-001: Custom State Machine vs Library

**Decision:** Build a lightweight custom `JobStateMachine` class rather than using a library.

**Rationale:**
- The job system has ~6 states and ~8 transitions — too small to justify a dependency.
- Custom implementation gives full control over logging, validation, and extensibility.
- No risk of library API changes breaking the system.

**Consequences:**
- (+) Zero new dependencies
- (+) Full control over behavior
- (-) Must implement and test ourselves

---

## ADR-002: DB-Backed Locks vs Redis

**Decision:** Persist locks in the existing SQLite database rather than introducing Redis.

**Rationale:**
- agents-ensemble already uses SQLite with WAL mode.
- Adding Redis would be a new infrastructure requirement.
- Lock volume is low (max 20 concurrent per queue).

**Consequences:**
- (+) No new infrastructure
- (+) Atomic with job state changes (same DB)
- (-) Not suitable for distributed deployments (future concern)

---

## ADR-003: Separate DLQ Table vs Status Flag

**Decision:** Use a separate `dead_letter_items` table rather than a DEAD_LETTER status on `job_queue_items`.

**Rationale:**
- DLQ items are "done" from the queue's perspective. Keeping them in the main table pollutes all queries.
- Separate table allows different schema.
- Main table stays small and fast for active job queries.

**Consequences:**
- (+) Clean separation of active vs dead jobs
- (-) Cross-table queries for full job history
- (-) Replay requires atomic cross-table coordination (mitigated by SQLite single-transaction)

---

## ADR-004: In-Process Event Bus vs External Message Broker

**Decision:** Use an in-process `asyncio.Event`-based event bus for job dispatch.

**Rationale:**
- The job processor runs in the same process as the service layer.
- `asyncio.Event` is battle-tested and zero-dependency.
- Falls back to polling if events are missed (self-healing).

**Consequences:**
- (+) Zero new dependencies
- (+) Sub-millisecond signaling
- (-) Events lost on process crash (acceptable — polling catches it)

---

## ADR-005: All Model Fields in Phase 1 vs Per-Phase Migrations (Revised v5)

**Decision:** Add all new fields to `JobItem` and `JobQueue` in Phase 1 and run a single migration.

**Rationale:**
- SQLite migrations are not always trivial — one migration is simpler.
- Fields with defaults are inert until code uses them.
- Avoids Phase N failing because a column wasn't added.

**Revised scope (v5):** Removed `max_duration_seconds`, `last_heartbeat_at`, `default_timeout_minutes` (no job-level timeout per ADR-009). Added fields: `retry_count`, `max_retries`, `failed_at`, `next_retry_at`, `idempotency_key` on JobItem. `default_max_retries` on JobQueue.

**Exceptions:** `job_locks` table (Phase 1) and `dead_letter_items` table (Phase 3) are separate CREATE TABLE statements.

**Migration system:** Uses `MigrationRunner` at `daemon/migrations/runner.py`. Auto-applied on startup at `daemon/manager.py:354-359`. File naming: `YYYYMMDD_HHMMSS_description.sql`.

---

## ADR-006: Exponential Backoff Formula

**Decision:** Use `delay = min(base * 2^retry_count + jitter, max)`.

**Rationale:**
- Standard exponential backoff prevents thundering herd.
- Jitter prevents synchronized retries.
- Matches existing `task_retry_backoff_*` config pattern.

---

## ADR-007: Auto-Retry Same Job vs New Job

**Decision:** Auto-retry transitions the **same job** in-place (FAILED→PENDING, same `job_id`). Manual retry API creates a **new job** with a **new `job_id`**.

**Rationale:**
- Auto-retry preserves the original `job_id` for traceability.
- Creating a new job on every auto-retry pollutes the job list.
- Manual retry API's "new job" behavior is a feature, not a bug.

**Consequences:**
- (+) Clean job lifecycle tracking
- (+) No breaking API changes
- (-) Two different retry semantics — must be documented

---

## ADR-008: Atomic State Transitions via Single-Statement SQL

**Decision:** All state transitions use `UPDATE ... SET status=? WHERE job_id=? AND status=?` with rowcount verification. Multi-table operations use a single SQLite session/transaction.

**Rationale:**
- Multiple concurrent actors modify job state: feedback observers, terminate_instance, manual API cancellations, startup recovery, retry schedulers.
- Read-then-write has a TOCTOU gap.
- SQLite's single-writer model makes atomic UPDATE+WHERE safe and efficient.

**Pattern:**
```python
def atomic_transition(session, job_id, from_status, to_status, **updates):
    stmt = update(JobItem).where(
        JobItem.job_id == job_id,
        JobItem.status == from_status
    ).values(status=to_status, **updates)
    result = session.exec(stmt)
    session.commit()
    if result.rowcount == 0:
        raise InvalidTransitionError(...)
```

**Consequences:**
- (+) No TOCTOU races
- (+) Concurrent actors naturally de-conflict (first writer wins)
- (-) Callers must handle `InvalidTransitionError` gracefully

---

## ADR-009: No Duplicate Timeout/Recovery — Tasks System Owns Execution

**Decision:** The job system does NOT implement job-level timeout, heartbeat, or crash recovery. The tasks system already handles execution-level concerns. The job system observes task results.

**Rationale:**

The tasks system is already mature:

| Task-Level Capability | Component | Detail |
|-----------------------|-----------|--------|
| Per-task timeout | `TimeoutMonitor` | 5 min default, cancels via `CancellationToken` |
| Graph-level timeout | `TaskProcessor` | 40 min default |
| Stale task detection | `StaleTaskRecovery` | 15 min threshold, cancels + retries |
| Task retry with backoff | `StaleTaskRecovery` | 60s base, 3 retries |
| Cancellation cascade | `CancellationToken` | Propagates through LangGraph |

**Removed from plan:**
- `JobTimeoutMonitor` service
- `max_duration_seconds` field
- Mandatory default timeout
- Job-level heartbeat / `last_heartbeat_at` field
- `TIMED_OUT` state

**Consequences:**
- (+) No duplicate timeout/recovery logic
- (+) Single source of truth for execution reliability
- (-) Job stays PROCESSING until instance completes (acceptable — observer catches this)

---

## ADR-010: Instance Lifecycle Events for Top-Level Instances (NEW)

**Decision:** Add `INSTANCE_LIFECYCLE` event publishing for top-level instances (parent_id=None) that have associated jobs. This is NEW functionality — currently, no events are published when top-level instances complete.

**Rationale:**

Codebase investigation revealed that `_process_child_completion_and_notify_parent()` at `daemon/manager.py:1730` returns early for instances without `parent_id`:

```python
if instance.parent_id is None:
    return  # ← No event published for job instances
```

Only child instances (those spawned by other agents) get `INSTANCE_COMPLETED` events. Job instances are top-level and silently complete without any notification.

Additionally, `terminate_instance()` does not publish any EventBus event. The observer would miss error/termination scenarios.

**Implementation approach:**
- Add `INSTANCE_LIFECYCLE` EventKind to the enum
- Add `_publish_instance_lifecycle_event()` method to InstanceManager
- Hook into: (a) the parent_id early return path, (b) `terminate_instance()`, (c) error handlers
- Event data: `{"instance_id": str, "status": str, "error": str|None}`
- Status values: `completed`, `terminated`, `error`

**Alternative considered:** Add separate `INSTANCE_TERMINATED` and `INSTANCE_ERROR` event kinds. Rejected in favor of a single `INSTANCE_LIFECYCLE` event with a `status` field — simpler to implement and subscribe to.

**Consequences:**
- (+) Observer can detect all instance lifecycle changes
- (+) Single event kind for all top-level instance transitions
- (-) Requires modification to InstanceManager (new code, not observation)
- (-) Must audit all instance status transition paths to ensure events are published

---

## ADR-011: Cancellation Cascade via Existing terminate_instance() (NEW)

**Decision:** Use existing `terminate_instance()` for cancellation cascade instead of creating a new `cancel_instance()` method.

**Rationale:**

`cancel_instance()` does not exist. The closest method is `cancel_instance_requests()` at `daemon/manager.py:2175`, which only cancels active LLM requests — it does NOT signal tasks to stop or force-terminate.

Creating a new `cancel_instance()` would duplicate most of `terminate_instance()` logic:
- Cascading to children
- Cancelling active requests
- Releasing locks
- Updating instance status
- Marking job status

**Implementation approach:**
1. `cancel_job()` calls `terminate_instance(job.instance_id)`
2. `terminate_instance()` marks job as FAILED (existing behavior at line 2240)
3. If caller wants CANCELLED status: `atomic_transition(FAILED → CANCELLED)` after terminate
4. This second transition is safe because `atomic_transition()` checks `WHERE status='FAILED'`

**Consequences:**
- (+) No new method to implement and test
- (+) Leverages existing cascade, lock release, and cleanup logic
- (-) Double transition (FAILED → CANCELLED) adds slight overhead
- (-) Brief moment where job shows FAILED before becoming CANCELLED (acceptable)

---

## ADR-012: EventBus Event Field Name (NEW)

**Decision:** The observer filters events using `event["event_type"]`, NOT `event["kind"]`.

**Rationale:**

Verified EventBus event structure at `daemon/services/event_bus.py:335-343`:

```python
event = {
    "instance_id": instance_id,    # Always present
    "event_type": event_type,      # Always present — NOT "kind"
    "event_id": event_id,          # Optional
    "data": data,                  # Optional
}
```

The `_broadcast_to_global()` method constructs events with `event_type` key. Using `kind` would never match.

**Consequences:**
- (+) Correct event filtering
- (-) Must be documented clearly for future maintainers
