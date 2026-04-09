# Lesson: SQLite Atomic Claim Race Condition Fix

**Date:** 2026-04-09
**File:** `daemon/repositories/task/repository.py:129`
**Commit:** `8f8b34e502ab3bd990682d5bf78dedf3d55bd98b`

## What Was the Issue

The `claim_pending_task()` method in `TaskRepository` had a critical race condition. When using `SQLModelSession(self.engine)` with QueuePool (multiple connections), concurrent claims each saw different pending tasks and ALL succeeded — meaning 5 threads could claim the same task simultaneously.

## Root Cause

- `SQLModelSession(self.engine)` creates **implicit transactions per session**
- With QueuePool, each thread got its own connection
- Each connection could see a different pending task (due to isolation)
- The `UPDATE ... WHERE id = (SELECT ... LIMIT 1)` was not atomic at the connection level

## How to Reproduce

```python
import threading

def try_claim(thread_id):
    repo = TaskRepository()
    task = repo.claim_pending_task("q1", f"worker-{thread_id}")
    print(f"thread-{thread_id}: claimed {task.id if task else None}")

threads = [threading.Thread(target=try_claim, args=(i,)) for i in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
# Without fix: Multiple threads claim the same task
```

## The Fix

```python
# Before (broken):
with SQLModelSession(self.engine) as db_session:
    result = db_session.exec(stmt)
    row = result.fetchone()

# After (correct):
with self.engine.begin() as conn:
    result = conn.execute(stmt)
    row = result.fetchone()
```

Using `engine.begin()` ensures a single connection-level transaction, making the `UPDATE ... WHERE ... LIMIT 1` truly atomic. SQLite's transaction isolation ensures only one thread can execute the UPDATE at a time.

## Key Takeaway

When implementing concurrent task claiming in SQLite:
- Always use `engine.begin()` for atomic operations
- Never rely on `SQLModelSession` for concurrency safety
- The `UPDATE ... WHERE ... ORDER BY ... LIMIT 1` pattern must be wrapped in a single transaction
