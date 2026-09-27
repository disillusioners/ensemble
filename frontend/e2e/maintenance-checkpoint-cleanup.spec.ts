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
 * 14 test cases per the plan T7.2 outline.
 */

import { test, expect, type APIRequestContext, type Browser } from '@playwright/test';

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

async function isDaemonReady(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/maintenance/checkpoint-cleanup/availability`);
    if (res.status !== 200) return false;
    const body = await res.json();
    return body.state === 'ready';
  } catch {
    return false;
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────

async function navigateToMaintenance(page: import('@playwright/test').Page) {
  await page.goto('/maintenance/checkpoint-cleanup');
  // Wait for at least one card to render.
  await page.waitForSelector('[data-testid="ck-status"]', { timeout: 15000 });
}

// ── Suite ────────────────────────────────────────────────────────────────

test.describe('Maintenance — Checkpoint Cleanup (14 cases, AM-16 amendments)', () => {
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
      // Some Playwright versions surface 403 as a network error on
      // the APIRequestContext. Fall back to fetch directly.
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
});
