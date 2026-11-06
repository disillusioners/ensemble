// The nest-reports PIXEL ITEM (the nest-anomaly lane, phase 4 — Task 7).
//
// ONE Playwright GEOMETRY capture (the house harness: geometry
// assertions, NOT screenshots — the FE-T1 lane's precedent at
// `.agents/shared/planning/metrics-unification/fe-t1-build/pixels/`).
//
// THE FIXTURE — the AUTH-OFF posture, hermetically:
//   * the app is served from the PRODUCTION BUILD's dist directory by a
//     tiny read-only static server (no daemon, no socket to the daemon,
//     no live install, no auth token);
//   * EVERY `/api/**` call is intercepted: the metrics door and the
//     nest-reports domain route answer
//     `503 {"error": {"detail": {"error": "AUTH_NOT_CONFIGURED"}}}` — the
//     LIVE posture; every other API call answers an empty 200 so the
//     shell boots.
//
// THE SIX ASSERTIONS (phase4-plan.md Task 7):
//   1. the central door-state panel is VISIBLE and its box is
//      NON-DEGENERATE (width > 0, height > 0);
//   2. EXACTLY ONE `data-error-class="auth_not_configured"` banner (the
//      single-banner discipline);
//   3. NO Retry button (the auth class is not retryable);
//   4. the runbook pointer `docs/runbooks/oauth2-auth.md` is visible
//      inside the banner;
//   5. the incident-list section renders a DEGRADED PLACEHOLDER with a
//      non-degenerate box (the tab never blanks);
//   6. the banner's box and the placeholder's box DO NOT INTERSECT.
//
// EVIDENCE: the JSON (the observed boxes) + a markdown receipt land in
// THIS directory. The binary evidence (the PNG) stays in /tmp.
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const REPO = '/home/nea/ensemble-src-wt-nest-anomaly-report';
const DIST = path.join(REPO, 'frontend', 'dist', 'frontend', 'browser');
const OUT_DIR = path.join(REPO, '.agents', 'shared', 'planning', 'nest-anomaly-report', 'pixels');
const { chromium } = require(path.join(REPO, 'frontend', 'node_modules', 'playwright'));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};

// The REAL wire shape: the daemon's `_err()` raises
// `HTTPException(detail={"error": ..., "message": ...})` and FastAPI
// renders it as `{"detail": {"error": ..., "message": ...}}` — the
// metrics door (`daemon/routers/metrics.py:124-133`) and the
// nest-reports route (`daemon/routers/nest_reports.py:82-91`) are
// byte-identical here.
const AUTH_OFF_BODY = JSON.stringify({
  detail: {
    error: 'AUTH_NOT_CONFIGURED',
    message: 'Auth not configured (fail-closed)',
  },
});

const ev = { items: [], api: [], observed: [], consoleErrors: [], pageErrors: [], boxes: {} };
const note = (id, ok, detail) => {
  ev.items.push({ id, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id}: ${detail}`);
};

function serve(dir) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, 'http://127.0.0.1');
      let file = path.join(dir, decodeURIComponent(url.pathname));
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        file = path.join(dir, 'index.html'); // the SPA fallback
      }
      const type = MIME[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'content-type': type });
      res.end(fs.readFileSync(file));
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const server = await serve(DIST);
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();

  // ── the auth-off fixture (every API call is intercepted) ──────────────
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/api/metrics/') || url.includes('/api/nest-reports/')) {
      ev.api.push({ method: route.request().method(), status: 503, url });
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: AUTH_OFF_BODY,
      });
      return;
    }
    ev.api.push({ method: route.request().method(), status: 200, url });
    // The shell's own reads — an empty envelope the FE can iterate.
    const body = url.endsWith('/api/projects')
      ? { projects: [] }
      : { instances: [], agents: [], jobs: [], items: [], projects: [] };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });

  page.on('response', async (r) => {
    if (!r.url().includes('/api/')) return;
    let body = '';
    try {
      body = (await r.text()).slice(0, 160);
    } catch {
      body = '<unreadable>';
    }
    ev.observed.push({ status: r.status(), url: r.url(), body });
  });
  page.on('console', (m) => {
    if (m.type() === 'error') ev.consoleErrors.push(m.text().slice(0, 200));
  });
  page.on('pageerror', (e) => ev.pageErrors.push(String(e).slice(0, 200)));

  await page.goto(`${base}/metrics?tab=nest-reports`, {
    waitUntil: 'domcontentloaded',
    timeout: 30000,
  });
  try {
    await page.waitForLoadState('networkidle', { timeout: 15000 });
  } catch {
    /* best effort */
  }
  // The tab body mounts → the incident list reads the domain route.
  await page.waitForSelector('[data-test="nest-reports-section"]', { timeout: 15000 });
  await page.waitForTimeout(2500);

  // ── the boxes ─────────────────────────────────────────────────────────
  const boxOf = async (selector) => {
    const locator = page.locator(selector).first();
    const count = await page.locator(selector).count();
    if (!count) return null;
    const box = await locator.boundingBox();
    return box ? { x: box.x, y: box.y, width: box.width, height: box.height } : null;
  };

  // ── the rendered evidence (the DOM the assertions read) ──────────────
  ev.dom = {
    catalogBannerHtml: await page
      .locator('[data-test="metrics-catalog-error"]')
      .first()
      .evaluate((el) => el.outerHTML)
      .catch(() => null),
    catalogBannerText: await page
      .locator('[data-test="metrics-catalog-error"]')
      .first()
      .innerText()
      .catch(() => null),
    errorClassAttributes: await page
      .locator('[data-error-class]')
      .evaluateAll((els) => els.map((el) => el.getAttribute('data-error-class')))
      .catch(() => []),
    nestSectionText: await page
      .locator('[data-test="nest-reports-section"]')
      .first()
      .innerText()
      .catch(() => null),
  };

  const bannerBox = await boxOf('[data-test="metrics-catalog-error"]');
  const tabBannerBox = await boxOf('[data-test="central-tab-error"]');
  const placeholderBox = await boxOf('[data-test="nest-reports-suppressed"]');
  const sectionBox = await boxOf('[data-test="nest-reports-section"]');
  ev.boxes = { bannerBox, tabBannerBox, placeholderBox, sectionBox };

  // ── 1: the central door-state panel is visible and non-degenerate ──────
  const bannerVisible = await page.locator('[data-test="metrics-catalog-error"]').isVisible();
  note(
    'PX-1 central door-state panel visible + non-degenerate',
    Boolean(bannerBox) && bannerBox.width > 0 && bannerBox.height > 0 && bannerVisible,
    `visible=${bannerVisible} box=${JSON.stringify(bannerBox)} tab_local_banner=${JSON.stringify(tabBannerBox)}`,
  );

  // ── 2: EXACTLY ONE auth_not_configured banner ─────────────────────────
  const bannerCount = await page.locator('[data-error-class="auth_not_configured"]').count();
  note(
    'PX-2 exactly ONE auth_not_configured banner',
    bannerCount === 1,
    `count=${bannerCount} (the domain panel is suppressed while the posture is stated)`,
  );

  // ── 3: NO Retry button ────────────────────────────────────────────────
  const retryButtons = await page.locator('[data-test="metrics-catalog-retry"]').count();
  const domainRetry = await page.locator('[data-test="nest-reports-retry"]').count();
  const anyRetry = await page.locator('button:has-text("Retry")').count();
  note(
    'PX-3 no Retry affordance',
    retryButtons === 0 && domainRetry === 0 && anyRetry === 0,
    `catalog_retry=${retryButtons} domain_retry=${domainRetry} any_retry_button=${anyRetry}`,
  );

  // ── 4: the runbook pointer is visible inside the banner ───────────────
  const pointerText = await page
    .locator('[data-test="metrics-catalog-error"]')
    .innerText()
    .catch(() => '');
  const pointerVisible = pointerText.includes('docs/runbooks/oauth2-auth.md');
  note(
    'PX-4 the runbook pointer is visible inside the banner',
    pointerVisible,
    `pointer_present=${pointerVisible}`,
  );

  // ── 5: the incident placeholder is degraded-but-present ────────────────
  note(
    'PX-5 the incident placeholder is non-degenerate',
    Boolean(placeholderBox) && placeholderBox.width > 0 && placeholderBox.height > 0,
    `placeholder=${JSON.stringify(placeholderBox)} section=${JSON.stringify(sectionBox)}`,
  );

  // ── 6: the two boxes do not intersect ─────────────────────────────────
  const intersects = (a, b) =>
    Boolean(a) &&
    Boolean(b) &&
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height;
  note(
    'PX-6 the banner and the placeholder do not intersect',
    !intersects(bannerBox, placeholderBox),
    `banner=${JSON.stringify(bannerBox)} placeholder=${JSON.stringify(placeholderBox)}`,
  );

  // The request storm check — the auth-off posture must not retry.
  const nestCalls = ev.api.filter((c) => c.url.includes('/api/nest-reports/')).length;
  note(
    'PX-7 no request storm (the incident read is not retried in a loop)',
    nestCalls <= 2,
    `nest_reports_calls=${nestCalls} total_api_calls=${ev.api.length}`,
  );

  await page.screenshot({ path: '/tmp/nest-anomaly-pixels/auth-off-nest-reports.png', fullPage: true });
  fs.writeFileSync(path.join(OUT_DIR, 'nest-reports-auth-off-geometry.json'), JSON.stringify(ev, null, 2));

  const failed = ev.items.filter((i) => !i.ok);
  console.log(`\nRESULT: ${failed.length === 0 ? 'GREEN' : 'RED'} — ${ev.items.length - failed.length}/${ev.items.length} assertions passed`);
  if (ev.consoleErrors.length) console.log('console errors:', ev.consoleErrors.slice(0, 5));
  if (ev.pageErrors.length) console.log('page errors:', ev.pageErrors.slice(0, 5));

  await browser.close();
  server.close();
  process.exit(failed.length === 0 ? 0 : 1);
})().catch((err) => {
  console.error('capture failed:', err);
  process.exit(2);
});
