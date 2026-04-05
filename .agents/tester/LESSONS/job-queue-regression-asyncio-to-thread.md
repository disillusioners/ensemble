# Job Queue Test Regression — asyncio.to_thread + SQLite in-memory

**Date:** 2026-04-06  
**Commit:** `5dcc584` — "fix: wrap sync DB operations in async context and document AsyncSqliteSaver threading"  
**Impact:** 59/60 job_queue tests broken (was 150/0 before this commit)

## Root Cause

The `asyncio.to_thread()` wrapper was added to `job_queue_service.py` to prevent event loop blocking. However, the test fixtures use `sqlite:///:memory:` which creates a **separate in-memory database per thread/connection**.

When `asyncio.to_thread()` runs DB operations in a thread pool, the operations get a different in-memory database than where `SQLModel.metadata.create_all(engine)` ran in the test fixture.

## Error
```
sqlite3.OperationalError: no such table: job_queue_items
```

## Fix Options
1. **Use `StaticPool`** in test fixtures with `check_same_thread=False` — shares single connection across threads
2. **Use file-based SQLite** in `/tmp` — same database accessible from any thread
3. **Use `creator` parameter** to return same connection — `connect_args={"check_same_thread": False}`

## Prevention
- When wrapping sync DB calls in `asyncio.to_thread`, verify test fixtures support cross-thread DB access
- SQLite in-memory is thread-local by default — always use `StaticPool` for testing async DB code
