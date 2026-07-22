# LESSON: Job Queue Indicator — Test Coverage Gap (Error Propagation)

**Date:** 2026-07-22  
**Component:** `job-queue-indicator.component.ts`  
**Branch:** `feature/job-queue-ui-improve`

## Context
The C3 fix changed `fetchJobs()` so that API errors propagate through `forkJoin` and reset both signals to `[]`. The error handler at lines 210-214:

```typescript
error: (err) => {
  console.error('[JobQueueIndicator] Failed to fetch jobs:', err);
  this.activeJobs.set([]);
  this.allRecentJobs.set([]);
}
```

This is new behavior (previously the service swallowed errors via `catchError(() => EMPTY)`), but it has no logic-mirror test.

## Gap
The 88 existing tests cover all count/format/status/navigation logic exhaustively, but the error-reset path is untested. Since this was an intentional behavior change (C3 fix), it should be verified.

## Recommendation
Add a test to the `MockJobQueueIndicatorComponent` that simulates error propagation. Since the logic-mirror pattern tests signals directly, this would require either:
1. Exposing the error handler as a testable method, OR
2. Testing via a spy on the service in a TestBed configuration (breaks convention)

Simplest approach: add a method to the mock class like `simulateFetchError()` that sets both signals to `[]`, then assert the downstream computeds (`displayText`, `isIdle`, `recentJobs`) reflect the reset state.

## Severity
Medium — not blocking, but this is a deliberate behavior change that should have regression coverage.
