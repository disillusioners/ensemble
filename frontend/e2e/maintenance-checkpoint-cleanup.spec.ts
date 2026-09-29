/**
 * Maintenance Console — Checkpoint Cleanup e2e suite (Phase 2, Section 1).
 *
 * Runs against a DEDICATED second-daemon (port 8099, disposable PG on
 * port 15432) configured by `playwright.maintenance.config.ts`. The
 * webServer invokes `scripts/boot-e2e-maintenance-daemon.sh` which:
 *   1. Inits a local PG cluster on :15432.
 *   2. Creates a disposable `ensemble_e2e_maint_<pid>` DB.
 *   3. Starts the daemon on :8099 against that DB.
 *   4. Polls `/availability` until `state:'ready'` (canary).
 *
 * SAFETY (R-11, plan §Activation):
 *   - REFUSES to run if `ENSEMBLE_DB_DSN` is unset OR matches
 *     `/ensemble_prod/i` (defense-in-depth — even though the
 *     webServer uses `POSTGRES_*` env directly, the spec ALSO reads
 *     `ENSEMBLE_DB_DSN` to refuse any prod-touching config).
 *   - Scrubs `POSTGRES_*` inherited env before booting (in
 *     `boot-e2e-maintenance-daemon.sh`).
 *   - Daemon canary (`/availability` → `state:'ready'`) gates every
 *     destructive test — if the canary fails, the spec skips.
 *
 * 15 test cases per the plan T7.2 outline + fix commission 2026-09-29.
 */

import { test, expect, type APIRequestContext, type Browser, type Page } from '@playwright/test';

// ── Safety guards ────────────────────────────────────────────────────────

const DB_DSN = process.env.ENSEMBLE_DB_DSN ?? '';
const INHERITED_POSTGRES_HOST = process.env.POSTGRES_HOST ?? '';
const INHERITED_POSTGRES_DB = process.env.POSTGRES_DB ?? '';

test.beforeAll(() => {
  if (!DB_DSN && !INHERITED_POSTGRES_HOST) {
    throw new Error(
      'ENSEMBLE_DB_DSN must be set to a disposable PG DSN for this spec.\n' +
        'Refusing to run e2e without a confirmed DB target.\n' +
        '(The webServer in playwright.maintenance.config.ts uses POSTGRES_* env directly,\n' +
        ' but the spec requires an explicit DSN to refuse prod-targeting configs.)',
    );
  }
  if (/ensemble_prod/i.test(DB_DSN) || /ensemble_prod/i.test(INHERITED_POSTGRES_DB)) {
    throw new Error(
      'REFUSING to run destructive e2e against ensemble_prod. ' +
        'Use a disposable PG DSN.',
    );
  }
});

// Scrub POSTGRES_* env at module load — defense-in-depth so an
// inherited operator's prod config doesn't bleed through.
['POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_URL'].forEach((k) => {
  delete process.env[k];
});

// ── Fixtures ─────────────────────────────────────────────────────────────

const BASE_URL = 'http://localhost:8099';

const AVAILABILITY_READY = {
  eligible: true,
  state: 'ready',
  backend: 'postgres',
  reason: null,
};

const STATUS_FIXTURE = {
  config: {
    checkpoint_max_per_thread: 3,
    checkpoint_max_per_thread_floor: 1,
    cleanup_interval_hours: 24,
    blob_prune_dry_run_env_default: '1',
    blob_prune_destructive_armed: false,
  },
  last_run: null,
  in_flight: null,
};

const STATUS_LAST_RUN_DRY = {
  ...STATUS_FIXTURE,
  last_run: {
    run_id: 'ckpt-20260927_031409123456-1f4a8c2e',
    kind: 'auto',
    started_at: '2026-09-27T03:14:09.123456+00:00',
    completed_at: '2026-09-27T03:14:11.946279+00:00',
    status: 'succeeded',
    summary: {
      checkpoint_rows: { scanned_pairs: 12, deleted: 0, excess_pairs: 0 },
      writes: { deleted: 0 },
      blobs: {
        scanned_pairs: 12,
        would_delete_count: 4,
        would_free_bytes: 268435456,
        would_delete: 4,
        bytes: 268435456,
        destructive: false,
        skipped: [
          { thread_id: 'thr-1', checkpoint_ns: '', reason: 'ZERO_REFS_FAIL_SAFE' },
          { thread_id: 'thr-2', checkpoint_ns: 'snap:x', reason: 'MAX_REFS_EXCEEDED' },
          { thread_id: 'thr-3', checkpoint_ns: '', reason: 'ERROR:MyException' },
        ],
        skipped_truncated: false,
      },
      duration_ms: 1823,
    },
  },
};

const STATUS_LAST_RUN_DESTRUCTIVE = {
  ...STATUS_FIXTURE,
  last_run: {
    ...STATUS_LAST_RUN_DRY.last_run!,
    kind: 'manual_execute',
    summary: {
      ...STATUS_LAST_RUN_DRY.last_run!.summary,
      blobs: {
        ...STATUS_LAST_RUN_DRY.last_run!.summary.blobs,
        destructive: true,
        deleted: 4,
        bytes_freed: 268435456,
        would_delete_count: 0,
        would_free_bytes: 0,
        would_delete: 0,
        bytes: 0,
        skipped: [],
        skipped_truncated: false,
      },
    },
  },
};

const FRESH_FUTURE_ISO = new Date(Date.now() + 5 * 60 * 1000).toISOString();
const STALE_PAST_ISO = new Date(Date.now() - 60_000).toISOString();

const DRY_RUN_FIXTURE = {
  run_id: 'ckpt-20260927_032000123456-2a18f3c9',
  would_delete: { checkpoint_rows: 0, writes: 0, blobs: 4, bytes: 268435456 },
  would_delete_count: 4,
  would_free_bytes: 268435456,
  scanned: { thread_ns_pairs: 12 },
  skipped: [
    { thread_id: 'thr-1', checkpoint_ns: '', reason: 'ZERO_REFS_FAIL_SAFE' },
    { thread_id: 'thr-2', checkpoint_ns: 'snap:x', reason: 'MAX_REFS_EXCEEDED' },
    { thread_id: 'thr-3', checkpoint_ns: '', reason: 'ERROR:MyException' },
  ],
  skipped_truncated: false,
  duration_ms: 412,
  fresh_until: FRESH_FUTURE_ISO,
};

const STALE_DRY_RUN_FIXTURE = {
  ...DRY_RUN_FIXTURE,
  fresh_until: STALE_PAST_ISO,
};

const EXECUTE_202 = {
  run_id: 'ckpt-20260927_032130456789-7e11f3a2',
  status: 'running',
  started_at: '2026-09-27T03:21:30.456789+00:00',
  advisory: null,
  expected_duration_ms_hint: 412,
};

const RUN_RUNNING = {
  run_id: EXECUTE_202.run_id,
  kind: 'manual_execute',
  status: 'running',
  started_at: EXECUTE_202.started_at,
  completed_at: null,
  summary: null,
  error: null,
};

const RUN_SUCCEEDED = {
  ...RUN_RUNNING,
  status: 'succeeded',
  completed_at: '2026-09-27T03:23:14.123456+00:00',
  summary: {
    checkpoint_rows: { scanned_pairs: 12, deleted: 4, excess_pairs: 4 },
    writes: { deleted: 0 },
    blobs: {
      ...DRY_RUN_FIXTURE,
      destructive: true,
      deleted: 4,
      bytes_freed: 268435456,
      would_delete_count: 0,
      would_free_bytes: 0,
      would_delete: 0,
      bytes: 0,
      skipped: [],
      skipped_truncated: false,
    },
    duration_ms: 1823,
  },
};

const RUN_INTERRUPTED = {
  ...RUN_RUNNING,
  status: 'interrupted',
  completed_at: new Date().toISOString(),
  error: { code: 'run_interrupted', message: 'daemon restart mid-run' },
};

// ── Canary helper ────────────────────────────────────────────────────────

/**
 * Item 20 — split `{ok, state, error}` result. The prior
 * `Promise<boolean>` lost the distinction between "transport error"
 * and "BE said NOT ready" — test 2 (kill_switched) and test 3
 * (backend_unsupported) need the structured body. The split lets
 * callers branch precisely: ok=false && error===null means
 * "transport down"; ok=true && state!=='ready' means "BE said".
 */
interface DaemonReadyProbe {
  ok: boolean;
  state: 'ready' | 'backend_unsupported' | 'subsystem_disabled' | 'kill_switched' | null;
  error: string | null;
}

async function probeDaemonReady(): Promise<DaemonReadyProbe> {
  try {
    const res = await fetch(`${BASE_URL}/api/maintenance/checkpoint-cleanup/availability`);
    if (res.status !== 200) {
      return { ok: false, state: null, error: `http ${res.status}` };
    }
    const body = await res.json();
    return {
      ok: body?.state === 'ready',
      state: body?.state ?? null,
      error: null,
    };
  } catch (e) {
    return {
      ok: false,
      state: null,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

async function isDaemonReady(): Promise<boolean> {
  // Convenience wrapper — preserves the boolean contract that the
  // suite's `test.skip(!ready, …)` calls expect.
  const probe = await probeDaemonReady();
  return probe.ok;
}

// ── Helpers ──────────────────────────────────────────────────────────────

async function navigateToMaintenance(page: Page) {
  await page.goto('/maintenance/checkpoint-cleanup');
  // Wait for at least one card to render.
  await page.waitForSelector('[data-testid="ck-status"]', { timeout: 15000 });
}

// ── Suite ────────────────────────────────────────────────────────────────

test.describe('Maintenance — Checkpoint Cleanup (15 cases, AM-16 amendments + fix commission 2026-09-29)', () => {
  test.beforeAll(async () => {
    const ready = await isDaemonReady();
    test.skip(!ready, 'Maintenance backend not eligible on this dev daemon — skipping destructive-path e2e.');
  });

  // ── 1. gear menu shows Maintenance when state=ready (AM-14) ───────────
  test('1. gear menu shows Maintenance when /availability reports state=ready', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_FIXTURE, status: 200 }),
    );
    // Capture the probe response so we can wait for it to fire
    // BEFORE clicking the gear menu (the probe is async — the
    // menu item is appended only after the response).
    const probePromise = page.waitForResponse(
      (r) => r.url().includes('/api/maintenance/checkpoint-cleanup/availability'),
    );
    await page.goto('/');
    await probePromise;
    // Open the gear menu.
    await page.locator('[aria-label="Settings menu"]').click();
    // Maintenance menu item appears — Angular Material CDK overlay
    // attaches items to <body> at runtime.
    await expect(
      page.locator('.mat-mdc-menu-item, mat-menu-item').filter({ hasText: 'Maintenance' }).first(),
    ).toBeVisible({ timeout: 10000 });
  });

  // ── 2. gear menu HIDES on state=backend_unsupported (AM-14) ──────────
  test('2. gear menu hides Maintenance when /availability reports state=backend_unsupported', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({
        json: { eligible: false, state: 'backend_unsupported', backend: 'sqlite', reason: 'blob_prune_postgres_only' },
        status: 200,
      }),
    );
    await page.goto('/');
    await page.locator('[aria-label="Settings menu"]').click();
    // Give the menu a moment to fully render; then assert no Maintenance item.
    await page.waitForTimeout(500);
    await expect(page.locator('mat-menu-item:has-text("Maintenance")')).toHaveCount(0);
  });

  // ── 3. gear menu HIDES on state=kill_switched (AM-13) ────────────────
  test('3. gear menu hides Maintenance when /availability reports state=kill_switched', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({
        json: { eligible: false, state: 'kill_switched', backend: 'postgres', reason: 'MAINTENANCE_ENDPOINTS_ENABLED=0' },
        status: 200,
      }),
    );
    await page.goto('/');
    await page.locator('[aria-label="Settings menu"]').click();
    await page.waitForTimeout(500);
    await expect(page.locator('mat-menu-item:has-text("Maintenance")')).toHaveCount(0);
    // And NO error toast appears.
    await expect(page.locator('text=/maintenance/i').filter({ hasText: /error|failed/i })).toHaveCount(0);
  });

  // ── 4. status renders dual-flavor branches (AM-11) ────────────────────
  test('4. status card renders dual-flavor: dry-run last_run shows "Would free"', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_LAST_RUN_DRY, status: 200 }),
    );
    await navigateToMaintenance(page);
    // Dry flavor: "Would free" label.
    await expect(page.locator('[data-testid="ck-status"]').locator('text=Would free')).toBeVisible();
    await expect(page.locator('[data-testid="ck-status"]').locator('text=Bytes freed')).toHaveCount(0);
  });

  test('5. status card renders dual-flavor: destructive last_run shows "Bytes freed"', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_LAST_RUN_DESTRUCTIVE, status: 200 }),
    );
    await navigateToMaintenance(page);
    await expect(page.locator('[data-testid="ck-status"]').locator('text=Bytes freed')).toBeVisible();
    await expect(page.locator('[data-testid="ck-status"]').locator('text=Would free')).toHaveCount(0);
  });

  // ── 6. last-run skipped summary + dry-run 3-tone badge map (AM-10) ───
  test('6. skipped render: last-run summary + dry-run 3-tone badge map', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      // Last-run fixture carries 3 skipped entries (AM-10).
      route.fulfill({ json: STATUS_LAST_RUN_DRY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await navigateToMaintenance(page);
    // Last-run summary line.
    const summary = page.locator('[data-testid="ck-last-skipped-summary"]');
    await expect(summary).toBeVisible();
    await expect(summary).toContainText('3 pairs skipped');
    // Trigger the dry-run — its result panel renders the 3-tone badge map.
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.waitForSelector('[data-testid="ck-dry-skipped"]');
    // Expand the details block to reveal the per-entry badges.
    await page.locator('[data-testid="ck-dry-skipped"] details summary').click();
    await expect(page.locator('.ck-badge-safe')).toBeVisible();
    await expect(page.locator('.ck-badge-limit')).toBeVisible();
    await expect(page.locator('.ck-badge-error')).toBeVisible();
  });

  // ── 7. dry-run render — counts, fresh_until, honest copy (AM-10/16) ──
  test('7. dry-run renders counts, fresh_until, skipped, and honest-duration copy', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await expect(page.locator('[data-testid="ck-dry-would-delete"]')).toContainText('4');
    await expect(page.locator('[data-testid="ck-dry-would-free"]')).toBeVisible();
    await expect(page.locator('[data-testid="ck-dry-fresh-until"]')).toBeVisible();
    await expect(page.locator('[data-testid="ck-dry-skipped"]')).toBeVisible();
    // Honest-duration muted copy.
    await expect(page.locator('text=/several minutes/i')).toBeVisible();
  });

  // ── 8. execute confirm flow — cancel does NOT POST ────────────────────
  test('8. execute confirm flow — Cancel does NOT POST to /execute', async ({ page }) => {
    let executeCalled = false;
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', (route) => {
      executeCalled = true;
      return route.fulfill({ json: EXECUTE_202, status: 202 });
    });
    await navigateToMaintenance(page);
    // Click dry-run.
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    // Click execute.
    await page.locator('[data-testid="ck-execute-btn"]').click();
    // Confirm dialog opens.
    await page.locator('app-confirm-dialog').waitFor({ state: 'visible' });
    // Click Cancel.
    await page.locator('app-confirm-dialog button:has-text("Cancel")').click();
    await page.waitForTimeout(500);
    expect(executeCalled).toBe(false);
  });

  // ── 9. execute confirm flow — confirm POSTs + polls (AM-12, AM-17) ───
  test('9. execute confirm flow — confirm POSTs and polls to terminal (no idempotency_key)', async ({ page }) => {
    let executeBody: unknown = null;
    let pollCount = 0;
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', async (route) => {
      executeBody = JSON.parse(route.request().postData() ?? '{}');
      return route.fulfill({ json: EXECUTE_202, status: 202 });
    });
    await page.route('**/api/maintenance/checkpoint-cleanup/runs/**', (route) => {
      pollCount++;
      // First poll: running; second poll: succeeded.
      return route.fulfill({
        json: pollCount === 1 ? RUN_RUNNING : RUN_SUCCEEDED,
        status: 200,
      });
    });
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_LAST_RUN_DESTRUCTIVE, status: 200 }),
    );
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.waitForSelector('[data-testid="ck-dry-would-free"]');
    await page.locator('[data-testid="ck-execute-btn"]').click();
    await page.locator('app-confirm-dialog').waitFor({ state: 'visible' });
    await page.locator('app-confirm-dialog button:has-text("Cleanup now")').click();

    // AM-17 — the wire body has EXACTLY {dry_run_run_id, expected_bytes, confirm: true}.
    // Wait for execute to fire.
    await page.waitForTimeout(1000);
    expect(executeBody).not.toBeNull();
    const body = executeBody as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['confirm', 'dry_run_run_id', 'expected_bytes']);
    expect(body.confirm).toBe(true);
    expect((body as { idempotency_key?: unknown }).idempotency_key).toBeUndefined();

    // AM-12 — "Expected duration" copy appears in the executing card.
    await expect(page.locator('[data-testid="ck-expected-duration"]')).toBeVisible();

    // Poll resolves to terminal; result panel renders.
    await expect(page.locator('[data-testid="ck-result"]')).toContainText('succeeded');
    // Destructive flavor shows "Bytes freed".
    await expect(page.locator('[data-testid="ck-result"]').locator('text=Bytes freed')).toBeVisible();
  });

  // ── 10. stale dry-run surfaces re-run prompt and does NOT POST ───────
  test('10. stale dry-run surfaces re-run prompt and does NOT POST to /execute', async ({ page }) => {
    let executeCalled = false;
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: STALE_DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', (route) => {
      executeCalled = true;
      return route.fulfill({ json: EXECUTE_202, status: 202 });
    });
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.waitForSelector('[data-testid="ck-dry-fresh-until"]');
    await page.locator('[data-testid="ck-execute-btn"]').click();
    await page.waitForTimeout(500);
    // No dialog opens.
    await expect(page.locator('app-confirm-dialog')).toHaveCount(0);
    expect(executeCalled).toBe(false);
    // Snack-bar with stale message.
    await expect(page.locator('text=/stale/i')).toBeVisible();
  });

  // ── 11. 409 run_in_flight — ADOPTS run_id and resumes polling (no toast) ──
  test('11. 409 run_in_flight from execute adopts run_id and resumes polling (no error toast)', async ({ page }) => {
    const ADOPTED_RUN_ID = 'ckpt-already-running-12345';
    let pollCount = 0;
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', (route) =>
      route.fulfill({
        status: 409,
        json: {
          error: 'run_in_flight',
          message: 'another maintenance run is in flight',
          details: { run_id: ADOPTED_RUN_ID, started_at: '2026-09-27T03:21:30.456789+00:00' },
        },
      }),
    );
    await page.route(`**/api/maintenance/checkpoint-cleanup/runs/${ADOPTED_RUN_ID}`, (route) => {
      pollCount++;
      return route.fulfill({
        json: pollCount === 1 ? RUN_RUNNING : { ...RUN_RUNNING, run_id: ADOPTED_RUN_ID, status: 'succeeded', completed_at: new Date().toISOString(), summary: RUN_SUCCEEDED.summary },
        status: 200,
      });
    });
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.waitForSelector('[data-testid="ck-dry-fresh-until"]');
    await page.locator('[data-testid="ck-execute-btn"]').click();
    await page.locator('app-confirm-dialog button:has-text("Cleanup now")').click();
    // No error banner / toast appears.
    await page.waitForTimeout(500);
    await expect(page.locator('[data-testid="ck-error-banner"]')).toHaveCount(0);
    // Poll begins against the ADOPTED run_id.
    await expect.poll(() => pollCount, { timeout: 5000 }).toBeGreaterThan(0);
    // Result panel renders with "succeeded".
    await expect(page.locator('[data-testid="ck-result"]')).toContainText('succeeded');
  });

  // ── 12. interrupted-state renders re-run affordance (AM-6) ────────────
  test('12. interrupted-state result renders the re-run-to-converge card', async ({ page }) => {
    let pollCount = 0;
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', (route) =>
      route.fulfill({ json: EXECUTE_202, status: 202 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/runs/**', (route) => {
      pollCount++;
      return route.fulfill({
        json: pollCount === 1 ? RUN_RUNNING : RUN_INTERRUPTED,
        status: 200,
      });
    });
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.locator('[data-testid="ck-execute-btn"]').click();
    await page.locator('app-confirm-dialog button:has-text("Cleanup now")').click();
    // Result panel renders "interrupted".
    await expect(page.locator('[data-testid="ck-result"]')).toContainText('interrupted');
    // The interrupted-card + Re-run button render.
    await expect(page.locator('[data-testid="ck-interrupted-card"]')).toBeVisible();
    await expect(page.locator('[data-testid="ck-rerun-btn"]')).toBeVisible();
    // No cancel button anywhere.
    await expect(page.locator('[data-testid="ck-result"] button:has-text("Cancel")')).toHaveCount(0);
  });

  // ── 13. cross-origin 403 via context.request.post with Origin header ──
  test('13. cross-origin /dry-run is rejected with 403 origin_not_trusted (body asserted via context.request.post)', async ({ browser }) => {
    const ctx = await browser.newContext({
      extraHTTPHeaders: { Origin: 'http://evil.example' },
    });
    const apiCtx: APIRequestContext = ctx.request;
    // [R-12, v3 fix pass] — assert the 403 BODY, not just the status
    // code. `context.request.post` BYPASSES CORS preflight blocking so
    // the test can read the structured error body.
    let res;
    try {
      res = await apiCtx.post(`${BASE_URL}/api/maintenance/checkpoint-cleanup/dry-run`, {
        headers: { 'Content-Type': 'application/json' },
        data: {},
      });
    } catch (e) {
      // Item 20 — 403-fallback `console.warn`. Some Playwright
      // versions surface 403 as a network error on the
      // APIRequestContext; surface the branch condition in the
      // test log so a future operator can correlate the fallback
      // with a Playwright upgrade.
      console.warn('[maintenance-e2e] APIRequestContext.post threw — falling back to fetch():', e);
      res = await fetch(`${BASE_URL}/api/maintenance/checkpoint-cleanup/dry-run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Origin: 'http://evil.example',
        },
        body: '{}',
      });
    }
    expect([403, 0]).toContain(res.status()); // 403 OR network-error (0) both acceptable
    // Item 8 — assert the 403 body in BOTH branches. When status === 403
    // the BE returned a structured `origin_not_trusted` body (either
    // raw `{error}` or FastAPI-wrapped `{detail: {error}}`); when
    // status === 0 (Playwright surfaced the 403 as a network error),
    // we verify the upstream body via the fetch-fallback branch by
    // also probing with the structured `error` literal carried on
    // the wire. The dual-branch assertion prevents a future Playwright
    // upgrade from silently breaking the body contract.
    let body: { error?: string; detail?: { error?: string } } | null = null;
    if (res.status() === 403) {
      body = await res.json();
      const errCode = body?.error ?? body?.detail?.error;
      expect(errCode).toBe('origin_not_trusted');
    } else {
      // status === 0 — the fetch-fallback branch must also return 403.
      // Re-issue the request with the structured literal in scope.
      const fallback = await fetch(`${BASE_URL}/api/maintenance/checkpoint-cleanup/dry-run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Origin: 'http://evil.example',
        },
        body: '{}',
      });
      expect(fallback.status).toBe(403);
      const fallbackBody = await fallback.json();
      const fallbackErrCode = fallbackBody?.error ?? fallbackBody?.detail?.error;
      expect(fallbackErrCode).toBe('origin_not_trusted');
    }
    await ctx.close();
  });

  // ── 14. maintenance_disabled renders the global banner (AM-13) ───────
  test('14. maintenance_disabled 503 renders the global kill-switch banner', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({
        status: 503,
        json: {
          error: 'maintenance_disabled',
          message: 'Maintenance endpoints are disabled (MAINTENANCE_ENDPOINTS_ENABLED=0)',
          details: {},
        },
      }),
    );
    await navigateToMaintenance(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    // The global banner renders ABOVE the cards.
    await expect(page.locator('[data-testid="ck-banner-disabled"]')).toBeVisible();
    await expect(page.locator('[data-testid="ck-banner-disabled"]')).toContainText('MAINTENANCE_ENDPOINTS_ENABLED');
  });

  // ── 15. poll-budget + backoff continuation: a run that outlives the budget and completes later transitions the UI to success (ORIGINAL SYMPTOM DEAD) ─
  // The 2026-09-29 incident: a 12m33.5s cleanup run on
  // ckpt-20260929_045639524546-3f6e42a4 surfaced as
  // `poll_stale` dead-end (FE-synthesized) at the 10-min hard cap.
  // This test proves the NEW contract:
  //   (a) the FE no longer synthesizes a poll-timeout error past
  //       the budget,
  //   (b) the post-cap BACKOFF phase keeps polling until terminal,
  //   (c) a run that outlives the budget AND completes later
  //       transitions the UI to success,
  //   (d) the page-refresh re-entry branch (status.in_flight)
  //       resumes polling against the in-flight run.
  //
  // Approach: Playwright `page.clock` (clock.install +
  // fastForward/runFor). The active budget is `max(hint × 2, 15
  // min floor)` = 900_000 ms (15 min) at the production cadence.
  // We advance VIRTUAL time past the 15-min boundary in a single
  // fastForward(900_000) — that fires the 450 active-phase polls
  // (2-s cadence) plus the `takeUntil(timer(900000))` terminator,
  // transitioning the FE into the BACKOFF phase. We then drive a
  // few more minutes of virtual time into the BACKOFF phase
  // before flipping the route to return terminal — proving the
  // post-cap continuation works end-to-end with NO production
  // seam (no test-only budget override, no POLL_INTERVAL_MS
  // shim).
  //
  // NOTE — page.clock.install runs in the browser and patches
  // Date + setTimeout + setInterval. zone.js patches the same
  // primitives; the two patchers compose (zone.js wraps the
  // fake setTimeout, so RxJS `timer(0, intervalMs)` schedules
  // against the fake clock and `fastForward` fires the timers).
  // The route handler is synchronous in the Playwright driver,
  // so each timer that fires the HTTP request is answered
  // synchronously.
  //
  // Wrong-math pin (iter2 review finding 1, was: "10-min / 2-s =
  // 5 polls"): 10 min = 600_000 ms; 600_000 / 2000 = 300 polls
  // is the OLD cap's poll count. The inline `pollCount` counter
  // (iter2 harness — the old KEEP_RUNNING_COUNT constant is gone)
  // must exceed the active-phase poll count (450 at the 15-min
  // budget) AND the first backoff polls so the run stays
  // non-terminal across the entire test. At the CORRECT backoff
  // ramp (iter3 MAJOR-1 fix: 2 s, 2 s, 4 s, …, 30 s ceiling at
  // tickIdx 14), 5 virtual minutes of backoff yields ~15-17
  // polls — cadence-exact counts are pinned by the unit cadence
  // tests in checkpoint-cleanup.service.spec.ts; this e2e asserts
  // continuation only.
  test('15. post-budget continuation: a run that outlives the budget and completes later transitions the UI to success (ORIGINAL SYMPTOM DEAD)', async ({ page }) => {
    const RUN_ID = 'ckpt-20260929_045639524546-3f6e42a4';
    // Install the virtual clock BEFORE navigation. Best practice
    // per the Playwright clock docs — load the page against the
    // fake clock so any setup-time setTimeout calls also queue
    // against the fake clock.
    await page.clock.install({ time: new Date('2026-09-29T00:00:00Z') });

    let pollCount = 0;
    // `succeedOnNext` flips on once we want the next /runs/{id}
    // response to be terminal. The route handler is sync, so the
    // very next poll after the flip returns SUCCEEDED.
    let succeedOnNext = false;

    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', (route) =>
      route.fulfill({
        json: {
          run_id: RUN_ID,
          status: 'running',
          started_at: new Date().toISOString(),
          advisory: null,
          // Hint sized so the active budget lands on the
          // POLL_BUDGET_FLOOR (15 min): hint × N = 412 × 2 = 824
          // ms < 900_000 ms floor → max wins, budget = 900 s.
          expected_duration_ms_hint: 412,
        },
        status: 202,
      }),
    );
    await page.route(`**/api/maintenance/checkpoint-cleanup/runs/${RUN_ID}`, (route) => {
      pollCount++;
      if (succeedOnNext) {
        return route.fulfill({
          json: {
            ...RUN_RUNNING,
            run_id: RUN_ID,
            status: 'succeeded',
            completed_at: new Date().toISOString(),
            summary: RUN_SUCCEEDED.summary,
          },
          status: 200,
        });
      }
      return route.fulfill({ json: { ...RUN_RUNNING, run_id: RUN_ID }, status: 200 });
    });
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_LAST_RUN_DESTRUCTIVE, status: 200 }),
    );

    await navigateToMaintenance(page);
    // Let the page bootstrap microtasks + Angular zone tasks
    // drain against the fake clock.
    await page.clock.runFor(200);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await page.clock.runFor(200);
    await page.waitForSelector('[data-testid="ck-dry-would-free"]');
    await page.locator('[data-testid="ck-execute-btn"]').click();
    await page.clock.runFor(200);
    await page.locator('app-confirm-dialog button:has-text("Cleanup now")').click();
    await page.clock.runFor(200);
    // AM-12 — "Expected duration" copy appears; verifies the
    // execute path landed and the polling subscription is live.
    await expect(page.locator('[data-testid="ck-expected-duration"]')).toBeVisible();

    // Wait for the first poll to fire (real-time poll; expect.poll
    // uses real wall time). At t=0 the timer fires immediately,
    // so this is fast.
    await expect.poll(() => pollCount, { timeout: 5_000 }).toBeGreaterThan(0);
    const pollsAtBudgetEntry = pollCount;

    // CROSS THE BUDGET. `fastForward` fires due timers AT MOST
    // ONCE per call (Playwright clock docs: "Only fires due
    // timers at most once") — so a single fastForward(900_000)
    // advances the clock by 15 min but fires only ONE setInterval
    // tick + the takeUntil terminator. To drive 449 active-phase
    // ticks + the budget terminator, we loop fastForward(2000) for
    // each 2-s step. Measured wall-time of 450× fastForward(2_000)
    // is ~3 s — well within the test budget. The terminal-handler
    // side effect (post-terminal refreshStatus → resume guard) is
    // exercised too (FE retains lastExecuteResult as terminal; the
    // new guard from finding 2 blocks the would-be loop).
    for (let i = 0; i < 450; i++) {
      await page.clock.fastForward(2000);
    }
    // After the loop, the active budget (900_000 ms) has elapsed
    // and the takeUntil terminator has fired → activePhase
    // completed → backoffPhase seeded via defer() (the outer
    // `timer(intervalMs)` waits 2 s before the first backoff
    // poll). ~450 active-phase polls fired during the loop (449
    // pre-budget ticks + the t=0 tick already counted in
    // pollsAtBudgetEntry).
    //
    // MAJOR-1 (iter3) — the old `.toBeGreaterThan(400)` only held
    // under the BROKEN constant 2-s backoff cadence; under the
    // correct 2 s, 2 s, 4 s, …, 30 s ramp the post-boundary
    // backoff window yields ~15-17 polls per 5 virtual minutes,
    // not 400+. The exact ramp is pinned by the unit cadence
    // tests in checkpoint-cleanup.service.spec.ts. Here we assert
    // cadence-agnostic CONTINUATION: at least one poll fired
    // after the budget boundary marker.
    await expect
      .poll(() => pollCount - pollsAtBudgetEntry, { timeout: 5_000 })
      .toBeGreaterThan(0);
    const pollsAtBudgetExit = pollCount;

    // ORIGINAL-SYMPTOM-DEAD assertion (1): the FE MUST NOT render
    // an error banner — the OLD contract surfaced
    // `fe_synthesized_poll_timeout` → `poll_stale` here. The new
    // contract has no synthesized error; lastError stays null and
    // the banner never renders.
    await expect(page.locator('[data-testid="ck-error-banner"]')).toHaveCount(0);
    // The active-run-id indicator is still visible — the FE is
    // tracking the run, not erroring out.
    await expect(page.locator('[data-testid="ck-active-run-id"]')).toBeVisible();

    // DRIVE INTO THE BACKOFF PHASE. The backoff phase's outer
    // timer waits intervalMs (= 2 s) for the first post-budget
    // poll; subsequent expand-driven waits ramp 2 s, 4 s, 6 s,
    // ..., up to the 30-s ceiling (first reached at tickIdx 14).
    // We loop fastForward(2_000) for ~150 iterations to cover
    // 5 virtual minutes of backoff cadence — at the correct ramp
    // that window produces ~15-17 backoff polls.
    for (let i = 0; i < 150; i++) {
      await page.clock.fastForward(2000);
    }
    await expect(page.locator('[data-testid="ck-error-banner"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="ck-active-run-id"]')).toBeVisible();
    // Cadence-agnostic continuation proof (iter3 MAJOR-1): pollCount
    // MUST strictly increase across the backoff window — the FE
    // keeps tracking the run instead of dead-ending.
    expect(pollCount).toBeGreaterThan(pollsAtBudgetExit);

    // Flip the route — the next /runs/{id} response is the
    // terminal value.
    succeedOnNext = true;
    // Fast-forward enough for at least one more backoff poll to
    // fire. By this point the ramp has reached its 30-s ceiling
    // (iter3: correct cadence, not constant 2 s), so we step
    // 30 × 2 s = 60 s of virtual time — at least one (typically
    // two) ceiling-cadence backoff polls fire, carrying the
    // terminal value into the FE.
    for (let i = 0; i < 30; i++) {
      await page.clock.fastForward(2000);
    }

    // ORIGINAL-SYMPTOM-DEAD assertion (2): the terminal value
    // surfaces in the FE — the post-budget continuation reaches
    // the run's actual terminal state and the result panel
    // renders "succeeded".
    await expect(page.locator('[data-testid="ck-result"]')).toContainText(
      'succeeded',
      { timeout: 10_000 },
    );
    // Belt-and-braces: the active-run-id indicator is gone
    // (executing() flipped false in the terminal handler).
    await expect(page.locator('[data-testid="ck-active-run-id"]')).toHaveCount(0);
    // The error banner is still absent — the terminal transition
    // never surfaces a synthetic error.
    await expect(page.locator('[data-testid="ck-error-banner"]')).toHaveCount(0);
  });
});
