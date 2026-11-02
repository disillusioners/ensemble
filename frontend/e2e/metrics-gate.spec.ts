/**
 * The Metrics page merge gate — a real-browser web-automation harness.
 *
 * NEW test artifact only (the FE /metrics charts page, central-metrics-v1
 * Phase 5). jsdom does NOT count — this spec runs REAL Chromium.
 *
 * ── Stub architecture (cloned from resources-gate.spec.ts) ────────────
 * Every API request is intercepted by `page.route` BEFORE it can reach the
 * dev-server proxy. The catch-all "any api path" route is registered FIRST
 * = lowest precedence (Playwright routes are LIFO), so the specific stubs
 * registered after it shadow it.
 *
 * Three door routes are stubbed with the FROZEN envelope shapes
 * (`docs/architecture/metrics-query-contract.md` §4):
 *   /api/metrics/families
 *   /api/metrics/snapshot
 *   /api/metrics/series
 *
 * ── Journey ──────────────────────────────────────────────────────────
 *   gear menu → "Metrics" → /metrics → Overview (stat cards) → Charts
 *   (one SVG per series) → Families (a live family + a dark family with
 *   its reason). Zero daemon throughout.
 *
 * The load-bearing assertion is the STEP-RENDERING one (approver B5): a
 * COUNTER series renders as a STEP (horizontal-then-vertical path
 * commands), a GAUGE renders as a LINE. A linear interpolation between
 * counter samples would assert values that were never observed.
 */
import { test, expect, type Page, type Route } from '@playwright/test';

// ── Fixture payloads (the frozen envelope shapes) ─────────────────────

const ENVELOPE = {
  schema_version: 1,
  vocabulary_version: 2,
  generated_at_utc: '2026-11-02T09:00:00+00:00',
  restart_epoch: 'boot-abc123',
  activation: {
    listener_enabled: false,
    census_enabled: true,
    query_api: 'auth-gated-always-on',
  },
};

const FAMILIES_PAYLOAD = {
  ...ENVELOPE,
  truncated: false,
  families: [
    {
      id: 'F4',
      names: [
        {
          name: 'ensemble_db_pool_requests_errors_total',
          kind: 'counter',
          labels: null,
          help: 'pool request errors',
          buckets: null,
          cardinality_class: 'open',
          monotonic: true,
          chart_role: 'step',
        },
        {
          name: 'ensemble_db_pool_native',
          kind: 'gauge',
          // W-6: /families carries the DECLARED value sets (list-valued
          // per key) — the frozen `labels:{key:[values]}` shape, never a
          // concrete labelling.
          labels: { pool: ['native', 'overflow'], quantity: ['checked_out', 'in_transit'] },
          help: 'native pool gauge',
          buckets: null,
          cardinality_class: 'closed',
          monotonic: false,
          chart_role: 'line',
        },
      ],
      live_series_count: 2,
      availability: { state: 'live' },
      role: 'chart',
      source_idiom: 'registry',
      durability: 'process',
      cardinality_contribution: 2,
    },
    {
      id: 'F6',
      names: [
        {
          name: 'ensemble_pg_backends',
          kind: 'gauge',
          labels: { state: ['active', 'idle'] },
          help: 'postgres backends',
          buckets: null,
          cardinality_class: 'closed',
          monotonic: false,
          chart_role: 'line',
        },
      ],
      live_series_count: 0,
      availability: { state: 'dark', reason: 'census_gated' },
      role: 'chart',
      source_idiom: 'postgres_census',
      durability: 'process',
      cardinality_contribution: 0,
    },
  ],
};

const SNAPSHOT_PAYLOAD = {
  ...ENVELOPE,
  // W-4: the snapshot is bounded like the other two routes.
  truncated: false,
  series: [
    {
      name: 'ensemble_db_pool_native',
      labels: { pool: 'native', quantity: 'checked_out' },
      kind: 'gauge',
      monotonic: false,
      value: 3,
      last_publish_age_s: 2.5,
    },
    {
      name: 'ensemble_pg_backends',
      labels: { state: 'active' },
      kind: 'gauge',
      monotonic: false,
      value: 4,
      last_publish_age_s: 1.5,
    },
  ],
};

// The trend points are epoch SECONDS (the ring's unit).
const SERIES_PAYLOAD = {
  ...ENVELOPE,
  window: {
    from_utc: '2026-11-02T08:00:00+00:00',
    to_utc: '2026-11-02T09:00:00+00:00',
    interval_s: 15,
    interval_s_source: 'observed',
  },
  last_sample_age_s: 2,
  stale: false,
  // W-2: the series page is bounded on two axes; the stub carries the
  // additive flag exactly as the door emits it.
  truncated: false,
  series: [
    {
      name: 'ensemble_db_pool_requests_errors_total',
      labels: {},
      kind: 'counter',
      monotonic: true,
      points: [
        [1_700_000_000, 4],
        [1_700_000_015, 4],
        [1_700_000_030, 9],
        [1_700_000_045, 9],
      ],
    },
    {
      name: 'ensemble_db_pool_native',
      labels: { pool: 'native', quantity: 'checked_out' },
      kind: 'gauge',
      monotonic: false,
      points: [
        [1_700_000_000, 1],
        [1_700_000_015, 3],
        [1_700_000_030, 2],
        [1_700_000_045, 5],
      ],
    },
  ],
};

function jsonRoute(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

let unstubbedCalls: string[] = [];
let doorCalls: string[] = [];

/** Install ALL stubs. Catch-all FIRST (lowest precedence), specifics after. */
async function installStubs(page: Page): Promise<void> {
  await page.route('**/api/**', (route) => {
    unstubbedCalls.push(route.request().url());
    return jsonRoute(route, {});
  });

  // ── App-boot stubs (bodies shaped to each consuming parser) ──
  await page.route('**/api/health', (route) => jsonRoute(route, { status: 'ok', version: 'stub' }));
  await page.route('**/api/migration/availability', (route) => jsonRoute(route, { postgres_env_set: false }));
  await page.route('**/api/settings/plane', (route) => jsonRoute(route, { enabled: false, url: null }));
  await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
    jsonRoute(route, { state: 'backend_unsupported' }),
  );
  await page.route('**/api/notifications/stream', (route) =>
    route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: ':ok\n\n' }),
  );
  await page.route(/\/api\/jobs\?/, (route) => jsonRoute(route, { jobs: [] }));
  await page.route(/\/api\/missions\?/, (route) =>
    jsonRoute(route, { missions: [], total: 0, limit: 20, offset: 0 }),
  );
  await page.route(/\/api\/instances\?/, (route) =>
    jsonRoute(route, { instances: [], total: 0, limit: 10, offset: 0, has_more: false }),
  );
  await page.route('**/api/projects', (route) => jsonRoute(route, { projects: [], total: 0 }));
  await page.route('**/api/agents', (route) => jsonRoute(route, { agents: [] }));
  await page.route('**/api/settings/default-agent-versions', (route) =>
    jsonRoute(route, { default_versions: {} }),
  );

  // ── The metrics query door — the three routes ──
  await page.route(/\/api\/metrics\/families/, (route) => {
    doorCalls.push(route.request().url());
    return jsonRoute(route, FAMILIES_PAYLOAD);
  });
  await page.route(/\/api\/metrics\/snapshot/, (route) => {
    doorCalls.push(route.request().url());
    return jsonRoute(route, SNAPSHOT_PAYLOAD);
  });
  await page.route(/\/api\/metrics\/series/, (route) => {
    doorCalls.push(route.request().url());
    return jsonRoute(route, SERIES_PAYLOAD);
  });
}

test.beforeEach(async ({ page }) => {
  unstubbedCalls = [];
  doorCalls = [];
  await installStubs(page);
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser-console] ${msg.text()}`);
  });
  page.on('pageerror', (err) => console.log(`[pageerror] ${err.message}`));
});

test('[P5] gear menu → /metrics → Overview stat cards → Charts → Families', async ({ page }) => {
  // ── Boot at '/' and open the gear menu (the only entry point). ──
  await page.goto('/');
  await expect(page.locator('[data-test="gear-menu"]')).toBeVisible();
  await page.locator('[data-test="gear-menu"]').click();

  const metricsItem = page.locator('a[mat-menu-item]').filter({ hasText: 'Metrics' });
  await expect(metricsItem).toBeVisible();
  await metricsItem.click();

  await expect(page).toHaveURL(/\/metrics/);

  // ── Three registry-driven tabs. ──
  const tabs = page.locator('[role="tab"]');
  await expect(tabs).toHaveCount(3);
  await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Charts' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Families' })).toBeVisible();

  // ── Overview: the stat cards of the CURRENT values. ──
  await expect(page.locator('[data-test="overview-grid"]')).toBeVisible();
  await expect(page.locator('[data-test="overview-grid"] .stat-card')).toHaveCount(2);
  await expect(
    page.locator('[data-test="overview-grid"]').getByText('ensemble_db_pool_native'),
  ).toBeVisible();

  // ── Charts: one SVG per returned series. ──
  await page.getByRole('tab', { name: 'Charts' }).click();
  await expect(page.locator('[data-test="charts-grid"]')).toBeVisible();
  await expect(page.locator('[data-test="svg-line-chart"]')).toHaveCount(2);

  // ── THE STEP-RENDERING ASSERTION (approver B5). ──
  const counterPath = page
    .locator('[data-test="series-line"][data-render-mode="step"]')
    .first();
  await expect(counterPath).toHaveCount(1);
  const counterD = (await counterPath.getAttribute('d')) ?? '';
  expect(counterD).toContain('H');
  expect(counterD).toContain('V');
  // A STEP path never contains a diagonal segment between two samples.
  expect(counterD).not.toMatch(/L\d+\.\d+,\d+\.\d+.*L\d+\.\d+,\d+\.\d+/);

  const gaugePath = page.locator('[data-test="series-line"][data-render-mode="line"]').first();
  await expect(gaugePath).toHaveCount(1);
  const gaugeD = (await gaugePath.getAttribute('d')) ?? '';
  expect(gaugeD).toContain('L');
  expect(gaugeD).not.toContain('H');
  expect(gaugeD).not.toContain('V');

  // The UTC axis labels are present (never local time).
  await expect(page.locator('[data-test="chart-tick"]').first()).toBeVisible();

  // ── Families: a live family and a DARK family with its reason. ──
  await page.getByRole('tab', { name: 'Families' }).click();
  await expect(page.locator('[data-test="families-table"]')).toBeVisible();
  await expect(page.locator('[data-test="family-live"]').first()).toBeVisible();
  await expect(page.locator('[data-test="family-dark"]').first()).toBeVisible();
  await expect(page.locator('[data-reason="census_gated"]')).toContainText(
    'the census sampler is gated',
  );

  // ── W-6: the DECLARED value sets render as a SET, never as one
  // concrete value. The stub carries {op-style list values}; the
  // Families tab renders `pool∈{native, overflow}`.
  const declared = page.locator('[data-test="declared-labels-ensemble_db_pool_native"]');
  await expect(declared).toBeVisible();
  await expect(declared).toContainText('pool∈{native, overflow}');
  await expect(declared).toContainText('quantity∈{checked_out, in_transit}');
  // The W-6 defect rendering — a vocabulary presented as one observed
  // value — never appears.
  await expect(page.locator('[data-test="families-table"]')).not.toContainText('pool=native');

  // ── The FE never polls the exposition listener surface. ──
  expect(doorCalls.some((url) => url.includes('/api/metrics/'))).toBe(true);
  expect(doorCalls.some((url) => url.includes('9798'))).toBe(false);
});

test('[P5] deep-link /metrics renders without a daemon', async ({ page }) => {
  await page.goto('/metrics');
  await expect(page.locator('[data-test="overview-grid"]')).toBeVisible();
  // The Families body is mounted lazily — the table exists once its tab
  // is selected.
  await page.getByRole('tab', { name: 'Families' }).click();
  await expect(page.locator('[data-test="families-table"]')).toBeVisible();
});
