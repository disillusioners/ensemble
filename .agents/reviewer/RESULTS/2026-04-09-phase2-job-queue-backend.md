# Phase 2 Review Summary — Backend Core Services for Named Per-Project Job Queues

**Review**: 🔴 **Needs Work — 5 Critical Issues**
**Sessions Used**: review-concurrency, review-services, review-integration
**Files**: 7 files, +1616/-428 lines (commit cce7976)

## 🔴 Critical Issues

### 1. [Concurrency] TOCTOU Race in JobProcessor → Phantom Lock Acquisition
- **File**: `job_processor.py:138-148` + `job_queue_service.py:501-579`
- **Severity**: 🔴 Critical
- **Description**: The processor lists pending jobs, then calls `acquire_queue_lock()` + `start_job_atomic()`. Between listing and locking, another processor can select the same job. Both acquire locks, but only one `start_job_atomic()` succeeds — leaving the other with a **phantom lock** on a job it never started, permanently consuming a concurrency slot.
- **Scenario**: Worker A and B both list pending → get same `job_X`. Both acquire lock. A's start succeeds. B's throws ValueError, releases lock → orphaned lock remains.
- **Fix**: Move `start_job_atomic()` to happen BEFORE lock acquisition, so the DB is the source of truth. Or use `SELECT ... FOR UPDATE`.

### 2. [Data Loss] Migration Wipes ALL Existing Jobs
- **File**: `daemon/migrations/versions/20260409_000001_add_job_queues_table.sql:10`
- **Severity**: 🔴 Critical
- **Description**: STEP 1 is `DELETE FROM job_queue_items;` — unconditionally deletes ALL jobs. Production jobs are permanently destroyed at migration time.
- **Fix**: Remove the DELETE. `ALTER TABLE ADD COLUMN` is safe for existing rows.

### 3. [Dead Letter] Jobs with `project_id` but Missing System Queue Are Never Processed
- **File**: `job_queue_service.py:97-110`
- **Severity**: 🔴 Critical
- **Description**: When `project_id` is set but `system_fifo_queue` doesn't exist, `resolved_queue_id` becomes `None`. `JobProcessor` only iterates queues — such jobs are **never picked up**.
- **Fix**: Fail the submission with a clear error, or use `get_next_pending_job()` as a catch-all fallback.

### 4. [IDOR Bypass] `enqueue()` Uses Mismatched `queue_id` After Warning
- **File**: `job_queue_service.py:119-124`
- **Severity**: 🔴 Critical (Security)
- **Description**: When `queue.project_id != project_id`, code logs warning but **continues to use the mismatched queue_id** (line 124: "Still use the queue_id as specified"). User can bypass project boundaries.
- **Fix**: Raise `ValueError` instead of continuing.

### 5. [Regression] Project-less Jobs Are Orphaned
- **File**: `job_processor.py:117-143`
- **Severity**: 🔴 Critical (Regression)
- **Description**: `JobProcessor` only iterates projects → queues. Jobs with `project_id=NULL` and `queue_id=NULL` are invisible. `get_next_pending_job()` exists but is **never called**. Phase 1 allowed project-less jobs — these are now orphaned.
- **Fix**: Add fallback in `_process_next_job()` that calls `get_next_pending_job()`.

## 🟡 Warnings

### 6. `complete_job_sync()` Permanent Lock Leak (`job_queue_service.py:628-680`)
### 7. `asyncio.Lock` Not Thread-Safe for Mixed Sync/Async (`job_lock_manager.py:69-111` vs `255-295`)
### 8. `cancel_job()` Double Lock Release (`job_queue_service.py:208-227`)
### 9. `delete_queue()` TOCTOU Window — Orphaned PROCESSING Job (`job_queue_mgmt_service.py:294-320`)
### 10. `auto_provision_system_queues()` TOCTOU (`job_queue_mgmt_service.py:48-99`)
### 11. `JobQueueMgmtService` Not Accessible from API Layer (`api.py:186-189`)
### 12. `reassign_pending_jobs_atomic()` FK Violation if `to_queue_id` Missing (`queue_repository.py:244-276`)

## 🟢 Suggestions

### 13. `retry_job()` Loses `queue_id` (`job_queue_service.py:254-261`)
### 14. `get_next_pending_job()` Is Dead Code (`job_queue_service.py:489-499`)
### 15. `to_dict()` Field Name Mismatch (`models.py:159` — `metadata` vs `job_metadata`)
### 16. `ValueError` Lacks HTTP Status Code Contract (`job_queue_mgmt_service.py:282-300`)

**Total: 16 findings (5 critical, 7 warnings, 4 suggestions)**
