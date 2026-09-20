/**
 * phase2_allidle.js — ALLIDLE dataset: empty-state + responsiveness assertions.
 * Assumes the mock BE was restarted with DATASET=allidle BEFORE this script
 * starts (the FE poll picks it up within 1-2 ticks; we also reopen the menu to
 * force the panel-open listInstanceTreeFull fetch).
 *
 * Exits 0 = pass, 1 = assertion failure, 2 = environment failure.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '..', '..', 'frontend', 'node_modules', 'playwright'));

const EVID_DIR = process.env.EVID_DIR || '/tmp/fe-idle-hide-smoke';
const BASE_URL = process.env.BASE_URL || 'http://localhost:4199';

function out(line) { process.stdout.write(line + '\n'); }

(async () => {
  fs.mkdirSync(EVID_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  page.on('pageerror', (err) => pageErrors.push(String(err)));

  await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const trigger = page.locator('button[aria-label="View job queue"]');
  await trigger.waitFor({ state: 'visible', timeout: 45000 });
  out('BOOT: app shell + job-queue trigger visible');

  const panel = page.locator('.job-queue-panel');

  // Open #1 — may still show MIXED-stale data (retained payload).
  await trigger.click();
  await panel.waitFor({ state: 'visible', timeout: 15000 });
  await page.waitForTimeout(2500);
  await page.keyboard.press('Escape');
  await panel.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  out('POLL-LEG: first open/close done (waiting one 8s poll tick for ALLIDLE data)');

  // Wait one full poll tick (>8s) so the 8s forkJoin fetches ALLIDLE payloads.
  await page.waitForTimeout(9500);

  // Open #2 — panel-open fetch fires listInstanceTreeFull against ALLIDLE mock.
  await trigger.click();
  await panel.waitFor({ state: 'visible', timeout: 15000 });
  let panelText = '';
  const deadline = Date.now() + 16000; // allow up to two more ticks
  while (Date.now() < deadline) {
    panelText = (await panel.innerText().catch(() => '')) || '';
    if (panelText.includes('No jobs')) break;
    await page.waitForTimeout(1000);
  }
  fs.writeFileSync(path.join(EVID_DIR, 'phase2_panel.txt'), panelText);
  await page.screenshot({ path: path.join(EVID_DIR, 'phase2_allidle.png'), fullPage: false });
  out('EVIDENCE: ' + path.join(EVID_DIR, 'phase2_allidle.png') + ', phase2_panel.txt');

  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail });

  check('empty state shows "No jobs"', panelText.includes('No jobs'),
    panelText.includes('No jobs') ? 'empty-title found' : 'NOT FOUND — panel text head: ' + JSON.stringify(panelText.slice(0, 160)));
  check('graceful subtitle "Queue is currently idle"', panelText.includes('Queue is currently idle'),
    panelText.includes('Queue is currently idle') ? 'subtitle found' : 'NOT FOUND');
  check('no MOCK instance rows leak', !panelText.includes('MOCK '),
    !panelText.includes('MOCK ') ? 'no MOCK names in panel' : 'LEAK: ' + JSON.stringify(panelText.slice(0, 200)));

  // App still responsive: shell interactive, evaluate round-trips, trigger still visible.
  const triggerStillThere = await trigger.isVisible();
  const evalOk = (await page.evaluate(() => 21 * 2)) === 42;
  check('app responsive after empty state', triggerStillThere && evalOk,
    `triggerVisible=${triggerStillThere} evalOk=${evalOk}`);

  let failed = 0;
  out('--- ASSERTIONS ---');
  for (const r of results) {
    out(`${r.pass ? 'PASS' : 'FAIL'} | ${r.name} | ${r.detail}`);
    if (!r.pass) failed++;
  }
  out('--- CONSOLE ---');
  out(`uncaught page errors: ${pageErrors.length}${pageErrors.length ? ' -> ' + JSON.stringify(pageErrors) : ''}`);
  const err404 = consoleErrors.filter((t) => /404|Failed to load resource/.test(t));
  const other = consoleErrors.filter((t) => !/404|Failed to load resource/.test(t));
  out(`console error totals: ${consoleErrors.length} (404/resource-class: ${err404.length}, other: ${other.length})`);
  for (const t of other.slice(0, 10)) out(`  OTHER-ERR: ${t.slice(0, 200)}`);

  await browser.close();
  out(`RESULT: ${failed === 0 && pageErrors.length === 0 ? 'PASS' : 'FAIL'} (${results.length - failed}/${results.length} assertions)`);
  process.exit(failed === 0 && pageErrors.length === 0 ? 0 : 1);
})().catch((e) => {
  out('ENV-FAIL: ' + (e && e.message ? e.message.split('\n')[0] : String(e)));
  process.exit(2);
});
