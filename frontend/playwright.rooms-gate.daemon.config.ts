// FE-UI lane marker (group-chat gc-fe-surfaces P4/G2 rooms gate): real-daemon leg.
/**
 * Playwright config — Group-Chat Rooms gate, REAL-DAEMON leg (G2 leader gate).
 *
 * Difference from `playwright.rooms-gate.config.ts` (the stubbed sibling):
 *   - NOTHING is stubbed in the spec except the single forced-fault legs
 *     (W1/W7), so every assertion runs against a REAL daemon + REAL Postgres
 *     rows.
 *   - The webServer is the FE dev server on 14205, which must be PRE-BOOTED
 *     by the caller against a fenced dev daemon:
 *
 *        cd frontend
 *        ./node_modules/.bin/ng serve --port 14205 \
 *            --proxy-config proxy.gcfe-e2e.conf.json
 *
 *     `reuseExistingServer: true` is deliberately set for THIS config ONLY:
 *     the caller owns that server (it is the fenced dev daemon's FE), so
 *     Playwright must attach to it instead of booting a second one. The
 *     stubbed sibling keeps `reuseExistingServer: false` (fail-loud) — the
 *     two postures are intentionally different.
 *
 * Ports: 14205 (FE) and the daemon behind the proxy (8079 in the G2 lane).
 * NEVER 9797/9798 (the live ensemble) or 8088 (its own daemon).
 *
 * testMatch is pinned to the daemon spec ONLY — the stubbed rows keep
 * running under their own config.
 */
import { defineConfig, devices } from '@playwright/test';

const _rawPort = process.env.ROOMS_GATE_DAEMON_PORT;
const _parsedPort = _rawPort ? Number(_rawPort) : NaN;
const PORT = Number.isFinite(_parsedPort) && _parsedPort > 0 ? _parsedPort : 14205;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: /group-chat-rooms-gate\.daemon\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  // Real-daemon legs include a 10s-poll freshness wait and a 12s
  // tab-hide window; 120s per test keeps those bounded with margin.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  outputDir: `/tmp/gcfe-g2-evidence-${process.pid}/artifacts`,
  reporter: [
    ['line'],
    ['json', { outputFile: `/tmp/gcfe-g2-evidence-${process.pid}/results.json` }],
  ],
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'off',
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    viewport: { width: 1400, height: 900 },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    // Only used when the caller has NOT pre-booted the server; with
    // reuseExistingServer the pre-booted instance is attached instead.
    command: `./node_modules/.bin/ng serve --port ${PORT} --proxy-config proxy.gcfe-e2e.conf.json`,
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
    cwd: __dirname,
  },
});
