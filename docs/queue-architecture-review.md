# Message Queue & Task System Architecture Review

**Date:** 2026-04-11  
**Reviewers:** Engineering Council (council session)  
**Status:** Issues Identified

> **Historical snapshot (2026-04-11).** This review of queue gaps predates the CorrelationManager migration. Most gaps are now RESOLVED: the sync/async lock mismatch was fixed via `asyncio.to_thread` wrapping; polling overhead reduced via the `notify_work` condition-variable pattern; incomplete processors removed. Retained as a historical record. For the current architecture, see [`docs/architecture/message-processing-and-correlation.md`](architecture/message-processing-and-correlation.md).

---

## Executive Summary

The codebase contains three overlapping queue systems that evolved over time. The council identified **8 gaps** (5 original + 3 discovered), ranging from critical bugs to medium-priority design debt.

| Priority | Gap | Severity | Effort |
|----------|-----|----------|--------|
| 1 | Sync/Async Lock Mismatch | 🔴 CRITICAL | Medium (2-3 days) |
| 2 | Non-Durable Locks | 🟠 HIGH | Medium |
| 3 | No Auto-Trigger After `complete_job()` | 🟡 MEDIUM | Low (half day) |
| 4 | Missing Integration Tests | 🟠 HIGH | Medium |
| 5 | SQLite Concurrency | 🟡 MEDIUM | Low |
| 6 | Incomplete Processors | 🟡 MEDIUM | Low-Medium |
| 7 | JobProcessor Polling Overhead | 🟢 LOW | N/A |
| 8 | Processor Naming Collision | 🟢 LOW | N/A |

---

## 1. Gap Severity Matrix

> **Resolution Status:** Most gaps resolved post-migration. Still active: **StaleTaskRecovery timing** (now configurable).

| # | Gap | Severity | Root Cause | Production Impact |
|---|-----|----------|------------|-------------------|
| 1 | **Sync/Async Lock Mismatch** | 🔴 **CRITICAL** | Design debt — async-only lock manager called from sync `terminate_instance()` | Queue stalls on every instance termination |
| 2 | **Non-Durable Locks** | 🟠 **HIGH** | Architectural choice (in-memory) | Duplicate job execution after restart |
| 3 | **No Auto-Trigger After `complete_job()`** | 🟡 **MEDIUM** | API design gap — caller must manually chain | Queue stalls **only for direct `complete_job()` callers**; manager-orchestrated flows are protected by `_complete_job_for_instance()` chaining |
| 4 | **Missing Integration Tests** | 🟠 **HIGH** | Test infrastructure gap | Untested concurrent behavior in production |
| 5 | **SQLite Concurrency** | 🟡 **MEDIUM** | Platform limitation (known, managed) | Bottleneck under load; `pool_size=1` serializes everything |
| 6 | **Incomplete Processors** | 🟡 **MEDIUM** | Incomplete implementation | `send_report` and `cleanup` task types will crash |
| 7 | **JobProcessor Polling Overhead** | 🟢 **LOW** | Architectural choice | Unnecessary DB load during quiet periods |
| 8 | **Processor Naming Collision** | 🟢 **LOW** | Naming convention issue | Developer confusion only |

---

## 2. Gap Details

### 2.1 Sync/Async Lock Mismatch (CRITICAL)

**Location:** `daemon/services/job_lock_manager.py`, `daemon/services/job_queue_service.py`

**Problem:** `JobLockManager` uses `asyncio.Lock` internally, but `terminate_instance()` calls sync methods that cannot await async locks:

```python
# daemon/services/job_queue_service.py:830
def release_locks_by_instance_sync(self, instance_id: str) -> list[str]:
    logger.warning(
        f"release_locks_by_instance_sync called for instance {instance_id}. "
        "Lock release cannot be done synchronously."
    )
    return []  # Returns EMPTY — locks NOT released!

# daemon/services/job_queue_service.py:734
    def trigger_next_job_sync(
        self,
        project_id: str,
        queue_id: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Trigger the next pending job for a queue or project (synchronous version).
        
        NOTE: This method has limitations with the new async-only lock manager.
        For new code, prefer the async trigger_next_job() method.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        # TODO: This sync method cannot properly use the async-only lock manager.
        # Migrate all callers to async trigger_next_job()
        
        # Get next pending job
        if queue_id:
            pending = self._repository.list_pending_by_queue(queue_id)
        else:
            pending = self._repository.list_pending_by_project(project_id)
        
        next_job = pending[0] if pending else None
        if next_job is None:
            return None
        
        # Get the job
        job = self._repository.get(next_job.job_id)
        if job is None:
            return None
        
        # Check if job is still pending
        if job.status != JobStatus.PENDING.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # If job has queue_id, we can't properly acquire async lock in sync context
        if job.queue_id and job.project_id:
            logger.warning(
                f"trigger_next_job_sync called with queue_id for job {job.job_id}. "
                "Lock acquisition will not work properly. Use async trigger_next_job() instead."
            )
            # Still try to start job atomically
            try:
                return self._repository.start_job_atomic(next_job.job_id, instance_id)
            except ValueError:
                return None
        
        # If job has project_id but no queue_id, try backward-compatible locking
        if job.project_id:
            acquired = self._lock_manager.acquire_sync(
                project_id=job.project_id,
                job_id=next_job.job_id,
                instance_id=instance_id,
            )
            
            if not acquired:
                return None
            
            try:
                return self._repository.start_job(next_job.job_id, instance_id)
            except ValueError:
                self._lock_manager.release_sync(job.project_id, next_job.job_id)
                return None
        
        # No project_id - start immediately without locking
        try:
            return self._repository.start_job(next_job.job_id, instance_id)
        except ValueError:
            return None
```

**Impact:** Every instance termination silently fails to release locks and trigger queued jobs. Queue stalls require StaleTaskRecovery (15+ minute recovery window).

**Root Cause:** Design debt — `asyncio.Lock` cannot be acquired from sync context without event loop access.

---

### 2.2 Non-Durable Locks (HIGH)

**Location:** `daemon/services/job_lock_manager.py`

**Problem:** `JobLockManager` stores locks in-memory (`_queue_locks` dict). On restart, locks are lost.

**Impact:** System can start duplicate jobs for same project/queue after restart.

**Mitigation:** Relies on StaleTaskRecovery to eventually clean up orphaned jobs.

---

### 2.3 No Auto-Trigger After `complete_job()` (MEDIUM)

**Location:** `daemon/services/job_queue_service.py:604-649`

**Problem:** `complete_job()` marks job as done but does NOT trigger the next job in queue.

```python
# daemon/services/job_queue_service.py:604
async def complete_job(
    self,
    job_id: str,
    success: bool = True,
    error: Optional[str] = None,
    result_summary: Optional[str] = None,
) -> Optional[JobItem]:
    # ... marks job completed/failed and releases lock ...
    return updated_job
```

**Impact:** Queue stalls if any direct caller uses `complete_job()` without manually calling `trigger_next_job()` afterward.

**Mitigating Factor:** `manager.py` (line 522) already works around this: `_complete_job_for_instance()` explicitly calls `trigger_next_job()` after completion. This means manager-orchestrated workflows are protected — **only direct `complete_job()` API callers** are affected.

---

### 2.4 Missing Integration Tests (HIGH)

**Problem:** No end-to-end test covering the full pipeline:
```
API → JobQueueService → JobProcessor → WorkerPool → TaskProcessor → completion
```

**Current Coverage:** Only component-level tests exist.

---

### 2.5 SQLite Concurrency (MEDIUM)

**Location:** `tests/conftest.py`, various test files

**Problem:** SQLite doesn't support true concurrent writes. Tests skip concurrent operations:

```python
@pytest.mark.skip(reason="SQLite does not support true concurrent writes - known limitation")
async def test_concurrent_enqueue_different_projects(...):
```

**Workaround:** Tests use `pool_size=1` to serialize connections.

**Production Impact:** Single-instance deployment only. Under load, all queue operations serialize through single DB connection.

---

### 2.6 Incomplete Processors (MEDIUM)

**Location:** `daemon/services/task_processor.py`

**Problem:** Only `ProcessMessageProcessor` is implemented:

```python
# daemon/services/task_processor.py:217
class SendReportProcessor(BaseProcessor):
    async def process(self, task: "Task", cancellation_token=None) -> dict[str, Any]:
        raise NotImplementedError("SendReportProcessor not yet implemented")

# daemon/services/task_processor.py:250
class CleanupProcessor(BaseProcessor):
    async def process(self, task: "Task", cancellation_token=None) -> dict[str, Any]:
        raise NotImplementedError("CleanupProcessor not yet implemented")
```

**Impact:** `send_report` and `cleanup` task types will crash at runtime.

---

### 2.7 JobProcessor Polling Overhead (LOW)

**Location:** `daemon/services/job_processor.py`

**Problem:** `JobProcessor` polls the database every 2.0 seconds. During quiet periods, this creates unnecessary DB load.

**Note:** Acceptable trade-off for current scale. Event-driven alternatives would add complexity.

---

### 2.8 Processor Naming Collision (LOW)

**Location:** `daemon/services/task_processor.py`

**Problem:** `TaskProcessor` is both:
1. The orchestrator class (`TaskProcessor` with `claim_task()`)
2. The base class for type-specific processors (`ProcessMessageProcessor`, `SendReportProcessor`, etc.)

**Impact:** Developer confusion. Not a runtime bug.

---

## 3. Recommended Fixes

### Priority 1: Fix Sync/Async Lock Mismatch

**Approach:** Dual-lock strategy — use `threading.Lock` for in-memory state, async coordination for queue triggers.

```python
# daemon/services/job_lock_manager.py

import threading
from typing import Optional

class JobLockManager:
    def __init__(self, ...):
        # Thread-safe lock for state management
        self._state_lock = threading.Lock()
        self._queue_locks: dict[tuple[str, str], list[LockInfo]] = {}
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
    
    def release_locks_by_instance_sync(self, instance_id: str) -> list[str]:
        """Now actually works — lock state is thread-safe."""
        released_projects: list[str] = []
        with self._state_lock:
            keys_to_release = [
                key for key, locks in self._queue_locks.items()
                if any(l.instance_id == instance_id for l in locks)
            ]
            for key in keys_to_release:
                self._queue_locks.pop(key, [])
                released_projects.append(key[1])  # project_id
        
        # Schedule async trigger on event loop
        if self._event_loop and released_projects:
            for project_id in set(released_projects):
                self._event_loop.call_soon_threadsafe(
                    lambda pid=project_id: asyncio.ensure_future(
                        self._trigger_next_job_async(pid)
                    )
                )
        return released_projects
    
    def trigger_next_job_sync(self, project_id: str) -> bool:
        """Schedule trigger on running event loop."""
        if not self._event_loop:
            logger.warning("No event loop available for trigger_next_job_sync")
            return False
        self._event_loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.trigger_next_job(project_id))
        )
        return True
```

**Effort:** Medium (2-3 days)

---

### Priority 2: Auto-Trigger in `complete_job()`

**Approach:** Embed trigger in `complete_job()` for defensive design.

```python
# daemon/services/job_queue_service.py:604

async def complete_job(
    self,
    job_id: str,
    success: bool = True,
    error: Optional[str] = None,
    result_summary: Optional[str] = None,
) -> Optional[JobItem]:
    """Complete a job and trigger the next queued job for the project."""
    # ... existing completion logic (lines 623-649) ...
    
    project_id = job.project_id
    
    # Release locks
    if job.queue_id and job.project_id:
        await self._lock_manager.release_queue_lock(job.project_id, job.queue_id, job_id)
    elif job.project_id:
        await self._lock_manager.release(job.project_id, job_id)
    
    # Automatically trigger next job
    try:
        await self.trigger_next_job(project_id)
    except Exception as e:
        logger.warning(f"Failed to trigger next job for {project_id}: {e}")
    
    return updated_job
```

**Effort:** Low (half day)

---

### Priority 3: Implement Missing Processors + Integration Test

**SendReportProcessor:**

```python
class SendReportProcessor(BaseProcessor):
    async def process(self, task: "Task", cancellation_token=None) -> dict[str, Any]:
        """Send completion report to parent instance or message source."""
        parent_id = task.metadata.get("parent_instance_id")
        report_data = task.metadata.get("report_data", {})
        
        if parent_id and self._manager:
            await self._manager.enqueue_message(
                instance_id=parent_id,
                message=json.dumps({
                    "type": "child_complete",
                    "child_instance_id": task.instance_id,
                    "report": report_data,
                }),
                source="system",
            )
        
        return {"success": True, "report_sent": parent_id is not None}
```

**CleanupProcessor:**

```python
class CleanupProcessor(BaseProcessor):
    async def process(self, task: "Task", cancellation_token=None) -> dict[str, Any]:
        """Clean up instance resources after completion."""
        instance_id = task.instance_id
        
        if self._manager and hasattr(self._manager, '_cleanup_instance_resources'):
            await self._manager._cleanup_instance_resources(instance_id)
        
        return {"success": True, "instance_id": instance_id}
```

**Integration Test:** (see `tests/job_queue/test_task_queue_integration.py::test_complete_end_to_end_scenario`)

# Proposed integration test (not yet implemented):

```python
# tests/job_queue/test_task_queue_integration.py:953

@pytest.mark.asyncio
async def test_full_job_lifecycle(tmp_path):
    """End-to-end: enqueue → process → complete → next job triggers."""
    db_file = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_file}", pool_size=1)
    SQLModel.metadata.create_all(engine)
    
    service = JobQueueService(engine=engine)
    await service.initialize()
    
    # Enqueue two jobs for same project
    job1 = await service.enqueue_job(project_id="proj-1", ...)
    job2 = await service.enqueue_job(project_id="proj-1", ...)
    
    assert job1.status == "PROCESSING"
    assert job2.status == "QUEUED"
    
    # Complete job1 → should auto-trigger job2
    await service.complete_job(job1.id)
    
    job2_refreshed = await service.get_job(job2.id)
    assert job2_refreshed.status == "PROCESSING"
```

**Effort:** Low-Medium (1-2 days)

---

## 4. Open Questions

| Question | Impact | Recommendation |
|----------|--------|----------------|
| WorkerPool vs JobProcessor overlap? | Architecture clarity | Clarify in design doc |
| SQLite WAL mode enabled? | ✅ RESOLVED | WAL mode enabled via `PRAGMA journal_mode=WAL` in `daemon/repositories/factory.py:89` |
| StaleTaskRecovery timing configurable? | SLA implications | Make interval configurable |
| PostgreSQL for production? | Scalability | Consider for multi-instance |

---

## 5. Files Reference

### Core Queue Files
- `daemon/queue.py` — Legacy in-memory queue (deprecated)
- `daemon/manager.py` — Main orchestrator, `enqueue_message()`
- `daemon/services/job_queue_service.py` — Job queue operations
- `daemon/services/job_processor.py` — Async job polling
- `daemon/services/job_lock_manager.py` — In-memory locking
- `daemon/services/task_processor.py` — Task routing
- `daemon/services/worker_pool.py` — Thread-based workers
- `daemon/services/event_bus.py` — Hybrid DB + asyncio streaming

### Repositories
- `daemon/repositories/job_queue/models.py` — SQLModel tables
- `daemon/repositories/job_queue/repository.py` — Job CRUD
- `daemon/repositories/task/models.py` — Task table
- `daemon/repositories/task/repository.py` — Atomic claim

### Tests
- `tests/job_queue/test_task_queue_integration.py`
- `tests/job_queue/test_task_queue_service.py`
- `tests/job_queue/test_job_processor.py`
- `tests/job_queue/test_task_lock_manager.py`
