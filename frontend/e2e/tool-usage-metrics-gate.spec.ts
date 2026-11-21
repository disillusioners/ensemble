/**
 * The TOOL-USAGE pixel item — the auth-off tab state (the
 * tool-usage-metrics-p1 lane, phase 4 — phase4-plan Task 7).
 *
 * NEW test artifact only. It runs under the EXISTING metrics gate
 * config (`playwright.metrics-gate.config.ts` — the file name matches
 * the config's testMatch) with the same stub architecture the gate
 * established: every API request is intercepted by `page.route` BEFORE
 * the dev-server proxy — nothing reaches a daemon.
 *
 * ── THE CAPTURE (Task 7, verbatim) ─────────────────────────────────────
 *
 * The app with the tool-usage source answering
 * `503 {error: {detail: {error: 'AUTH_NOT_CONFIGURED'}}}` — the LIVE
 * auth-off posture — then navigate `/metrics?tab=tool-usage`:
 *
 *   1. the tab's door-state panel is VISIBLE and its bounding box is
 *      NON-DEGENERATE (width > 0, height > 0);
 *   2. the Retry button is ABSENT (auth-off is not retryable);
 *   3. NO table rows render (the empty/error state is honest — no fake
 *      data), and the empty hint NEVER renders on a failure.
 *
 * GEOMETRY assertions, not screenshots — the evidence lands in the
 * lane's `pixels/` dir as recorded numbers.
 */
import { test, expect, type Page, type Route } from '@playwright/test';

// ── Fixture payloads (the frozen envelope shapes, cloned from the gate) ─

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
  families: [],
};

const SNAPSHOT_PAYLOAD = { ...ENVELOPE, truncated: false, series: [] };

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
  truncated: false,
  series: [],
};

const RESOURCE_LIVE_PAYLOAD = {
  ts: '2026-10-18T17:00:00',
  sample_level: 'full',
  daemon: { pid: 4242, cpu_pct: 3.1, rss_mb: 1720, threads: 44, fds: 34, uptime_s: 90_000 },
  cgroup: {
    memory: { current_mb: 5900, peak_mb: 6331, anon_mb: 1900, file_mb: 3400, slab_mb: 350 },
    cpu: { usage_usec: 123_456_789, throttled_usec: 100, nr_throttled: 0 },
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

const RESOURCE_HISTORY_PAYLOAD = { points: [], resolution_used: 'raw', rollup: 'none', truncated: false };
const RESOURCE_ANOMALIES_PAYLOAD = { anomalies: [] };

/** The AUTH-OFF tool-usage answer — the lane's captured posture. The
 *  BODY is the daemon's FastAPI error shape (`detail.error`); the
 *  HttpErrorResponse envelope (`err.error` = this body) is what the
 *  decoder reads. */
const TOOL_USAGE_AUTH_OFF = { detail: { error: 'AUTH_NOT_CONFIGURED' } };

function jsonRoute(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

/** Install ALL stubs. Catch-all FIRST (lowest precedence), specifics after. */
async function installStubs(page: Page): Promise<void> {
  await page.route('**/api/**', (route) => jsonRoute(route, {}));

  // ── App-boot stubs ──
  await page.route('**/api/health', (route) => jsonRoute(route, { status: 'ok', version: 'stub' }));
  // The session bootstrap probe — MUST be SessionUser-shaped: a 200 with
  // a non-shaped body is treated as an auth redirect (SessionService),
  // which would bounce the deep link to /api/auth/login.
  await page.route('**/api/auth/me', (route) =>
    jsonRoute(route, {
      subject: 'gate',
      scopes: ['maintenance:read', 'metrics:read'],
      auth_method: 'none',
      expires_at: '2030-01-01T00:00:00+00:00',
    }),
  );
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
  await page.route('**/api/projects', (route) =>
    jsonRoute(route, { projects: [{ project_id: 'proj-1', name: 'Ensemble' }], total: 1 }),
  );
  await page.route('**/api/agents', (route) => jsonRoute(route, { agents: [] }));
  await page.route('**/api/settings/default-agent-versions', (route) =>
    jsonRoute(route, { default_versions: {} }),
  );

  // ── The central metrics door answers 200 (the central posture is NOT
  //    the fixture — ONLY the tool-usage source is auth-off). ──
  await page.route(/\/api\/metrics\/families/, (route) => jsonRoute(route, FAMILIES_PAYLOAD));
  await page.route(/\/api\/metrics\/snapshot/, (route) => jsonRoute(route, SNAPSHOT_PAYLOAD));
  await page.route(/\/api\/metrics\/series/, (route) => jsonRoute(route, SERIES_PAYLOAD));

  // ── The resource module (tab 1 — defensive stubs; the deep link does
  //    not select it, but the page may still boot its surfaces). ──
  await page.route(/\/api\/resources\/live/, (route) => jsonRoute(route, RESOURCE_LIVE_PAYLOAD));
  await page.route(/\/api\/resources\/self-cost/, (route) =>
    jsonRoute(route, RESOURCE_LIVE_PAYLOAD.self_cost),
  );
  await page.route(/\/api\/resources\/history/, (route) => jsonRoute(route, RESOURCE_HISTORY_PAYLOAD));
  await page.route(/\/api\/resources\/anomalies(\?|$)/, (route) =>
    jsonRoute(route, RESOURCE_ANOMALIES_PAYLOAD),
  );

  // ── THE FIXTURE: the tool-usage domain source answers the auth-off 503. ──
  await page.route(/\/api\/tool-usage\/summary/, (route) => jsonRoute(route, TOOL_USAGE_AUTH_OFF, 503));
  await page.route(/\/api\/tool-usage\/daily/, (route) => jsonRoute(route, TOOL_USAGE_AUTH_OFF, 503));
}

test.beforeEach(async ({ page }) => {
  await installStubs(page);
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser-console] ${msg.text()}`);
  });
  page.on('pageerror', (err) => console.log(`[pageerror] ${err.message}`));
});

test('[tool-usage P4] the auth-off tab renders ONE fail-closed banner — visible, non-degenerate, no Retry, zero rows', async ({
  page,
}) => {
  await page.goto('/metrics?tab=tool-usage');

  // ── Assertion 1 — the door-state panel is VISIBLE and its bounding
  //    box is NON-DEGENERATE (width > 0, height > 0). ──
  const panel = page.locator('[data-test="tool-usage-error"]');
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.width ?? 0).toBeGreaterThan(0);
  expect(box?.height ?? 0).toBeGreaterThan(0);

  // The geometry evidence — recorded numbers for the pixels file.
  const evidence = {
    capture: 'auth-off tab state',
    url: '/metrics?tab=tool-usage',
    fixture: "503 {error: {detail: {error: 'AUTH_NOT_CONFIGURED'}}}",
    panel_test_id: 'tool-usage-error',
    panel_bbox: box,
    retry_present: await page.locator('[data-test="tool-usage-retry"]').count(),
    table_rows: await page.locator('[data-test^="tool-usage-row-"]').count(),
    table_present: await page.locator('[data-test="tool-usage-table"]').count(),
    empty_hint_present: await page.locator('[data-test="tool-usage-empty"]').count(),
  };
  console.log('[tool-usage-pixel-evidence] ' + JSON.stringify(evidence));

  // ── Assertion 2 — the Retry button is ABSENT (auth-off is not
  //    retryable: the copy is data-driven, retryable=false). ──
  await expect(page.locator('[data-test="tool-usage-retry"]')).toHaveCount(0);

  // ── Assertion 3 — NO table rows render (the empty/error state is
  //    honest — no fake data), and the empty hint NEVER renders on a
  //    failure (empty ≠ error). ──
  await expect(page.locator('[data-test="tool-usage-table"]')).toHaveCount(0);
  await expect(page.locator('[data-test^="tool-usage-row-"]')).toHaveCount(0);
  await expect(page.locator('[data-test="tool-usage-empty"]')).toHaveCount(0);

  // ── Exactly ONE banner — the trend section's banner is suppressed
  //    under the posture (the posture is stated once). ──
  await expect(page.locator('[data-test="tool-usage-trend-error"]')).toHaveCount(0);
  await expect(page.locator('[data-error-class="auth_not_configured"]')).toHaveCount(1);
});
