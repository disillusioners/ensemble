/**
 * phase1_mixed.js — MIXED dataset assertions for the job-queue indicator
 * "Live conversations" idle-hiding feature. Driven by Playwright-as-library
 * (frontend/node_modules/playwright, Chromium headless).
 *
 * Exits 0 = all assertions pass, 1 = assertion failure (details on stdout),
 * 2 = environment failure (page never became interactive).
 * Evidence (panel DOM text + screenshot) -> EVID_DIR (default /tmp/fe-idle-hide-smoke).
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '..', '..', 'frontend', 'node_modules', 'playwright'));

const EVID_DIR = process.env.EVID_DIR || '/tmp/fe-idle-hide-smoke';
const BASE_URL = process.env.BASE_URL || 'http://localhost:4199';

const RUN_ROOT = 'MOCK RUN-ROOT RW2';
const ORPHAN = 'MOCK ORPHAN-CHILD OP9';
const DONE_ROOT = 'MOCK DONE-ROOT DN5';
const ERR_ROOT = 'MOCK ERR-ROOT ER8';
const IDLE_ROOT = 'MOCK IDLE-ROOT QW7';
const IDLE_CHILD = 'MOCK IDLE-CHILD QC3';
const RCPT_IDLE_PARENT = 'MOCK-RCPT-IDLEPARENT Z4';
const RCPT_RUNROOT = 'MOCK-RCPT-RUNROOT J1';
const RCPT_ORPHAN = 'MOCK-RCPT-ORPHAN K2';

function out(line) { process.stdout.write(line + '\n'); }

(async () => {
  fs.mkdirSync(EVID_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  page.on('pageerror', (err) => pageErrors.push(String(err)));

  // 1) boot
  await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const trigger = page.locator('button[aria-label="View job queue"]');
  await trigger.waitFor({ state: 'visible', timeout: 45000 });
  out('BOOT: app shell + job-queue trigger visible');

  // 2) open the indicator menu; wait for the panel and its data.
  //    Poll tick is 8s; panel-open fires listInstanceTreeFull immediately.
  await trigger.click();
  const panel = page.locator('.job-queue-panel');
  await panel.waitFor({ state: 'visible', timeout: 15000 });
  out('PANEL: opened');

  // Wait until the panel text contains at least one MOCK name (data arrived),
  // up to ~16s (two poll ticks) — panel-open fetch usually lands faster.
  let panelText = '';
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    panelText = (await panel.innerText().catch(() => '')) || '';
    if (panelText.includes(RUN_ROOT) || panelText.includes('No jobs')) break;
    await page.waitForTimeout(750);
  }
  fs.writeFileSync(path.join(EVID_DIR, 'phase1_panel.txt'), panelText);
  await page.screenshot({ path: path.join(EVID_DIR, 'phase1_mixed.png'), fullPage: false });
  out('EVIDENCE: ' + path.join(EVID_DIR, 'phase1_mixed.png') + ', phase1_panel.txt');

  // 3) assertions
  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail });

  // NOTE: .section-title renders text-transform:uppercase, and innerText
  // reflects the TRANSFORMED text — match case-insensitively.
  const lower = panelText.toLowerCase();
  check('section "Live conversations" present', lower.includes('live conversations'),
    lower.includes('live conversations') ? 'section-title found' : 'MISSING section title');
  check('(b) RUN-ROOT visible', panelText.includes(RUN_ROOT),
    panelText.includes(RUN_ROOT) ? 'found in panel' : 'NOT FOUND');
  check('(d) ORPHAN-CHILD visible (promoted)', panelText.includes(ORPHAN),
    panelText.includes(ORPHAN) ? 'found in panel' : 'NOT FOUND');
  check('(e) DONE-ROOT (terminal completed) visible', panelText.includes(DONE_ROOT),
    panelText.includes(DONE_ROOT) ? 'found in panel' : 'NOT FOUND');
  check('(f) ERR-ROOT (terminal error) visible', panelText.includes(ERR_ROOT),
    panelText.includes(ERR_ROOT) ? 'found in panel' : 'NOT FOUND');
  check('(a) IDLE-ROOT hidden', !panelText.includes(IDLE_ROOT),
    !panelText.includes(IDLE_ROOT) ? 'absent from panel' : 'LEAKED into panel');
  check('(c) IDLE-CHILD hidden', !panelText.includes(IDLE_CHILD),
    !panelText.includes(IDLE_CHILD) ? 'absent from panel' : 'LEAKED into panel');
  check('(g) receipt bound to hidden IDLE-ROOT still visible', panelText.includes(RCPT_IDLE_PARENT),
    panelText.includes(RCPT_IDLE_PARENT) ? 'found in panel' : 'NOT FOUND');
  check('(g) receipts on live rows visible', panelText.includes(RCPT_RUNROOT) && panelText.includes(RCPT_ORPHAN),
    `runroot=${panelText.includes(RCPT_RUNROOT)} orphan=${panelText.includes(RCPT_ORPHAN)}`);

  // Structural: ORPHAN-CHILD renders at root depth (aria-level="1") because its
  // idle parent was filtered out of the wire page (orphan-promotes-to-root).
  const orphanLevel = await page.locator('li[role="treeitem"]', { hasText: ORPHAN })
    .first().getAttribute('aria-level').catch(() => null);
  check('(d) ORPHAN-CHILD rendered as a ROOT (aria-level=1)', orphanLevel === '1',
    `aria-level=${orphanLevel}`);
  // RUN-ROOT stays a root (aria-level=1) too
  const runLevel = await page.locator('li[role="treeitem"]', { hasText: RUN_ROOT })
    .first().getAttribute('aria-level').catch(() => null);
  check('(b) RUN-ROOT rendered as a ROOT (aria-level=1)', runLevel === '1',
    `aria-level=${runLevel}`);

  // 4) report
  let failed = 0;
  out('--- ASSERTIONS ---');
  for (const r of results) {
    out(`${r.pass ? 'PASS' : 'FAIL'} | ${r.name} | ${r.detail}`);
    if (!r.pass) failed++;
  }

  // Console noise report (expected: 404s for unmocked endpoints)
  const uncaught = pageErrors;
  out('--- CONSOLE ---');
  out(`uncaught page errors: ${uncaught.length}${uncaught.length ? ' -> ' + JSON.stringify(uncaught) : ''}`);
  const err404 = consoleErrors.filter((t) => /404|Failed to load resource/.test(t));
  const other = consoleErrors.filter((t) => !/404|Failed to load resource/.test(t));
  out(`console error totals: ${consoleErrors.length} (404/resource-class: ${err404.length}, other: ${other.length})`);
  for (const t of other.slice(0, 10)) out(`  OTHER-ERR: ${t.slice(0, 200)}`);

  await browser.close();
  out(`RESULT: ${failed === 0 && uncaught.length === 0 ? 'PASS' : 'FAIL'} (${results.length - failed}/${results.length} assertions)`);
  process.exit(failed === 0 && uncaught.length === 0 ? 0 : 1);
})().catch((e) => {
  out('ENV-FAIL: ' + (e && e.message ? e.message.split('\n')[0] : String(e)));
  process.exit(2);
});
