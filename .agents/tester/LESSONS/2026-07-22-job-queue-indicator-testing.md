# LESSON: Job Queue Indicator Testing — Frontend Test Patterns

Date: 2026-07-22
Feature: Job Queue Status Indicator (frontend only)
Commit: 3c64d094 (feature/job-queue-ui)

## What Was Tested
A new Angular standalone component `JobQueueIndicatorComponent` added to the app header, showing a badge count of queued+active jobs with a per-project tooltip breakdown. Polls `GET /api/jobs?status=queued,active` every 8 seconds.

## Testing Approach (3 parallel workers)
1. **Build check** — `npx ng build` (infrastructure, no skill needed for compile-only)
2. **Unit tests** — Jest with logic-mirror pattern (skill: test-pack-execution)
3. **Smoke test** — dev server + HTTP check + bundle grep (infrastructure)

All three ran in parallel (independent, no dependencies). Total wall-clock ≈ time of slowest worker.

## Key Patterns Learned

### 1. Logic-Mirror Test Pattern (NOT TestBed)
This project does NOT use Angular TestBed/ComponentFixture for component tests. Instead:
- Create a `Mock<Component>Component` class that replicates the real component's signals/computed
- Test the logic directly — no DOM rendering, no ComponentFixture
- Pattern source: `frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.spec.ts`
- Use `createMockJob` from `frontend/src/app/testing/job-test-helpers.ts`

### 2. Jest CLI Flag Version Difference
- Jest 30+ requires `--testPathPatterns` (PLURAL), not `--testPathPattern` (singular)
- The test-pack-execution skill template uses the singular form — workers must adapt

### 3. Component Counting Logic Gotcha
The `groupByProject` method creates a bucket for EVERY job, including terminal-status jobs. This means:
- A job with `status='completed'` renders as `"proj-A: "` (empty parts, since neither running nor pending counter incremented)
- It does NOT show as "No active jobs" — that only appears when the jobs array is truly empty
- Tests must account for this: assert counting behavior, not just label strings

### 4. Bundle Verification for SPA Smoke Tests
For Angular SPAs, the component selector does NOT appear in initial HTML (client-side rendered).
To verify the component is included in the build, grep the compiled JS bundle instead:
- `grep "ComponentName" frontend/dist/frontend/*.js`
- `grep "selector-name" frontend/dist/frontend/*.js`
This confirms compilation + inclusion without needing a browser.

## ensure.md Notes
- ensure.md is backend-focused (PostgreSQL, concurrency, deadlock) — no frontend-specific requirements
- Frontend changes do not trigger ensure.md validation requirements (all Core requirements are backend packs)
- README.md noted ensure.md as "NOT YET CREATED" but it now exists at rules/ensure.md — minor doc staleness
