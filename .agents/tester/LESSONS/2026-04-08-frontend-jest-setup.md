# Phase 5 Frontend Test Setup — Jest with Angular 21

## Date: 2026-04-08

## Key Learnings

### Jest Setup for Angular 21 (esbuild)
- Angular 21 with `@angular/build:application` (esbuild-based) does NOT include Karma by default
- `tsconfig.spec.json` existed but referenced `vitest/globals` — needed update to `jest` types
- `jest-preset-angular` works well with Angular 21 — configure in `jest.config.js` with `setup-jest.ts`
- No browser needed — runs in jsdom

### Test Patterns
- **Models**: Test helper functions directly (isTerminalStatus, getStatusColor, getPriorityColor), verify interface shape with type-level checks
- **Services with HttpClient**: Use `HttpClientTestingModule` + `HttpTestingController` for all HTTP mocking
- **SSE Service**: Mock `EventSource` constructor globally (`(globalThis as any).EventSource = MockEventSource`). Use `NgZone.run()` for signal updates
- **Components**: Use `TestBed.configureTestingModule` with provider overrides. Mock MatDialog, MatSnackBar, Router, and all injected services
- **Component computed signals**: Test directly by creating component with `input()` signals set via TestBed

### Phase 4 Changes Validated
- `message` field is now optional in Job model — tests verify both with/without message
- Source badge (`job().source`) renders in job-detail-drawer template
- `cancelled_at` displays in timeline when present
- SSE service works without removed `currentObserver` and `Observer<T>` interface

### Performance
- 148 tests run in 2.4s — well under the 2-minute unit test pack timeout
- No flaky tests detected

## Files Reference
- Config: `frontend/jest.config.js`, `frontend/setup-jest.ts`
- Helpers: `frontend/src/app/testing/job-test-helpers.ts`
- 5 spec files across models, services, pages, components
