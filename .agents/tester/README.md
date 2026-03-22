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

### Latest Test Run: 2026-03-22 (Completion)
- **Test Type**: Browser Automation (Playwright)
- **Status**: READY (7/7 scenarios passed)
- **Report**: `.agents/tester/RESULTS/2026-03-22-scheduler-create-test.md`
- **Screenshots**: `test-results/` directory
- **Quick Fixes Applied**: 2 (API format fix, click handler fix)
- **Commit**: 353b530

### Previous Test Run: 2026-03-22 (Initial)
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
