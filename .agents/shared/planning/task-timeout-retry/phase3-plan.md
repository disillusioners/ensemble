# Phase 3: Repository Layer

## Objective

Add new repository methods for retry scheduling, cancellation requests, and enhanced task claiming that respects backoff delays. These methods implement the data access patterns needed by Worker (Phase 4) and StaleTaskRecovery (Phase 5).

## Coupling

- **Depends on**: Phase 1 (model fields must exist)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/repositories/task/repository.py` (shared with Phase 4, Phase 5)
- **Shared APIs/interfaces**: claim_pending_task(), schedule_retry(), request_cancel(), find_cancellable_tasks()
- **Why this coupling**: Worker calls claim_pending_task() and schedule_retry(); StaleTaskRecovery calls request_cancel() and find_cancellable_tasks()

## Context

### Current Repository Methods
- `create()`, `get()`, `get_by_instance()`, `get_by_message()`
- `claim_pending_task()` — atomic UPDATE-RETURNING, claims next PENDING task
- `complete_task()`, `fail_task()` — update status + result/error
- `find_stale_running_tasks()`, `reset_stale_tasks()` — for StaleTaskRecovery
- `get_pending_count()`, `count_by_status()`
- `delete()`, `delete_by_instance()`

### What's Needed
1. Enhanced `claim_pending_task()` — respect `next_retry_at` delay
2. `schedule_retry()` — create new Task with backoff, mark parent CANCELLED + set `retry_scheduled=True` (atomic)
3. `request_cancel()` — set cancel_requested flag on running task
4. `find_cancellable_tasks()` — find running tasks that have exceeded timeout
5. `fail_task_permanent()` — mark task FAILED with no retry (max retries exceeded)
6. `force_cancel_and_schedule_retry()` — atomic cancel + retry in single transaction (W1 fix)
7. `find_orphaned_cancelled_tasks()` — find CANCELLED tasks without retry child (S3 fix)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Enhance claim_pending_task() | Add `next_retry_at IS NULL OR next_retry_at <= :now` condition. <!-- FIX: W2 --> Use datetime objects, not isoformat() strings | `daemon/repositories/task/repository.py` |
| 2 | Add schedule_retry() method | Create new Task with retry_count+1, calculate backoff, mark parent CANCELLED + set `retry_scheduled=True` — all in one transaction. <!-- FIX: C1 --> Use correct column name `task_type`. <!-- FIX: S1 --> Atomic retry_scheduled guard | `daemon/repositories/task/repository.py` |
| 3 | Add request_cancel() method | Atomic UPDATE via `engine.begin()`: set cancel_requested=True, cancel_requested_at=now WHERE id=:task_id AND status='running' AND retry_scheduled=0 | `daemon/repositories/task/repository.py` |
| 4 | Add find_cancellable_tasks() | Find RUNNING tasks with cancel_requested=False that have exceeded timeout threshold. <!-- FIX: W3 --> Use `engine.begin()` | `daemon/repositories/task/repository.py` |
| 5 | Add cancel_task() method | <!-- FIX: W3 --> Use `engine.begin()` not Session for all mutations. Set retry_scheduled guard | `daemon/repositories/task/repository.py` |
| 6 | Add force_cancel_and_schedule_retry() | <!-- FIX: W1 --> Single-transaction cancel + retry to prevent orphaned CANCELLED tasks | `daemon/repositories/task/repository.py` |
| 7 | Add find_orphaned_cancelled_tasks() | <!-- FIX: S3 --> Find CANCELLED tasks with retry_scheduled=False (crash between cancel and retry) | `daemon/repositories/task/repository.py` |
| 8 | Add get_retry_chain() method | Get all tasks in a retry chain (same instance_id + message_id) for debugging/logging | `daemon/repositories/task/repository.py` |
| 9 | Write comprehensive unit tests | Test all new methods with in-memory SQLite, cover edge cases including double-retry guard | `tests/message_queue_redesign/test_task_repository.py` |

## Key Files

- `daemon/repositories/task/repository.py` — TaskRepository class (360 lines)
- `daemon/repositories/task/models.py` — Task model with new fields from Phase 1
- `tests/message_queue_redesign/test_task_repository.py` — Existing tests (336 lines)
- `tests/message_queue_redesign/conftest.py` — Test fixtures

## Detailed Implementation

### 1. Enhanced claim_pending_task()

Current:
```python
stmt = text("""
    UPDATE task
    SET status = :status_running, worker_id = :worker_id, started_at = :started_at
    WHERE id = (
        SELECT id FROM task
        WHERE status = :status_pending
        ORDER BY created_at ASC
        LIMIT 1
    )
    RETURNING *
""")
```

Enhanced:
```python
def claim_pending_task(self, worker_id: str) -> Task | None:
    """Atomically claim the next eligible pending task.
    
    Only claims tasks that are ready (no backoff delay remaining).
    """
    now = datetime.now(timezone.utc)
    
    with self.engine.begin() as conn:
        stmt = text("""
            UPDATE task
            SET status = :status_running,
                worker_id = :worker_id,
                started_at = :started_at
            WHERE id = (
                SELECT id FROM task
                WHERE status = :status_pending
                AND (next_retry_at IS NULL OR next_retry_at <= :now)
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *
        """)
        row = conn.execute(stmt, {
            "status_running": TaskStatus.RUNNING.value,
            "worker_id": worker_id,
            "started_at": now,           # <!-- FIX: W2 — pass datetime object, not isoformat() string -->
            "status_pending": TaskStatus.PENDING.value,
            "now": now,                   # <!-- FIX: W2 — pass datetime object, not isoformat() string -->
        }).fetchone()
        
        if row is None:
            return None
        
        return self._row_to_task(row)
```

### 2. schedule_retry() Method

<!-- FIX: C1 — Column name is `task_type` (not `type`). SQLModel metadata.create_all() creates the table
     before file migrations run, so the actual DB column matches the Python field name `task_type`.
     The SQL migration file uses `type` but is dead code — the table already exists when it runs. -->
<!-- FIX: S1 — Sets retry_scheduled=True on parent in the same transaction, preventing double-retry -->
<!-- FIX: W2 — Uses datetime objects consistently, not isoformat() strings -->

```python
def schedule_retry(
    self,
    task_id: int,
    max_retries: int,
    backoff_base: int = 60,
    backoff_max: int = 3600,
) -> Task | None:
    """Create a new Task for retry with exponential backoff.
    
    Marks the parent task as CANCELLED with retry_scheduled=True and creates
    a new PENDING task with incremented retry_count and calculated next_retry_at.
    
    All operations are in a single transaction — crash-safe.
    
    Returns the new retry task, or None if max retries exceeded or parent
    already has retry_scheduled=True (double-retry guard).
    """
    with self.engine.begin() as conn:
        # Get parent task
        parent_row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()
        
        if parent_row is None:
            return None
        
        parent = dict(parent_row._mapping)
        current_retry_count = parent.get("retry_count", 0)
        
        # <!-- FIX: S1 — Check retry_scheduled guard to prevent double-retry -->
        if parent.get("retry_scheduled", 0):
            return None  # Retry already scheduled by another process
        
        # Check retry limit
        if current_retry_count >= max_retries:
            return None  # Max retries exceeded
        
        new_retry_count = current_retry_count + 1
        
        # Calculate exponential backoff
        delay_seconds = min(
            backoff_base * (2 ** current_retry_count),
            backoff_max
        )
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        now = datetime.now(timezone.utc)
        
        # Mark parent as CANCELLED and set retry_scheduled guard
        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    cancel_requested = 1,
                    cancel_requested_at = :cancelled_at,
                    completed_at = :completed_at,
                    retry_scheduled = 1
                WHERE id = :id
            """),
            {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "cancelled_at": now,
                "completed_at": now,
                "id": task_id,
            }
        )
        
        # Create new retry task
        <!-- FIX: C1 — Use `task_type` column name, not `type` -->
        result = conn.execute(
            text("""
                INSERT INTO task (task_type, instance_id, message_id, status, 
                                  retry_count, next_retry_at, created_at)
                VALUES (:task_type, :instance_id, :message_id, :status_pending,
                        :retry_count, :next_retry_at, :created_at)
                RETURNING *
            """),
            {
                "task_type": parent["task_type"],   # <!-- FIX: C1 — column is `task_type`, not `type` -->
                "instance_id": parent["instance_id"],
                "message_id": parent.get("message_id"),
                "status_pending": TaskStatus.PENDING.value,
                "retry_count": new_retry_count,
                "next_retry_at": next_retry_at,     # <!-- FIX: W2 — datetime object, not isoformat() -->
                "created_at": now,                   # <!-- FIX: W2 — datetime object, not isoformat() -->
            }
        ).fetchone()
        
        return self._row_to_task(result)
```

### 3. request_cancel() Method

```python
def request_cancel(self, task_id: int) -> bool:
    """Atomically request cancellation of a running task.
    
    Sets cancel_requested=True on the task. The worker thread
    checks this flag periodically and will stop gracefully.
    
    Returns True if the flag was set, False if task not found,
    already cancelled, or retry already scheduled.
    """
    now = datetime.now(timezone.utc)
    
    with self.engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE task
                SET cancel_requested = 1,
                    cancel_requested_at = :cancelled_at
                WHERE id = :id
                AND status = :status_running
                AND cancel_requested = 0
                AND retry_scheduled = 0
            """),
            {
                "cancelled_at": now,                    # <!-- FIX: W2 — datetime object -->
                "id": task_id,
                "status_running": TaskStatus.RUNNING.value,
            }
        )
        return result.rowcount > 0
```

### 4. find_cancellable_tasks() Method

```python
def find_cancellable_tasks(self, threshold_minutes: int) -> list[Task]:
    """Find running tasks that have exceeded the timeout threshold
    and haven't been marked for cancellation yet."""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    
    with self.engine.begin() as conn:     # <!-- FIX: W3 — use engine.begin() for consistency -->
        stmt = text("""
            SELECT * FROM task
            WHERE status = :status_running
            AND started_at < :threshold
            AND cancel_requested = 0
        """)
        rows = conn.execute(stmt, {
            "status_running": TaskStatus.RUNNING.value,
            "threshold": threshold,
        }).fetchall()
        return [self._row_to_task(row) for row in rows]
```

### 5. cancel_task() Method

<!-- FIX: W3 — Use engine.begin() not Session for all mutation methods.
     Session uses autocommit=False which won't participate in shared transactions.
     engine.begin() gives explicit transaction control and consistency with other methods. -->

```python
def cancel_task(self, task_id: int, reason: str = "") -> Task | None:
    """Directly cancel a task (mark as CANCELLED).
    
    Used by StaleTaskRecovery when worker doesn't respond to
    cancel_requested flag within grace period.
    """
    now = datetime.now(timezone.utc)
    
    with self.engine.begin() as conn:    # <!-- FIX: W3 — engine.begin() for transaction consistency -->
        # Check current status
        row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()
        
        if row is None:
            return None
        
        current = self._row_to_task(row)
        if current.status not in (TaskStatus.RUNNING.value, TaskStatus.PENDING.value):
            return None
        
        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    cancel_requested = 1,
                    cancel_requested_at = :cancelled_at,
                    completed_at = :completed_at,
                    error = :error
                WHERE id = :id
            """),
            {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "cancelled_at": now,
                "completed_at": now,
                "error": f"Task cancelled: {reason}",
                "id": task_id,
            }
        )
        
        # Re-fetch to return updated task
        updated_row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()
        return self._row_to_task(updated_row) if updated_row else None
```

### 6. get_retry_chain() Method

```python
def get_retry_chain(self, instance_id: str, message_id: str) -> list[Task]:
    """Get all tasks in a retry chain for debugging."""
    with self.engine.begin() as conn:     # <!-- FIX: W3 — use engine.begin() -->
        stmt = text("""
            SELECT * FROM task
            WHERE instance_id = :instance_id
            AND message_id = :message_id
            ORDER BY retry_count ASC
        """)
        rows = conn.execute(stmt, {
            "instance_id": instance_id,
            "message_id": message_id,
        }).fetchall()
        return [self._row_to_task(row) for row in rows]
```

### 7. force_cancel_and_schedule_retry() Method

<!-- FIX: W1 — Single-transaction cancel + retry prevents orphaned CANCELLED tasks.
     If crash occurs after cancel_task() but before schedule_retry(), the task is
     left as CANCELLED with retry_scheduled=False — detectable by find_orphaned_cancelled_tasks(). -->

```python
def force_cancel_and_schedule_retry(
    self,
    task_id: int,
    max_retries: int,
    reason: str,
    backoff_base: int = 60,
    backoff_max: int = 3600,
) -> Task | None:
    """Atomically cancel a task and schedule a retry in a single transaction.
    
    Combines cancel_task() + schedule_retry() to prevent the window where
    a crash would leave an orphaned CANCELLED task with no retry child.
    
    Returns the new retry task, or None if max retries exceeded.
    """
    now = datetime.now(timezone.utc)
    
    with self.engine.begin() as conn:
        # Get parent task
        parent_row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()
        
        if parent_row is None:
            return None
        
        parent = dict(parent_row._mapping)
        
        # Check guards
        if parent.get("retry_scheduled", 0):
            return None  # Already has retry scheduled
        
        current_retry_count = parent.get("retry_count", 0)
        if current_retry_count >= max_retries:
            return None  # Max retries exceeded
        
        new_retry_count = current_retry_count + 1
        
        # Calculate backoff
        delay_seconds = min(
            backoff_base * (2 ** current_retry_count),
            backoff_max
        )
        next_retry_at = now + timedelta(seconds=delay_seconds)
        
        # Force-cancel parent and set retry_scheduled guard
        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    cancel_requested = 1,
                    cancel_requested_at = :now,
                    completed_at = :now,
                    error = :error,
                    retry_scheduled = 1
                WHERE id = :id
            """),
            {
                "status_cancelled": TaskStatus.CANCELLED.value,
                "now": now,
                "error": f"Force cancelled: {reason}",
                "id": task_id,
            }
        )
        
        # Create retry child
        result = conn.execute(
            text("""
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, next_retry_at, created_at)
                VALUES (:task_type, :instance_id, :message_id, :status_pending,
                        :retry_count, :next_retry_at, :created_at)
                RETURNING *
            """),
            {
                "task_type": parent["task_type"],
                "instance_id": parent["instance_id"],
                "message_id": parent.get("message_id"),
                "status_pending": TaskStatus.PENDING.value,
                "retry_count": new_retry_count,
                "next_retry_at": next_retry_at,
                "created_at": now,
            }
        ).fetchone()
        
        return self._row_to_task(result)
```

### 8. find_orphaned_cancelled_tasks() Method

<!-- FIX: S3 — Detects tasks that were cancelled but never got a retry child.
     This covers the crash-between-cancel-and-retry gap, even with W1's
     force_cancel_and_schedule_retry() which minimizes but doesn't eliminate the window. -->

```python
def find_orphaned_cancelled_tasks(self) -> list[Task]:
    """Find CANCELLED tasks that never got a retry child.
    
    These are tasks where:
    - status = 'cancelled'
    - retry_scheduled = False (or the retry_scheduled flag was set but child doesn't exist)
    - retry_count < max_retries (retry should have been scheduled)
    
    Used by startup recovery to detect crash-before-retry scenarios.
    """
    with self.engine.begin() as conn:
        stmt = text("""
            SELECT t1.* FROM task t1
            WHERE t1.status = :status_cancelled
            AND t1.retry_scheduled = 0
            AND NOT EXISTS (
                SELECT 1 FROM task t2
                WHERE t2.instance_id = t1.instance_id
                AND t2.message_id = t1.message_id
                AND t2.retry_count > t1.retry_count
            )
        """)
        rows = conn.execute(stmt, {
            "status_cancelled": TaskStatus.CANCELLED.value,
        }).fetchall()
        return [self._row_to_task(row) for row in rows]
```

## Testing Strategy

### Unit Tests for Each Method

```python
# test_claim_with_retry_delay
def test_claim_respects_retry_delay(repository):
    """Tasks with future next_retry_at are not claimed."""
    task1 = repository.create("process_message", "inst-1", "msg-1")
    task1.next_retry_at = future_time  # Not yet ready
    task2 = repository.create("process_message", "inst-2", "msg-2")
    # task2 has no next_retry_at — should be claimed first
    claimed = repository.claim_pending_task("worker-1")
    assert claimed.id == task2.id

def test_claim_picks_up_delayed_task_when_ready(repository):
    """Tasks whose next_retry_at has passed are claimable."""
    task = repository.create("process_message", "inst-1", "msg-1")
    # Set next_retry_at to past
    repository.update_next_retry_at(task.id, past_time)
    claimed = repository.claim_pending_task("worker-1")
    assert claimed.id == task.id

# test_schedule_retry
def test_schedule_retry_creates_new_task(repository):
    task = repository.create("process_message", "inst-1", "msg-1")
    retry_task = repository.schedule_retry(task.id, max_retries=3)
    assert retry_task.retry_count == 1
    assert retry_task.next_retry_at > datetime.now(timezone.utc)
    assert retry_task.status == TaskStatus.PENDING.value
    # Parent should be CANCELLED
    parent = repository.get(task.id)
    assert parent.status == TaskStatus.CANCELLED.value

def test_schedule_retry_returns_none_when_max_exceeded(repository):
    task = repository.create("process_message", "inst-1", "msg-1")
    task.retry_count = 3
    repository.update_retry_count(task.id, 3)
    result = repository.schedule_retry(task.id, max_retries=3)
    assert result is None

def test_schedule_retry_exponential_backoff(repository):
    """Verify backoff: 60s, 120s, 240s, etc."""
    ...

# test_request_cancel
def test_request_cancel_sets_flag(repository):
    task = repository.create("process_message", "inst-1", "msg-1")
    repository.claim_pending_task("worker-1")  # Set to RUNNING
    result = repository.request_cancel(task.id)
    assert result is True
    updated = repository.get(task.id)
    assert updated.cancel_requested is True
    assert updated.cancel_requested_at is not None

def test_request_cancel_idempotent(repository):
    """Second request_cancel returns False (already cancelled)."""
    ...

# test_find_cancellable_tasks
def test_finds_tasks_past_threshold(repository):
    """Running tasks past threshold with cancel_requested=False are found."""
    ...

# test_cancel_task
def test_cancel_running_task(repository):
    """Direct cancellation marks task as CANCELLED."""
    ...
```

## Constraints

- All mutation methods must use `engine.begin()` (transactions) for atomicity <!-- FIX: W3 -->
- claim_pending_task must remain a single UPDATE-RETURNING statement (no read-then-write)
- schedule_retry must be a single transaction (parent CANCELLED + retry_scheduled=True + child CREATED) <!-- FIX: S1 -->
- All datetime values passed as datetime objects, not isoformat() strings <!-- FIX: W2 -->
- Column name is `task_type` (not `type`) — SQLModel creates the table, not the SQL migration <!-- FIX: C1 -->
- Backward compatible: existing tests must pass (claim_pending_task still works for tasks without next_retry_at)
- SQLite-safe: no unsupported operations

## Deliverables

- [ ] Enhanced claim_pending_task() with retry-delay awareness (datetime objects)
- [ ] schedule_retry() with exponential backoff, correct column names (C1), retry_scheduled guard (S1)
- [ ] request_cancel() with atomic flag setting, retry_scheduled guard
- [ ] find_cancellable_tasks() via engine.begin()
- [ ] cancel_task() via engine.begin() (W3 fix)
- [ ] force_cancel_and_schedule_retry() — single-transaction cancel+retry (W1 fix)
- [ ] find_orphaned_cancelled_tasks() — detect crash-before-retry (S3 fix)
- [ ] get_retry_chain() via engine.begin()
- [ ] All existing repository tests still pass
- [ ] New tests for all new methods including double-retry guard, backoff calculation
