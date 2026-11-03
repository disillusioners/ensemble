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
 *   (one PANEL per returned series) → Families (a live family + a dark
 *   family with its reason). Zero daemon throughout.
 *
 * ── THE CANVAS-FATAL ASSERTIONS, RE-SPECIFIED (T1 phase 4, task 4.10) ──
 *
 * Four assertions in this file were canvas-fatal: under a Chart.js
 * canvas renderer there is no SVG `d` attribute and no `chart-tick`
 * element. They are re-specified here in THREE leg-classes, and the
 * `d`-ATTRIBUTE ASSERTIONS ARE **RETIRED — NOT PRESERVED**:
 *
 *   | # | the retired assertion          | the replacement                        |
 *   |---|---------------------------------|----------------------------------------|
 *   | 1 | the COUNT on `[data-test="svg-line-chart"]` | the per-series PROXY count (`[data-test="chart-panel-proxy"]`) |
 *   | 2 | the STEP path's `d` geometry (`H`/`V` present, no diagonal) | the proxy's `data-render-mode="step"` — the GEOMETRY moved to the renderer spec |
 *   | 3 | the LINE path's `d` mirror (`L` present, no `H`/`V`) | the proxy's `data-render-mode="line"` — the GEOMETRY moved to the renderer spec |
 *   | 4 | the `[data-test="chart-tick"]` SVG text | the wrapper's UTC TICK TEXT in its hidden surface (`[data-test="chart-panel-tick"]`) |
 *
 * Leg-classes 2 and 3 are ONE leg now: the DOM carries the render MODE
 * and the renderer spec carries the geometry
 * (`house-svg-renderer.spec.ts` — "a COUNTER renders STEP and a GAUGE
 * renders LINEAR").
 *
 * The e2e's TAB-SHAPE expectations are phase 7's task 7.9 (the shell
 * rewiring in phase 2 replaced the three tabs with the manifest-driven
 * seven); this file's chart legs are re-specified HERE, against the
 * canvas panel.
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

/**
 * The DEGRADED families payload — the door answered a FAILED registry
 * read, so the body is a 200 with an EMPTY family list. The only thing
 * that distinguishes it from an empty registry is
 * `activation.registry_read_ok === false`.
 */
const DEGRADED_FAMILIES_PAYLOAD = {
  ...ENVELOPE,
  activation: { ...ENVELOPE.activation, registry_read_ok: false },
  truncated: false,
  families: [],
};

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

  // ── Charts: one PANEL per returned series. ──
  await page.getByRole('tab', { name: 'Charts' }).click();
  await expect(page.locator('[data-test="charts-grid"]')).toBeVisible();

  // ── LEG-CLASS 1 — the per-series PROXY count (the retired
  // `[data-test="svg-line-chart"]` count). The canvas is not
  // inspectable; the proxy is the stable per-series handle. ──
  await expect(page.locator('[data-test="chart-panel-proxy"]')).toHaveCount(2);

  // ── LEG-CLASS 2 — the STEP/LINE distinction, carried by the proxy.
  //
  // The `d`-ATTRIBUTE GEOMETRY ASSERTIONS ARE **RETIRED — NOT
  // PRESERVED**: under a canvas renderer there is no `d` attribute. The
  // geometric truth (a COUNTER renders horizontal-then-vertical, a GAUGE
  // renders a diagonal, and a STEP never carries a diagonal) moved to
  // the renderer spec:
  // `src/app/components/charts/house-svg-renderer/house-svg-renderer.spec.ts`.
  await expect(
    page.locator('[data-test="chart-panel-proxy"][data-render-mode="step"]'),
  ).toHaveCount(1);
  await expect(
    page.locator('[data-test="chart-panel-proxy"][data-render-mode="line"]'),
  ).toHaveCount(1);

  // ── LEG-CLASS 3 — the UTC tick text (the retired `[data-test="chart-tick"]`
  // SVG text), read from the wrapper's hidden surface. The label is UTC
  // in every process timezone (never local). ──
  const ticks = page.locator('[data-test="chart-panel-tick"]');
  await expect(ticks).toHaveCount(5);
  await expect(ticks.first()).toHaveText(/^\d{2}:\d{2}$/);

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

test('[P5] a FAILED registry read renders the DISTINCT degraded badge (never the empty panel, never the error banner)', async ({ page }) => {
  // Re-stub the families route with the DEGRADED body (a 200 with an
  // empty family list + registry_read_ok=false). The stub is registered
  // AFTER the catch-all, so it shadows it (Playwright routes are LIFO).
  await page.route(/\/api\/metrics\/families/, (route) => {
    doorCalls.push(route.request().url());
    return jsonRoute(route, DEGRADED_FAMILIES_PAYLOAD);
  });

  await page.goto('/');
  await page.locator('[data-test="gear-menu"]').click();
  const metricsItem = page.locator('a[mat-menu-item]').filter({ hasText: 'Metrics' });
  await expect(metricsItem).toBeVisible();
  await metricsItem.click();
  await expect(page).toHaveURL(/\/metrics/);

  // ── The DISTINCT degraded surface. ──
  const badge = page.locator('[data-test="metrics-registry-degraded"]');
  await expect(badge).toBeVisible();
  await expect(badge).toContainText(/incomplete/i);

  // ── Never the transport-failure banner (the 200-EMPTY pin). ──
  await expect(page.locator('[data-test="metrics-catalog-error"]')).toHaveCount(0);

  // ── Never the empty panel (the payload is UNKNOWN, not empty). ──
  await expect(page.locator('[data-test="metrics-empty-panel"]')).toHaveCount(0);
});
