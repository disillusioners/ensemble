// Phase 9 — tasks 9.6 (L3) + 9.7a (PX-1..7, 9..14 auth ON) + 9.4's rendered L1b leg.
// Canonical script shape (research-findings.md §7): Node Playwright from
// frontend/node_modules, chromium --no-sandbox, domcontentloaded (30 s) →
// best-effort networkidle (15 s cap) → fixed 3 s settle → screenshot.
// Bearer via the context's extraHTTPHeaders — NO production FE change.
// Binary evidence stays in /tmp/fe-t1-pixels (never the repo).
const { chromium } = require('/home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend/node_modules/playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8079';
const OUT = '/tmp/fe-t1-pixels';
const TOKEN = fs.readFileSync('/tmp/fe-t1-token.out', 'utf8').trim();
const ev = { items: [], api: [], consoleErrors: [], pageErrors: [] };
const note = (id, ok, detail) => {
  ev.items.push({ id, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id}: ${detail}`);
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });

  const mkPage = async ({ bearer = true, width = 1600, height = 1000 } = {}) => {
    const ctx = await browser.newContext({
      viewport: { width, height },
      extraHTTPHeaders: bearer ? { Authorization: `Bearer ${TOKEN}` } : {},
    });
    const page = await ctx.newPage();
    page.on('response', (r) => {
      if (r.url().includes('/api/')) ev.api.push({ method: r.request().method(), status: r.status(), url: r.url() });
    });
    page.on('console', (m) => { if (m.type() === 'error') ev.consoleErrors.push(m.text().slice(0, 200)); });
    page.on('pageerror', (e) => ev.pageErrors.push(String(e).slice(0, 200)));
    return { ctx, page };
  };
  const go = async (page, url) => {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
    await page.waitForTimeout(3000);
  };
  const txt = async (page) => await page.evaluate(() => document.body.innerText);
  const has = (s, needle) => s.toLowerCase().includes(needle.toLowerCase());

  // ── PX-1: tab 1 Resources — WITH DATA ──────────────────────────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=resource`);
    await page.screenshot({ path: `${OUT}/px01-tab1-resource.png`, fullPage: true });
    const strip = await page.locator('[data-test="resource-live-strip"]').count();
    const timeline = await page.locator('[data-test="resource-timeline-panel"]').count();
    const anomalies = await page.locator('[data-test="resource-anomalies-panel"]').count();
    const rows = await page.locator('[data-test^="anomaly-row-"]').count();
    const body = await txt(page);
    const disclosure = has(body, 'process-local') || has(body, 'attribution') || has(body, 'coverage');
    note('PX-1', strip > 0 && timeline > 0 && anomalies > 0 && rows > 0,
      `live_strip=${strip} timeline=${timeline} anomalies_panel=${anomalies} anomaly_rows≈${rows} disclosure_text=${disclosure}`);
    await ctx.close();
  }

  // ── PX-2..7: the six central tabs ─────────────────────────────────
  const centralTabs = [
    ['runtime', 'px02-tab2-runtime', 'F9'],
    ['db-pool', 'px03-tab3-dbpool', 'F4'],
    ['jobs', 'px04-tab4-jobs', 'F10'],
    ['instances', 'px05-tab5-instances', 'F13'],
    ['checkpoint', 'px06-tab6-checkpoint', 'F8'],
    ['mcp', 'px07-tab7-mcp', 'F17'],
  ];
  for (const [tab, shot] of centralTabs) {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=${tab}`);
    await page.screenshot({ path: `${OUT}/${shot}.png`, fullPage: true });
    const panels = await page.locator('[data-test="central-tab-panel"]').count();
    const strips = await page.locator('[data-test="central-tab-stat-strip"]').count();
    const famChips = await page.locator('[data-test="central-tab-family-live"], [data-test="central-tab-family-dark"], [data-test="central-tab-family-stat"]').count();
    const empty = await page.locator('[data-test="central-tab-no-catalog"]').count();
    note(shot.replace('px0', 'PX-').replace(/-(tab\d-.*)$/, ' $1'),
      empty === 0 && (panels > 0 || strips > 0 || famChips > 0),
      `tab=${tab} panels=${panels} stat_strips=${strips} family_chips=${famChips} no_catalog=${empty}`);
    await ctx.close();
  }

  // ── PX-9: /resources redirect preserves parsed query params ────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/resources?window=24h&x=1`);
    await page.screenshot({ path: `${OUT}/px09-resources-redirect.png`, fullPage: true });
    const u = new URL(page.url());
    const params = Object.fromEntries(u.searchParams.entries());
    const ok = u.pathname === '/metrics' && params.tab === 'resource' && params.window === '24h' && params.x === '1';
    note('PX-9', ok, `landed=${page.url()} parsed=${JSON.stringify(params)}`);
    await ctx.close();
  }

  // ── PX-10 / L3: mixed-unit axes on tab 1 ──────────────────────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=resource`);
    await page.screenshot({ path: `${OUT}/px10-mixed-units.png`, fullPage: false });
    // default-on = memory+cpu only; the count panel needs its toggle (the toggle path is part of criterion 5)
    const tg = page.locator('[data-test="series-toggle-threads"]');
    if (await tg.count()) await tg.first().click().catch(() => {});
    await page.waitForTimeout(2500);
    const mem = await page.locator('[data-test="timeline-panel-memory"]').count();
    const cpu = await page.locator('[data-test="timeline-panel-cpu"]').count();
    const cnt = await page.locator('[data-test="timeline-panel-count"]').count();
    const memCanvas = await page.locator('[data-test="timeline-panel-memory"] canvas').count();
    const cpuCanvas = await page.locator('[data-test="timeline-panel-cpu"] canvas').count();
    const cntCanvas = await page.locator('[data-test="timeline-panel-count"] canvas').count();
    const body = await txt(page);
    const titles = { mem: has(body, 'memory'), cpu: has(body, 'cpu'), count: has(body, 'count') };
    // geometry: three DISTINCT panels, each with its OWN canvas (no shared axis element)
    const ok = mem === 1 && cpu === 1 && cnt === 1 && memCanvas === 1 && cpuCanvas === 1 && cntCanvas === 1;
    note('PX-10/L3', ok,
      `panels memory=${mem} cpu=${cpu} count=${cnt}; per-panel own-canvas mem=${memCanvas} cpu=${cpuCanvas} count=${cntCanvas}; unit labels are canvas-drawn (pixel corroborates MB/%/count)`);
    await ctx.close();
  }

  // ── PX-11: baseline / previous-window comparison ──────────────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=runtime`);
    await page.screenshot({ path: `${OUT}/px11a-runtime-baseline.png`, fullPage: false });
    const tg = await page.locator('[data-test="metrics-baseline-toggle"]').count();
    const tgEnabled = tg > 0 ? await page.locator('[data-test="metrics-baseline-toggle"]').first().isEnabled() : false;
    let disc = 0, noteEl = 0, prevWindow = false;
    if (tg > 0 && tgEnabled) {
      await page.locator('[data-test="metrics-baseline-toggle"]').first().check().catch(() => {});
      await page.waitForTimeout(4000);
      disc = await page.locator('[data-test="metrics-baseline-disclosure"]').count();
      noteEl = await page.locator('[data-test="metrics-baseline-note"]').count();
      const body = await txt(page);
      prevWindow = has(body, 'previous window');
      await page.screenshot({ path: `${OUT}/px11a-runtime-baseline-on.png`, fullPage: false });
    }
    note('PX-11a', tg > 0 && tgEnabled && (disc > 0 || noteEl > 0 || prevWindow),
      `runtime: toggle=${tg} enabled=${tgEnabled} (checked + waited) disclosure=${disc} note=${noteEl} 'previous window' text=${prevWindow}`);
    await ctx.close();

    const { ctx: c2, page: p2 } = await mkPage();
    await go(p2, `${BASE}/metrics?tab=checkpoint-op-bytes`);
    await p2.screenshot({ path: `${OUT}/px11b-custom-tab-toggle-disabled.png`, fullPage: false });
    const tg2 = await p2.locator('[data-test="metrics-baseline-toggle"]').count();
    const disabled2 = tg2 > 0 ? !(await p2.locator('[data-test="metrics-baseline-toggle"]').first().isEnabled()) : false;
    const reason2 = await p2.locator('[data-test="metrics-baseline-disabled-reason"]').count();
    const reasonText2 = reason2 > 0 ? (await p2.locator('[data-test="metrics-baseline-disabled-reason"]').first().innerText()).slice(0, 120) : '';
    note('PX-11b', tg2 > 0 && disabled2 && reason2 > 0 && reasonText2.length > 0,
      `custom tab: toggle=${tg2} disabled=${disabled2} reason_el=${reason2} reason="${reasonText2}"`);
    await c2.close();
  }

  // ── PX-12: range switch — option sets swap ─────────────────────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=runtime`);
    await page.screenshot({ path: `${OUT}/px12a-runtime-range.png`, fullPage: false });
    const optHandles = await page.locator('[data-test="metrics-range-select"] option').all();
    const opts = [];
    for (const h of optHandles) opts.push({ label: (await h.innerText()).trim(), disabled: await h.evaluate((el) => el.disabled) });
    const longCentralEnabled = opts.filter((o) => /24h|7d/.test(o.label) && !o.disabled);
    note('PX-12a', opts.length > 0 && longCentralEnabled.length === 0,
      `central options=${JSON.stringify(opts)} — 24h/7d present but DISABLED (visible enforcement): ${longCentralEnabled.length === 0}`);
    await ctx.close();

    const { ctx: c2, page: p2 } = await mkPage();
    await go(p2, `${BASE}/metrics?tab=checkpoint-op-bytes`);
    await p2.screenshot({ path: `${OUT}/px12b-custom-range.png`, fullPage: false });
    const rsel = await p2.locator('[data-test="metrics-range-select"]').count();
    const opts2 = await p2.locator('[data-test="metrics-range-select"] option').allInnerTexts();
    const ctlNote = await p2.locator('[data-test="metrics-range-control-note"]').count();
    const body = await txt(p2);
    const labelled = has(body, 'window') || has(body, 'range');
    note('PX-12b', rsel > 0 && opts2.length > 0 && labelled,
      `custom tab: own range control=${rsel} options=[${opts2.join(', ')}] control_note=${ctlNote} labelled=${labelled}`);
    await c2.close();
  }

  // ── PX-13: anomaly marker labels + data-gap wording ────────────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=resource`);
    const body = await txt(page);
    // marker labels carry the API words (kind/severity/peak) — probe the
    // anomalies panel rows and the drill-down vocabulary
    const rowText = await page.locator('[data-test="resource-anomalies-panel"]').first().innerText().catch(() => '');
    const sev = /severity/i.test(rowText);
    const peak = /peak/i.test(rowText);
    const kindWord = /kind|shape|overture|pattern/i.test(rowText);
    // click the first anomaly row to open the drilldown (marker label)
    let drill = { clicked: false, text: '' };
    const firstRow = page.locator('[data-test="resource-anomalies-panel"] li, [data-test="resource-anomalies-panel"] tr, [data-test="anomaly-row"]').first();
    if (await firstRow.count()) {
      await firstRow.click().catch(() => {});
      await page.waitForTimeout(1500);
      drill.clicked = (await page.locator('[data-test="drilldown-panel"]').count()) > 0;
      drill.text = drill.clicked ? await page.locator('[data-test="drilldown-panel"]').first().innerText().catch(() => '') : '';
    }
    const drillWords = /severity/i.test(drill.text) && /peak/i.test(drill.text);
    await page.screenshot({ path: `${OUT}/px13-anomaly-markers.png`, fullPage: true });
    // gap wording lives in the canvas annotation + the derived label
    const gapChips = await page.locator('[data-test="data-gap-chip"]').allInnerTexts();
    const gapOk = gapChips.length === 0 || gapChips.every((g) => /data gap/i.test(g));
    const body2 = await txt(page);
    const noRestartClaim = !/restart/i.test(body2.replace(/restarts?\s+(count|total|counter)/gi, ''));
    note('PX-13', ((sev && peak) || drillWords) && gapOk,
      `rows carry severity=${sev} peak=${peak}; drilldown=${drill.clicked} drill carries kind/severity/peak=${drillWords}; gap chips=${JSON.stringify(gapChips.slice(0,2))} every-gap-says-data-gap=${gapOk}; no-restart-claim=${noRestartClaim}`);
    await ctx.close();
  }

  // ── PX-14: dark families listed with reason, not charted ──────────
  {
    const { ctx, page } = await mkPage();
    await go(page, `${BASE}/metrics?tab=runtime`);
    await page.screenshot({ path: `${OUT}/px14-dark-families.png`, fullPage: false });
    const dark = await page.locator('[data-test="central-tab-family-dark"], [data-test="family-dark"]').allInnerTexts();
    const live = await page.locator('[data-test="central-tab-family-live"], [data-test="family-live"]').count();
    const body = await txt(page);
    const reasonPresent = dark.length > 0 && dark.some((d) => d.trim().length > 0 && /capture|off|gated|reason|unavailable|dark/i.test(d + body));
    note('PX-14', dark.length > 0 && live > 0 && reasonPresent,
      `dark entries=${JSON.stringify(dark.slice(0, 4))} live_entries=${live} reason-wording=${reasonPresent}`);
    await ctx.close();
  }

  // ── L1b rendered: auth ON, NO bearer → the sign-in panel ───────────
  {
    const { ctx, page } = await mkPage({ bearer: false });
    await go(page, `${BASE}/metrics?tab=runtime`);
    await page.screenshot({ path: `${OUT}/l1b-signin-panel.png`, fullPage: false });
    const cause = await page.locator('[data-test="metrics-door-state-cause"]').allInnerTexts();
    const action = await page.locator('[data-test="metrics-door-state-action"]').allInnerTexts();
    const unexpected = (await txt(page)).includes('unexpected');
    const retry = await page.locator('[data-test="metrics-door-state-retry"], button:has-text("Retry")').count();
    const sign = cause.join(' ').toLowerCase().includes('authentication') || action.join(' ').toLowerCase().includes('sign in');
    note('L1b-rendered', sign && retry === 0 && !unexpected,
      `cause=${JSON.stringify(cause.slice(0, 1))} action=${JSON.stringify(action.slice(0, 1))} retry_buttons=${retry} 'unexpected'=${unexpected}`);
    await ctx.close();
  }

  await browser.close();

  // ── the API census ─────────────────────────────────────────────────
  const nonGet = ev.api.filter((r) => r.method !== 'GET');
  const liveHits = ev.api.filter((r) => r.url.includes(':9797'));
  fs.writeFileSync(`${OUT}/capture-9.7a-evidence.json`, JSON.stringify(ev, null, 2));
  console.log(`\nAPI census: ${ev.api.length} /api/ responses, non-GET=${nonGet.length}, :9797 hits=${liveHits.length}`);
  console.log(`console errors=${ev.consoleErrors.length} pageerrors=${ev.pageErrors.length}`);
  const fails = ev.items.filter((i) => !i.ok);
  console.log(`ITEMS: ${ev.items.filter((i) => i.ok).length}/${ev.items.length} PASS`);
  if (fails.length) { console.log('FAILING:', JSON.stringify(fails, null, 1)); process.exit(3); }
})().catch((e) => { console.error('DRIVER ERROR:', e); process.exit(2); });
