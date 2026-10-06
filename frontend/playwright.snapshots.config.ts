// frontend/playwright.snapshots.config.ts
//
// Amendment pass 5 FINAL (blocker #1) — MIRRORS the in-repo
// maintenance precedent end-to-end. Loaded via:
//   cd frontend && npx playwright test \
//     --config playwright.snapshots.config.ts e2e/snapshots.spec.ts
//
// Mechanics mirrored from frontend/playwright.maintenance.config.ts:
//   - dedicated daemon port 18279 (vs maintenance's 8099,
//     dev.sh's 8079)
//   - dedicated FE port 14199 (vs maintenance's 4299, dev's 4199)
//   - BOOT_SCRIPT path under frontend/scripts/ (NOT repo root
//     scripts/) — verified at maintenance:42
//   - PROXY_SNAPSHOTS = frontend/proxy.conf.snapshots.json (mirrors
//     maintenance's PROXY_E2E at :43)
//   - testDir './e2e' + testMatch snapshots.spec.ts (mirrors
//     maintenance's :46-47 base testDir)
//   - globalTeardown backstop at './e2e/global-teardown-snapshots.ts'
//     (mirrors maintenance:52 — optional-but-recommended for the
//     SIGKILL race-loser against the boot script's trap)
//   - workers 1, timeout 90000 (mirrors maintenance:56-58 — second-
//     daemon boot + canary)
//   - webServer[0]: BOOT_SCRIPT start, reuseExistingServer: false,
//     timeout 120_000 (mirrors maintenance:69-72)
//   - webServer[1]: npx ng serve --port 14199 --proxy-config
//     proxy.conf.snapshots.json, reuseExistingServer: false,
//     timeout 180_000 (mirrors maintenance:95-98)
//
// Citations to the base config being overridden:
//   - testDir: './e2e'                       (playwright.config.ts:4)
//   - webServer[0].port: 8079                (playwright.config.ts:20)
//   - webServer[0].reuseExistingServer: true (playwright.config.ts:21)
//   - webServer[1].port: 4199                (playwright.config.ts:33)
//   - webServer[1].reuseExistingServer: true (playwright.config.ts:34)
//
// `dev.sh` is NOT edited (pass 5 FINAL blocker #1 — `dev.sh:128` is
// `export PORT=8079` unconditional; the port comes from the boot
// script's own `export PORT="$DAEMON_PORT"` line, which mirrors
// maintenance's boot-e2e-maintenance-daemon.sh:139). The dead
// `ENSEMBLE_PORT` / `SNAPSHOTS_API_BASE` / `API_TARGET` env knobs
// from amendment pass 3/4 are REMOVED (grep-evidenced in §7 run
// brief — 0 hits).
//
// `permissions: ['clipboard-read', 'clipboard-write']` is added to
// the `use:` block so the Playwright context can exercise the
// copy-id assertion at e2e step 5 (clipboard.readText() requires
// the browser context to grant clipboard-read permission).
//
import { defineConfig, devices } from '@playwright/test';
import { join } from 'path';

const HERE = __dirname;
const REPO_ROOT = join(HERE, '..', '..');
const BOOT_SCRIPT = join(HERE, 'scripts', 'boot-e2e-snapshots-daemon.sh');
const PROXY_SNAPSHOTS = join(HERE, 'proxy.conf.snapshots.json');

export default defineConfig({
  testDir: './e2e',
  // Run ONLY the snapshots spec under this dedicated config. The
  // shared e2e/ dir hosts the rest of the FE spec pack (maintenance,
  // liveness, workspace, …); without this pin a bare
  // `npx playwright test --config playwright.snapshots.config.ts`
  // would sweep every spec into the strict-port snapshots run.
  // Mirrors the maintenance mechanism (testDir + filename pin) as an
  // in-config belt instead of relying on the CLI path filter.
  testMatch: /snapshots\.spec\.ts/,
  // Race-loser backstop — mirrors playwright.maintenance.config.ts:52.
  // The boot script's TERM/INT+EXIT trap is the primary path; this
  // is the deterministic backstop when Playwright force-kills the
  // webServer group before pg_ctl stop finishes.
  globalTeardown: './e2e/global-teardown-snapshots.ts',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 90000, // Mirrors maintenance:58 — second-daemon boot + canary
  use: {
    baseURL: 'http://localhost:14199',
    trace: 'on-first-retry',
    actionTimeout: 20000,
    // pass 5 FINAL item #5 — clipboard perms for step 5 copy-id
    // assertion (the drawer's copy-id button writes the UUID to
    // navigator.clipboard; the spec reads it back via
    // page.evaluate(() => navigator.clipboard.readText()) — the
    // Playwright context must grant both permissions for the read
    // to succeed without a permission prompt race).
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  // Dedicated second-daemon (mirrors playwright.maintenance.config.ts:67-101).
  // Boots: disposable PG (port 15532) → ensemble daemon (port 18279).
  // The FE dev server proxies /api + /ws → 18279 via
  // frontend/proxy.conf.snapshots.json (mirrors
  // frontend/proxy.conf.e2e.json shape exactly; only the target port
  // differs — 18279 vs 8099).
  webServer: [
    {
      command: `${BOOT_SCRIPT} start`,
      port: 18279,
      reuseExistingServer: false, // [R-11] refuse foreign daemon
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || '',
        LOG_LEVEL: process.env.LOG_LEVEL || 'info',
        // NOTE: snapshots has NO equivalent of
        // MAINTENANCE_ENDPOINTS_ENABLED — the snapshots router is
        // unconditional in this branch (no env gate; the toggle is
        // UI-only and routes /api/settings/snapshot-create, not
        // /api/snapshots). Deliberately omitted (mirrors maintenance
        // shape minus the maintenance-specific flag).
      },
    },
    {
      // FE dev server bound to a dedicated port (NOT 4199 — that's
      // the dev project). The proxy file points /api + /ws → 18279.
      command: `npx ng serve --port 14199 --proxy-config ${PROXY_SNAPSHOTS}`,
      port: 14199,
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
  projects: [
    {
      name: 'snapshots',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
