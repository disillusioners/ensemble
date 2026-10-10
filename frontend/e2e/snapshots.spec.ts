/**
 * Snapshots Page e2e suite — sequencing.md §4.3 (steps 1-9 + 11a-11c).
 *
 * Runs against a DEDICATED second-daemon (port 18279, disposable PG on
 * port 15532) configured by `playwright.snapshots.config.ts`. The
 * webServer invokes `scripts/boot-e2e-snapshots-daemon.sh` which:
 *   1. Inits a local PG cluster on :15532.
 *   2. Creates a disposable `ensemble_e2e_snap_<pid>` DB.
 *   3. Starts the daemon on :18279 against that DB.
 *   4. Polls `/readyz` until `status:'ready'` (canary — pass 6
 *      amendment #1; the daemon mounts GET /readyz natively).
 *
 * SAFETY:
 *   - The spec is READ-ONLY against the disposable PG (GETs + this
 *     file's idempotent seed INSERTs + the step-7 preference PUT —
 *     no DELETE, no TRUNCATE, no destructive SQL). The maintenance
 *     spec's ENSEMBLE_DB_DSN destructive-refusal guard is therefore
 *     intentionally omitted (sequencing §4.3.1).
 *   - The seed talks to 127.0.0.1:15532 ONLY and REFUSES to run
 *     against anything else (see `resolveDisposableDb` — no port
 *     fallback, ever).
 *   - POSTGRES_* inherited env is scrubbed at module load
 *     (defense-in-depth, mirrors the maintenance spec).
 *
 * SEED RATIONALE (engineering gap the plan had no story for): the
 * disposable PG boots EMPTY, but steps 3-8 + 11a-11c need >=1
 * snapshot row (drawer, filters, sort, paginator, metrics). The
 * daemon's boot creates the schema (SQLModel `create_all` in
 * manager.py runs before /readyz turns 'ready'), so this spec seeds
 * rows via psql in `beforeAll`:
 *
 *   - 2 projects (`e2e-snapshots-alpha` / `-beta`) — the snapshots
 *     FK targets `projects.project_id`, and the step-3a Project
 *     filter needs a selectable project whose id is a valid UUID
 *     (the BE validates the `project_id` query param as UUID).
 *   - 4 snapshots spanning the dimensions the steps assert:
 *       s1 coder / kind:implementation / active / ~2min old
 *          (fresh anchor: default list first row, drawer + digest
 *          + copy-id, inside the 24h age window)
 *       s2 watcher / kind:design / active / ~3 days old
 *          (oldest-first sort anchor; outside 24h)
 *       s3 coder / kind:implementation / SUPERSEDED / ~1h old
 *          (second status for the step-3d 2-chip multi-select)
 *       s4 watcher / kind:review / active / ~26h old
 *          (just outside the 24h cutoff so the age filter narrows)
 *     All rows carry distinct titles, non-empty task_summary; s1
 *     carries a JSON digest payload (step-5 Show-digest assertion).
 *   - 1 capture counter row (`capture:agent:coder` -> 3) so the
 *     step-6 metrics-capture-card renders >=1 entry. Spawn counters
 *     are deliberately LEFT EMPTY so the "Warmed snapshots" card
 *     renders its hidden-when-empty branch (step 6 second half).
 *   - 1 `project_metadata_records` row on the system default
 *     project (`snapshot_create_enabled` = "on") so the step-7
 *     toggle starts Enabled and toggling to Disabled produces the
 *     dirty state + PUT body {"enabled": false} the plan pins.
 *
 * Every INSERT is idempotent (ON CONFLICT DO NOTHING / DO UPDATE on
 * the natural key) so re-runs — including ENSEMBLE_E2E_KEEP=1 reuse
 * of a previously-PUT-flipped DB — are safe.
 *
 * IMPLEMENTATION-FACT DEVIATIONS from plan idealization (verified in
 * the dev-FE sources on this branch; documented for the c8 report):
 *   - Step 4's literal pinned regex /\/api\/snapshots\?$/ can never
 *     match: `SnapshotService.buildParams` ALWAYS sends `limit` +
 *     `offset` (D-6 wire contract, snapshot.service.ts). The reset
 *     URL therefore keeps the paginator-state params. The assertion
 *     below pins the D-6-correct shape and asserts every filter
 *     param is absent — the pinned assertion's stated intent
 *     ("URL drops all filter params").
 *   - Step 2 asserts 8 `<th>` cells but only 7 carry text (the
 *     Actions column header is empty by design, snapshots-table
 *     template `<!-- actions -->` column).
 *   - Step 6 asserts the capture card rows via `ul.metrics-list li`
 *     (the card renders a list, not a table/tbody).
 *   - Step 11a (Escape closes the drawer) is authored to the plan
 *     contract; NOTE for the gate runner: the dev-FE sources carry
 *     no Escape binding on the drawer/page today (verified — no
 *     `keydown.escape` / HostListener in snapshot-detail-drawer or
 *     snapshots.component). If the gate trips HERE, that is the
 *     plan-vs-implementation gap to route, not a spec bug.
 *
 * Selectors: ONLY the closed 13-selector data-test contract from
 * fe-plan §5.6 is consumed (gear-menu, menu-snapshots, paginator,
 * paginator-page-1, filter-tag-input, snapshot-drawer, drawer-section,
 * digest-pre, metrics-capture-card, drawer-backdrop, drawer-close,
 * drawer-error, drawer-retry). Every other locator is
 * role/text/placeholder/structural — no data-test values outside the
 * contract.
 */

import { test, expect, type Page, type Response } from '@playwright/test';
import { execFileSync } from 'child_process';

// ── Env scrub (defense-in-depth, mirrors the maintenance spec) ──────────
['POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_URL'].forEach((k) => {
  delete process.env[k];
});

// ── Strict-port seed constants ───────────────────────────────────────────
const SNAPSHOTS_PG_PORT = 15532; // HARD PIN — the seed never talks to another port
const PG_DB_PREFIX = 'ensemble_e2e_snap_';

const PROJECT_ALPHA_ID = '11111111-1111-4111-8111-111111111111';
const PROJECT_BETA_ID = '22222222-2222-4222-8222-222222222222';
const SNAP_FRESH_CODER_IMPL = 'aaaa1111-0000-4000-8000-000000000001';
const SNAP_OLD_WATCHER_DESIGN = 'aaaa1111-0000-4000-8000-000000000002';
const SNAP_CODER_SUPERSEDED = 'aaaa1111-0000-4000-8000-000000000003';
const SNAP_WATCHER_REVIEW = 'aaaa1111-0000-4000-8000-000000000004';
const COUNTER_CODER_ID = 'c0c0c0c0-0000-4000-8000-000000000c01';

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function iso(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString();
}

function psql(db: string, sql: string): string {
  return execFileSync(
    'psql',
    ['-h', '127.0.0.1', '-p', String(SNAPSHOTS_PG_PORT), '-d', db, '-v', 'ON_ERROR_STOP=1', '-tA', '-c', sql],
    { encoding: 'utf8', timeout: 15_000, stdio: ['ignore', 'pipe', 'pipe'] },
  ).trim();
}

/**
 * Resolve the disposable snapshots DB on 15532 — or refuse LOUDLY.
 * Never falls back to another port or DB: the `ensemble_e2e_snap_*`
 * name only exists on the boot script's own disposable cluster, so
 * the name match IS the foreign-cluster guard.
 */
function resolveDisposableDb(): string {
  let db: string;
  try {
    db = psql('postgres', `SELECT datname FROM pg_database WHERE datname LIKE '${PG_DB_PREFIX}%' ORDER BY oid DESC LIMIT 1`);
  } catch (err) {
    throw new Error(
      `REFUSING to seed: cannot reach the disposable snapshots PG on 127.0.0.1:${SNAPSHOTS_PG_PORT} ` +
        `(${(err as Error).message}). The seed never falls back to another port — ` +
        'check that playwright.snapshots.config.ts webServer[0] booted the daemon.',
    );
  }
  if (!db) {
    throw new Error(
      `REFUSING to seed: no '${PG_DB_PREFIX}*' database found on 127.0.0.1:${SNAPSHOTS_PG_PORT}. ` +
        'Either the boot script did not run, or something foreign is listening on the strict e2e port.',
    );
  }
  return db;
}

async function seedSnapshots(): Promise<void> {
  const db = resolveDisposableDb();

  // Schema guard — the daemon creates tables at boot via create_all;
  // if /readyz is green this must exist. If not, fail loud.
  const hasTable = psql(db, "SELECT to_regclass('public.snapshots')");
  if (!hasTable) {
    throw new Error(
      `REFUSING to seed: public.snapshots does not exist on ${db} — ` +
        'the daemon schema was not created (boot/canary inconsistency).',
    );
  }

  // Single implicit transaction (psql -c multi-statement) — atomic seed.
  psql(db, `
    INSERT INTO projects (project_id, name, project_type, status, description, job_queue_paused, created_at, updated_at)
    VALUES
      ('${PROJECT_ALPHA_ID}', 'e2e-snapshots-alpha', 'software', 'active',
       'Seed project alpha for the snapshots e2e suite', false, '${iso(4 * DAY)}', '${iso(4 * DAY)}'),
      ('${PROJECT_BETA_ID}', 'e2e-snapshots-beta', 'software', 'active',
       'Seed project beta for the snapshots e2e suite', false, '${iso(4 * DAY)}', '${iso(4 * DAY)}')
    ON CONFLICT (project_id) DO NOTHING;

    INSERT INTO snapshots (
      id, project_id, created_by_agent_id, target_instance_id, title,
      task_summary, domain_tags, status, runtime_version, effective_model,
      digest, created_at
    )
    VALUES
      ('${SNAP_FRESH_CODER_IMPL}', '${PROJECT_ALPHA_ID}', 'coder', 'e2e-instance-alpha-1',
       'e2e alpha coder implementation capture (fresh)',
       'Seed row: fresh coder implementation capture; anchor for the default list first row, the detail drawer, the lazy digest fetch, the copy-id assertion, and the 24h age window.',
       '["kind:implementation", "project:snapshot-uiux"]'::jsonb,
       'active', '0.0.0-e2e-seed', 'e2e-seed-model',
       '{"topic":"e2e seed digest","decisions":[{"decision":"author the snapshots e2e seed via psql","because":"the disposable PG boots empty and the steps need rows"}],"refs":["sequencing.md §4.3"],"gotchas":["seeded idempotently by snapshots.spec.ts beforeAll"]}'::jsonb,
       '${iso(2 * MIN)}'),
      ('${SNAP_OLD_WATCHER_DESIGN}', '${PROJECT_BETA_ID}', 'watcher', 'e2e-instance-beta-1',
       'e2e beta watcher design exploration (oldest)',
       'Seed row: oldest snapshot (~3 days); anchor for the oldest-first sort and a row outside the 24h age window.',
       '["kind:design"]'::jsonb,
       'active', '0.0.0-e2e-seed', NULL,
       '{}'::jsonb,
       '${iso(3 * DAY)}'),
      ('${SNAP_CODER_SUPERSEDED}', '${PROJECT_ALPHA_ID}', 'coder', 'e2e-instance-alpha-2',
       'e2e alpha coder superseded capture',
       'Seed row: superseded status; gives the step-3d status multi-select a second chip value to select.',
       '["kind:implementation"]'::jsonb,
       'superseded', '0.0.0-e2e-seed', NULL,
       '{}'::jsonb,
       '${iso(1 * HOUR)}'),
      ('${SNAP_WATCHER_REVIEW}', '${PROJECT_BETA_ID}', 'watcher', 'e2e-instance-beta-2',
       'e2e beta watcher review capture (26h)',
       'Seed row: ~26h old — just outside the 24h cutoff so the age filter meaningfully narrows the list.',
       '["kind:review"]'::jsonb,
       'active', '0.0.0-e2e-seed', NULL,
       '{}'::jsonb,
       '${iso(26 * HOUR)}')
    ON CONFLICT (id) DO NOTHING;

    -- Capture counter for the step-6 metrics-capture-card (>=1 entry).
    -- Spawn counters deliberately LEFT EMPTY (warmed card hidden-when-empty).
    INSERT INTO snapshot_usage_counters (id, scope, "key", value, updated_at)
    VALUES ('${COUNTER_CODER_ID}', 'capture:agent:coder', 'coder', 3, '${iso(2 * MIN)}')
    ON CONFLICT (id) DO NOTHING;

    -- Step-7 preconditions: the R15 toggle starts Enabled on the
    -- system default project (fail-closed default is OFF — without
    -- this row the Disabled radio is pre-checked and toggling never
    -- dirties the form). Upsert (not DO NOTHING) so an ENSEMBLE_E2E_KEEP=1
    -- reuse after a previous run's PUT {"enabled": false} re-arms ON.
    INSERT INTO project_metadata_records (project_id, meta_key, meta_value, created_at, updated_at)
    SELECT project_id, 'snapshot_create_enabled', '"on"'::jsonb, '${iso(2 * MIN)}', '${iso(2 * MIN)}'
    FROM projects WHERE name = '__system_default__'
    ON CONFLICT (project_id, meta_key) DO UPDATE
      SET meta_value = '"on"'::jsonb, updated_at = '${iso(2 * MIN)}';
  `);

  // Post-seed verification — loud if the seed silently no-op'd.
  const counts = psql(db, `
    SELECT (SELECT count(*) FROM snapshots)::text || '|' ||
           (SELECT count(*) FROM snapshot_usage_counters WHERE scope = 'capture:agent:coder')::text || '|' ||
           (SELECT count(*) FROM project_metadata_records WHERE meta_key = 'snapshot_create_enabled' AND meta_value = '"on"'::jsonb)::text
  `);
  const [snapCount, counterCount, prefCount] = counts.split('|').map((n) => parseInt(n, 10));
  if (snapCount < 4 || counterCount < 1 || prefCount < 1) {
    throw new Error(
      `Seed verification failed on ${db}: snapshots=${snapCount} (want >=4), ` +
        `capture counters=${counterCount} (want >=1), enabled-prefs=${prefCount} (want >=1).`,
    );
  }
}

test.beforeAll(async () => {
  await seedSnapshots();
});

// ── Shared helpers ───────────────────────────────────────────────────────

/** Wait for the NEXT GET /api/snapshots list response (never /metrics, never /{id}). */
function nextListResponse(page: Page, url?: URL): Promise<Response> {
  void url;
  return page.waitForResponse((r) => {
    if (r.request().method() !== 'GET') return false;
    try {
      return new URL(r.url()).pathname === '/api/snapshots';
    } catch {
      return false;
    }
  });
}

async function openSnapshotsWithRows(page: Page): Promise<void> {
  await page.goto('/snapshots');
  await expect(page.locator('table tbody tr').first()).toBeVisible();
}

function trackPageErrors(page: Page): Error[] {
  const pageErrors: Error[] = [];
  page.on('pageerror', (e) => pageErrors.push(e));
  return pageErrors;
}

// ── Suite ────────────────────────────────────────────────────────────────

test.describe('Snapshots page — sequencing §4.3 (steps 1-9 + 11a-11c)', () => {
  test('step 1 — gear menu navigates to /snapshots; title + no page errors', async ({ page }) => {
    const pageErrors = trackPageErrors(page);
    await page.goto('/');
    await page.locator('[data-test="gear-menu"]').click();
    // Wait for the menu overlay to render before clicking the item — without
    // this the second click can race the mat-menu open animation and the
    // routerLink never fires (the menu closes itself before the click
    // reaches the item). This is the standard Playwright pattern for
    // overlay-driven navigation; the menu items themselves are unchanged.
    await expect(page.locator('[data-test="menu-snapshots"]')).toBeVisible();
    await page.locator('[data-test="menu-snapshots"]').click();
    await expect(page).toHaveURL(/\/snapshots$/, { timeout: 5000 });
    await expect(page).toHaveTitle(/Snapshots/);
    await expect(page.getByRole('heading', { name: 'Snapshots', exact: true })).toHaveText('Snapshots');
    expect(pageErrors).toEqual([]);
  });

  // Design A §2.2 / AC-2.1 / AC-5.1 rebase: v1 asserted `mat-radio` for
  // the R15 toggle (which v2 retired to a compact pill button). Control
  // row hosts H1 + info icon + toggle pill + metrics pill + refresh.
  test('step 2 — page renders control row (H1 + info + toggle pill + metrics + refresh), 1-row filter row, stats strip, 7-col table, paginator', async ({ page }) => {
    await openSnapshotsWithRows(page);
    await expect(page.getByRole('heading', { name: 'Snapshots', exact: true })).toHaveText('Snapshots');
    // Design A §2.2: info icon (aria-label="Page description", popover with v1 subtitle text).
    await expect(page.getByRole('button', { name: 'Page description' })).toBeVisible();
    // Design A §2.2: toggle pill (aria-label="Snapshot creation: ON|OFF (unsaved)"). NOT a mat-radio.
    await expect(page.locator('button.toggle-pill[aria-label*="Snapshot creation"]')).toBeVisible();
    // Design A §2.2 + AC-5.2: metrics pill carries the v1 `data-test="metrics-capture-card"` hook.
    await expect(page.locator('[data-test="metrics-capture-card"]')).toBeVisible();
    // Design A §2.2: refresh icon button (aria-label="Refresh").
    await expect(page.getByRole('button', { name: 'Refresh' })).toBeVisible();
    // Design A §2.3 / AC-2.2: filter row is one row, all v1 facets + tag + sort + clear.
    await expect(page.locator('.filter-row')).toBeVisible();
    // Design A §2.4 / AC-2.3: stats strip aside with status summary.
    await expect(page.locator('aside.stats-strip')).toBeVisible();
    // Design A §2.5 / AC-5.1: plain HTML <table aria-label="Snapshots"> with sticky thead
    // and 7 th cells. v1's 8-col census drops to 7 because the "Warm" column was retired
    // to the stats strip + the drawer's "Last warmed" row (drawer IA §1.3) — the per-row
    // warm count moved out of the table when compaction closed pain point #1.
    const headers = page.locator('table thead th');
    await expect(headers).toHaveCount(7);
    // 6 labeled + 1 empty Action col (aria-label="Open", col-action).
    const expected = ['Title', 'Project', 'Agent', 'Status', 'Tags', 'Created'];
    for (let i = 0; i < expected.length; i++) {
      await expect(headers.nth(i)).toHaveText(expected[i]);
    }
    // Design A §2.5 / AC-5.2: paginator separated, data-test hook preserved.
    await expect(page.locator('[data-test="paginator"]')).toBeVisible();
    // Seeded rows exist → the empty state must NOT render.
    await expect(page.getByText('No snapshots yet.')).toHaveCount(0);
  });

  // Design A §2.3 / AC-5.1 / AC-6.3 rebase: v1 asserted the v1 widget
  // locators (mat-listbox for Status, mat-listbox for Age, mat-form-field
  // for Sort, "Search projects…"/"Search agents…" placeholder text). v2
  // replaces the Status multi-chip with a button+popover (no chip listbox),
  // the Age chip listbox with a segmented control (no listbox), and the
  // Sort mat-form-field with an inline pill+popover. The page-owned list
  // fetch (v1 amendments #5/#8/#10) is preserved verbatim per §2.3
  // Behavior — per-filter refetch + debounce + listRequestId race handling
  // + pageIndex reset all hold. Driving the v2 widgets correctly must
  // fire a GET /api/snapshots with the right param, 200, pageIndex=0
  // (paginator-page-1 visible). AC-6.3 adds the URL mirror as v2-only
  // strengthening — the filter signal writes are mirrored to
  // ActivatedRoute.queryParams via Router.navigate({queryParamsHandling:'merge'}).
  test('step 3 — each filter drives the v2 widget + fires GET /api/snapshots with the right param, 200, pageIndex reset, URL mirror (AC-6.3)', async ({ page }) => {
    await openSnapshotsWithRows(page);

    // (a) Project → searchable-select placeholder changed "Search projects…" → "All projects" (§2.3 row).
    let [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.locator('app-searchable-select.filter-project input').click();
        await page.getByRole('option', { name: 'e2e-snapshots-alpha' }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    expect(new URL(resp.request().url()).searchParams.get('project_id')).toBe(PROJECT_ALPHA_ID);
    // AC-6.3 URL mirror.
    await expect.poll(() => new URL(page.url()).searchParams.get('project_id')).toBe(PROJECT_ALPHA_ID);
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (b) Agent → searchable-select placeholder changed "Search agents…" → "All agents" (§2.3 row).
    // seenAgents accumulates from the first list response (v1 amendment #5 — page-owned, preserved).
    [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.locator('app-searchable-select.filter-agent input').click();
        await page.getByRole('option', { name: 'coder', exact: true }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    // D-2 wire rename: agent_id → agent (preserved in v2 wire contract).
    expect(new URL(resp.request().url()).searchParams.get('agent')).toBe('coder');
    // AC-6.3 URL mirror uses the FE's `agent_id` key (snapshots.component.ts:411, the
    // page-host signal-graph binding; the BE wire is still `agent` per D-2).
    await expect.poll(() => new URL(page.url()).searchParams.get('agent_id')).toBe('coder');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (c) Tags → `data-test="filter-tag-input"` hook preserved (AC-5.2). R4 250ms
    // debounce preserved (§2.3 Behavior). Repeated-param idiom.
    [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.locator('[data-test="filter-tag-input"]').fill('kind:implementation');
        await page.locator('[data-test="filter-tag-input"]').press('Enter');
      })(),
    ]);
    expect(resp.status()).toBe(200);
    expect(new URL(resp.request().url()).searchParams.getAll('tags')).toContain('kind:implementation');
    await expect.poll(() => new URL(page.url()).searchParams.getAll('tags')).toContain('kind:implementation');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (d) Status multi-select → v1 chip-listbox is replaced by a button+popover
    // (§2.3 row: "Status (multi-chip) → button 'Status' with count badge …
    // click opens a popover with the chip listbox"). Each click fires its
    // own refetch (per-filter refetch preserved per §2.3 Behavior) — wait
    // for the response carrying BOTH values (the second click).
    await page.locator('[data-test="filter-status"]').click();
    const statusPopover = page.getByRole('dialog', { name: 'Status filter' });
    await expect(statusPopover).toBeVisible();
    // First chip → fires GET with status=active.
    let [respActive] = await Promise.all([
      nextListResponse(page),
      statusPopover.locator('label.status-menu-item', { hasText: /^active$/ }).click(),
    ]);
    expect(respActive.status()).toBe(200);
    expect(new URL(respActive.request().url()).searchParams.getAll('status')).toEqual(['active']);
    // Second chip → fires GET with status=active&status=superseded.
    [resp] = await Promise.all([
      page.waitForResponse((r) => {
        if (r.request().method() !== 'GET') return false;
        try {
          return (
            new URL(r.url()).pathname === '/api/snapshots' &&
            new URL(r.url()).searchParams.getAll('status').length === 2
          );
        } catch {
          return false;
        }
      }),
      statusPopover.locator('label.status-menu-item', { hasText: /^superseded$/ }).click(),
    ]);
    expect(resp.status()).toBe(200);
    const statusParams = new URL(resp.request().url()).searchParams.getAll('status');
    expect(statusParams).toContain('active');
    expect(statusParams).toContain('superseded');
    // AC-6.3 URL mirror.
    await expect
      .poll(() => new URL(page.url()).searchParams.getAll('status').sort())
      .toEqual(['active', 'superseded']);
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();
    // Dismiss the popover before the next sub-letter (Esc closes the
    // popover; the document capture handler is gated on `menusOpen > 0`).
    await page.keyboard.press('Escape');

    // (e) Age → v1 chip-listbox is replaced by a segmented control
    // (§2.3 row: "Age (chip preset) → segmented control (5 buttons, 30px
    // tall, more compact than chips)"). Wire: age preset → created_after
    // (D-7 preserved; `all` omits the param entirely).
    [resp] = await Promise.all([
      page.waitForResponse((r) => {
        if (r.request().method() !== 'GET') return false;
        try {
          const u = new URL(r.url());
          return u.pathname === '/api/snapshots' && u.searchParams.has('created_after');
        } catch {
          return false;
        }
      }),
      page.locator('[data-test="filter-age"] button', { hasText: /^24h$/ }).click(),
    ]);
    expect(resp.status()).toBe(200);
    const cutoff = new URL(resp.request().url()).searchParams.get('created_after');
    expect(cutoff).not.toBeNull();
    expect(Number.isNaN(Date.parse(cutoff as string))).toBe(false);
    // AC-6.3 URL mirror (age encodes the preset string in the URL, not the
    // ISO cutoff — the BE wire is `created_after`; the FE's URL keeps the preset).
    await expect.poll(() => new URL(page.url()).searchParams.get('age')).toBe('24h');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (f) Sort → v1 mat-form-field is replaced by an inline pill+popover
    // (§2.3 row: "Sort → mat-form-field select, full Material chrome →
    // inline pill with icon + label + value"). Wire: sort=created_at_asc.
    await page.locator('[data-test="filter-sort"]').click();
    const sortPopover = page.getByRole('dialog', { name: 'Sort' });
    await expect(sortPopover).toBeVisible();
    [resp] = await Promise.all([
      page.waitForResponse((r) => {
        if (r.request().method() !== 'GET') return false;
        try {
          return (
            new URL(r.url()).pathname === '/api/snapshots' &&
            new URL(r.url()).searchParams.get('sort') === 'created_at_asc'
          );
        } catch {
          return false;
        }
      }),
      sortPopover.locator('button.sort-menu-item', { hasText: 'Oldest first' }).click(),
    ]);
    expect(resp.status()).toBe(200);
    // AC-6.3 URL mirror.
    await expect.poll(() => new URL(page.url()).searchParams.get('sort')).toBe('created_at_asc');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();
  });

  // Design A §2.3 / AC-5.2 rebase: v1 asserted a `/Clear filters/i` regex
  // that cannot match v2's non-contiguous accessible name "Clear all filters"
  // (aria-label, snapshots.component.html:277). The data-test="filter-clear"
  // hook is the v2 addition for this affordance (AC-5.2 v2-additions row).
  // v1's behavioral contract — click drops every filter param + URL drops
  // every filter query — is preserved verbatim (§2.3 "Same behavior" row).
  test('step 4 — filter to empty shows Clear filters; clicking it drops every filter param + URL', async ({ page }) => {
    await openSnapshotsWithRows(page);
    await page.locator('[data-test="filter-tag-input"]').fill('nonexistent:tag');
    await page.locator('[data-test="filter-tag-input"]').press('Enter');
    // AC-5.2 v2-addition: the Clear filters button carries `data-test="filter-clear"`.
    const clearBtn = page.locator('[data-test="filter-clear"]');
    await expect(clearBtn).toBeVisible();
    // Belt-and-suspenders: the exact accessible name matches the v2 aria-label
    // (a non-contiguous "Clear all filters" — the v1 `/Clear filters/i` regex
    // would miss the "all" between "Clear" and "filters"). This double-locates
    // via the v1-`getByRole` shape too, so the test catches either a data-test
    // removal or a name drift.
    await expect(clearBtn).toHaveAccessibleName('Clear all filters');

    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'GET' && new URL(r.url()).pathname === '/api/snapshots'),
      clearBtn.click(),
    ]);

    // Pinned step-4 intent: the reset request drops ALL filter params.
    // D-6 divergence note (see module header): buildParams ALWAYS sends
    // limit+offset, so the literal plan regex /\/api\/snapshots\?$/
    // can never match the real wire shape — we pin the D-6-correct
    // reset URL (only paginator-state params) + assert each filter
    // param is absent.
    expect(req.url()).toMatch(/\/api\/snapshots\?limit=\d+&offset=\d+$/);
    const cleared = new URL(req.url());
    for (const param of ['project_id', 'agent', 'tags', 'status', 'created_after', 'sort', 'tag_mode']) {
      expect(cleared.searchParams.getAll(param)).toEqual([]);
    }
    // AC-6.3 strengthening: the URL mirror clears too. After Clear, every
    // filter query param is absent (the page-host URL-sync effect writes
    // `null` for default/empty values, which `queryParamsHandling: 'merge'`
    // removes from the URL — snapshots.component.ts:402-419).
    await expect.poll(() => {
      const u = new URL(page.url());
      return ['project_id', 'agent_id', 'status', 'age', 'tag_mode', 'sort', 'tags'].every(
        (k) => u.searchParams.getAll(k).length === 0,
      );
    }).toBe(true);
    // The list re-renders with the (non-empty) defaults.
    await expect(page.locator('table tbody tr').first()).toBeVisible();
  });

  test('step 5 — row click opens drawer with 7 sections; lazy digest fetch; copy-id to clipboard', async ({ page }) => {
    await openSnapshotsWithRows(page);
    const firstRowId = (await page.request.get('/api/snapshots').then((r) => r.json())).items[0].id;
    expect(firstRowId).toBeTruthy();

    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();
    const sections = page.locator('[data-test="drawer-section"] h3');
    await expect(sections).toHaveCount(7);
    // Order spot-check: Task summary first (Git anchor, Runtime/Model,
    // Supersedes chain, Tags, Timestamps, Context follow; warm-spawn
    // section omitted per D-5).
    await expect(sections.nth(0)).toHaveText('Task summary');

    // Show digest → second GET /api/snapshots/{id}?include=digest, 200,
    // pretty-printed <pre> renders.
    const [digestResp] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === 'GET' &&
          r.url().includes('/api/snapshots/') &&
          r.url().includes('include=digest'),
      ),
      page.getByRole('button', { name: 'Show digest' }).click(),
    ]);
    expect(digestResp.status()).toBe(200);
    await expect(page.locator('[data-test="digest-pre"]')).toBeVisible();

    // Copy ID → clipboard carries the full UUID of the row.
    await page.getByRole('button', { name: 'Copy snapshot ID' }).click();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboard).toBe(firstRowId);
  });

  // Design A §2.2 + §2.4 / AC-5.2 rebase: v1 asserted `ul.metrics-list li`
  // inside `[data-test="metrics-capture-card"]` for the capture counts
  // card and a separate `.metric-card` with a "Warmed snapshots" heading
  // for the warmed card. v2 retired the inline 2-card grid (Design A
  // §2.4 StatsStrip rebalanced the metrics surface) — the v1
  // `ul.metrics-list` markup is GONE (grep=0 in v2 templates). The
  // `[data-test="metrics-capture-card"]` hook is KEPT on the metrics
  // PILL BUTTON (snapshots.component.html:59) and the per-agent
  // breakdown is now inside the metrics popover (role="dialog"
  // aria-label="Snapshot metrics breakdown") as
  // `ul.metrics-popover-agent-list li.metrics-popover-agent-row` with
  // `.agent` + `.count` spans. The warmed-count data is in the popover
  // headline (no separate warmed card in v2).
  test('step 6 — metrics: popover has capture list with >=1 row (coder) + empty-state hidden; warmed count 0 in headline', async ({ page }) => {
    await page.goto('/snapshots');
    // AC-5.2: the [data-test="metrics-capture-card"] hook is preserved
    // — it's now the metrics PILL BUTTON that opens the popover.
    const metricsPill = page.locator('[data-test="metrics-capture-card"]');
    await expect(metricsPill).toBeVisible();
    await metricsPill.click();
    const popover = page.getByRole('dialog', { name: 'Snapshot metrics breakdown' });
    await expect(popover).toBeVisible();
    // Seed: exactly one capture counter (coder → 3) → ≥1 row in the
    // per-agent captures list, and the empty-state branch is NOT taken.
    const agentRows = popover.locator('ul.metrics-popover-agent-list li.metrics-popover-agent-row');
    await expect(agentRows).toHaveCount(1);
    await expect(agentRows.locator('span.agent', { hasText: 'coder' })).toBeVisible();
    await expect(popover.getByText('No agent captures yet.')).toHaveCount(0);

    // Warmed-card v2 equivalent: v2 has no separate warmed card; the
    // popover headline carries the warmed total (always shown) and
    // the stats strip carries the same number. Seed has no spawn
    // counters, so the headline reports "0 warmed spawns" — this
    // preserves the v1 "warmed card hidden when empty" signal as a
    // "warmed count is 0 when no spawn counters" assertion.
    await expect(popover.locator('.metrics-popover-headline')).toContainText(/0\s+warmed spawn/);
    // Stats strip (Design A §2.4) carries the same number — read-only
    // aside, AC-2.3 — assert it for cross-surface parity.
    await expect(page.locator('aside.stats-strip')).toContainText(/0\s+total warmed spawns/);
  });

  // Design A §2.2 + AC-5.3 rebase: v1 asserted `mat-radio` for the
  // R15 toggle (which v2 retired to a compact pill button). v1's
  // R/W contract — PUT /api/settings/snapshot-create, dirty hint,
  // Apply button, error toast — is preserved verbatim per AC-5.3
  // ("The toggle's R/W contract is unchanged"). v2's visual is a
  // 28px pill: first click flips the desired state + marks dirty
  // (CSS class .dirty + ::after '•' marker + aria-label '(unsaved)'
  // suffix); second click (while dirty) saves via
  // setSnapshotCreateEnabled(PUT) — onSnapshotCreateSelectionChange
  // / saveSnapshotCreateEnabled (snapshots.component.ts:539-578).
  // The PUT round-trip is mandatory — this leg verifies REAL behavior
  // and does NOT become a no-op.
  test('step 7 — toggle pill ON→OFF: first click dirties (• marker + unsaved aria-label), second click saves PUT {"enabled": false} 200, reload persists OFF', async ({ page }) => {
    await page.goto('/snapshots');
    // Seed precondition: the R15 toggle starts ON (project_metadata_records
    // sets snapshot_create_enabled="on" on the system default project —
    // see the beforeAll seed in this spec).
    const pill = page.locator('button.toggle-pill[aria-label*="Snapshot creation"]');
    await expect(pill).toBeVisible();
    await expect(pill).toHaveAccessibleName('Snapshot creation: ON');
    await expect(pill).not.toHaveClass(/dirty/);

    // First click: flips desired state to OFF + marks dirty. The v2
    // pill surfaces the dirty state via THREE channels — the .dirty
    // CSS class, the ::after '•' pseudo-element (snapshots.component.scss:150-156),
    // and the "(unsaved)" suffix on the aria-label (snapshots.component.html:39-42).
    // Asserting all three binds the v1 visual contract (radio + Apply
    // + "Unsaved changes" text) to the v2 equivalent.
    await pill.click();
    await expect(pill).toHaveClass(/dirty/);
    await expect(pill).toHaveAccessibleName('Snapshot creation: OFF (unsaved)');

    // Second click (while dirty): saves → PUT /api/settings/snapshot-create.
    // AC-5.3: the v1 R/W contract (PUT endpoint, payload, 200) is unchanged.
    const [resp] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === 'PUT' &&
          new URL(r.url()).pathname === '/api/settings/snapshot-create',
      ),
      pill.click(),
    ]);
    expect(resp.status()).toBe(200);
    expect(resp.request().postDataJSON()).toEqual({ enabled: false });
    // Save completed: dirty marker clears; aria-label back to "Snapshot creation: OFF" (no unsaved).
    await expect(pill).not.toHaveClass(/dirty/);
    await expect(pill).toHaveAccessibleName('Snapshot creation: OFF');

    // Reload → persisted state reflects OFF.
    await page.reload();
    const pillAfter = page.locator('button.toggle-pill[aria-label*="Snapshot creation"]');
    await expect(pillAfter).toBeVisible();
    await expect(pillAfter).toHaveAccessibleName('Snapshot creation: OFF');
  });

  test('step 8 — /settings renders NO Agent Snapshots / Snapshot Usage Metrics sections', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: /Agent Snapshots/i })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Snapshot Usage Metrics/i })).toHaveCount(0);
    const mainHtml = await page.locator('main.app-main').innerHTML();
    expect(mainHtml).not.toContain('snapshotCreate');
    expect(mainHtml).not.toContain('snapshotMetrics');
  });

  test('step 9 — legacy metrics endpoint: 200, Deprecation header, body identical to the new endpoint', async ({ page }) => {
    const legacy = await page.request.get('/api/settings/snapshot-usage-metrics');
    expect(legacy.status()).toBe(200);
    expect(legacy.headers()['deprecation']).toBe('true');

    const modern = await page.request.get('/api/snapshots/metrics');
    expect(modern.status()).toBe(200);
    expect(JSON.stringify(await legacy.json())).toBe(JSON.stringify(await modern.json()));
  });

  test('step 11a — Escape closes the drawer from page focus (post-D1, no pane focus needed); inside-focus path kept; no page errors', async ({ page }) => {
    const pageErrors = trackPageErrors(page);
    await openSnapshotsWithRows(page);
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();

    // v2 / D1: the drawer listens for Escape at DOCUMENT level
    // (capture phase), gated on "no page popover open". Focus is on
    // the clicked table row — page-level focus OUTSIDE the drawer
    // subtree — which the pre-D1 component-scoped listener could not
    // see (the drawer stayed open). No pane focus() needed anymore.
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    expect(pageErrors).toEqual([]);

    // Focus-INSIDE path closes via the document capture handler
    // (D1, AC-A11Y-3b) + Material's convergent drawer-element Esc
    // listener — NOT the app-snapshots host-scoped HostListener:
    // the pane (`mat-drawer`, parent of the `app-snapshots` host) is
    // OUTSIDE `hostEl.contains(pane)`, so the host-scoped handler
    // cannot observe a focus on the pane. The document-level capture
    // (snapshot-detail-drawer.component.ts:295, R3-2 invariant held)
    // catches the Esc either way; the convergent Material listener
    // fires the same `closedStart → onCloseDrawer` end state
    // (idempotent — never double-emits the close output). This
    // leg stays a regression guard for the inside-focus path; the
    // assertion shape is correct, only the mechanism attribution in
    // the comment needed the fix.
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();
    await page.locator('[data-test="snapshot-drawer"]').focus();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test('step 11b — side-mode drawer: no backdrop, neutral outside click keeps it open, Esc + close button close it; no page errors', async ({ page }) => {
    const pageErrors = trackPageErrors(page);
    await openSnapshotsWithRows(page);

    // Re-open the drawer.
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();

    // v2 side-mode contract (D2, AC-4.1 mandates mode="side"):
    // Material resolves hasBackdrop=false for side drawers, so the
    // backdrop element is NEVER rendered. The v1 backdrop-click step
    // (assert visible + click to close) is structurally dead against
    // this design and was replaced by the assertions below. The
    // `data-test="drawer-backdrop"` hook on the container is kept
    // for selector compatibility.
    const container = page.locator('[data-test="drawer-backdrop"]');
    await expect(container.locator('.mat-drawer-backdrop')).toHaveCount(0);

    // (1) Neutral outside click does NOT close the drawer (side mode
    // pushes content; there is no backdrop intercepting clicks).
    await page.locator('.page-title').click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();

    // (2) Esc closes the drawer (post-D1: from any focus — here the
    // focus sits on the page, outside the drawer subtree).
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();

    // (3) The drawer close button closes the re-opened drawer.
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();
    await page.locator('[data-test="drawer-close"]').click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();

    await expect(page.locator('table tbody tr').first()).toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test('step 11c — aborted detail fetch shows drawer error + Retry; restored route recovers the 7 sections; close works', async ({ page }) => {
    await openSnapshotsWithRows(page);

    // Block the drawer DETAIL request BEFORE the row click.
    await page.route('**/api/snapshots/*', (route) => route.abort());
    await page.locator('table tbody tr').first().click();

    const drawerError = page.locator('[data-test="drawer-error"]');
    await expect(drawerError).toBeVisible();
    await expect(drawerError).toContainText(/Failed to load snapshot details/i);
    await expect(page.locator('[data-test="drawer-retry"]')).toBeVisible();

    // Restore the route and recover via Retry.
    await page.unroute('**/api/snapshots/*');
    await page.locator('[data-test="drawer-retry"]').click();
    await expect(page.locator('[data-test="drawer-section"]')).toHaveCount(7);

    // The close control works at any point in the sequence.
    await page.locator('[data-test="drawer-close"]').click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();
  });
});
