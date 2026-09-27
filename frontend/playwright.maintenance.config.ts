import { defineConfig, devices } from '@playwright/test';
import { join } from 'path';

/**
 * Dedicated Playwright config for the Maintenance Console e2e suite.
 *
 * [R-11, v3 fix pass — leader ruling 4b]:
 *   - Dedicated daemon port (8099 — NOT the developer's :8079).
 *   - `reuseExistingServer: false` — a reused foreign daemon would
 *     point the destructive spec at an unknown DB.
 *   - Daemon canary before ANY destructive test (asserts
 *     `/availability` → state:'ready').
 *   - Disposable-PG via the bootstrap script
 *     `scripts/boot-e2e-maintenance-daemon.sh` (initdb + pg_ctl +
 *     createdb on port 15432, distinct from dev :5432).
 *
 * This config is INVOKED separately from the default
 * `playwright.config.ts`:
 *
 *   npx playwright test --config playwright.maintenance.config.ts \
 *     --project maintenance maintenance-checkpoint-cleanup
 *
 * The default `playwright.config.ts` (8079/4199, reuseExisting:true)
 * is UNTOUCHED by this config.
 */
const HERE = __dirname;
const REPO_ROOT = join(HERE, '..', '..');
const BOOT_SCRIPT = join(HERE, 'scripts', 'boot-e2e-maintenance-daemon.sh');
const PROXY_E2E = join(HERE, 'proxy.conf.e2e.json');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 90000, // Longer than default — second-daemon boot + canary
  use: {
    baseURL: 'http://localhost:4299',
    trace: 'on-first-retry',
    actionTimeout: 20000,
  },
  // Dedicated second-daemon (NOT dev.sh which hardcodes :8079).
  // Boots: disposable PG (port 15432) → ensemble daemon (port 8099).
  // The FE dev server proxies `/api` → 8099 via `proxy.conf.e2e.json`.
  webServer: [
    {
      command: `${BOOT_SCRIPT} start`,
      port: 8099,
      reuseExistingServer: false, // [R-11] refuse foreign daemon
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || 'e2e-placeholder-key-not-used',
        LOG_LEVEL: process.env.LOG_LEVEL || 'info',
        MAINTENANCE_ENDPOINTS_ENABLED: process.env.MAINTENANCE_ENDPOINTS_ENABLED || '1',
      },
    },
    {
      // FE dev server bound to a dedicated port (NOT 4199 — that's
      // the dev project). The proxy file points /api → 8099.
      command: `npx ng serve --port 4299 --proxy-config ${PROXY_E2E}`,
      port: 4299,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
  projects: [
    {
      name: 'maintenance',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
