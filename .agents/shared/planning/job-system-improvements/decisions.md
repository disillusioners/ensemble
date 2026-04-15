# Architecture Decisions — Job System Improvements

## ADR-001: Custom State Machine vs Library

**Decision:** Build a lightweight custom `JobStateMachine` class rather than using a library like `transitions` or `python-statemachine`.

**Rationale:**
- The job system has ~8 states and ~10 transitions — too small to justify a dependency.
- Custom implementation gives full control over logging, validation, and extensibility.
- No risk of library API changes breaking the system.
- The transition table is a simple dict — easy to read and extend.

**Consequences:**
- (+) Zero new dependencies
- (+) Full control over behavior
- (-) Must implement and test ourselves
- (-) No built-in visualization (acceptable tradeoff)

---

## ADR-002: DB-Backed Locks vs Redis

**Decision:** Persist locks in the existing SQLite database rather than introducing Redis.

**Rationale:**
- agents-ensemble already uses SQLite and has WAL mode enabled for write concurrency.
- Adding Redis would be a new infrastructure requirement — unacceptable for an open-source project that should run with minimal setup.
- Lock volume is low (max 20 concurrent per queue, typically 1-5 queues per project).
- SQLite handles this write volume easily.

**Consequences:**
- (+) No new infrastructure
- (+) Atomic with job state changes (same DB)
- (-) Not suitable for distributed deployments (future concern)
- (-) Write contention under extreme load (mitigated by WAL mode)

---

## ADR-003: Separate DLQ Table vs Status Flag

**Decision:** Use a separate `dead_letter_items` table rather than adding a DEAD_LETTER status to `job_queue_items`.

**Rationale:**
- DLQ items are fundamentally different: they're "done" from the queue's perspective. Keeping them in the main table would pollute all job queries and require constant `WHERE status != 'DEAD_LETTER'` filters.
- Separate table allows different schema (no `instance_id`, added `reason`, `moved_to_dlq_at`).
- Main table stays small and fast for active job queries.
- DLQ cleanup doesn't affect main table performance.

**Consequences:**
- (+) Clean separation of active vs dead jobs
- (+) Different schemas optimized for different use cases
- (-) Cross-table queries for full job history
- (-) Replay requires atomic cross-table coordination (mitigated by SQLite single-transaction)

---

## ADR-004: In-Process Event Bus vs External Message Broker

**Decision:** Use an in-process `asyncio.Event`-based event bus for job dispatch rather than an external broker (Redis Pub/Sub, RabbitMQ, etc.).

**Rationale:**
- The job processor already runs in the same process as the service layer.
- No network overhead or additional infrastructure.
- `asyncio.Event` is battle-tested and zero-dependency.
- Falls back to polling if events are missed (self-healing).

**Consequences:**
- (+) Zero new dependencies
- (+) Sub-millisecond signaling
- (+) Self-healing with polling fallback
- (-) Events lost on process crash (acceptable — polling catches it)
- (-) Not suitable for multi-process deployments (future concern)

> **Pre-existing note:** The project already has an `EventBus` (`daemon/services/event_bus.py`) for SSE checkpoint delivery at the TASK level. `DispatchEventBus` is a separate component at the JOB level — different scope, different consumers.

---

## ADR-005: All Model Fields in Phase 1 vs Per-Phase Migrations (Revised)

**Decision:** Add all new fields to `JobItem` and `JobQueue` in Phase 1 and run a single migration, even though some fields are only activated in later phases.

**Rationale:**
- SQLite migrations are not always trivial — adding columns requires care.
- Running one migration is simpler and less error-prone than 4 separate migrations.
- Fields with defaults are inert until code uses them — no behavioral change.
- Avoids the risk of Phase N failing because a column wasn't added.

**Revised scope (v2):** This principle now applies to **all tables** — both `job_queue_items` and `job_queues`. Queue-level fields (`default_timeout_minutes`, `default_max_retries`) are also added in Phase 1, not deferred to their activation phase.

**Exceptions:** The `job_locks` table (Phase 1) and `dead_letter_items` table (Phase 3) are separate CREATE TABLE statements. `dead_letter_items` is deferred to Phase 3 because it's a new table, not a column addition — and the table only makes sense when the DLQ service exists.

**Consequences:**
- (+) Single migration point for column additions
- (+) Simpler upgrade path
- (-) "Dead" columns between phases (acceptable — they have defaults)
- (-) Slightly larger initial diff

---

## ADR-006: Exponential Backoff Formula

**Decision:** Use `delay = min(base * 2^retry_count + jitter, max)` where jitter is a random value between 0 and `base`.

**Rationale:**
- Standard exponential backoff prevents thundering herd on transient failures.
- Jitter prevents synchronized retries from multiple failed jobs.
- Cap (`max`) prevents unreasonably long waits.
- Formula matches the existing `task_retry_backoff_*` config pattern.

**Consequences:**
- (+) Industry-standard retry pattern
- (+) Configurable via existing config mechanism
- (-) Requires documentation for operators to understand retry timing

---

## ADR-007: Auto-Retry Same Job vs New Job

**Decision:** Auto-retry transitions the **same job** in-place (FAILED→PENDING, same `job_id`). The manual retry API (`POST /api/jobs/{job_id}/retry`) remains unchanged — it creates a **new job** with a **new `job_id`**.

**Rationale:**
- Auto-retry must preserve the original `job_id` to maintain traceability (one logical work unit = one job).
- Creating a new job on every auto-retry would pollute the job list and make it hard to track a single work item's lifecycle.
- The manual retry API's "new job" behavior is a feature — it's a deliberate re-submission, not an automatic recovery. Changing it would break API consumers.
- `retry_count` on the job tracks auto-retries only. Manual retries start fresh at 0.

**Consequences:**
- (+) Clean job lifecycle tracking (one job = one logical work unit)
- (+) No breaking API changes — manual retry unchanged
- (+) `retry_count` gives clear visibility into auto-retry history
- (-) Two different retry semantics (internal vs API) — must be documented
- (-) API consumers can't observe auto-retries via job_id changes (acceptable — they see status flip back to PENDING)

---

## ADR-008: Atomic State Transitions via Single-Statement SQL

**Decision:** All state transitions use `UPDATE ... SET status=? WHERE job_id=? AND status=?` with rowcount verification — no read-then-write. Multi-table operations (DLQ move, replay) use a single SQLite session/transaction.

**Rationale:**
- Multiple concurrent actors modify job state: timeout monitors, instance completion callbacks, manual API cancellations, startup recovery, and retry schedulers.
- A read-then-write pattern (read current state → validate → write new state) has a TOCTOU gap — between the read and write, another actor can change the state, causing invalid or duplicate transitions.
- SQLite's single-writer model makes atomic UPDATE+WHERE both safe and efficient.
- The existing `start_job_atomic()` already uses this pattern — extending it to all transitions is consistent.
- For multi-table operations (DLQ move inserts into `dead_letter_items` and updates `job_queue_items`), both tables are in the same SQLite database, so a single session transaction provides cross-table atomicity.

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
- (+) No TOCTOU races — the database is the source of truth
- (+) Concurrent actors naturally de-conflict (first writer wins, others get rowcount=0)
- (+) Crash safety — SQLite auto-rollbacks uncommitted transactions
- (-) Callers must handle `InvalidTransitionError` gracefully (expected in concurrent scenarios)
- (-) Pre-validation via `can_transition()` is informational only — not authoritative
