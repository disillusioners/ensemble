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
    INSERT INTO projects (project_id, name, project_type, status, description, created_at, updated_at)
    VALUES
      ('${PROJECT_ALPHA_ID}', 'e2e-snapshots-alpha', 'software', 'active',
       'Seed project alpha for the snapshots e2e suite', '${iso(4 * DAY)}', '${iso(4 * DAY)}'),
      ('${PROJECT_BETA_ID}', 'e2e-snapshots-beta', 'software', 'active',
       'Seed project beta for the snapshots e2e suite', '${iso(4 * DAY)}', '${iso(4 * DAY)}')
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
    await page.locator('[data-test="menu-snapshots"]').click();
    expect(new URL(page.url()).pathname).toBe('/snapshots');
    await expect(page).toHaveTitle(/Snapshots/);
    await expect(page.locator('h1')).toHaveText('Snapshots');
    expect(pageErrors).toEqual([]);
  });

  test('step 2 — page renders header, toggle, metrics strip, filter bar, 8-column table, paginator', async ({ page }) => {
    await openSnapshotsWithRows(page);
    await expect(page.locator('h1')).toHaveText('Snapshots');
    await expect(page.getByRole('radio', { name: 'Enabled' })).toBeVisible();
    await expect(page.getByRole('radio', { name: 'Disabled' })).toBeVisible();
    await expect(page.locator('[data-test="metrics-capture-card"]')).toBeVisible();
    await expect(page.locator('section.filter-bar')).toBeVisible();
    // 8 columns (Title, Project, Agent, Status, Tags, Created, Warm, Actions);
    // the Actions header cell is empty by design — assert the 7 labeled ones.
    const headers = page.locator('table thead th');
    await expect(headers).toHaveCount(8);
    const expected = ['Title', 'Project', 'Agent', 'Status', 'Tags', 'Created', 'Warm'];
    for (let i = 0; i < expected.length; i++) {
      await expect(headers.nth(i)).toHaveText(expected[i]);
    }
    await expect(page.locator('[data-test="paginator"]')).toBeVisible();
    // Seeded rows exist → the empty state must NOT render.
    await expect(page.getByText('No snapshots yet.')).toHaveCount(0);
  });

  test('step 3 — each filter fires GET /api/snapshots with the right param, 200, pageIndex reset', async ({ page }) => {
    await openSnapshotsWithRows(page);

    // (a) Project → a known seeded project id.
    let [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.getByPlaceholder('Search projects…').click();
        await page.getByRole('option', { name: 'e2e-snapshots-alpha' }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    expect(new URL(resp.request().url()).searchParams.get('project_id')).toBe(PROJECT_ALPHA_ID);
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (b) Agent → "coder" (D-2 wire rename: agent_id → agent).
    [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.getByPlaceholder('Search agents…').click();
        await page.getByRole('option', { name: 'coder', exact: true }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    expect(new URL(resp.request().url()).searchParams.get('agent')).toBe('coder');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (c) Tags → kind:implementation (chip appears immediately; the
    // list fetch is debounced ~250ms — the waitForResponse window
    // covers it). Repeated-param idiom.
    [resp] = await Promise.all([
      nextListResponse(page),
      (async () => {
        await page.locator('[data-test="filter-tag-input"]').fill('kind:implementation');
        await page.locator('[data-test="filter-tag-input"]').press('Enter');
      })(),
    ]);
    expect(resp.status()).toBe(200);
    expect(new URL(resp.request().url()).searchParams.getAll('tags')).toContain('kind:implementation');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (d) Status multi-select → 2 chips (active + superseded). The
    // chip-listbox emits a change per click, so wait for the request
    // carrying BOTH values.
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
      (async () => {
        const statusList = page.getByRole('listbox', { name: 'Filter by status' });
        await statusList.getByRole('option', { name: 'active', exact: true }).click();
        await statusList.getByRole('option', { name: 'superseded', exact: true }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    const statusParams = new URL(resp.request().url()).searchParams.getAll('status');
    expect(statusParams).toContain('active');
    expect(statusParams).toContain('superseded');
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (e) Age → "24h" → created_after=<ISO cutoff> (D-7).
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
      page.getByRole('listbox', { name: 'Filter by age' }).getByRole('option', { name: '24h', exact: true }).click(),
    ]);
    expect(resp.status()).toBe(200);
    const cutoff = new URL(resp.request().url()).searchParams.get('created_after');
    expect(cutoff).not.toBeNull();
    expect(Number.isNaN(Date.parse(cutoff as string))).toBe(false);
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();

    // (f) Sort → "Oldest first" → sort=created_at_asc.
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
      (async () => {
        await page.locator('mat-form-field.sort-field').click();
        await page.getByRole('option', { name: 'Oldest first' }).click();
      })(),
    ]);
    expect(resp.status()).toBe(200);
    await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();
  });

  test('step 4 — filter to empty shows Clear filters; clicking it drops every filter param', async ({ page }) => {
    await openSnapshotsWithRows(page);
    await page.locator('[data-test="filter-tag-input"]').fill('nonexistent:tag');
    await page.locator('[data-test="filter-tag-input"]').press('Enter');
    const clearBtn = page.getByRole('button', { name: /Clear filters/i });
    await expect(clearBtn).toBeVisible();

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

  test('step 6 — metrics: capture card >=1 row; warmed card hidden when spawn counts empty', async ({ page }) => {
    await page.goto('/snapshots');
    const captureCard = page.locator('[data-test="metrics-capture-card"]');
    await expect(captureCard).toBeVisible();
    // Seed: exactly one capture counter (coder → 3) and NO "empty" note.
    await expect(captureCard.locator('ul.metrics-list li')).toHaveCount(1);
    await expect(captureCard.getByText('coder')).toBeVisible();
    await expect(captureCard.getByText('No captures yet.')).toHaveCount(0);

    // Warmed card: spawn_counts_per_snapshot is EMPTY in the seed → the
    // card renders NO entries (hidden-when-empty, not blank).
    const warmedCard = page.locator('.metric-card', {
      has: page.getByRole('heading', { name: 'Warmed snapshots' }),
    });
    await expect(warmedCard).toHaveCount(1);
    await expect(warmedCard.locator('ul.metrics-list')).toHaveCount(0);
  });

  test('step 7 — toggle Enabled→Disabled: dirty hint, PUT {"enabled": false}, 200, hint hides, reload persists', async ({ page }) => {
    await page.goto('/snapshots');
    // Seed precondition: the R15 toggle starts Enabled.
    await expect(page.getByRole('radio', { name: 'Enabled' })).toBeChecked({ timeout: 15000 });

    const disabledRadio = page.getByRole('radio', { name: 'Disabled' });
    await disabledRadio.check();
    await expect(page.getByText('Unsaved changes')).toBeVisible();
    const applyBtn = page.getByRole('button', { name: 'Apply' });
    await expect(applyBtn).toBeEnabled();

    const [resp] = await Promise.all([
      page.waitForResponse(
        (r) => r.request().method() === 'PUT' && new URL(r.url()).pathname === '/api/settings/snapshot-create',
      ),
      applyBtn.click(),
    ]);
    expect(resp.status()).toBe(200);
    expect(resp.request().postDataJSON()).toEqual({ enabled: false });
    await expect(page.getByText('Unsaved changes')).toHaveCount(0);

    // Reload → persisted state reflects Disabled.
    await page.reload();
    await expect(page.getByRole('radio', { name: 'Disabled' })).toBeChecked();
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

  test('step 11a — Escape closes the drawer; table stays interactive; no page errors', async ({ page }) => {
    const pageErrors = trackPageErrors(page);
    await openSnapshotsWithRows(page);
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible();
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test('step 11b — backdrop click closes the re-opened drawer; no page errors', async ({ page }) => {
    const pageErrors = trackPageErrors(page);
    await openSnapshotsWithRows(page);

    // Re-open the drawer.
    await page.locator('table tbody tr').first().click();
    await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();

    // drawer-backdrop is bound to the <mat-drawer-container>; assert
    // the Material-owned descendant backdrop directly, then close via
    // the container-center click (leader-pinned implementation fact).
    const container = page.locator('[data-test="drawer-backdrop"]');
    await expect(container.locator('.mat-drawer-backdrop')).toBeVisible();
    await container.click();

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
