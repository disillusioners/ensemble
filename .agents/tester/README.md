# Testing Documentation - agents-ensemble

## Project Overview

**agents-ensemble** is a persistent multi-agent daemon built with LangGraph. It features:
- Angular frontend for UI (scheduler dashboard)
- Python backend with FastAPI
- SQLite database for persistence
- HTTP API for agent communication

## Test Types

### Unit Tests
- **Backend**: Python tests in `tests/` directory
- **Frontend**: Angular unit tests with Jasmine/Karma

### E2E Tests
- **Browser Automation**: Playwright/Puppeteer for frontend UI testing
- **API Tests**: Backend endpoint validation

### Mock Tests
- Mock external services (LLM APIs, databases)
- Test scheduler functionality without real dependencies

## Quick Start

### Run Unit Tests
```bash
# Backend
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
pytest tests/

# Job Queue API Tests (comprehensive mock tests)
pytest tests/mock_test_job_queue_api.py -v

# Frontend
cd frontend
npm test
```

### Run E2E Browser Tests
```bash
# Start frontend dev server
cd frontend
ng serve

# Run browser automation tests (in another terminal)
# See test-results/ for screenshots and evidence
```

## Test Results

### Latest Test Run: 2026-03-23 (Jobs Frontend UI Fixes)
- **Test Type**: Browser Automation (Playwright) - UI Fix Validation
- **Status**: ✅ ALL TESTS PASSED (2/2 fixes verified)
- **Report**: `.agents/tester/RESULTS/2026-03-23-jobs-ui-fix-browser-automation.md`
- **Screenshots**: `test-results/jobs-ui-fix/` (5 screenshots)
- **Execution Time**: ~5 minutes
- **Fixes Verified**:
  - ✅ Fix #1: Spinning icon for processing jobs - WORKING (sync icon with CSS animation)
  - ✅ Fix #2: Hover feedback on pause toggles - WORKING (color change CSS)
- **Quick Fixes Applied**: 0 (none needed)
- **Session ID**: ses_2e71b5385ffe7AfYoQoYYvQbkZ

### Previous Test Run: 2026-03-23 (View Session Fix Validation)
- **Test Type**: Browser Automation (Playwright) - Fix Verification
- **Status**: ✅ ALL TESTS PASSED (4/4 scenarios)
- **Report**: `.agents/tester/RESULTS/2026-03-23-view-session-fix-validation.md`
- **Screenshots**: `test-results/view-session-fix/` (5 screenshots)
- **Execution Time**: ~5 minutes
- **Commit Verified**: 8105626
- **Fix Verified**: ✅ View Session button now navigates correctly and shows error message instead of silent redirect
- **Session ID**: ses_2e8a24f75ffexAeqetxvCqJKEW

### Previous Test Run: 2026-03-23 (Bug Fix Validation - Browser Automation)
- **Test Type**: Browser Automation (Playwright) - Bug Fix Validation
- **Status**: ⚠️ PARTIAL PASS (2/3 fixes verified, 1 bug still present)
- **Report**: `.agents/tester/RESULTS/2026-03-23-bugfix-validation-browser-automation.md`
- **Screenshots**: `test-results/bugfix-validation/` (18 screenshots)
- **Execution Time**: ~40 minutes
- **Fixes Verified**:
  - ✅ Fix #1: Connection error debouncing and retry logic - WORKING
  - ❌ Fix #2: View Session button navigation - STILL BROKEN (navigates to `/` instead of `/sessions/:sessionId`)
  - ✅ Fix #3: Agent dropdown accessibility - FIXED (ARIA attributes added)
- **Quick Fixes Applied**: 1 (commit a6e67f7 - agent dropdown ARIA attributes)
- **Session ID**: ses_2e8da5c80ffeKgIMijHdxI6la3

### Previous Test Run: 2026-03-23 (Job Queue Frontend - Browser Automation)
- **Test Type**: Browser Automation (Playwright)
- **Status**: ⚠️ PARTIAL PASS (9/9 features work, 3 bugs found)
- **Report**: `.agents/tester/RESULTS/2026-03-23-job-queue-frontend-browser-automation.md`
- **Screenshots**: `test-results/job-queue/` (21 screenshots)
- **Execution Time**: ~15 minutes
- **Bugs Found**: 3 (1 High, 2 Medium)
  - HIGH: Connection error alert (backend connectivity)
  - MEDIUM: View Session button doesn't work
  - MEDIUM: Agent dropdown accessibility issues
- **Quick Fixes Applied**: 0 (bugs require investigation)
- **Session ID**: ses_2e90bd87cffeu3C8RD5kkk6IH4

### Previous Test Run: 2026-03-22 (Job Queue Backend API)
- **Test Type**: Mock Tests (Backend API with pytest)
- **Status**: ✅ READY (48/48 tests passed)
- **Report**: `.agents/tester/RESULTS/2026-03-22-job-queue-backend-api-mock-tests.md`
- **Test File**: `tests/mock_test_job_queue_api.py` (1,027 lines)
- **Execution Time**: 1.37 seconds
- **Quick Fixes Applied**: 0 (all tests passed on first run)
- **Coverage**: All 6 API endpoints + edge cases

### Previous Test Run: 2026-03-22 (Scheduler Frontend - Completion)
- **Test Type**: Browser Automation (Playwright)
- **Status**: READY (7/7 scenarios passed)
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-create-test.md`
- **Screenshots**: `test-results/` directory
- **Quick Fixes Applied**: 2 (API format fix, click handler fix)
- **Commit**: 353b530

### Previous Test Run: 2026-03-22 (Scheduler Frontend - Initial)
- **Test Type**: Browser Automation (Playwright)
- **Status**: PARTIAL (5/7 scenarios passed, 2 skipped)
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-frontend-e2e.md`
- **Screenshots**: `test-results/` directory

## Project Structure

```
agents-ensemble/
├── frontend/              # Angular application
│   ├── src/app/
│   │   ├── components/    # UI components
│   │   ├── pages/         # Page components (schedules)
│   │   └── services/      # API services
├── daemon/                # Python backend
├── tests/                 # Backend unit tests
└── .agents/tester/        # Testing documentation (this directory)
```

## Testing Conventions

### Frontend Testing
- Component tests: Test component logic and template rendering
- E2E tests: Test complete user workflows with browser automation
- API mocking: Mock backend responses for isolated frontend tests

### Backend Testing
- Unit tests: Test individual functions and classes
- Integration tests: Test API endpoints with test database
- Mock external services: Use mocks for LLM APIs

## Notes

- Frontend runs on port 4200 by default
- Backend API runs on port 8000 by default
- Use ports > 10000 for mock services
