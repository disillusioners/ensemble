# Phase 3: Backend — Testing

## Objective
Add comprehensive tests for the job completion callback mechanism (Phase 1), the JobProcessor (currently 0% coverage), and the API route layer with schema changes (Phase 2).

## Coupling
- **Depends on**: Phase 1 (completion callback), Phase 2 (schema changes)
- **Coupling type**: tight with Phase 1 (tests the code written there), loose with Phase 2 (tests the API contract)
- **Shared files with other phases**: Tests import from all Phase 1 and Phase 2 files
- **Why tight**: Tests directly verify the `_complete_job_for_instance()` helper, `get_job_by_instance()`, and the integration points added in Phase 1

## Context

### Current Test Coverage
| Component | Coverage | Gap |
|-----------|----------|-----|
| `JobLockManager` | ~100% | None |
| `JobRepository` | ~95% | None significant |
| `JobQueueService` | ~70% | `retry_job()`, `update_job()`, `get_next_pending_job()`, `start_job()` |
| `JobProcessor` | **0%** | Entire module untested |
| API routes | **0%** | No HTTP layer tests |
| Manager job integration | **0%** | No tests for job completion from instance lifecycle |

### Test Files Structure
```
tests/
├── job_queue/
│   ├── test_job_lock_manager.py        ← exists (~100% coverage)
│   ├── test_job_repository.py          ← exists (~95% coverage)
│   ├── test_job_queue_service.py       ← exists (~70% coverage)
│   ├── test_job_processor.py           ← NEW (0% → target 80%+)
│   ├── test_job_api_routes.py          ← NEW
│   └── test_manager_job_integration.py ← NEW (completion callback tests)
└── integration/
    └── test_job_integration.py         ← NEW (end-to-end, stretch goal)
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Test `_complete_job_for_instance()` helper | Unit tests for the new helper: success, failure, no-job, no-service, already-terminal | `tests/job_queue/test_manager_job_integration.py` (new) |
| 2 | Test job completion from `_process_queue()` success | Mock instance execution success → verify `complete_job()` called with correct `result_summary` | `tests/job_queue/test_manager_job_integration.py` |
| 3 | Test job failure from `_process_queue()` max retries | Mock max retries exceeded → verify `complete_job(success=False)` called | `tests/job_queue/test_manager_job_integration.py` |
| 4 | Test job failure from `terminate_instance()` | Terminate instance with active job → verify job marked FAILED | `tests/job_queue/test_manager_job_integration.py` |
| 5 | Test `get_job_by_instance()` public API | New public method on JobQueueService — basic lookup and not-found cases | Update `tests/job_queue/test_job_queue_service.py` |
| 6 | Test `complete_job()` with `result_summary` parameter | Verify the new parameter is passed through to repository | Update `tests/job_queue/test_job_queue_service.py` |
| 7 | Test `JobProcessor` polling and processing | Full poll → dequeue → spawn → enqueue flow; verify premature `trigger_next_job()` removed | `tests/job_queue/test_job_processor.py` (new) |
| 8 | Test API routes with new schema fields | Verify all 6 endpoints return `source`, `job_metadata`, `cancelled_at`; verify `create_job()` uses `_job_to_response()` | `tests/job_queue/test_job_api_routes.py` (new) |
| 9 | Test concurrent completion safety | Two paths (success + terminate) racing to complete same job → verify idempotency | `tests/job_queue/test_manager_job_integration.py` |

## Detailed Implementation

### Task 1-4: Manager Job Integration Tests

**File**: `tests/job_queue/test_manager_job_integration.py` (new)

**Setup**: Create a test fixture that:
- Creates an InstanceManager with mocked JobQueueService
- Creates a mock instance with an associated job
- Provides helpers to simulate instance completion scenarios

**Test cases**:
```python
class TestCompleteJobForInstance:
    """Tests for InstanceManager._complete_job_for_instance()"""
    
    async def test_success_marks_job_completed(self):
        """When instance succeeds, job should be COMPLETED with result_summary"""
    
    async def test_failure_marks_job_failed(self):
        """When instance fails, job should be FAILED with error message"""
    
    async def test_no_job_found_is_noop(self):
        """Instance with no associated job should not error"""
    
    async def test_no_service_wired_is_noop(self):
        """When _job_queue_service is None, should return silently"""
    
    async def test_already_terminal_state_is_noop(self):
        """Job already in COMPLETED/FAILED/CANCELLED should not be updated"""
    
    async def test_triggers_next_job_for_project(self):
        """After completing, should trigger next pending job for same project"""
    
    async def test_does_not_trigger_without_project(self):
        """Jobs without project_id should not attempt trigger_next_job"""
    
    async def test_uses_public_get_job_by_instance(self):
        """Verify helper calls public get_job_by_instance(), not _repository"""
        # Mock get_job_by_instance, verify it's called
        # Verify _repository.get_by_instance is NOT called directly

class TestProcessQueueJobCompletion:
    """Tests for job completion integration in _process_queue()"""
    
    async def test_message_success_completes_job(self):
        """Successful message processing → job COMPLETED with result_summary"""
    
    async def test_message_success_passes_result_content(self):
        """Result content (truncated) is passed as result_summary"""
    
    async def test_message_max_retries_fails_job(self):
        """Max retries exceeded → job FAILED with error message"""

class TestTerminateInstanceJobCompletion:
    """Tests for job completion on instance termination"""
    
    async def test_terminate_marks_processing_job_failed(self):
        """Terminating instance with PROCESSING job → job FAILED"""
    
    async def test_terminate_no_job_is_noop(self):
        """Terminating instance without job should not error"""
    
    async def test_terminate_completed_job_is_noop(self):
        """Terminating instance with already COMPLETED job → no update"""
```

### Task 5: Test `get_job_by_instance()`

**File**: Update `tests/job_queue/test_job_queue_service.py`

Add tests:
```python
async def test_get_job_by_instance_found(self):
    """Returns job when instance_id matches"""

async def test_get_job_by_instance_not_found(self):
    """Returns None when no job has this instance_id"""

async def test_get_job_by_instance_sync(self):
    """Sync version works correctly"""
```

### Task 6: Test `complete_job()` with `result_summary`

**File**: Update `tests/job_queue/test_job_queue_service.py`

Add tests:
```python
async def test_complete_job_with_custom_result_summary(self):
    """When result_summary is provided, it's stored (not hardcoded default)"""

async def test_complete_job_without_result_summary_uses_default(self):
    """When result_summary is None, uses default 'Job completed successfully'"""

async def test_complete_job_failure_ignores_result_summary(self):
    """result_summary is only used for success=True, not for failures"""
```

### Task 7: JobProcessor Tests

**File**: `tests/job_queue/test_job_processor.py` (new)

**Setup**: Mock `JobQueueService`, `InstanceManager`, `ProjectRepository`

**Test cases**:
```python
class TestJobProcessor:
    """Tests for JobProcessor background polling"""
    
    async def test_poll_picks_up_pending_job(self):
        """Poll should find and process pending jobs"""
    
    async def test_skips_paused_project(self):
        """Jobs for paused projects should be skipped"""
    
    async def test_spawn_failure_marks_job_failed(self):
        """If instance spawn fails, job should be marked FAILED"""
    
    async def test_enqueue_failure_marks_job_failed(self):
        """If message enqueue fails, job should be marked FAILED"""
    
    async def test_no_pending_jobs_is_noop(self):
        """Poll with no pending jobs should return without error"""
    
    async def test_does_not_call_trigger_next_job(self):
        """Verify premature trigger_next_job() is NOT called after enqueue"""
        # Critical: verify the removal from Phase 1 Task 6
```

### Task 8: API Route Tests

**File**: `tests/job_queue/test_job_api_routes.py` (new)

**Setup**: Use FastAPI `TestClient` with mocked `JobQueueService`

**Test cases**:
```python
class TestJobAPIRoutes:
    """Tests for /api/jobs endpoints"""
    
    async def test_create_job_returns_all_fields(self):
        """POST /api/jobs response includes source, metadata, cancelled_at"""
    
    async def test_create_job_uses_to_response_helper(self):
        """Verify create_job returns all fields (not just partial inline construction)"""
    
    async def test_get_job_returns_all_fields(self):
        """GET /api/jobs/{id} includes all new schema fields"""
    
    async def test_list_jobs_returns_all_fields(self):
        """GET /api/jobs includes source, metadata, cancelled_at for each job"""
    
    async def test_cancel_job_returns_cancelled_at(self):
        """DELETE /api/jobs/{id} returns cancelled_at timestamp"""
    
    async def test_retry_job_returns_source_and_metadata(self):
        """POST /api/jobs/{id}/retry preserves source and metadata in new job"""
    
    async def test_sse_stream_emits_status_updates(self):
        """GET /api/jobs/{id}/events emits events on status changes"""
```

### Task 9: Concurrency Safety Test

```python
async def test_concurrent_completion_is_idempotent(self):
    """Both _process_queue success and terminate_instance racing to complete
    the same job should not cause errors. First one wins, second is no-op."""
    # Create job in PROCESSING state
    # Use asyncio.gather() to simulate concurrent completion and termination
    # Verify: job ends in a terminal state (either COMPLETED or FAILED)
    # Verify: no exceptions raised
    # Verify: lock released exactly once
    # Verify: trigger_next_job called at most once
```

**Note on concurrency testing approach**: `asyncio.gather()` is useful here to simulate concurrent execution of the completion and termination paths. The idempotent guard (checking `job.status` before updating) should prevent double-completion.

## Key Files
- `tests/job_queue/test_manager_job_integration.py` — New: manager + job completion tests
- `tests/job_queue/test_job_processor.py` — New: processor polling tests
- `tests/job_queue/test_job_api_routes.py` — New: API route tests
- `tests/job_queue/test_job_queue_service.py` — Update: add `get_job_by_instance()` and `complete_job(result_summary=)` tests
- `tests/integration/test_job_integration.py` — New: end-to-end (stretch goal)

## Constraints
- Tests must not require running daemon or real LLM
- All external dependencies (InstanceManager, JobQueueService) must be mockable
- SQLite in-memory for test DB
- Follow existing test patterns from `test_job_lock_manager.py` and `test_job_repository.py`
- Use function/method names as primary references (line numbers are approximate)

## Deliverables
- [ ] `_complete_job_for_instance()` tested (success, failure, no-job, no-service, already-terminal paths)
- [ ] Job completion from `_process_queue()` tested (success and max-retries)
- [ ] Job completion from `terminate_instance()` tested
- [ ] `get_job_by_instance()` public method tested
- [ ] `complete_job(result_summary=)` parameter tested
- [ ] `JobProcessor` has 80%+ test coverage
- [ ] Premature `trigger_next_job()` removal verified in processor test
- [ ] All 6 API routes tested with new schema fields
- [ ] `create_job()` verified to return complete data (not partial inline)
- [ ] Concurrent completion safety test passes
