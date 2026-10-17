/**
 * vacuum-ungate 31bedca78 — focused e2e: zero-reclaimable dry-run must
 * keep the Step-2 Continue ENABLED and reach Step 3 (Confirm & Execute).
 *
 * Background: `canContinueFromStep2()` previously required
 * `dry.would_delete_count > 0`, so a dry-run reporting nothing
 * prune-eligible dead-ended the wizard at Step 2. Commit 31bedca78
 * removed that condition — VACUUM FULL still rewrites/compacts tables
 * and returns space even with zero prune-eligible rows, so the
 * operator must always be able to advance to the confirmation step.
 * The BE execute path already accepts echo-of-0 (AM-3 byte-count
 * match: 0 == 0; pinned by the pre-existing
 * `test_execute_zero_refs_no_mass_delete` integration test), so the
 * gate was frontend-only.
 *
 * DISCOVERY (2026-10-17): NO leg in
 * `maintenance-checkpoint-cleanup-wizard.spec.ts` (w1–w7) or
 * `maintenance-checkpoint-cleanup.spec.ts` covers the
 * zero-reclaimable → Continue → Step 3 path — every existing leg
 * runs a non-zero DRY_RUN_FIXTURE. This file closes that gap:
 *
 *   w8. (depth-1, THE core regression assertion) intercept the
 *       dry-run endpoint with a ZERO fixture → walk Step 1 → run
 *       dry-run → Step 2 renders the ZERO reclaimable display
 *       UNCHANGED (0 rows / 0 B) → `ck-continue-btn` is ENABLED →
 *       click → Step 3 confirmation UI (`ck-execute-btn`) visible.
 *   w9. (depth-2) continue from Step 3 with the VACUUM FULL control
 *       ticked (default) → confirm → execute POST captured with
 *       `expected_bytes: 0` (the echo-of-0 wire proof) → poll
 *       resolves → Result panel renders "Run completed" — the full
 *       zero-reclaimable journey completes without error.
 *
 * Fixture/route/page-object patterns are cloned from
 * `maintenance-checkpoint-cleanup-wizard.spec.ts` (w5/w6 shapes).
 * No product code is touched by this spec; run it focused:
 *
 *   cd frontend && npx playwright test \
 *     --config playwright.maintenance.config.ts \
 *     --project maintenance -g "vacuum-ungate"
 *
 * Port note: this suite runs against the DEDICATED e2e harness —
 * daemon :8099 + FE dev-server :4299 + disposable PG :15432
 * (NOT the dev daemon :8079 / dev FE :4199).
 */

import { test, expect, type Page } from '@playwright/test';

// ── Safety guards (mirror the wizard spec — verbatim pattern) ────────────

const DB_DSN = process.env.ENSEMBLE_DB_DSN ?? '';
const INHERITED_POSTGRES_HOST = process.env.POSTGRES_HOST ?? '';
const INHERITED_POSTGRES_DB = process.env.POSTGRES_DB ?? '';

test.beforeAll(() => {
  if (!DB_DSN && !INHERITED_POSTGRES_HOST) {
    throw new Error(
      'ENSEMBLE_DB_DSN must be set to a disposable PG DSN for this spec.\n' +
        'Refusing to run e2e without a confirmed DB target.',
    );
  }
  if (/ensemble_prod/i.test(DB_DSN) || /ensemble_prod/i.test(INHERITED_POSTGRES_DB)) {
    throw new Error(
      'REFUSING to run destructive e2e against ensemble_prod. ' +
        'Use a disposable PG DSN.',
    );
  }
});

['POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_URL'].forEach((k) => {
  delete process.env[k];
});

// ── Fixtures (cloned from the wizard spec; ZERO dry-run variant) ─────────

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
    blob_prune_destructive_default: true,
  },
  last_run: null,
  in_flight: null,
};

const FRESH_FUTURE_ISO = new Date(Date.now() + 5 * 60 * 1000).toISOString();

/**
 * THE fixture under test — the wizard spec's DRY_RUN_FIXTURE shape
 * with every reclaimable field zeroed (would_delete_count: 0,
 * would_delete.blobs: 0, would_delete.bytes: 0, would_free_bytes: 0).
 * Pre-31bedca78 this response disabled the Step-2 Continue; post-
 * ungate it must not.
 */
const ZERO_DRY_RUN_FIXTURE = {
  run_id: 'ckpt-20261017_000100000000-bbb00001',
  would_delete: { checkpoint_rows: 0, writes: 0, blobs: 0, bytes: 0 },
  would_delete_count: 0,
  would_free_bytes: 0,
  scanned: { thread_ns_pairs: 12 },
  skipped: [],
  skipped_truncated: false,
  duration_ms: 380,
  fresh_until: FRESH_FUTURE_ISO,
};

const EXECUTE_202 = {
  run_id: 'ckpt-20261017_000200000000-bbb00002',
  status: 'running',
  started_at: '2026-10-17T00:02:00.000000+00:00',
  advisory: null,
  expected_duration_ms_hint: 380,
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

/** Zero-reclaimable success — nothing deleted, nothing freed, no error. */
const RUN_SUCCEEDED_ZERO = {
  ...RUN_RUNNING,
  status: 'succeeded',
  completed_at: '2026-10-17T00:02:11.000000+00:00',
  summary: {
    checkpoint_rows: { scanned_pairs: 12, deleted: 0, excess_pairs: 0 },
    writes: { deleted: 0 },
    blobs: {
      scanned_pairs: 12,
      deleted: 0,
      bytes_freed: 0,
      destructive: true,
      would_delete_count: 0,
      would_free_bytes: 0,
      would_delete: 0,
      bytes: 0,
      skipped: [],
      skipped_truncated: false,
    },
    duration_ms: 1210,
  },
};

// ── Helpers (cloned from the wizard spec) ────────────────────────────────

async function isDaemonReady(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/maintenance/checkpoint-cleanup/availability`);
    if (res.status !== 200) return false;
    const body = await res.json();
    return body?.state === 'ready';
  } catch {
    return false;
  }
}

async function navigateToMaintenance(page: Page) {
  await page.goto('/maintenance/checkpoint-cleanup');
  await page.waitForSelector('[data-testid="ck-status"]', { timeout: 15000 });
}

async function advanceToStep2(page: Page) {
  await page.locator('[data-testid="ck-continue-btn"]').first().click();
  await page.locator('[data-testid="ck-dry-run-btn"]').waitFor({ state: 'visible', timeout: 5000 });
}

async function advanceToStep3(page: Page) {
  // Footer Continue is the single sanctioned Step 2 → Step 3 nav path
  // (header clicks are inert under the pointer-events fence — see the
  // wiznav-fix rationale in the wizard spec).
  await page.locator('[data-testid="ck-continue-btn"]').first().click();
  await page.locator('[data-testid="ck-execute-btn"]').waitFor({ state: 'visible', timeout: 5000 });
}

// ── Suite ────────────────────────────────────────────────────────────────

test.describe('Maintenance — vacuum-ungate (zero-reclaimable dry-run keeps Continue enabled)', () => {
  test.beforeAll(async () => {
    const ready = await isDaemonReady();
    test.skip(!ready, 'Maintenance backend not eligible on this dev daemon — skipping vacuum-ungate e2e.');
  });

  // ── w8 (depth-1) — THE core regression assertion ───────────────────────
  test('w8: vacuum-ungate — zero-reclaimable dry-run keeps Continue enabled and reaches Step 3', async ({ page }) => {
    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: ZERO_DRY_RUN_FIXTURE, status: 200 }),
    );

    await page.setViewportSize({ width: 1024, height: 768 });
    await navigateToMaintenance(page);

    // Step 1 → Step 2 via the dry-run path.
    await advanceToStep2(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();

    // (b) Step 2 renders the ZERO reclaimable display — the display is
    // unchanged by the ungate; the values are still shown, as zeros.
    // formatBytes(0) → "0 B" (binary formatter, 0 < 1024 → "0 B").
    const wouldDelete = page.locator('[data-testid="ck-dry-would-delete"]').first();
    const wouldFree = page.locator('[data-testid="ck-dry-would-free"]').first();
    await expect(wouldDelete).toBeVisible();
    await expect(wouldFree).toBeVisible();
    await expect(wouldDelete).toHaveText('0');
    await expect(wouldFree).toHaveText('0 B');

    // (c) THE core regression assertion — Continue (ck-continue-btn)
    // must be ENABLED at Step 2 with a zero-reclaimable dry-run on
    // record. Pre-31bedca78, canContinueFromStep2() returned false and
    // this button rendered disabled.
    const continueBtn = page.locator('[data-testid="ck-continue-btn"]').first();
    await expect(continueBtn).toBeVisible();
    await expect(continueBtn).toBeEnabled();

    // (d) Click Continue → Step 3 confirmation UI is visible.
    await advanceToStep3(page);
    await expect(page.locator('[data-testid="ck-execute-btn"]').first()).toBeVisible();
  });

  // ── w9 (depth-2) — zero-reclaimable execute completes without error ────
  test('w9: vacuum-ungate — zero-reclaimable run executes (echo-of-0) and completes without error', async ({ page }) => {
    let executeBody: Record<string, unknown> | null = null;
    let pollCount = 0;

    await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
      route.fulfill({ json: AVAILABILITY_READY, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/status', (route) =>
      route.fulfill({ json: STATUS_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/dry-run', (route) =>
      route.fulfill({ json: ZERO_DRY_RUN_FIXTURE, status: 200 }),
    );
    await page.route('**/api/maintenance/checkpoint-cleanup/execute', async (route) => {
      const raw = route.request().postData() ?? '{}';
      executeBody = JSON.parse(raw) as Record<string, unknown>;
      return route.fulfill({ json: EXECUTE_202, status: 202 });
    });
    await page.route('**/api/maintenance/checkpoint-cleanup/runs/**', (route) => {
      pollCount++;
      return route.fulfill({
        json: pollCount === 1 ? RUN_RUNNING : RUN_SUCCEEDED_ZERO,
        status: 200,
      });
    });

    await page.setViewportSize({ width: 1024, height: 768 });
    await navigateToMaintenance(page);

    // Walk Step 1 → dry-run → Step 2 → Continue → Step 3.
    await advanceToStep2(page);
    await page.locator('[data-testid="ck-dry-run-btn"]').click();
    await expect(page.locator('[data-testid="ck-dry-would-delete"]').first()).toHaveText('0');
    await advanceToStep3(page);

    // VACUUM FULL control is ticked by default (§D-7.3 always-ticked
    // contract — the reason zero-reclaimable runs remain meaningful:
    // FULL still rewrites/compacts the tables).
    const checkboxInput = page
      .locator('[data-testid="ck-vacuum-full-checkbox"] input[type="checkbox"]')
      .first();
    await expect(checkboxInput).toBeChecked();

    // Confirm → execute → poll resolves → completion WITHOUT error.
    await page.locator('[data-testid="ck-execute-btn"]').first().click();
    await page.locator('app-confirm-dialog').waitFor({ state: 'visible' });
    await page.locator('app-confirm-dialog button:has-text("Cleanup now")').click();
    await expect(page.locator('[data-testid="ck-result"]')).toContainText('Run completed');
    await expect(page.locator('.ck-result-banner-completed')).toBeVisible();

    // Wire proof — the execute POST carried the ZERO echo
    // (expected_bytes: 0 == would_free_bytes: 0; the AM-3 byte-count
    // match the BE accepts) with vacuum_full: true.
    expect(executeBody).not.toBeNull();
    expect(executeBody!.confirm).toBe(true);
    expect(executeBody!.dry_run_run_id).toBe(ZERO_DRY_RUN_FIXTURE.run_id);
    expect(executeBody!.expected_bytes).toBe(0);
    expect(executeBody!.vacuum_full).toBe(true);
  });
});
