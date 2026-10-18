/**
 * Resources page merge gate — real-browser web-automation harness.
 *
 * NEW test artifact only (FE /resources observability page, P5 T7).
 * jsdom does NOT count — this spec runs REAL Chromium via Playwright.
 *
 * ── Stub architecture (cloned from snapshots-v2-gate) ─────────────
 * Every API request (URL prefix "api/") is intercepted by `page.route`
 * BEFORE it can reach the dev-server proxy (proxy.conf.json → :8079).
 * The catch-all "any api path" route (registered FIRST = lowest
 * precedence in Playwright's LIFO matching) guarantees NOTHING leaks to
 * a real backend; the specific stubs below shadow it.
 *
 * Six GET endpoints are stubbed with fixture JSON mirroring the FROZEN
 * §2.8 payload shapes:
 *   /api/resources/live
 *   /api/resources/history
 *   /api/resources/anomalies
 *   /api/resources/anomalies/{id}/evidence
 *   /api/resources/traceback
 *   /api/resources/self-cost
 *
 * ── Journey ──
 *   gear menu → "Resources" → /resources → two tabs → Live shows the
 *   stubbed RSS + self-cost chip → Timeline renders ≥1 SVG path + an
 *   anomaly marker → marker click shows the evidence + traceback panel.
 *   Zero daemon throughout.
 */
import { test, expect, type Page, type Route } from '@playwright/test';

// ── Fixture payloads (§2.8 frozen shapes; naive-UTC timestamps, D10) ──

const LIVE_PAYLOAD = {
  ts: '2026-10-18T17:00:00',
  sample_level: 'full',
  daemon: { pid: 4242, cpu_pct: 3.1, rss_mb: 1720, threads: 44, fds: 34, uptime_s: 90_000 },
  cgroup: {
    memory: { current_mb: 5900, peak_mb: 6331, anon_mb: 1900, file_mb: 3400, slab_mb: 350 },
    cpu: { usage_usec: 123_456_789, throttled_usec: 100, nr_throttled: 3 },
  },
  children: [],
  instances: [],
  attribution: { attributed_mb: 240, unattributed_mb: 60, coverage_pct: 80 },
  overruns: { count: 0, last_ts: null },
  self_cost: {
    cpu_pct: 0.42,
    cumulative_cpu_s: 12.5,
    buffers_bytes: 2_097_152,
    rss_budget_mb: 50,
    threads: 1,
    samples: 320,
    ticks_skipped: 2,
    flush: { batches: 5, bytes: 4096, errors: 0 },
    profiling: { active: false, capabilities: [] },
  },
};

const HISTORY_PAYLOAD = {
  points: [
    { t: '2026-10-18T16:00:00', daemon_cpu: 1, daemon_rss_mb: 1700, cgroup_mem_mb: 5800, threads: 40, instances_active: 5 },
    { t: '2026-10-18T16:15:00', daemon_cpu: 2, daemon_rss_mb: 1750, cgroup_mem_mb: 5850, threads: 42, instances_active: 5 },
    { t: '2026-10-18T16:30:00', daemon_cpu: 3, daemon_rss_mb: 1800, cgroup_mem_mb: 5900, threads: 44, instances_active: 6 },
    { t: '2026-10-18T16:45:00', daemon_cpu: 2, daemon_rss_mb: 1850, cgroup_mem_mb: 5950, threads: 44, instances_active: 5 },
    { t: '2026-10-18T17:00:00', daemon_cpu: 4, daemon_rss_mb: 1900, cgroup_mem_mb: 6000, threads: 46, instances_active: 5 },
  ],
  resolution_used: 'raw',
  rollup: 'none',
  truncated: false,
};

const ANOMALY = {
  id: 'anom-1',
  kind: 'memory',
  t_start: '2026-10-18T16:30:00',
  t_end: '2026-10-18T16:40:00',
  peak: 1900,
  baseline: 1700,
  severity: 'high',
  series_key: 'daemon_rss_mb',
  shape: 'ramp',
};

const ANOMALIES_PAYLOAD = { anomalies: [ANOMALY] };

const EVIDENCE_PAYLOAD = {
  anomaly: ANOMALY,
  samples: [],
  top_pids_per_bucket: [],
  children_delta: [],
  contributors: [
    { instance_id: 'inst-1', agent_id: 'worker', share_pct: 62.5 },
    { instance_id: 'inst-2', agent_id: 'developer', share_pct: 20 },
    { share_pct: 17.5, unattributed: true },
  ],
};

const TRACEBACK_PAYLOAD = {
  window: { t0: '2026-10-18T16:29:00.000Z', t1: '2026-10-18T16:41:00.000Z' },
  instances_alive: [{ instance_id: 'inst-1', agent: 'worker', status: 'running' }],
  in_flight_tasks: [{ work_id: 'work-9', status: 'PROCESSING' }],
  spawned_children: [
    { pid: 100, kind: 'chart_render', name: 'mmdc', instance_id: 'inst-1' },
    { pid: 101, kind: 'bash', name: 'python' },
  ],
  structural: { queue_depth: 3, workers: 2, checkpoints: 12, gc_events: 5 },
  findings: [],
};

const SELF_COST_PAYLOAD = LIVE_PAYLOAD.self_cost;

function jsonRoute(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

let unstubbedCalls: string[] = [];

/** Install ALL stubs. Catch-all FIRST (lowest precedence), specifics after. */
async function installStubs(page: Page): Promise<void> {
  await page.route('**/api/**', (route) => {
    unstubbedCalls.push(route.request().url());
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
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

  // ── Resources surface — the six endpoints (§2.8) ──
  await page.route('**/api/resources/live', (route) => jsonRoute(route, LIVE_PAYLOAD));
  await page.route(/\/api\/resources\/history/, (route) => jsonRoute(route, HISTORY_PAYLOAD));
  await page.route('**/api/resources/self-cost', (route) => jsonRoute(route, SELF_COST_PAYLOAD));
  await page.route(/\/api\/resources\/traceback/, (route) => jsonRoute(route, TRACEBACK_PAYLOAD));
  // NOTE: the evidence and list-route patterns are DISJOINT (`(\?|$)`
  // anchor vs `/evidence` suffix) — their relative order carries no
  // precedence. The load-bearing ordering fact is the catch-all
  // `**/api/**` registered FIRST = LOWEST precedence: Playwright routes
  // are LIFO, so these specific stubs registered after it shadow it.
  await page.route(/\/api\/resources\/anomalies\/[^/]+\/evidence/, (route) =>
    jsonRoute(route, EVIDENCE_PAYLOAD),
  );
  await page.route(/\/api\/resources\/anomalies(\?|$)/, (route) => jsonRoute(route, ANOMALIES_PAYLOAD));
}

test.beforeEach(async ({ page }) => {
  unstubbedCalls = [];
  await installStubs(page);
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser-console] ${msg.text()}`);
  });
  page.on('pageerror', (err) => console.log(`[pageerror] ${err.message}`));
  page.on('requestfailed', (req) => {
    if (req.url().includes('/api/')) {
      console.log(`[requestfailed] ${req.method()} ${req.url()} — ${req.failure()?.errorText}`);
    }
  });
});

test('[AC8] gear menu → /resources → two tabs → live + self-cost → timeline + marker drill-down', async ({
  page,
}) => {
  // ── Boot at '/' and open the gear menu (D2 — the ONLY entry point). ──
  await page.goto('/');
  await expect(page.locator('[data-test="gear-menu"]')).toBeVisible();
  await page.locator('[data-test="gear-menu"]').click();

  const resourcesItem = page.locator('a[mat-menu-item]').filter({ hasText: 'Resources' });
  await expect(resourcesItem).toBeVisible();
  await resourcesItem.click();

  await expect(page).toHaveURL(/\/resources/);

  // ── Two registry-driven tabs. ──
  const tabs = page.locator('[role="tab"]');
  await expect(tabs).toHaveCount(2);
  await expect(page.getByRole('tab', { name: 'Live' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Timeline' })).toBeVisible();

  // ── Live tab: stubbed RSS + self-cost chip (AC6/AC8). ──
  await expect(page.locator('[data-test="live-content"]')).toBeVisible();
  await expect(page.locator('[data-test="stat-daemon-rss"]')).toContainText('1720');
  const chip = page.locator('[data-test="self-cost-chip"]');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText('0.42%');
  await expect(chip).toContainText('320');

  // ── Timeline tab: ≥1 SVG path + an anomaly marker. ──
  await page.getByRole('tab', { name: 'Timeline' }).click();
  await expect(page.locator('[data-test="timeline-chart"]')).toBeVisible();

  const lines = page.locator('[data-test="series-line"]');
  await expect(lines.first()).toBeVisible();
  const lineCount = await lines.count();
  expect(lineCount).toBeGreaterThanOrEqual(1);
  const firstD = await lines.first().getAttribute('d');
  expect((firstD ?? '').length).toBeGreaterThan(0);

  const marker = page.locator('[data-test="anomaly-marker"]').first();
  await expect(marker).toBeVisible();

  // ── Marker click → evidence + traceback drill-down panel. ──
  // Click the marker's dedicated hit-circle: the <g>'s bbox center sits
  // mid-plot where a grid line intercepts pointer events, while the hit
  // circle (transparent fill, pointer-events: all) is centered on the dot.
  await marker.locator('.marker-hit').click();
  const panel = page.locator('[data-test="drilldown-panel"]');
  await expect(panel).toBeVisible();

  await expect(page.locator('[data-test="anomaly-kind"]')).toContainText('memory');
  await expect(page.locator('[data-test="anomaly-severity"]')).toContainText('high');
  await expect(page.locator('[data-test="anomaly-observed"]')).toContainText('1900');
  await expect(page.locator('[data-test="anomaly-baseline"]')).toContainText('1700');

  // Contributors (evidence) incl. the unattributed honesty row.
  const contributors = page.locator('[data-test="contributors"]');
  await expect(contributors).toBeVisible();
  await expect(contributors).toContainText('62.5');
  await expect(contributors).toContainText('unattributed');

  // Traceback children incl. the chart_render hot signal.
  const children = page.locator('[data-test="traceback-children"]');
  await expect(children).toBeVisible();
  await expect(children).toContainText('chart_render');
  await expect(children.locator('.child-row.hot')).toHaveCount(1);
});

test('[AC8] deep-link /resources renders without a daemon', async ({ page }) => {
  await page.goto('/resources');
  await expect(page.locator('[data-test="live-content"]')).toBeVisible();
  await expect(page.locator('[data-test="stat-daemon-rss"]')).toContainText('1720');
  // Everything above came from page.route stubs — assert the catch-all
  // swallowed nothing unexpected beyond known boot calls.
  expect(unstubbedCalls.some((u) => u.includes('/api/resources/'))).toBe(false);
});
