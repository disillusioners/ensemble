/**
 * Snapshots v2 merge gate — real-browser web-automation harness.
 *
 * NEW test artifact only (v2 merge gate commission). Product code FROZEN.
 * jsdom does NOT count — this spec runs REAL Chromium via Playwright.
 *
 * ── Stub architecture ─────────────────────────────────────────────
 * Every API request (URL prefix "api/") is intercepted by `page.route`
 * BEFORE it can reach the dev-server proxy (proxy.conf.json → :8079).
 * The catch-all "any api path" route guarantees NOTHING leaks to a
 * real backend.
 *
 * Playwright precedence note: routes are matched LAST-registered-first
 * (LIFO). The task brief says "catch-all registered LAST" — that is
 * Puppeteer semantics (registration-order handlers). In Playwright the
 * equivalent "checked last / lowest precedence" position is FIRST
 * registration, so the catch-all is registered FIRST here; specific
 * stubs registered after it shadow it. Any request no specific stub
 * handles falls through to the catch-all → 200 {} + recorded in
 * `unstubbedCalls` (surfaced as console evidence per test).
 *
 * ── Legs (one test() per leg, titles prefixed [AC-x.y]) ───────────
 *  [SMOKE]        gear nav → table → drawer sections → v1/v2 hooks →
 *                 backdrop spot-check → toggle R/W (PUT contract)
 *  [AC-2.4]       page-chrome height budget (≤200, expect ≈147)
 *  [AC-3.5]       scroll containment + pinned paginator @1280×900
 *  [AC-4.5]       drawer sticky header during body scroll
 *  [AC-6.1]       status chips: exact icons + spin + reduced-motion
 *  [AC-6.2]       2-region drawer rail @≥1280 vs <1280
 *  [AC-6.3]       URL sync, back/forward re-seed, sort round-trip
 *  [AC-A11Y.3a]   focus trap (success + error drawer states)
 *  [AC-A11Y.3b]   Esc closes popover before drawer (metrics + info)
 *
 * Known-conformant (do NOT false-fail): metricsPillAriaLabel says
 * "warmed spawns" (not "12 warmed"); residual hover tooltips at page
 * scss L96/L130/L189 are reviewer-ruled conformant; popovers are
 * click-opened mat-menus for info-icon + metrics-pill only (recon
 * found NO separate "status" popover — the status FILTER menu is a
 * faceted dropdown, not a status popover; its absence is noted in the
 * Esc-popover leg output).
 */
import { test, expect, type Page, type Route } from '@playwright/test';

// ─────────────────────────────────────────────────────────────────────
// Stub data — 60 rows (snap-001..060), all 5 statuses, ≥2 running,
// chain supersedes links, created_at spread (newest = snap-060).
// The detail endpoint 500s for EXACTLY ONE id (snap-059) → error drawer.
// ─────────────────────────────────────────────────────────────────────

const STATUSES = [
  'active',
  'superseded',
  'running',
  'failed',
  'interrupted',
] as const;
type StubStatus = (typeof STATUSES)[number];

const STATUS_ICON: Record<StubStatus, string> = {
  active: 'check_circle',
  running: 'autorenew',
  superseded: 'history',
  failed: 'error',
  interrupted: 'warning',
};

const ERROR_SNAPSHOT_ID = 'snap-058';

const ROWS_LENGTH = 60;

/** ISO-8601 stub timestamp — snap-060 is NEWEST (desc sort → row 1),
 * snap-001 oldest; ~37min apart (created_at spread). */
function stubIso(i: number): string {
  const base = Date.UTC(2026, 9, 10, 12, 0, 0); // 2026-10-10T12:00:00Z
  return new Date(base - (ROWS_LENGTH - i) * 37 * 60_000).toISOString();
}

function pad3(n: number): string {
  return String(n).padStart(3, '0');
}

interface StubRow {
  id: string;
  project_id: string;
  created_by_agent_id: string;
  target_instance_id: string;
  title: string;
  domain_tags: string[];
  status: StubStatus;
  supersedes_snapshot_id: string | null;
  git_sha: string;
  git_branch: string;
  git_dirty: boolean;
  repo_path: string;
  runtime_version: string;
  effective_model: string;
  created_at: string;
}

function makeRow(i: number): StubRow {
  return {
    id: `snap-${pad3(i)}`,
    project_id: 'proj-alpha-0001',
    created_by_agent_id: i % 2 === 0 ? 'tester' : 'worker',
    target_instance_id: `inst-${pad3(i)}-aaaa-bbbb`,
    title: `Snapshot snap-${pad3(i)}`,
    domain_tags: [i % 2 === 0 ? 'snapshots' : 'planner', 'gate'],
    status: STATUSES[(i - 1) % 5],
    // Chain: every row supersedes its numeric predecessor (row 1 is root).
    supersedes_snapshot_id: i === 1 ? null : `snap-${pad3(i - 1)}`,
    git_sha: `abc1234${pad3(i)}`,
    git_branch: 'feature/snapshots-redesign-v2',
    git_dirty: i % 7 === 0,
    repo_path: '/home/nea/ensemble-src',
    runtime_version: 'v0.18.5',
    effective_model: 'stub-model-x',
    created_at: stubIso(i),
  };
}

const ROWS: StubRow[] = Array.from({ length: 60 }, (_, k) => makeRow(k + 1));

function detailPayload(id: string): Record<string, unknown> {
  const row = ROWS.find((r) => r.id === id);
  if (!row) return {};
  return {
    ...row,
    task_summary: `Stub task summary for ${id} — capture gate payload.`,
    digest:
      id === 'snap-060'
        ? {
            summary: 'Stub digest for digest-pre rendering',
            entry_count: 3,
            warm_ratio: 0.4,
          }
        : {},
  };
}

const METRICS_PAYLOAD = {
  capture_counts: {
    worker: { created: 30 },
    tester: { created: 30 },
  },
  spawn_counts_per_snapshot: [
    { snapshot_id: 'snap-060', count: 3 },
    { snapshot_id: 'snap-059', count: 1 },
  ],
};

// ─────────────────────────────────────────────────────────────────────
// Route wiring
// ─────────────────────────────────────────────────────────────────────

interface CapturedRequest {
  url: string;
  method: string;
  body: unknown;
}

function jsonRoute(route: Route, payload: unknown, status = 200): void {
  void route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

/** Parse `limit`/`offset`/`sort` from the captured list URL, emulate the
 * BE sort, and slice ROWS. Default sort created_at_desc (newest first). */
function listResponseFor(url: string): { items: StubRow[]; total: number } {
  const u = new URL(url);
  const limit = Number(u.searchParams.get('limit') ?? '25');
  const offset = Number(u.searchParams.get('offset') ?? '0');
  const sort = u.searchParams.get('sort') ?? 'created_at_desc';
  const sorted = [...ROWS].sort((a, b) => {
    switch (sort) {
      case 'created_at_asc':
        return a.created_at.localeCompare(b.created_at);
      case 'title_asc':
        return a.title.localeCompare(b.title);
      case 'created_at_desc':
      default:
        return b.created_at.localeCompare(a.created_at);
    }
  });
  return { items: sorted.slice(offset, offset + limit), total: ROWS.length };
}

/**
 * Install ALL stubs on a page. Registered FIRST → catch-all (lowest
 * precedence in Playwright LIFO), then specific stubs shadow it.
 */
async function installStubs(page: Page): Promise<void> {
  // ── Safety net: catch-all → 200 {} + record. Registered FIRST so it
  // is consulted LAST (Playwright LIFO); specific stubs below shadow it.
  // Nothing can leak to the dev-server proxy (:8079).
  await page.route('**/api/**', (route) => {
    const url = route.request().url();
    unstubbedCalls.push(url);
    void route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{}',
    });
  });

  // ── App-boot stubs (bodies shaped to each consuming parser) ──
  // app.ts loadHealth: any object truthy → `@if (health())` gate.
  await page.route('**/api/health', (route) => jsonRoute(route, { status: 'ok', version: 'stub' }));
  // app.ts checkMigrationAvailability: reads `postgres_env_set` (false → Database menu hidden).
  await page.route('**/api/migration/availability', (route) => jsonRoute(route, { postgres_env_set: false }));
  // app.ts checkPlaneAvailability: reads `enabled` (+ `url` only when enabled).
  await page.route('**/api/settings/plane', (route) => jsonRoute(route, { enabled: false, url: null }));
  // app.ts checkMaintenanceAvailability: reads `state`; non-'ready' keeps Maintenance menu hidden.
  await page.route('**/api/maintenance/checkpoint-cleanup/availability', (route) =>
    jsonRoute(route, { state: 'backend_unsupported' }));
  // notification.service: EventSource — keep the stream parse-clean.
  await page.route('**/api/notifications/stream', (route) =>
    void route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: ':ok\n\n' }));

  // ── App-shell indicator polls (job-queue-indicator / mission badge /
  // instance poll). These are KNOWN boot calls; giving them shaped stubs
  // keeps the catch-all for genuinely-unexpected URLs only — a bare {}
  // here crashes their parsers (`.filter` on undefined) and aborts
  // change detection app-wide, breaking the table render. ──
  // job.service list* → JobListResponse envelope {jobs: [...]} (every
  // consumer maps `response.jobs`; a bare array leaves `undefined`).
  await page.route(/\/api\/jobs\?/, (route) => jsonRoute(route, { jobs: [] }));
  // mission.service.listMissions → MissionListResponse envelope.
  await page.route(/\/api\/missions\?/, (route) =>
    jsonRoute(route, { missions: [], total: 0, limit: 20, offset: 0 }));
  // api.service.listInstances → InstanceListResponse envelope.
  await page.route(/\/api\/instances\?/, (route) =>
    jsonRoute(route, { instances: [], total: 0, limit: 10, offset: 0, has_more: false }));

  // project.service cold bootstrap on the page (projects signal starts
  // empty). NOTE envelope shape: ProjectListResponse = {projects, total}.
  await page.route('**/api/projects', (route) => jsonRoute(route, { projects: [], total: 0 }));

  // api.service.listAgents → AgentListResponse envelope (Home page).
  await page.route('**/api/agents', (route) => jsonRoute(route, { agents: [] }));
  // api.service.getDefaultAgentVersions (Home page).
  await page.route('**/api/settings/default-agent-versions', (route) =>
    jsonRoute(route, { default_versions: {} }));

  // ── Snapshot surface ──
  // settings.service R15 R/W. GET returns the current stub value; PUT
  // persists + echoes the request body's `enabled` (server round-trip).
  let snapshotCreateEnabled = true; // start ON so the leg can round-trip OFF first
  await page.route('**/api/settings/snapshot-create', (route) => {
    const req = route.request();
    if (req.method() === 'PUT') {
      let body: unknown = null;
      try {
        body = JSON.parse(req.postData() ?? 'null');
      } catch {
        body = null;
      }
      putRequests.push({ url: req.url(), method: 'PUT', body });
      const enabled = Boolean((body as { enabled?: boolean } | null)?.enabled);
      snapshotCreateEnabled = enabled;
      return jsonRoute(route, { enabled });
    }
    return jsonRoute(route, { enabled: snapshotCreateEnabled });
  });

  // GET /api/snapshots (list; limit/offset always sent per D-6).
  await page.route(/\/api\/snapshots\?/, (route) => {
    listRequests.push(route.request().url());
    return jsonRoute(route, listResponseFor(route.request().url()));
  });

  // GET /api/snapshots/metrics (register AFTER the detail glob so LIFO
  // checks it first — it also matches the `snapshots/<something>` shape).
  await page.route(/\/api\/snapshots\/metrics/, (route) =>
    jsonRoute(route, METRICS_PAYLOAD));

  // GET /api/snapshots/{id}[?include=digest]; snap-059 → 500 (error drawer).
  await page.route(/\/api\/snapshots\/(?!metrics)[\w.-]+(\?.*)?$/, (route) => {
    const u = new URL(route.request().url());
    const id = (u.pathname.split('/').pop() ?? '').trim();
    if (id === ERROR_SNAPSHOT_ID) {
      return jsonRoute(route, { detail: `stubbed server error for ${id}` }, 500);
    }
    const includeDigest = u.searchParams.get('include') === 'digest';
    const payload = detailPayload(id);
    return jsonRoute(route, { ...payload, digest: includeDigest ? payload.digest : {} });
  });
}

// Captured-request ledgers (reset per test in beforeEach).
let listRequests: string[] = [];
let putRequests: CapturedRequest[] = [];
let unstubbedCalls: string[] = [];

test.beforeEach(async ({ page }) => {
  listRequests = [];
  putRequests = [];
  unstubbedCalls = [];
  await installStubs(page);
  // ── Diagnostics: browser console errors, uncaught page errors, and
  // failed requests surface as test stdout evidence. ──
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser-console] ${msg.text()}`);
  });
  page.on('pageerror', (err) => console.log(`[pageerror] ${err.message}`));
  page.on('requestfailed', (req) => {
    if (req.url().includes('/api/')) {
      console.log(`[requestfailed] ${req.method()} ${req.url()} — ${req.failure()?.errorText}`);
    }
  });
  page.on('request', (req) => {
    const u = req.url();
    if (/\/api\/snapshots(\?|$)/.test(u)) {
      console.log(`[api-list-request] ${req.method()} ${u}`);
    }
  });
  page.on('response', (resp) => {
    const u = resp.url();
    if (/\/api\/snapshots(\?|$)/.test(u)) {
      console.log(`[api-list-response] ${resp.status()} ${u}`);
    }
  });
});

test.afterEach(async () => {
  // Surface unstubbed traffic as evidence (expected: requests like
  // /api/jobs SSE-adjacent probes or future endpoints — all swallowed
  // by the catch-all, none reach a real backend).
  if (unstubbedCalls.length) {
    console.log(`[stub-catch-all] ${unstubbedCalls.length} unstubbed call(s) → 200 {}`);
    for (const u of unstubbedCalls.slice(0, 10)) console.log(`  ${u}`);
  }
});

// ─────────────────────────────────────────────────────────────────────
// Shared helpers
// ─────────────────────────────────────────────────────────────────────

/** Navigate to /snapshots and wait for real rows to render. */
async function openSnapshotsPage(page: Page): Promise<void> {
  await page.goto('/snapshots');
  await expect(page.locator('[data-test="snapshots-table"]')).toBeVisible();
  await expect(page.locator('[data-test="snapshot-row"]').first()).toBeVisible();
}

/** Open the snapshots page via the real gear-menu path from '/'. */
async function navViaGearMenu(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.locator('[data-test="gear-menu"]')).toBeVisible();
  await page.locator('[data-test="gear-menu"]').click();
  const item = page.locator('[data-test="menu-snapshots"]');
  await expect(item).toBeVisible();
  await item.click();
  await expect(page).toHaveURL(/\/snapshots/);
  await expect(page.locator('[data-test="snapshots-table"]')).toBeVisible();
}

/** Open the drawer for the row whose title contains `titlePart`. */
async function openDrawerByTitle(page: Page, titlePart: string): Promise<void> {
  await page
    .locator('[data-test="snapshot-row"]')
    .filter({ hasText: titlePart })
    .first()
    .click();
  await expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible();
  await expect(page.locator('.snapshot-drawer')).toBeVisible();
}

const SECTION_HEADINGS = [
  'Task summary',
  'Git anchor',
  'Runtime / Model',
  'Supersedes chain',
  'Tags',
  'Timestamps',
  'Context',
];

/**
 * [AC-A11Y.3a helper] Press Tab `n` times, snapshotting the active
 * element each step. Returns {inside: boolean[], visited: string[]} —
 * `inside` = activeElement is within the `.snapshot-drawer` host;
 * `visited` = data-test attrs + class handles observed.
 */
async function tabCycle(page: Page, n: number): Promise<{ inside: boolean[]; visited: string[] }> {
  const inside: boolean[] = [];
  const visited: string[] = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press('Tab');
    const snap = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return { inside: false, tag: 'none' };
      const drawerHost = el.closest('.snapshot-drawer');
      const dt = el.getAttribute('data-test');
      const handle = dt ?? `${el.tagName.toLowerCase()}.${[...el.classList].join('.')}`;
      return { inside: drawerHost !== null, tag: handle };
    });
    inside.push(snap.inside);
    visited.push(snap.tag);
  }
  return { inside, visited };
}

// ─────────────────────────────────────────────────────────────────────
// Legs
// ─────────────────────────────────────────────────────────────────────

test('[SMOKE] gear nav → table → drawer sections → v1/v2 hooks → backdrop → toggle R/W', async ({ page }) => {
  test.setTimeout(120_000); // longest leg — composite
  await navViaGearMenu(page);
  await expect(page).toHaveURL(/\/snapshots/);

  // ── Activate one filter so the v2 hooks (filter-active-count /
  // filter-clear — @if-guarded on hasActiveFilters) render. ──
  await page.locator('[data-test="filter-status"]').click();
  const failedItem = page.locator('.status-menu-item').filter({ hasText: 'failed' });
  await expect(failedItem).toBeVisible();
  await failedItem.locator('input[type="checkbox"]').check();
  await expect(page.locator('[data-test="filter-active-count"]')).toHaveText('1 filter');
  await expect(page.locator('[data-test="filter-clear"]')).toBeVisible();
  // Close the status menu (Esc — menu is an overlay; drawer not open yet).
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-test="filter-active-count"]')).toBeVisible();

  // ── Drawer: first (filtered) row ──
  await page.locator('[data-test="snapshot-row"]').first().click();
  const drawerPanel = page.locator('[data-test="snapshot-drawer"]');
  await expect(drawerPanel).toBeVisible();

  // Exactly 7 drawer-section headings in DOM order; Digest NOT counted.
  const headings = await page
    .locator('section[data-test="drawer-section"] > h3')
    .allTextContents();
  console.log(`[SMOKE] drawer-section headings (${headings.length}): ${JSON.stringify(headings)}`);
  expect(headings).toEqual(SECTION_HEADINGS);

  // v1 hooks present.
  await expect(page.locator('[data-test="metrics-capture-card"]')).toBeVisible();
  await expect(page.locator('mat-drawer-container[data-test="drawer-backdrop"]')).toBeVisible();
  expect(await page.locator('[data-test="snapshot-row"]').count()).toBeGreaterThan(0);
  await expect(page.locator('[data-test="paginator"]')).toBeVisible();
  await expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible();
  // v2 hooks present (drawer side).
  await expect(page.locator('[data-test="drawer-predecessor"]')).toBeVisible();
  await expect(page.locator('[data-test="drawer-digest-toggle"]')).toBeVisible();

  // ── BACKDROP spot-check: side mode → NO .mat-drawer-backdrop in DOM;
  // container carries the v1 hook; neutral click keeps drawer open. ──
  const backdropCount = await page.locator('.mat-drawer-backdrop').count();
  console.log(`[SMOKE] .mat-drawer-backdrop elements in DOM: ${backdropCount}`);
  expect(backdropCount).toBe(0);
  await page.locator('table[data-test="snapshots-table"] thead th.col-title').click();
  await expect(drawerPanel).toBeVisible();
  await expect(page.locator('.snapshot-drawer')).toBeVisible();

  // ── Toggle R/W (real 2-click save protocol: click=flip+dirty,
  // click-again=PUT save; UI reflects the server response). ──
  const pill = page.locator('.toggle-pill');
  await expect(pill).toHaveAttribute('aria-label', /Snapshot creation: ON/);

  // Round 1: flip to OFF (dirty), then save → PUT {"enabled":false}.
  await pill.click();
  await expect(pill).toHaveAttribute('aria-label', /OFF \(unsaved\)/);
  await pill.click(); // dirty → save
  await expect
    .poll(() => putRequests.length, { timeout: 10_000 })
    .toBe(1);
  expect(putRequests[0].body).toEqual({ enabled: false });
  await expect(pill).toHaveAttribute('aria-label', /Snapshot creation: OFF(?! \()/);
  await expect(pill.locator('.pill-state')).toHaveText('OFF');
  console.log(`[SMOKE] PUT#1 ${JSON.stringify(putRequests[0].body)} → UI reflects OFF`);

  // Round 2: flip to ON (dirty), then save → PUT {"enabled":true}.
  await pill.click();
  await expect(pill).toHaveAttribute('aria-label', /ON \(unsaved\)/);
  await pill.click(); // dirty → save
  await expect
    .poll(() => putRequests.length, { timeout: 10_000 })
    .toBe(2);
  expect(putRequests[1].body).toEqual({ enabled: true });
  await expect(pill).toHaveAttribute('aria-label', /Snapshot creation: ON(?! \()/);
  await expect(pill.locator('.pill-state')).toHaveText('ON');
  console.log(`[SMOKE] PUT#2 ${JSON.stringify(putRequests[1].body)} → UI reflects ON`);
});

test('[AC-2.4] page chrome height budget: control+filter+stats ≤ 200 @1280×900', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  const heights = await page.evaluate(() => {
    const h = (sel: string) =>
      document.querySelector(sel)?.getBoundingClientRect().height ?? -1;
    return {
      control: h('.control-row'),
      filter: h('.filter-row'),
      stats: h('.stats-strip'),
    };
  });
  const sum = heights.control + heights.filter + heights.stats;
  console.log(`[AC-2.4] heights: control=${heights.control} filter=${heights.filter} stats=${heights.stats} sum=${sum}`);
  expect(sum).toBeLessThanOrEqual(200);
});

test('[AC-3.5] scroll containment + pinned paginator @1280×900, pageSize 50', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);

  // pageSize 50 via the paginator select (60 stubbed rows → 50 rendered).
  await page.locator('[data-test="paginator"] .mat-mdc-paginator-page-size-select').click();
  await page.getByRole('option', { name: '50' }).click();
  await expect
    .poll(() => page.locator('[data-test="snapshot-row"]').count(), { timeout: 10_000 })
    .toBe(50);

  // .table-scroll scrolls; page chrome does NOT.
  const tableScroll = page.locator('.table-scroll');
  const scrollMetrics = await tableScroll.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
  }));
  console.log(`[AC-3.5] table-scroll scrollHeight=${scrollMetrics.scrollHeight} clientHeight=${scrollMetrics.clientHeight}`);
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);

  await tableScroll.evaluate((el) => {
    el.scrollTop = 300;
  });
  const scrollTop = await tableScroll.evaluate((el) => el.scrollTop);
  console.log(`[AC-3.5] table-scroll scrollTop after set: ${scrollTop}`);
  expect(scrollTop).toBeGreaterThan(0);

  const docMetrics = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    scrollTop: document.scrollingElement?.scrollTop ?? -1,
  }));
  console.log(`[AC-3.5] document scrollHeight=${docMetrics.scrollHeight} innerHeight=${docMetrics.innerHeight} scrollTop=${docMetrics.scrollTop}`);
  expect(docMetrics.scrollHeight).toBeLessThanOrEqual(docMetrics.innerHeight);

  // Wheel attempts (over the table area AND the control row) must not
  // scroll the document.
  await page.mouse.move(640, 500);
  await page.mouse.wheel(0, 600);
  await page.mouse.move(640, 30); // control-row area
  await page.mouse.wheel(0, 600);
  const docScrollAfterWheel = await page.evaluate(
    () => document.scrollingElement?.scrollTop ?? -1,
  );
  console.log(`[AC-3.5] document scrollTop after wheel attempts: ${docScrollAfterWheel}`);
  expect(docScrollAfterWheel).toBe(0);

  // Paginator pinned: boundingBox bottom ≈ viewport height (±2px).
  const paginatorBox = await page.locator('[data-test="paginator"]').boundingBox();
  expect(paginatorBox).not.toBeNull();
  const bottom = paginatorBox!.y + paginatorBox!.height;
  console.log(`[AC-3.5] paginator bottom=${bottom} viewport=900 delta=${Math.abs(bottom - 900)}`);
  expect(Math.abs(bottom - 900)).toBeLessThanOrEqual(2);
});

test('[AC-4.5] drawer header stays pinned during body scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  await openDrawerByTitle(page, 'snap-060');

  const header = page.locator('.drawer-header');
  const before = await header.boundingBox();
  expect(before).not.toBeNull();

  const body = page.locator('.drawer-body');
  await body.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  const scrolledTo = await body.evaluate((el) => el.scrollTop);
  console.log(`[AC-4.5] drawer-body scrollTop after scroll-to-bottom: ${scrolledTo}`);
  expect(scrolledTo).toBeGreaterThan(0);

  await expect(header).toBeVisible();
  const after = await header.boundingBox();
  expect(after).not.toBeNull();
  const delta = Math.abs(after!.y - before!.y);
  console.log(`[AC-4.5] header y before=${before!.y} after=${after!.y} delta=${delta}`);
  expect(delta).toBeLessThanOrEqual(1);
});

test('[AC-6.1] status chips: exact icons, spin animation, reduced-motion off-switch', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page); // default pageSize 25 — all 5 statuses on page 1

  for (const s of STATUSES) {
    const chip = page.locator(`.status-chip.status-${s}`).first();
    await expect(chip).toBeVisible();
    const icon = chip.locator('.status-icon');
    const iconText = ((await icon.textContent()) ?? '').trim();
    console.log(`[AC-6.1] status=${s} iconText='${iconText}' expected='${STATUS_ICON[s]}'`);
    expect(iconText).toBe(STATUS_ICON[s]);
  }

  const spinName = await page
    .locator('.status-chip.status-running .status-icon')
    .first()
    .evaluate((el) => getComputedStyle(el).animationName);
  console.log(`[AC-6.1] .status-running .status-icon animation-name (default): ${spinName}`);
  // Angular emulated encapsulation scopes @keyframes names
  // (e.g. `_ngcontent-ng-c…_spin`) — assert the spin keyframe by suffix.
  expect(spinName.endsWith('spin')).toBe(true);

  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reduced = await page
    .locator('.status-chip.status-running .status-icon')
    .first()
    .evaluate((el) => {
      const cs = getComputedStyle(el);
      return { animationName: cs.animationName, animationDuration: cs.animationDuration };
    });
  console.log(`[AC-6.1] reduced-motion: animationName=${reduced.animationName} duration=${reduced.animationDuration}`);
  expect(reduced.animationName === 'none' || reduced.animationDuration === '0s').toBe(true);
});

test('[AC-6.2] 2-region drawer: rail+grid @1280, single-column @1024', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  await openDrawerByTitle(page, 'snap-060');

  const rail = page.locator('aside.drawer-rail');
  await expect(rail).toBeVisible();
  const at1280 = await page.evaluate(() => {
    const grid = document.querySelector('.drawer-grid') as HTMLElement;
    const railEl = document.querySelector('aside.drawer-rail') as HTMLElement;
    return {
      gridDisplay: getComputedStyle(grid).display,
      gridCols: getComputedStyle(grid).gridTemplateColumns,
      railDisplay: getComputedStyle(railEl).display,
    };
  });
  console.log(`[AC-6.2] @1280: grid=${at1280.gridDisplay} cols='${at1280.gridCols}' rail=${at1280.railDisplay}`);
  expect(at1280.gridDisplay).toBe('grid');
  expect(at1280.gridCols.startsWith('240px')).toBe(true);
  expect(at1280.railDisplay).not.toBe('none');

  // Shrink below the breakpoint (same page, drawer still open).
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.waitForTimeout(150); // let the CSS media query re-evaluate
  const at1024 = await page.evaluate(() => {
    const grid = document.querySelector('.drawer-grid') as HTMLElement;
    const railEl = document.querySelector('aside.drawer-rail') as HTMLElement;
    return {
      gridDisplay: getComputedStyle(grid).display,
      railDisplay: getComputedStyle(railEl).display,
    };
  });
  console.log(`[AC-6.2] @1024: grid=${at1024.gridDisplay} rail=${at1024.railDisplay}`);
  expect(at1024.railDisplay).toBe('none');
  expect(at1024.gridDisplay).toBe('flex');
});

test('[AC-6.3] URL sync: status param, active-count, back/forward re-seed, sort round-trip', async ({ page }) => {
  test.setTimeout(90_000);
  await openSnapshotsPage(page);

  // ── status=failed via the status filter menu ──
  await page.locator('[data-test="filter-status"]').click();
  const failedItem = page.locator('.status-menu-item').filter({ hasText: 'failed' });
  await failedItem.locator('input[type="checkbox"]').check();
  await expect(page).toHaveURL(/status=failed/);
  // Close the status menu overlay — its backdrop would block the
  // gear-menu click below.
  await page.keyboard.press('Escape');
  await expect
    .poll(() => listRequests.some((u) => new URL(u).searchParams.get('status') === 'failed'))
    .toBe(true);
  await expect(page.locator('[data-test="filter-active-count"]')).toHaveText('1 filter');
  console.log(`[AC-6.3] status=failed in URL + captured list request; active-count=1`);

  // ── Same-route gear-menu nav pushes a param-less history entry and
  // re-seeds (queryParamMap subscription) → filters cleared. ──
  await page.locator('[data-test="gear-menu"]').click();
  await page.locator('[data-test="menu-snapshots"]').click();
  await expect(page).not.toHaveURL(/status=failed/);
  await expect(page.locator('[data-test="filter-active-count"]')).toHaveCount(0);

  // ── goBack → re-seeded to status=failed (control value + URL). ──
  await page.goBack();
  await expect(page).toHaveURL(/status=failed/);
  await expect(page.locator('[data-test="filter-active-count"]')).toHaveText('1 filter');
  const failedCheckboxBack = page
    .locator('.status-menu-item')
    .filter({ hasText: 'failed' })
    .locator('input[type="checkbox"]');
  await page.locator('[data-test="filter-status"]').click();
  await expect(failedCheckboxBack).toBeChecked();
  await page.keyboard.press('Escape');
  console.log('[AC-6.3] goBack → status=failed re-seeded (URL + control value)');

  // ── goForward → re-seeded to cleared state. ──
  await page.goForward();
  await expect(page).not.toHaveURL(/status=failed/);
  await expect(page.locator('[data-test="filter-active-count"]')).toHaveCount(0);
  console.log('[AC-6.3] goForward → filters cleared again');

  // ── sort round-trip: title_asc appears in URL + captured request,
  // then default (Newest first) removes the param. NOTE: the sort menu
  // container stopPropagation means it does NOT auto-close on item
  // click — Esc closes it before the next trigger click. ──
  await page.locator('[data-test="filter-sort"]').click();
  await page.locator('.sort-menu-item').filter({ hasText: 'Title A–Z' }).click();
  await expect(page).toHaveURL(/sort=title_asc/);
  await expect
    .poll(() => listRequests.some((u) => new URL(u).searchParams.get('sort') === 'title_asc'))
    .toBe(true);
  await page.keyboard.press('Escape'); // close the still-open sort menu
  console.log('[AC-6.3] sort=title_asc round-trips (URL + captured request)');

  await page.locator('[data-test="filter-sort"]').click();
  await page.locator('.sort-menu-item').filter({ hasText: 'Newest first' }).click();
  await expect(page).not.toHaveURL(/sort=/);
  await page.keyboard.press('Escape');
  console.log('[AC-6.3] sort param removed on default re-selection');
});

test('[AC-A11Y.3a] success drawer: initial focus on close, Tab cycle trapped + wraps', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  await openDrawerByTitle(page, 'snap-060'); // has predecessor + digest

  // Initial focus lands on drawer-close (cdkFocusInitial).
  const initial = await page.evaluate(() => document.activeElement?.getAttribute('data-test'));
  console.log(`[AC-A11Y.3a] initial focus data-test: ${initial}`);
  expect(initial).toBe('drawer-close');

  // Render the digest block so digest-copy becomes focusable.
  await page.locator('[data-test="drawer-digest-toggle"]').click();
  await expect(page.locator('[data-test="digest-pre"]')).toBeVisible();
  await expect(page.locator('[data-test="digest-copy"]').first()).toBeVisible();

  const { inside, visited } = await tabCycle(page, 20);
  const escapes = inside.filter((v) => !v).length;
  console.log(`[AC-A11Y.3a] ${visited.length} tab stops; escapes outside drawer: ${escapes}`);
  console.log(`[AC-A11Y.3a] visited: ${JSON.stringify(visited)}`);
  expect(escapes).toBe(0);

  for (const hook of ['drawer-close', 'drawer-copy-id', 'drawer-predecessor', 'drawer-digest-toggle', 'digest-copy']) {
    expect(visited).toContain(hook);
  }
  // Wrap evidence: focus returns to the first focusable (drawer-close)
  // after the last one → visited more than once.
  const closeVisits = visited.filter((v) => v === 'drawer-close').length;
  console.log(`[AC-A11Y.3a] drawer-close visited ${closeVisits}× (wrap evidence)`);
  expect(closeVisits).toBeGreaterThanOrEqual(2);
});

test('[AC-A11Y.3a] error drawer (detail 500): Tab cycle trapped, includes retry', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  await openDrawerByTitle(page, ERROR_SNAPSHOT_ID); // stubbed 500

  await expect(page.locator('[data-test="drawer-error"]')).toBeVisible();
  await expect(page.locator('[data-test="drawer-retry"]')).toBeVisible();

  const { inside, visited } = await tabCycle(page, 12);
  const escapes = inside.filter((v) => !v).length;
  console.log(`[AC-A11Y.3a] error drawer visited: ${JSON.stringify(visited)}; escapes: ${escapes}`);
  expect(escapes).toBe(0);
  expect(visited).toContain('drawer-retry');
  expect(visited).toContain('drawer-close');
  const closeVisits = visited.filter((v) => v === 'drawer-close').length;
  expect(closeVisits).toBeGreaterThanOrEqual(2); // wrap
});

test('[AC-A11Y.3b] Esc closes popover first; second Esc closes drawer (metrics + info)', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await openSnapshotsPage(page);
  await openDrawerByTitle(page, 'snap-060');
  const drawerPanel = page.locator('[data-test="snapshot-drawer"]');

  // ── metrics pill popover ──
  await page.locator('.metrics-pill').click();
  const metricsPopover = page.locator('.metrics-popover');
  await expect(metricsPopover).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(metricsPopover).toHaveCount(0);
  await expect(drawerPanel).toBeVisible();
  const focusAfterEsc1 = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return 'none';
    const inDrawer = el.closest('.snapshot-drawer') !== null;
    return `${el.tagName.toLowerCase()}${el.classList.length ? '.' + [...el.classList].join('.') : ''} inDrawer=${inDrawer}`;
  });
  console.log(`[AC-A11Y.3b] metrics popover closed by Esc; drawer still open; focus after Esc#1: ${focusAfterEsc1}`);
  await page.keyboard.press('Escape');
  const focusAfterEsc2 = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    return el ? `${el.tagName.toLowerCase()}.${[...el.classList].join('.')}` : 'none';
  });
  console.log(`[AC-A11Y.3b] after Esc#2 focus: ${focusAfterEsc2}`);
  await expect(drawerPanel).toBeHidden();
  console.log('[AC-A11Y.3b] second Esc closed drawer (metrics path)');

  // Re-open for the info popover path.
  await openDrawerByTitle(page, 'snap-060');
  await expect(drawerPanel).toBeVisible();
  await page.locator('.info-icon-btn').click();
  const infoPopover = page.locator('.info-popover');
  await expect(infoPopover).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(infoPopover).toHaveCount(0);
  await expect(drawerPanel).toBeVisible();
  console.log('[AC-A11Y.3b] info popover closed by Esc; drawer still open');
  await page.keyboard.press('Escape');
  await expect(drawerPanel).toBeHidden();
  console.log('[AC-A11Y.3b] second Esc closed drawer (info path)');

  // Recon note: NO separate "status" popover exists in source — the
  // status FILTER menu (checkbox dropdown) is a filter control, not a
  // status popover. Its absence is asserted implicitly by the selector
  // census below (logged as evidence).
  const statusPopoverCount = await page.locator('.status-popover').count();
  console.log(`[AC-A11Y.3b] .status-popover elements in source DOM: ${statusPopoverCount} (expected 0 — no such popover exists)`);
});
