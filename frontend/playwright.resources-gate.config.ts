/**
 * Playwright config — Resources page merge gate (real-browser, stubbed API).
 *
 * NEW test artifact only (FE /resources observability page, P5 T7).
 * Product code frozen behind the gate.
 *
 * Mechanism (cloned from `playwright.snapshots-v2stub.config.ts`, D8):
 *   - FE dev server (`ng serve`) started/stopped by Playwright's own
 *     `webServer` lifecycle. NO daemon, NO database, NO uv sync, NO dev.sh.
 *   - ALL API calls stubbed via `page.route` inside the spec — nothing
 *     reaches the dev-server proxy (proxy.conf.json → :8079) because
 *     interception happens pre-network.
 *   - testMatch is RESTRICTED to the gate spec ONLY. The default testMatch
 *     would sweep every spec in ./e2e including daemon-dependent ones —
 *     do not widen it.
 *   - Single-shot deterministic gate: workers 1, retries 0, fullyParallel
 *     false.
 *
 * Port: default 14202 (≥14201; distinct from the snapshots gate's 14201);
 * env override RESOURCES_GATE_PORT. Occupants are NEVER killed.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.RESOURCES_GATE_PORT ?? '14202');
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  // CRITICAL: gate spec ONLY. Sweeping ./e2e broadly would run the
  // daemon-dependent specs against a stubbed no-daemon dev server.
  testMatch: /resources-gate\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Binary evidence stays OUT of the repo (never committed).
  outputDir: '/tmp/resources-gate-evidence/artifacts',
  reporter: [
    ['line'],
    ['json', { outputFile: '/tmp/resources-gate-evidence/results.json' }],
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
    // ng serve cold compile on this worktree measured ~45-75s; 180s
    // leaves generous headroom.
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
    cwd: __dirname,
  },
});
