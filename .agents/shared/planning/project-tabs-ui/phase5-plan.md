# Phase 5: E2E Tests with Playwright

## Objective
Install Playwright as the e2e testing framework and write browser automation tests that verify the complete project-tabs feature: tab creation, switching, filtering, closing, and persistence.

## Coupling
- **Depends on**: Phase 2 (backend API filter must work) **AND** Phase 3 (tab UI components must exist)
- **Coupling type**: independent
- **Shared files with other phases**: None — tests consume the UI as a black box
- **Shared APIs/interfaces**: None — tests interact via browser and API
- **Why this coupling**: Can run in parallel with Phase 4 since it tests Phase 2+3 output

## Context
- No e2e testing framework currently installed
- Unit tests use Jest 29
- Frontend is Angular 21 with standalone components
- **Backend port is 8088** (see `config.yaml:18`) — NOT 8000 (C7)
- **Backend entry point is `python -m daemon`** (see `daemon/__main__.py`) — NOT `python -m uvicorn main:app` (C8)
- Project creation API expects **flat body**: `{ "name": "..." }` — NOT nested `{ data: { name: ... } }` (C9)
- **There is NO `DELETE /api/projects/{id}` endpoint** — tests must use alternative cleanup strategy (C11)
- Backend must be running for e2e tests (need both API server + frontend dev server)
- Instance creation with project requires Phase 2's `project_id` param to be complete (C10)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Install Playwright | `npm install -D @playwright/test`, install browsers, create `playwright.config.ts` | `frontend/package.json`, `frontend/playwright.config.ts` (new) |
| 2 | Configure Playwright for Angular | Base URL to dev server at localhost:4200. WebServer config: backend on port **8088** via `python -m daemon`, frontend via `npm start` | `frontend/playwright.config.ts` |
| 3 | Create test fixtures/helpers | Helpers: create test projects via API (flat body: `{ name: "..." }`), create test instances with `project_id`, cleanup strategy | `frontend/e2e/fixtures/` (new) |
| 4 | Define cleanup strategy | Since no DELETE /api/projects exists: (a) use test-specific project names with timestamp prefix, (b) track created IDs for post-suite cleanup via direct DB, or (c) add a temporary internal test-only cleanup endpoint | `frontend/e2e/fixtures/cleanup.ts` (new) |
| 5 | Test: "All" tab is always visible and active on load | Navigate to `/instances/{someId}`, verify "All" tab exists, is selected, shows all instances | `frontend/e2e/project-tabs.spec.ts` (new) |
| 6 | Test: Add project tab via "+" button | Click "+", select a project from dropdown, verify new tab appears | `frontend/e2e/project-tabs.spec.ts` |
| 7 | Test: Switching tabs filters instances | Open a project tab that has instances, verify instance list shows only that project's instances | `frontend/e2e/project-tabs.spec.ts` |
| 8 | Test: Close project tab | Click close on a project tab, verify it disappears and "All" becomes active | `frontend/e2e/project-tabs.spec.ts` |
| 9 | Test: "All" tab cannot be closed | Verify "All" tab has no close button or close is disabled | `frontend/e2e/project-tabs.spec.ts` |
| 10 | Test: Tab state persists after page refresh | Open tabs, refresh page, verify tabs are restored | `frontend/e2e/project-tabs.spec.ts` |
| 11 | Test: "+" menu shows only unopened projects | Open 2 project tabs, verify only remaining projects show in "+" menu | `frontend/e2e/project-tabs.spec.ts` |
| 12 | Test: Background tabs are inactive | Switch between tabs, intercept network requests, verify only active tab's project_id is being polled | `frontend/e2e/project-tabs.spec.ts` |
| 13 | Add npm scripts for e2e | `"e2e": "playwright test"`, `"e2e:headed": "playwright test --headed"` | `frontend/package.json` |
| 14 | Document e2e test setup | README explaining how to run e2e tests, prerequisites, cleanup | `frontend/e2e/README.md` (new) |

## Key Files

### New Files
- `frontend/playwright.config.ts` — Playwright configuration
- `frontend/e2e/fixtures/test-helpers.ts` — Seed data, API helpers
- `frontend/e2e/fixtures/cleanup.ts` — Cleanup strategy
- `frontend/e2e/project-tabs.spec.ts` — Main e2e test file
- `frontend/e2e/README.md` — E2E test documentation

### Modified Files
- `frontend/package.json` — Add Playwright dependency + npm scripts

## Implementation Notes

### Playwright Config — Correct Ports & Commands (C7, C8)
```typescript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: 'cd ../../ && python -m daemon',  // C8: correct entry point
      port: 8088,                                 // C7: correct port from config.yaml
      reuseExistingServer: true,
      cwd: process.cwd(),
    },
    {
      command: 'npm start',
      port: 4200,
      reuseExistingServer: true,
    },
  ],
});
```

### Test Fixture Helpers — Flat Body for Project Creation (C9)
```typescript
// e2e/fixtures/test-helpers.ts
import { request, APIRequestContext } from '@playwright/test';

const API_BASE = 'http://localhost:8088';

export async function createTestProject(name: string): Promise<{ project_id: string }> {
  const api = await request.newContext({ baseURL: API_BASE });
  // C9: FLAT body, NOT nested { data: { name: ... } }
  const response = await api.post('/api/projects', {
    data: { name },
  });
  return await response.json();
}

export async function createTestInstance(agentId: string, projectId?: string) {
  const api = await request.newContext({ baseURL: API_BASE });
  const body: any = { agent_id: agentId };
  if (projectId) {
    body.project_id = projectId;  // C10: requires Phase 2 to be complete
  }
  const response = await api.post('/api/instances', { data: body });
  return await response.json();
}
```

### Cleanup Strategy — No DELETE /api/projects (C11)
```typescript
// e2e/fixtures/cleanup.ts
// Strategy: Track all created resources, use test-scoped naming

const createdProjectIds: string[] = [];
const createdInstanceIds: string[] = [];

export function trackProject(id: string) { createdProjectIds.push(id); }
export function trackInstance(id: string) { createdInstanceIds.push(id); }

export async function cleanupTestData() {
  const api = await request.newContext({ baseURL: 'http://localhost:8088' });

  // Instances CAN be deleted
  for (const id of createdInstanceIds) {
    await api.delete(`/api/instances/${id}`).catch(() => {});
  }

  // Projects CANNOT be deleted via API (no DELETE endpoint) — C11
  // Options:
  // 1. Leave test projects (use unique names with timestamp to avoid collision)
  // 2. Directly manipulate SQLite DB:
  //    sqlite3 data/daemon.db "DELETE FROM projects WHERE project_id IN (...)"
  // 3. Add a temporary test-only DELETE endpoint guarded by env var
  //
  // RECOMMENDED: Option 1 for CI, option 2 for local dev
}
```

### Test Structure
```typescript
// e2e/project-tabs.spec.ts
import { test, expect } from '@playwright/test';
import { createTestProject, createTestInstance, cleanupTestData } from './fixtures/test-helpers';

test.describe('Project Tabs', () => {
  let projectId1: string;
  let projectId2: string;
  let instanceId: string;

  test.beforeAll(async () => {
    // Seed test data
    const p1 = await createTestProject(`E2E Test Alpha ${Date.now()}`);
    const p2 = await createTestProject(`E2E Test Beta ${Date.now()}`);
    projectId1 = p1.project_id;
    projectId2 = p2.project_id;

    // Create instance in project 1 (requires Phase 2 API)
    const inst = await createTestInstance('test-agent', projectId1);
    instanceId = inst.instance_id;
  });

  test.afterAll(async () => {
    await cleanupTestData();
  });

  test('shows All tab by default on chat page', async ({ page }) => {
    await page.goto(`/instances/${instanceId}`);
    const allTab = page.locator('.tab-bar .all-tab');
    await expect(allTab).toBeVisible();
    await expect(allTab).toHaveClass(/active/);
  });

  test('adds project tab from + menu', async ({ page }) => {
    await page.goto(`/instances/${instanceId}`);
    await page.locator('.tab-bar .add-btn').click();
    const menuItem = page.locator('.dropdown-menu .menu-item').first();
    await expect(menuItem).toBeVisible();
    await menuItem.click();

    const projectTab = page.locator('.project-tab').first();
    await expect(projectTab).toBeVisible();
    await expect(projectTab).toHaveClass(/active/);
  });

  test('filters instances by project tab', async ({ page }) => {
    // Navigate, open project tab with instances, verify filtering
  });

  test('closes project tab and returns to All', async ({ page }) => {
    // Open a project tab, click close, verify it's gone and All is active
  });

  test('All tab has no close button', async ({ page }) => {
    const allTab = page.locator('.tab-bar .all-tab');
    const closeBtn = allTab.locator('.close-btn');
    await expect(closeBtn).not.toBeVisible();
  });

  test('persists tabs after refresh', async ({ page }) => {
    await page.goto(`/instances/${instanceId}`);
    // Add a tab
    await page.locator('.tab-bar .add-btn').click();
    await page.locator('.dropdown-menu .menu-item').first().click();
    const tabCount = await page.locator('.project-tab').count();

    // Refresh
    await page.reload();
    const tabCountAfter = await page.locator('.project-tab').count();
    expect(tabCountAfter).toBe(tabCount);
  });

  test('+ menu shows only unopened projects', async ({ page }) => {
    // Open all projects, verify dropdown is empty/disabled
  });

  test('background tab generates no polling requests', async ({ page }) => {
    const requests: string[] = [];
    page.on('request', req => {
      if (req.url().includes('/api/instances')) {
        requests.push(req.url());
      }
    });
    // Switch tabs, wait 12s, verify only active tab's project is polled
  });
});
```

## Constraints
- Backend port is **8088** (not 8000) — see `config.yaml:18` (C7)
- Backend starts via **`python -m daemon`** (not `python -m uvicorn main:app`) (C8)
- Project creation uses **flat body** `{ name: "..." }` (C9)
- No `DELETE /api/projects/{id}` exists — use alternative cleanup (C11)
- Instance creation with `project_id` requires Phase 2 complete (C10)
- Tests must not depend on production data — use seed data
- Must work in CI (headless) and local (headed) modes
- No changes to production code — only test files and config

## Deliverables
- [ ] Playwright installed and configured (correct port 8088, correct command `python -m daemon`)
- [ ] E2E test suite covers all tab operations (open, close, switch, filter)
- [ ] Tests verify "All" tab cannot be closed
- [ ] Tests verify "+" menu shows only unopened projects
- [ ] Tests verify tab state persists after refresh
- [ ] Tests verify background tab polling is inactive
- [ ] Cleanup strategy documented for missing DELETE endpoint
- [ ] npm scripts for running e2e tests
- [ ] All e2e tests pass consistently
