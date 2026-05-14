# E2E Tests for Project Tabs Feature

End-to-end tests using Playwright to verify the project tabs functionality.

## Prerequisites

Before running E2E tests, ensure:

1. **Node.js** is installed (v18+ recommended)
2. **Python** is installed (for the backend)
3. **Backend dependencies** are installed: `cd ../.. && uv sync`
4. **Frontend dependencies** are installed: `npm install`

## Running Tests

### Run tests in headless mode (default)

```bash
npm run e2e
```

### Run tests in headed mode (with browser visible)

```bash
npm run e2e:headed
```

### Run specific test

```bash
npx playwright test project-tabs.spec.ts
```

### Open HTML report after test run

```bash
npx playwright show-report
```

## Test Configuration

The Playwright configuration (`playwright.config.ts`) handles:

- **Backend startup**: Automatically starts the Python daemon on port 8088
- **Frontend startup**: Automatically starts Angular dev server on port 4200
- **Sequential execution**: Tests run serially due to shared localStorage state
- **Timeout**: 30 second timeout per test

## Cleanup Strategy

### Instance Cleanup

Test instances are automatically deleted after test completion via the `cleanupAll()` function in `fixtures/cleanup.ts`.

### Project Cleanup

Projects **cannot be deleted via API** (no `DELETE /api/projects` endpoint exists). To avoid pollution:

- Tests use unique timestamped names (e.g., `Test Project 123456789-add`)
- Projects persist but won't interfere with subsequent test runs
- If you need a clean slate, manually delete project records from the database (`data/ensemble.db`)

## Test Coverage

The E2E tests verify:

1. **Default state**: "All" tab visible and active on page load
2. **Add project tab**: Opening project tab from "+" menu
3. **Tab filtering**: Switching tabs correctly filters instances
4. **Close tab**: Closing project tabs works correctly
5. **All tab protection**: "All" tab cannot be closed
6. **Persistence**: Tab state survives page refresh
7. **Menu filtering**: "+" menu shows only unopened projects
8. **Empty state**: Empty project shows appropriate message

## Troubleshooting

### Tests fail with "connection refused"

Ensure both backend and frontend servers can start. Check that port 8088 and 4200 are available.

### Tests are flaky

The tests use `waitForSelector` and explicit waits. If timing issues occur:

- Check network latency to backend (port 8088)
- Increase `actionTimeout` in `playwright.config.ts`

### localStorage issues

Each test clears localStorage in `afterEach` to ensure isolation. If you need to debug with persistent state, comment out the `test.afterEach` block temporarily.
