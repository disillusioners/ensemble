# Lessons Learned

## 2026-03-22: Scheduler Frontend E2E Testing (Completion)

### Issues Found and Fixed

#### Issue 1: Backend API Response Format Mismatch
- **Problem**: The `/api/schedules` endpoint returned `{ sources: [...] }` but the frontend expected `{ schedules: [...] }`
- **Impact**: Schedule cards wouldn't display because data was in wrong property
- **Fix**: 
  - Added `ScheduleInfo` and `ScheduleListResponse` models to `daemon/models.py`
  - Updated `/api/schedules` endpoint to return `ScheduleListResponse` with `schedules` array
- **Commit**: 353b530

#### Issue 2: Missing Click Handler for Detail Drawer
- **Problem**: Schedule cards had no click handler to open the detail drawer
- **Impact**: Clicking on schedule cards did nothing
- **Fix**:
  - Added `view = output<Schedule>()` to `schedule-card.component.ts`
  - Added `onView()` method to emit the view event
  - Added `(click)="onView()"` to card template
  - Added `(view)="onViewScheduleDetails($event)"` binding in schedules template
- **Commit**: 353b530

### Test Results
- All 7/7 scenarios passed after fixes
- Browser automation successfully created schedules through UI
- Schedule cards display correctly with schedule data
- Detail drawer opens and shows schedule information

### Key Learnings
1. **API Contract Testing**: Frontend and backend must agree on response format
2. **Event Wiring**: Angular outputs must be wired to parent component handlers
3. **E2E Testing Value**: Browser automation catches integration issues that unit tests miss
4. **Quick Fixes Work**: Both issues were < 20 lines and fixed in same session

---

## 2026-03-22: Scheduler Frontend E2E Testing (Initial)

### Test Environment Setup
- **Frontend**: Angular dev server runs on port 4200
- **Browser Automation**: Playwright works well for Angular apps
- **Screenshots**: Essential for debugging and evidence collection

### Key Findings

#### ✅ What Worked Well
1. **Angular Components**: All schedule-related components compile and render correctly
2. **Navigation**: Routing works smoothly between pages
3. **Dialog System**: Create Schedule dialog opens with all required form fields
4. **Filters**: Status and type filters are interactive and functional
5. **No Console Errors**: Clean browser console during testing

#### ⚠️ Areas for Improvement
1. **Empty State Testing**: Need to test with actual schedule data to verify card and drawer components
2. **Backend Integration**: Should test with backend API running for full E2E flow
3. **Form Validation**: Should test form validation in Create Schedule dialog
4. **CRUD Operations**: Should test create, update, delete operations

### Test Coverage Gaps
- Schedule card rendering (requires data)
- Schedule detail drawer (requires data)
- Schedule CRUD operations
- Form validation
- Error handling
- Backend API integration

### Recommendations for Future Testing
1. **Mock Data Strategy**: Create test schedules via API before running UI tests
2. **API Mocking**: Consider mocking backend API responses for isolated frontend testing
3. **Test Data Management**: Implement test data cleanup between test runs
4. **Accessibility Testing**: Add accessibility audits to browser automation tests
5. **Performance Testing**: Measure page load times and interaction delays

### Quick Fixes Applied
None - all components functioned correctly during this test session.

### Screenshots Location
All test screenshots saved to: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/test-results/`

---

## Testing Best Practices

### Browser Automation with Playwright
- Take screenshots at each test step for evidence
- Check console for errors during navigation
- Test empty states AND populated states
- Verify all interactive elements (buttons, filters, links)
- Test navigation between pages

### Angular Testing
- Verify components compile without errors
- Test component templates render correctly
- Test form components with all input types
- Test dialog/modal opening and closing
- Test routing and navigation

### ENSURE.md Validation
- Validate critical requirements first
- Document all test results with evidence
- Report skipped tests with clear reasons
- Provide recommendations for improvement


---

## 2026-03-22: Job Queue Backend API Mock Tests

### Testing Approach
- **Method**: FastAPI TestClient with in-memory SQLite database
- **Coverage**: 48 comprehensive tests covering all 6 API endpoints
- **Execution Time**: 1.37 seconds (very fast)
- **Result**: 100% pass rate (48/48 tests passed)

### Test Categories Implemented
1. **Job Submission** (12 tests) - POST /api/jobs
2. **Job Retrieval** (3 tests) - GET /api/jobs/{id}
3. **Job Listing** (7 tests) - GET /api/jobs
4. **Job Cancellation** (5 tests) - DELETE /api/jobs/{id}
5. **Job Retry** (4 tests) - POST /api/jobs/{id}/retry
6. **Job Events** (3 tests) - GET /api/jobs/{id}/events (SSE)
7. **Edge Cases** (11 tests) - Unicode, concurrency, boundaries
8. **Performance** (2 tests) - Large queues, rapid submissions

### Issues Found
**None** - All tests passed on first run. No bugs discovered.

### Warnings Observed
- **242 deprecation warnings** about `datetime.utcnow()` usage
- **Affected files**: 
  - `daemon/repositories/job_queue/models.py:66`
  - `daemon/repositories/job_queue/repository.py` (multiple lines)
  - `daemon/services/job_queue_service.py:160`
- **Recommendation**: Update to `datetime.now(datetime.UTC)` in future refactor
- **Impact**: Low (deprecation warning only, not breaking)

### Key Findings

#### ✅ What Works Well
1. **API Design**: Clean REST API with proper status codes (200, 202, 404, 409, 422)
2. **State Machine**: Job state transitions enforced correctly (pending → processing → completed/failed)
3. **Priority Handling**: Priority 1-10 validation works, ordering maintained
4. **Concurrent Operations**: 20+ concurrent enqueues handled without race conditions
5. **Error Handling**: Proper HTTP status codes for all error scenarios
6. **SSE Support**: Server-Sent Events endpoint works correctly
7. **Unicode Support**: Unicode and special characters handled correctly
8. **Pagination**: Limit/offset pagination works correctly
9. **Metadata**: Custom metadata preserved across job lifecycle

#### 🔍 Edge Cases Tested
- Priority boundaries (0, 11, -1)
- Empty payloads
- Missing required fields
- Non-existent job IDs
- Invalid state transitions (cancel completed, retry pending)
- Concurrent submissions to same project
- Different projects running in parallel
- Unicode in all text fields
- Null bytes in messages
- Very long agent directory paths
- Large payloads (>1KB)
- Empty/whitespace-only messages
- Duplicate job ID prevention

#### 🎯 Performance Observations
- 50 rapid sequential submissions: Fast
- 100 jobs in queue listing: Fast
- 20 concurrent enqueues: No race conditions

### Test Architecture Insights
1. **In-Memory SQLite**: Perfect for unit/API tests (fast, isolated)
2. **TestClient**: FastAPI TestClient is excellent for API testing
3. **Fixture Design**: Proper fixture isolation ensures test independence
4. **Async Support**: pytest-asyncio works well for concurrent tests

### Recommendations

#### Immediate
1. ✅ Commit test file to repository
2. ⚠️ Add to CI/CD pipeline for automated testing

#### Future Improvements
1. Add SSE event payload validation tests
2. Add job timeout handling tests
3. Add database connection failure tests
4. Add authentication/authorization tests
5. Add rate limiting tests
6. Add performance benchmarks with timing assertions
7. Fix datetime.utcnow() deprecation warnings

### Quick Fixes Applied
**None** - All tests passed without code changes.

### Test File Details
- **File**: `tests/mock_test_job_queue_api.py`
- **Lines**: 1,027
- **Classes**: 8 test classes
- **Tests**: 48 test methods
- **Fixtures**: 6 test fixtures

### Key Learnings
1. **Comprehensive Testing**: Testing all endpoints and edge cases reveals robust implementation
2. **Fast Execution**: In-memory database makes tests run in <2 seconds
3. **Mock vs Real**: TestClient + in-memory DB is perfect middle ground between unit and integration tests
4. **Edge Cases Matter**: Unicode, concurrency, and boundary tests often reveal hidden bugs
5. **Warning Monitoring**: Deprecation warnings should be tracked for future maintenance
6. **No Bugs Found**: Well-designed implementation with good test coverage from the start

