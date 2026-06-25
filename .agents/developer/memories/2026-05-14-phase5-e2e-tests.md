# Phase 5: E2E Browser Automation Tests — Project Tabs (2026-05-14)

## Architecture
- **Framework**: Playwright (`@playwright/test`) in `frontend/` directory
- **Config**: `frontend/playwright.config.ts` — dual webServer (backend 8088, frontend 4200)
- **Tests**: `frontend/e2e/project-tabs.spec.ts` — 8 tests, serial execution
- **Helpers**: `frontend/e2e/fixtures/test-helpers.ts` (API calls), `cleanup.ts` (resource tracking)

## Key Selectors (from actual HTML)
- Tab bar: `.tab-bar`
- Tab items: `.tab-bar .tab` 
- Active tab: `.tab.active`
- "All" tab: first `.tab` (no close button)
- "+" button: `.tab-add`
- Close button: `.tab-close` (mat-icon "close")
- Instance items: `a[href^="/instances/"]` (more reliable than `.instance-item`)
- Empty state: `.empty-text`

## Bug Fixes Found During Testing
1. **angular.json**: Port was 4199, should be 4200 (Angular CLI default)
2. **proxy.conf.json**: Proxy pointed to 8079, should be 8088 (actual backend port from config.yaml)
3. **daemon/routers/projects.py**: `_project_to_response()` crashed with None values for list/dict fields — added `or []` / `or {}`

## Cleanup Strategy
- Instances: DELETE via `/api/instances/{id}` — works
- Projects: NO DELETE endpoint — use timestamp-prefixed unique names, instances cleaned up
- localStorage: Tests clear `ensemble-project-tabs` key before each run

## Reviewer Fixes Applied (2nd commit: f74cf15)
- **Fix 1**: Added Test 9 — background tab polling verification (intercept `/api/instances` requests, verify only active tab polls)
- **Fix 2**: Test 3 strengthened — exact instance ID assertions via `a[href="/instances/{id}"]` selectors instead of weak count checks
- **Fix 3**: localStorage clear moved from `afterEach` to `beforeEach` — fixes persistence test
- **Fix 4**: Replaced `waitForTimeout(500)` with `waitForResponse()` in test 3
- **Fix 5**: README.md port reference 4199 → 4200

## Test Results (Final)
- 9 tests, all passing (50.8s runtime — polling test adds ~24s for two 12s wait cycles)
- Tests run serially (data dependencies between tests)

## NPM Scripts
- `npm run e2e` — headless
- `npm run e2e:headed` — with browser visible
