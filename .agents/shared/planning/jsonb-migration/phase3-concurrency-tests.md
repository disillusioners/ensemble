# Phase 3: PostgreSQL Concurrency Tests

## Objective
Port the most critical concurrency test scenarios from SQLite approximations to real PostgreSQL, using separate connections to prove true EPQ (EvalPlanQual) re-evaluation, row-level locking, and atomic `jsonb_set` operations under concurrent access. These tests validate the concurrency remediation work against the actual production database engine.

## Coupling
- **Depends on**: Phase 2 (requires `pg_engine`, `pg_session_factory` fixtures from `tests/conftest_postgres.py`)
- **Coupling type**: tight — Phase 3 imports and directly uses Phase 2's fixture API. Must wait for Phase 2 review approval.
- **Shared files with other phases**: None (Phase 3 creates new test files only)
- **Shared APIs/interfaces**: Consumes `pg_engine`, `pg_session_factory`, `pg_repository_factory` from Phase 2
- **Why this coupling**: Every Phase 3 test depends on the PG fixture API being stable and the TRUNCATE isolation strategy working correctly.

## Context
- **7 existing concurrency test files** (all SQLite-based):
  1. `tests/job_queue/test_watcher_repository_concurrent.py` — Barrier + ThreadPoolExecutor + file-backed SQLite
  2. `tests/job_queue/test_idempotent_enqueue_atomic.py` — Barrier + Thread + file-backed SQLite
  3. `tests/job_queue/test_atomic_transition.py` — Barrier + Thread + in-memory SQLite
  4. `tests/message_queue_redesign/test_atomic_dequeue.py` — Barrier + 2/N threads
  5. `tests/message_queue_redesign/test_task_retry_repository.py` — NullPool + WAL (most aggressive SQLite)
  6. `tests/test_project_repository_atomic.py` — Barrier + Thread
  7. `tests/repositories/infra/test_infra_repository.py` — Barrier + Thread

- **Key limitation acknowledged in existing tests**: `test_task_retry_repository.py` explicitly states: *"the production code targets PostgreSQL which has native row-level locking"* — SQLite tests are approximations.

- **Concurrency remediation patterns to validate**:
  1. **Atomic status transitions** — `UPDATE ... SET status = X WHERE status = Y` with WHERE guard
  2. **Optimistic locking** — `version_id_col` on Task, JobItem, InfraAsset (version mismatch → rollback)
  3. **Atomic JSONB updates** — `jsonb_set()` / `json_set()` dialect-aware per-key writes (requires JSONB type from Phase 1!)
  4. **Unique constraint races** — INSERT ON CONFLICT (upserts)
  5. **Job lock slot claiming** — atomic `INSERT ON CONFLICT DO NOTHING` for slot acquisition

- **EPQ (EvalPlanQual)**: PostgreSQL's mechanism for re-evaluating queries when locked rows are modified by a concurrent transaction. This is the core behavior that SQLite cannot replicate. Tests must use **separate connections** (not the same connection) to trigger real lock contention.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `tests/postgres/conftest.py` concurrency helpers | Shared helpers: `pg_two_connections` (returns two independent sessions), `barrier_and_threads` factory, seed helpers (seed_job, seed_instance, seed_project). Reduce duplication. | `tests/postgres/conftest.py` |
| 2 | Port atomic status transition test | Test that concurrent `UPDATE ... SET status='running' WHERE status='pending'` on the SAME job results in exactly ONE winner. Uses 2+ separate connections + Barrier. Validates WHERE-status-guard pattern. | `tests/postgres/test_pg_atomic_transition.py` (new) |
| 3 | Port idempotent enqueue race test | Test concurrent idempotent enqueue with same `idempotency_key`. Exactly ONE insert succeeds, others get existing row (upsert). Validates `idx_job_idempotency` unique partial index + ON CONFLICT. | `tests/postgres/test_pg_idempotent_enqueue.py` (new) |
| 4 | Port job lock slot claiming test | Test concurrent `acquire_queue_lock` via slot INSERT. Validates `uq_job_locks_slot` unique constraint. Uses N threads, each trying to claim slot 0. | `tests/postgres/test_pg_job_lock_slot.py` (new) |
| 5 | Port optimistic locking (version) test | Test that concurrent updates to the same Task (version field) cause the loser to get a StaleDataError / version conflict. Validates `version_id_col` ORM optimistic locking on real PG. | `tests/postgres/test_pg_optimistic_locking.py` (new) |
| 6 | Create JSONB atomic update test | Test that `jsonb_set()` atomic per-key updates work correctly under concurrent access. Two connections update different keys of the same JSONB column. Both succeed without read-modify-write races. **Requires Phase 1 JSONB columns.** | `tests/postgres/test_pg_jsonb_atomic_update.py` (new) |
| 7 | Create instance mapping upsert race test | Test concurrent `create_instance_mapping` with same (source_id, external_user_id). Exactly ONE insert wins (validates `uq_instance_mappings_source_user`). | `tests/postgres/test_pg_instance_mapping_upsert.py` (new) |
| 8 | Document test running instructions | Add `tests/postgres/README.md` with: how to start PG, how to run tests, how to interpret EPQ-related results, expected race outcomes. | `tests/postgres/README.md` (new) |

## Key Files

| File | Purpose |
|------|---------|
| `tests/postgres/conftest.py` | PG-specific concurrency helpers and seed factories |
| `tests/postgres/test_pg_atomic_transition.py` | Status transition race (WHERE guard validation) |
| `tests/postgres/test_pg_idempotent_enqueue.py` | Idempotent insert race (unique index validation) |
| `tests/postgres/test_pg_job_lock_slot.py` | Slot claiming race (unique constraint validation) |
| `tests/postgres/test_pg_optimistic_locking.py` | Version column optimistic locking on real PG |
| `tests/postgres/test_pg_jsonb_atomic_update.py` | `jsonb_set` concurrent key updates (requires Phase 1) |
| `tests/postgres/test_pg_instance_mapping_upsert.py` | Instance mapping upsert race |
| `tests/postgres/README.md` | Documentation for PG concurrency test suite |

## Test Design Patterns

### Pattern: Two Separate Connections (EPQ validation)

```python
def test_concurrent_status_transition(pg_engine):
    """Two connections race to transition the same job pending→running.
    Exactly one must win. Validates WHERE-status-guard + EPQ."""
    # Seed a job in 'pending' status
    seed_job(pg_engine, job_id="job-1", status="pending")
    
    # Two independent sessions (separate connections = real lock contention)
    Session = sessionmaker(bind=pg_engine)
    results = {}
    barrier = threading.Barrier(2)
    
    def worker(name):
        session = Session()
        barrier.wait()  # synchronize start
        try:
            result = session.execute(text(
                "UPDATE job_queue_items SET status='running' "
                "WHERE id='job-1' AND status='pending'"
            ))
            session.commit()
            results[name] = result.rowcount  # 1=winner, 0=loser
        finally:
            session.close()
    
    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    
    # Exactly one winner
    winners = sum(1 for v in results.values() if v == 1)
    assert winners == 1, f"Expected exactly 1 winner, got {winners}"
```

### Pattern: N-Thread Barrier Race

```python
def test_concurrent_slot_claim(pg_engine, n_threads=8):
    """N threads race to claim the same lock slot.
    Exactly one INSERT succeeds via ON CONFLICT DO NOTHING."""
    barrier = threading.Barrier(n_threads)
    Session = sessionmaker(bind=pg_engine)
    
    def worker():
        session = Session()
        barrier.wait()
        try:
            session.execute(text(
                "INSERT INTO job_locks (project_id, queue_id, lock_slot) "
                "VALUES ('p1', 'q1', 0) ON CONFLICT DO NOTHING"
            ))
            session.commit()
        finally:
            session.close()
    
    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    
    # Exactly one lock row exists
    with pg_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM job_locks")).scalar()
    assert count == 1
```

### Pattern: JSONB Concurrent Key Update (requires Phase 1)

```python
def test_jsonb_concurrent_key_update(pg_engine):
    """Two connections update DIFFERENT keys of the same JSONB column.
    Both updates survive — no read-modify-write race."""
    seed_job(pg_engine, job_id="job-1", metadata={"a": 1, "b": 1})
    
    Session = sessionmaker(bind=pg_engine)
    barrier = threading.Barrier(2)
    
    def update_key(key, value):
        session = Session()
        barrier.wait()
        try:
            # jsonb_set is atomic per-key — no read-modify-write
            session.execute(text(
                "UPDATE job_queue_items SET metadata = jsonb_set(metadata, :path, :val) "
                "WHERE id='job-1'"
            ), {"path": f"{{{key}}}", "val": json.dumps(value)})
            session.commit()
        finally:
            session.close()
    
    t1 = Thread(target=update_key, args=("a", 2))
    t2 = Thread(target=update_key, args=("b", 3))
    t1.start(); t2.start(); t1.join(); t2.join()
    
    with pg_engine.connect() as conn:
        meta = conn.execute(text("SELECT metadata FROM job_queue_items WHERE id='job-1'")).scalar()
    
    assert meta["a"] == 2
    assert meta["b"] == 3  # Both updates survived!
```

### Why SQLite Can't Do This

SQLite (even with WAL + NullPool + file-backed) has database-level write locking (single writer). PostgreSQL has MVCC with row-level locking + EPQ:
- **SQLite**: Two threads hitting the same row → one blocks until the other commits. No EPQ. The WHERE guard works but there's no real "race" — it's serialized.
- **PostgreSQL**: Two connections can simultaneously evaluate `WHERE status='pending'`, both see the row, both try to UPDATE. EPQ kicks in: the second updater re-checks the WHERE clause after the first commits. If status changed, the second UPDATE affects 0 rows.

These PG tests prove the production code path actually works under real concurrency.

## Constraints
- **Separate connections**: Every concurrency test MUST use separate connections (separate `Session()` instances), NOT the same connection. This is what triggers real PG locking.
- **No mocks**: These tests MUST execute real SQL against real PostgreSQL. No `MagicMock`, no `patch()`.
- **Deterministic outcomes**: Despite concurrency, the assertions must be deterministic (exactly 1 winner, exactly 1 row, etc.). This is the whole point — the database guarantees these outcomes.
- **TRUNCATE isolation**: Tests rely on Phase 2's `_pg_truncate_tables` autouse fixture. Each test starts with clean tables.
- **JSONB dependency**: Task 6 (jsonb_atomic_update) requires Phase 1 columns to be JSONB. If Phase 1 isn't done, this test will fail with CannotCoerce.
- **Repeat runs**: Consider running each race test 5-10 times in a loop to catch rare interleavings. The SQLite tests do this; PG tests should too.

## Deliverables
- [ ] `tests/postgres/conftest.py` with concurrency helpers (two-connection, barrier, seed factories)
- [ ] `tests/postgres/test_pg_atomic_transition.py` — status transition race (1+ test)
- [ ] `tests/postgres/test_pg_idempotent_enqueue.py` — idempotent insert race (1+ test)
- [ ] `tests/postgres/test_pg_job_lock_slot.py` — slot claiming race (1+ test)
- [ ] `tests/postgres/test_pg_optimistic_locking.py` — version conflict on real PG (1+ test)
- [ ] `tests/postgres/test_pg_jsonb_atomic_update.py` — jsonb_set concurrent key updates (1+ test)
- [ ] `tests/postgres/test_pg_instance_mapping_upsert.py` — mapping upsert race (1+ test)
- [ ] `tests/postgres/README.md` — documentation
- [ ] All tests pass with `pytest -m postgres` against real PostgreSQL
- [ ] All tests deterministic (no flaky failures across 10 repeat runs)
