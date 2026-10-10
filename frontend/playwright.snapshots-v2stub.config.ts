/**
 * Playwright config — Snapshots v2 merge gate (real-browser, stubbed API).
 *
 * NEW test artifact only (v2 merge gate commission). Product code frozen.
 *
 * Mechanism (decided, do not deviate):
 *   - FE dev server (`ng serve`) started/stopped by Playwright's own
 *     `webServer` lifecycle. NO daemon, NO database, NO uv sync, NO dev.sh.
 *   - ALL API calls stubbed via `page.route` inside the spec — nothing
 *     reaches the dev-server proxy (proxy.conf.json → :8079) because
 *     interception happens pre-network.
 *   - testMatch is RESTRICTED to the gate spec ONLY. The default testMatch
 *     would sweep every spec in ./e2e including daemon-dependent ones —
 *     do not widen it.
 *   - Single-shot deterministic gate: workers 1, retries 0, fullyParallel
 *     false. Flakes are handled by the retry-budget process, not in-run
 *     retries.
 *
 * Port: default 14201 (verified free at authoring time); the pack script
 * (`test/packs/snapshots_v2_webauto_test.sh`) re-picks the first FREE port
 * in 14199..14210 at run time and exports SNAPV2_GATE_PORT. Occupants are
 * NEVER killed — the pack skips to the next free port.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.SNAPV2_GATE_PORT ?? '14201');
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  // CRITICAL: gate spec ONLY. Sweeping ./e2e broadly would run the
  // daemon-dependent v1 specs against a stubbed no-daemon dev server.
  testMatch: /snapshots-v2-gate\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Binary evidence stays OUT of the repo (never committed).
  outputDir: '/tmp/snapv2-gate-evidence/artifacts',
  reporter: [
    ['line'],
    ['json', { outputFile: '/tmp/snapv2-gate-evidence/results.json' }],
  ],
  use: {
    baseURL: BASE_URL,
    permissions: ['clipboard-read', 'clipboard-write'],
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
    // Calibration: ng serve cold compile on this worktree measured ~
    // 45-75s in the smoke run; 180s leaves generous headroom. Adjust
    // only with fresh evidence.
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
    cwd: __dirname,
  },
});
