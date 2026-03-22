# Mock Tests Inventory

## Browser Automation Test: Scheduler Frontend E2E

### Metadata
- **Created**: 2025-01-20
- **Script**: `frontend-e2e-scheduler.test.ts` (to be created)
- **Language**: TypeScript with Playwright
- **Status**: PLANNED

### Configuration
- **Timeout**: 60 seconds
- **Frontend Port**: 4200 (development server)
- **Backend Port**: 8000 (API server)
- **Cleanup**: Kill dev server processes after test

### What It Tests
- Navigation to `/schedules` route
- Schedule list/grid view rendering
- Create Schedule dialog functionality
- Schedule card display
- Schedule detail drawer
- Status filter functionality
- Navigation links

### Test Scenarios
1. **Navigate to Schedules Page**
   - Open browser to `http://localhost:4200/schedules`
   - Verify page loads with title/header
   - Verify schedule list container exists

2. **Verify Schedule List/Grid View**
   - Check for schedule cards or grid items
   - Verify layout renders correctly

3. **Test Create Schedule Button**
   - Click "Create Schedule" button
   - Verify dialog opens
   - Verify dialog contains form fields

4. **Verify Schedule Cards**
   - Check schedule cards display with mock data
   - Verify card layout and information

5. **Test Schedule Detail Drawer**
   - Click on a schedule card
   - Verify detail drawer opens
   - Verify drawer displays schedule details

6. **Test Status Filters**
   - Click status filter buttons (active, paused, etc.)
   - Verify schedule list updates based on filter

7. **Test Navigation Link**
   - Click schedules navigation link
   - Verify navigation to `/schedules`

### Success Criteria
- [ ] All scenarios pass
- [ ] Page load time < 5 seconds
- [ ] No browser console errors
- [ ] No process leaks (cleanup successful)

### Implementation Notes
- Requires frontend dev server running on port 4200
- May need to mock backend API responses
- Use Playwright for cross-browser compatibility
- Take screenshots on failure for debugging

### Last Run
- **Date**: 2026-03-22
- **Session**: ses_2e980fc65ffe7xYPaFW6xSuoaI
- **Result**: PASS (7/7 scenarios passed)
- **Quick Fixes Applied**:
  1. Backend API format fix: `/api/schedules` now returns `{ schedules: [...] }`
  2. Frontend click handler: Added click-to-view for schedule detail drawer
- **Commit**: 353b530
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-create-test.md`
- **Screenshots**: `/test-results/` directory (21 screenshots)

### Previous Run
- **Date**: 2026-03-22
- **Session**: ses_2e996ea1fffeXwXa1vEK7WyK2K
- **Result**: PARTIAL (5/7 scenarios passed, 2 skipped due to no data)
- **Quick Fixes**: None required
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-frontend-e2e.md`
- **Screenshots**: `/test-results/` directory (12 screenshots)



---

## Mock Test: Job Queue Backend API

### Metadata
- **Created**: 2026-03-22
- **Script**: `tests/mock_test_job_queue.py` (to be created)
- **Language**: Python with pytest
- **Status**: PLANNED

### Configuration
- **Timeout**: 300 seconds (5 minutes)
- **Backend Port**: 10080 (mock port > 10000)
- **Database**: Temporary SQLite (test.db)
- **Mock Dependencies**: None (SQLite is mocked via temp DB)

### What It Tests
- Job queue backend API endpoints
- CRUD operations for jobs
- Queue management (enqueue, dequeue, status)
- Job lifecycle (pending → processing → completed/failed)
- Priority handling
- Concurrent operations
- Error handling and edge cases

### Test Scenarios

#### 1. Job Submission (POST /api/jobs)
1. **Basic Submission** - Submit valid job, expect 200/202 response
2. **With Priority** - Submit job with priority 1-10, verify queue position
3. **Missing Required Fields** - Submit job without required fields, expect 422 error
4. **Invalid Priority** - Submit job with priority 0 or >10, expect 422 error
5. **Empty Payload** - Submit empty body, expect 422 error
6. **Agent Not Found** - Submit job with non-existent agent, expect 404 error

#### 2. Job Retrieval (GET /api/jobs/{id})
1. **Existing Job** - Get job by valid ID, expect 200 with job details
2. **Non-existent Job** - Get job by invalid ID, expect 404 error
3. **Invalid ID Format** - Get job with invalid ID format, expect 422 error

#### 3. Job Listing (GET /api/jobs)
1. **List All Jobs** - Get all jobs, expect 200 with list
2. **Filter by Status** - Filter by pending/processing/completed/failed
3. **Filter by Agent** - Filter by agent name
4. **Pagination** - Test limit and offset parameters
5. **Empty Result** - List with non-matching filters, expect empty list

#### 4. Job Cancellation (DELETE /api/jobs/{id})
1. **Cancel Pending Job** - Cancel pending job, expect 200
2. **Cancel Processing Job** - Cancel job being processed, expect 200
3. **Cancel Completed Job** - Try to cancel completed job, expect 409 error
4. **Cancel Non-existent Job** - Cancel with invalid ID, expect 404 error
5. **Cancel Already Cancelled** - Cancel already cancelled job, expect 409 error

#### 5. Job Retry (POST /api/jobs/{id}/retry)
1. **Retry Failed Job** - Retry failed job, expect 200, job reset to pending
2. **Retry Completed Job** - Try to retry completed job, expect 409 error
3. **Retry Pending Job** - Try to retry pending job, expect 409 error
4. **Retry Non-existent Job** - Retry with invalid ID, expect 404 error

#### 6. Job Events (GET /api/jobs/{id}/events)
1. **Subscribe to Events** - Subscribe to job events, expect SSE stream
2. **Event Types** - Verify queued, processing, completed, failed events
3. **Job Not Found** - Subscribe to non-existent job events, expect 404

#### 7. Edge Cases
1. **Concurrent Enqueues** - Submit 50+ jobs concurrently, verify all processed
2. **Priority Ordering** - Submit jobs with different priorities, verify order
3. **Duplicate ID Handling** - Verify no duplicate job IDs
4. **Special Characters** - Job names with unicode/special characters
5. **Large Payload** - Job with large payload (>1MB), verify handling

### API Endpoints Under Test
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/jobs` | Submit job |
| GET | `/api/jobs/{id}` | Get job status |
| GET | `/api/jobs` | List jobs with filters |
| DELETE | `/api/jobs/{id}` | Cancel job |
| POST | `/api/jobs/{id}/retry` | Retry failed job |
| GET | `/api/jobs/{id}/events` | SSE stream for updates |

### Success Criteria
- [ ] All job submission tests pass (6 scenarios)
- [ ] All job retrieval tests pass (3 scenarios)
- [ ] All job listing tests pass (5 scenarios)
- [ ] All job cancellation tests pass (5 scenarios)
- [ ] All job retry tests pass (4 scenarios)
- [ ] All job events tests pass (3 scenarios)
- [ ] All edge case tests pass (5 scenarios)
- [ ] Response times within expected thresholds
- [ ] No database corruption
- [ ] All processes cleaned up after test

### Implementation Notes
- Use FastAPI TestClient for API testing
- Use temp SQLite database for each test
- Mock job processor to avoid actual execution
- Implement proper cleanup between tests
- Test both success and error paths
- Verify response schemas match OpenAPI spec

### Quick Fix Criteria
- Fix invalid test assertions
- Fix mock setup issues
- Fix response parsing bugs
- Fix test data setup issues

---

## Mock Test: Job Queue Concurrency

### Metadata
- **Created**: 2026-03-22
- **Script**: `tests/mock_test_job_queue_concurrent.py` (to be created)
- **Language**: Python with pytest
- **Status**: PLANNED

### Configuration
- **Timeout**: 180 seconds
- **Backend Port**: 10081 (mock port > 10000)
- **Concurrency Level**: 100 concurrent requests
- **Database**: Temporary SQLite

### What It Tests
- Concurrent job submissions
- Race conditions in job processing
- Lock manager behavior under load
- Priority queue ordering under load
- Database consistency with concurrent writes

### Test Scenarios

#### 1. Concurrent Enqueue
1. **100 Concurrent Submissions** - Submit 100 jobs simultaneously
   - All should receive successful responses
   - All should be persisted in database
   - No race conditions or deadlocks

#### 2. Priority Ordering Under Load
1. **Interleaved Priority** - Submit jobs with mixed priorities concurrently
   - Higher priority jobs should be processed first
   - Order should be maintained despite concurrency

#### 3. Concurrent Status Updates
1. **Multiple Status Reads** - 50 concurrent GET requests for same job
   - All should return consistent state
   - No partial or corrupted responses

#### 4. Lock Contention
1. **Cancel While Processing** - Submit job and try to cancel immediately
   - Verify proper lock acquisition
   - Verify state transitions are atomic

#### 5. Database Consistency
1. **Concurrent Updates** - Multiple concurrent operations on same job
   - No data corruption
   - No duplicate records
   - Correct final state

### Success Criteria
- [ ] 100 concurrent submissions all succeed
- [ ] Priority ordering maintained under load
- [ ] No database deadlocks
- [ ] No race conditions detected
- [ ] All locks properly released
- [ ] Database integrity maintained

### Implementation Notes
- Use asyncio or threading for concurrency
- Implement proper timeout handling
- Monitor for deadlocks with timeout
- Log any race condition occurrences
- Verify database integrity after each test

