// FE-UI lane marker (group-chat gc-fe-surfaces P4 rooms gate): room chat.
/**
 * Playwright config — Group-Chat Rooms merge gate (real browser, stubbed API).
 *
 * Dedicated config (W-14 choice (b)): does NOT inherit the shared
 * `playwright.config.ts` (which boots dev.sh on 8079 and sweeps all 35 e2e
 * specs). Binds NEITHER 9797/9798 (live) NOR 8079/8088 (other dev lanes);
 * picks its own FE port (default 14204). The guard against an occupant is
 * `reuseExistingServer: false` in the webServer block below — Playwright
 * FAILS LOUDLY instead of attaching to a server already on the port, so a
 * stray process on 14204 can never be silently reused (and is never
 * killed). That IS the whole guard: the webServer block sets NO env, and
 * none is needed — `ng serve` boots NO daemon for this rooms gate (there
 * is no sweep to pin and no ambient-allow env), and every API call is
 * stubbed inside the spec (the metrics-gate precedent).
 *
 * Mechanism (cloned from `playwright.metrics-gate.config.ts`):
 *   - FE dev server (`ng serve`) started/stopped by Playwright's webServer.
 *   - ALL API calls are stubbed via `page.route` inside the spec.
 *   - testMatch RESTRICTED to the rooms-gate spec ONLY.
 *   - single-shot deterministic gate: workers 1, retries 0, fullyParallel false.
 *
 * Port: default 14204; env override ROOMS_GATE_PORT. Occupants are NEVER killed.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.ROOMS_GATE_PORT ?? '14204');
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: /group-chat-rooms-gate\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: '/tmp/rooms-gate-evidence/artifacts',
  reporter: [
    ['line'],
    ['json', { outputFile: '/tmp/rooms-gate-evidence/results.json' }],
  ],
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'off',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npx ng serve --port ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
    cwd: __dirname,
  },
});
