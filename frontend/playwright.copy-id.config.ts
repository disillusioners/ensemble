// frontend/playwright.copy-id.config.ts
//
// Verbatim structural clone of frontend/playwright.snapshots.config.ts —
// SAME boot script, SAME proxy, SAME ports, SAME globalTeardown, SAME
// clipboard permissions; ONLY change: testMatch → /instance-id-copy\.spec\.ts/.
//
// Reuses the snapshots lane because the feature under test (the
// click-to-copy instance-id chip) needs the SAME second-daemon
// disposable-PG bootstrap shape — only the spec file differs. Sharing
// the lane keeps the dedicated-port / disposable-DB / dual-webServer
// ceremony identical to the snapshots spec, with zero new env knobs.
//
// Loaded via:
//   cd frontend && npx playwright test \
//     --config playwright.copy-id.config.ts e2e/instance-id-copy.spec.ts
//
// Mechanics mirrored from frontend/playwright.snapshots.config.ts:
//   - dedicated daemon port 18279 (vs maintenance's 8099,
//     dev.sh's 8079)
//   - dedicated FE port 14199 (vs maintenance's 4299, dev's 4199)
//   - BOOT_SCRIPT path under frontend/scripts/ (REUSED — the snapshots
//     boot script works for any spec that needs a fresh disposable PG
//     + daemon; it does not bind to the spec it boots for)
//   - PROXY = frontend/proxy.conf.snapshots.json (REUSED — points
//     /api + /ws → 18279; the spec's request URLs are agnostic)
//   - testDir './e2e' + testMatch /instance-id-copy\.spec\.ts/ (mirror
//     of snapshots' filename pin, one spec per config — no cross-
//     contamination between in-flight FE lanes)
//   - globalTeardown backstop at './e2e/global-teardown-snapshots.ts'
//     (REUSED — the script's teardown is keyed on the pid file +
//     pg_e2e_snap_* dir prefix, both of which are lane-wide, not
//     spec-wide. Symmetric: the boot script owns the primary teardown
//     path; the backstop covers the SIGKILL race-loser.)
//   - workers 1, timeout 90000 (mirror — second-daemon boot + canary)
//   - webServer[0]: boot script start, reuseExistingServer: false,
//     timeout 120_000
//   - webServer[1]: npx ng serve --port 14199 --proxy-config
//     proxy.conf.snapshots.json, reuseExistingServer: false,
//     timeout 180_000
//   - use.permissions ['clipboard-read', 'clipboard-write'] (required
//     for the S3 / S7 / S8 copy-id assertions — the spec reads back
//     via page.evaluate(() => navigator.clipboard.readText()))
import { defineConfig, devices } from '@playwright/test';
import { join } from 'path';

const HERE = __dirname;
const REPO_ROOT = join(HERE, '..', '..');
// REUSED — same boot script as the snapshots lane. The script's
// contract is "give me a fresh disposable PG + daemon on 18279"; it
// does not key on the spec file it boots for.
const BOOT_SCRIPT = join(HERE, 'scripts', 'boot-e2e-snapshots-daemon.sh');
// REUSED — same proxy file as the snapshots lane (/api + /ws → 18279).
const PROXY_SNAPSHOTS = join(HERE, 'proxy.conf.snapshots.json');

export default defineConfig({
  testDir: './e2e',
  // Run ONLY the instance-id-copy spec under this dedicated config.
  // Without this pin a bare `npx playwright test --config
  // playwright.copy-id.config.ts` would sweep every spec in e2e/ into
  // the strict-port run. Mirrors snapshots' in-config pin.
  testMatch: /instance-id-copy\.spec\.ts/,
  // Race-loser backstop — mirrors playwright.snapshots.config.ts:71.
  // The boot script's TERM/INT+EXIT trap is the primary path; this
  // is the deterministic backstop when Playwright force-kills the
  // webServer group before pg_ctl stop finishes.
  globalTeardown: './e2e/global-teardown-snapshots.ts',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  timeout: 90000, // Mirrors snapshots:77 — second-daemon boot + canary
  use: {
    baseURL: 'http://localhost:14199',
    trace: 'on-first-retry',
    actionTimeout: 20000,
    // clipboard perms for the S3 / S7 / S8 copy-id assertions. The
    // component writes the full UUID via navigator.clipboard.writeText
    // (with a CDK execCommand fallback); the spec reads it back via
    // page.evaluate(() => navigator.clipboard.readText()). Playwright's
    // context must grant both permissions or the read races a
    // permission prompt.
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  // Dedicated second-daemon (mirrors playwright.snapshots.config.ts:96-124).
  // Boots: disposable PG (port 15532) → ensemble daemon (port 18279).
  // The FE dev server proxies /api + /ws → 18279 via
  // frontend/proxy.conf.snapshots.json.
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
      },
    },
    {
      // FE dev server bound to the dedicated snapshots port 14199 (NOT
      // 4199 — that's the dev lane). The proxy file points /api + /ws
      // → 18279.
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
      name: 'copy-id',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
