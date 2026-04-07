# Phase 5: Frontend — Testing

## Objective
Establish test infrastructure for the frontend job queue components and services, and write initial tests for `job.service.ts`, `job-sse.service.ts`, and the main `jobs.component.ts`.

## Coupling
- **Depends on**: Phase 4 (tests the cleaned-up code)
- **Coupling type**: loose — can write tests against current code and update after Phase 4
- **Shared files with other phases**: Tests import from Phase 4 files
- **Why loose**: Tests verify behavior, not implementation details. Phase 4 removes dead code but doesn't change behavior.

## Context

### Current State
- **Zero test files exist** for any frontend job-related code (*.spec.ts files absent)
- **No test runner configured** (no karma.conf.js or jest.config.js found)
- **Angular schematics set to `skipTests: true`** in `angular.json`
- This is a greenfield test setup effort

### Test Framework Decision
Angular 17+ defaults to Jest. Given the project's modern Angular setup, **Jest** is recommended:
- Faster than Karma
- Better TypeScript support
- No browser needed
- Simpler configuration

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Set up Jest test runner | Install jest, configure `jest.config.js`, update `angular.json` | `frontend/` root |
| 2 | Create test utilities | Mock HTTP backend, SSE mock, test data factories | `frontend/src/testing/` (new) |
| 3 | Test `job.service.ts` | HTTP methods: create, get, list, cancel, retry | `frontend/src/app/services/job.service.spec.ts` (new) |
| 4 | Test `job-sse.service.ts` | SSE connection, reconnection, event parsing | `frontend/src/app/services/job-sse.service.spec.ts` (new) |
| 5 | Test `jobs.component.ts` | Component initialization, filter changes, pause/resume | `frontend/src/app/pages/jobs/jobs.component.spec.ts` (new) |

## Detailed Implementation

### Task 1: Jest Setup

**Install**:
```bash
cd frontend
npm install --save-dev jest @types/jest jest-environment-jsdom ts-jest
```

**Configuration** (`frontend/jest.config.js`):
```javascript
module.exports = {
  preset: 'jest-preset-angular',
  setupFilesAfterSetup: ['<rootDir>/setup-jest.ts'],
  testPathIgnorePatterns: ['<rootDir>/node_modules/', '<rootDir>/dist/'],
  testMatch: ['**/*.spec.ts'],
  collectCoverageFrom: [
    'src/app/**/*.ts',
    '!src/app/**/*.spec.ts',
    '!src/app/**/*.module.ts',
  ],
};
```

**Setup file** (`frontend/setup-jest.ts`):
```typescript
import 'jest-preset-angular/setup-jest';
```

**Update `angular.json`**: Change `skipTests` default to `false` for future schematics (optional).

### Task 2: Test Utilities

**File**: `frontend/src/testing/job-test-helpers.ts` (new)

```typescript
import { Job, JobStatus, JobSource } from '@app/models/job.model';

export function createMockJob(overrides?: Partial<Job>): Job {
  return {
    job_id: 'test-job-123',
    agent_id: 'coder',
    message: 'Fix the login bug',
    source: JobSource.API,
    project_id: 'project-123',
    priority: 5,
    status: JobStatus.PENDING,
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    instance_id: null,
    error_message: null,
    result_summary: null,
    job_metadata: {},
    cancelled_at: null,
    ...overrides,
  };
}

export function createMockJobList(count: number): Job[] {
  return Array.from({ length: count }, (_, i) => 
    createMockJob({ 
      job_id: `job-${i}`,
      priority: Math.min(10, i + 1),
      status: i < count / 2 ? JobStatus.PENDING : JobStatus.COMPLETED,
    })
  );
}
```

### Task 3: `job.service.ts` Tests

**File**: `frontend/src/app/services/job.service.spec.ts` (new)

```typescript
describe('JobService', () => {
  let service: JobService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    // Standard Angular TestBed setup with HttpClientTestingModule
  });

  describe('createJob', () => {
    it('should POST to /api/jobs with correct payload');
    it('should return 200 for immediate processing');
    it('should return 202 for queued jobs');
  });

  describe('getJob', () => {
    it('should GET /api/jobs/{id}');
    it('should return job with all fields including source and metadata');
  });

  describe('listJobs', () => {
    it('should GET /api/jobs with query params');
    it('should support status filter');
    it('should support project_id filter');
  });

  describe('cancelJob', () => {
    it('should DELETE /api/jobs/{id}');
  });

  describe('retryJob', () => {
    it('should POST /api/jobs/{id}/retry');
  });
});
```

### Task 4: `job-sse.service.ts` Tests

**File**: `frontend/src/app/services/job-sse.service.spec.ts` (new)

```typescript
describe('JobSseService', () => {
  let service: JobSseService;

  describe('connectToJob', () => {
    it('should establish SSE connection to /api/jobs/{id}/events');
    it('should emit status_update events');
    it('should emit completed event on terminal state');
    it('should handle connection errors');
    it('should reconnect with exponential backoff');
  });

  describe('disconnect', () => {
    it('should close SSE connection');
    it('should clean up EventSource');
  });
});
```

**Note**: Testing SSE requires mocking `EventSource`. Use a library like `mock-event-source` or create a simple mock:

```typescript
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;
  
  constructor(url: string) { this.url = url; }
  close() {}
  addEventListener(type: string, handler: (e: MessageEvent) => void) {}
}
```

### Task 5: `jobs.component.ts` Tests

**File**: `frontend/src/app/pages/jobs/jobs.component.spec.ts` (new)

```typescript
describe('JobsComponent', () => {
  let component: JobsComponent;
  let fixture: ComponentFixture<JobsComponent>;

  describe('initialization', () => {
    it('should load jobs on init');
    it('should apply default filters');
  });

  describe('filter changes', () => {
    it('should reload jobs when status filter changes');
    it('should reload jobs when project filter changes');
  });

  describe('pause/resume', () => {
    it('should call pauseProjectQueue on toggle');
    it('should call resumeProjectQueue on toggle');
  });

  describe('job actions', () => {
    it('should call cancelJob on cancel click');
    it('should call retryJob on retry click');
  });
});
```

## Key Files
- `frontend/jest.config.js` — New: Jest configuration
- `frontend/setup-jest.ts` — New: Jest setup
- `frontend/src/testing/job-test-helpers.ts` — New: Test utilities
- `frontend/src/app/services/job.service.spec.ts` — New: Service tests
- `frontend/src/app/services/job-sse.service.spec.ts` — New: SSE service tests
- `frontend/src/app/pages/jobs/jobs.component.spec.ts` — New: Component tests

## Constraints
- Jest must work with Angular's module system (use `jest-preset-angular`)
- SSE testing requires mocking `EventSource` (browser API)
- Component tests require Angular TestBed setup
- No real HTTP calls — all via HttpClientTestingModule
- Tests should run in < 10 seconds total

## Deliverables
- [ ] Jest configured and running (`npm test` works)
- [ ] Test helpers for creating mock jobs
- [ ] `job.service.ts` tests: all HTTP methods
- [ ] `job-sse.service.ts` tests: connection, events, reconnection
- [ ] `jobs.component.ts` tests: initialization, filters, actions
- [ ] All tests pass (`npm test` exits 0)
