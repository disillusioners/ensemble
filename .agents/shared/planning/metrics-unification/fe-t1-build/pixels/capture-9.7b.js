// Phase 9 — task 9.7b: PX-8, the AUTH_NOT_CONFIGURED actionable empty state
// (auth OFF). 1600×1000 AND 900×1000. Also the L2/L4 tab-1-still-lit proof.
const { chromium } = require('/home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend/node_modules/playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8079';
const OUT = '/tmp/fe-t1-pixels';
const ev = { items: [], api: [] };
const note = (id, ok, detail) => { ev.items.push({ id, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${id}: ${detail}`); };

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });

  const mkPage = async (width, height) => {
    const ctx = await browser.newContext({ viewport: { width, height } });
    const page = await ctx.newPage();
    page.on('response', (r) => { if (r.url().includes('/api/')) ev.api.push({ method: r.request().method(), status: r.status(), url: r.url() }); });
    return { ctx, page };
  };
  const go = async (page, url) => {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
    await page.waitForTimeout(3000);
  };

  for (const [w, h, tag] of [[1600, 1000, '1600'], [900, 1000, '900']]) {
    const { ctx, page } = await mkPage(w, h);
    await go(page, `${BASE}/metrics?tab=runtime`);
    await page.screenshot({ path: `${OUT}/px08-auth-not-configured-${tag}x${h}.png`, fullPage: false });

    const cause = await page.locator('[data-test="metrics-door-state-cause"]').allInnerTexts();
    const meaning = await page.locator('[data-test="metrics-door-state-meaning"]').allInnerTexts();
    const action = await page.locator('[data-test="metrics-door-state-action"]').allInnerTexts();
    const pointer = await page.locator('[data-test="metrics-door-state-pointer"]').allInnerTexts();
    const retry = await page.locator('button:has-text("Retry"), [data-test="metrics-door-state-retry"]').count();
    const panels = await page.locator('[data-test="metrics-door-state-cause"]').count();
    const body = await page.evaluate(() => document.body.innerText);
    const chip = (body.match(/metrics door:\s*\S+/g) || []).slice(0, 3);
    const chipGreen = /metrics door:\s*ok/i.test(body);
    const noDup = panels === 1;
    const runbook = pointer.join(' ').includes('docs/runbooks/oauth2-auth.md');
    const causeNamed = cause.join(' ').toLowerCase().includes('auth is not configured');
    const centralDark = meaning.join(' ').toLowerCase().includes('no metrics');

    // tab 1 STILL LIT (AM-1): live strip + timeline with real values, no auth panel on tab 1
    await go(page, `${BASE}/metrics?tab=resource`);
    await page.screenshot({ path: `${OUT}/l2l4-tab1-still-lit-${tag}x${h}.png`, fullPage: false });
    const strip = await page.locator('[data-test="resource-live-strip"]').count();
    const timeline = await page.locator('[data-test="resource-timeline-panel"]').count();
    // scoped to the TAB BODY (the page-scoped catalog panel sits OUTSIDE .metrics-tabs-host by design — AM-1)
    const t1AuthPanels = await page.locator('.metrics-tabs-host [data-test="metrics-door-state-cause"]').count();
    const t1Body = await page.evaluate(() => document.body.innerText);
    const rssShown = /rss|MB/i.test(t1Body);

    note(`PX-8@${tag}`, noDup && retry === 0 && runbook && causeNamed && !chipGreen && centralDark,
      `${w}x${h}: cause_panels=${panels} (no duplicate banner: ${noDup}) cause_named=${causeNamed} runbook_pointer=${runbook} (${pointer.join(' ').slice(0, 40)}…) retry_buttons=${retry} chip=${JSON.stringify(chip)} chip_green=${chipGreen} central_dark=${centralDark}`);
    note(`L2/L4-tab1-lit@${tag}`, strip > 0 && timeline > 0 && t1AuthPanels === 0 && rssShown,
      `tab1: live_strip=${strip} timeline=${timeline} auth_panels_on_tab1=${t1AuthPanels} rss_text=${rssShown}`);

    // the 900 px collision check: the banner must not overflow horizontally
    if (w === 900) {
      const overflow = await page.evaluate(() => {
        const b = document.querySelector('[data-test="metrics-door-state-cause"]')?.closest('section,div');
        if (!b) return 'no-banner';
        return b.scrollWidth > b.clientWidth + 2 ? `overflow ${b.scrollWidth}>${b.clientWidth}` : 'none';
      });
      note('PX-8-900px-collision', overflow === 'none' || overflow === 'no-banner', `banner horizontal overflow: ${overflow}`);
    }
    await ctx.close();
  }

  await browser.close();
  const nonGet = ev.api.filter((r) => r.method !== 'GET');
  fs.writeFileSync(`${OUT}/capture-9.7b-evidence.json`, JSON.stringify(ev, null, 2));
  console.log(`\nAPI census: ${ev.api.length} responses, non-GET=${nonGet.length}`);
  const fails = ev.items.filter((i) => !i.ok);
  console.log(`ITEMS: ${ev.items.filter((i) => i.ok).length}/${ev.items.length} PASS`);
  if (fails.length) { console.log(JSON.stringify(fails, null, 1)); process.exit(3); }
})().catch((e) => { console.error('DRIVER ERROR:', e); process.exit(2); });
