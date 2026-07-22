/**
 * E2E test pack: Job Queue Status Indicator Redesign
 *
 * Validates 5 scenarios against the redesigned header indicator:
 *   A) Header shows "X/Y" text format (not icon+badge)
 *   B) Clicking X/Y button opens a Material dropdown menu
 *   C) Dropdown contains Running + Recent sections
 *   D) Hovering X/Y button shows tooltip
 *   E) Clicking a job row navigates (if jobs exist)
 *
 * Uses Playwright (node, not python) since @playwright/test is in frontend.
 *
 * Dual-layer timeout: This script has a 4.5min internal hard cap; the
 * outer bash `timeout 300` adds the second layer.
 */
const { chromium } = require('playwright');

const URL = 'http://localhost:4199';
const SCREENSHOT_DIR = __dirname + '/screenshots';
const INTERNAL_TIMEOUT_MS = 270000; // 4.5 min inner guard
const NAV_TIMEOUT_MS = 30000;

// Results collector
const results = {};
let browser;

async function run() {
  const deadline = Date.now() + INTERNAL_TIMEOUT_MS;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(NAV_TIMEOUT_MS);

  function remainingMs() {
    return Math.max(0, deadline - Date.now());
  }

  // --- Navigate to the app ---
  console.log('[setup] Navigating to ' + URL + ' ...');
  await page.goto(URL, { waitUntil: 'networkidle', timeout: Math.min(NAV_TIMEOUT_MS, remainingMs()) }).catch(e => {
    console.log('[setup] networkidle failed (' + e.message.slice(0, 80) + '), retrying with domcontentloaded');
  });
  // fallback: ensure we at least have DOM
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: Math.min(NAV_TIMEOUT_MS, remainingMs()) });

  // Wait for the app-shell / header to render. The indicator button is .queue-button
  console.log('[setup] Waiting for app-header + queue-button ...');
  await page.waitForSelector('app-job-queue-indicator button.queue-button', {
    timeout: Math.min(NAV_TIMEOUT_MS, remainingMs()),
  });

  // Give Angular a moment to settle signal updates (initial poll fires on init)
  await page.waitForTimeout(2000);

  // =========================================================
  // SCENARIO A: Header shows "X/Y" text format
  // =========================================================
  console.log('\n--- Scenario A: X/Y text format ---');
  try {
    const countSpan = page.locator('app-job-queue-indicator .queue-count');
    await countSpan.waitFor({ state: 'visible', timeout: 10000 });
    const text = (await countSpan.textContent()).trim();
    console.log('  Found text: "' + text + '"');
    const xyMatch = /^\d+\/\d+$/.test(text);
    results.A = xyMatch ? 'PASS' : 'FAIL';
    console.log('  X/Y regex match: ' + xyMatch);

    // Screenshot: header showing X/Y text
    await page.screenshot({
      path: SCREENSHOT_DIR + '/scenario-a-xy-text.png',
      fullPage: false,
    });
    console.log('  Screenshot: scenario-a-xy-text.png');

    // Also verify it's a button (not icon with badge)
    const isButton = await page.locator('button.queue-button').count();
    console.log('  queue-button count: ' + isButton);
    if (isButton < 1) results.A = 'FAIL';
  } catch (e) {
    results.A = 'FAIL';
    console.log('  ERROR: ' + e.message.slice(0, 200));
  }

  // =========================================================
  // SCENARIO B: Clicking the X/Y button opens a dropdown menu
  // =========================================================
  console.log('\n--- Scenario B: Click opens dropdown ---');
  let dropdownOpened = false;
  try {
    const btn = page.locator('button.queue-button');
    await btn.click();

    // Material menu overlay renders the panel. Wait for it.
    const panel = page.locator('.job-queue-panel, .cdk-overlay-pane .job-queue-dropdown');
    await panel.first().waitFor({ state: 'visible', timeout: 8000 });
    dropdownOpened = true;

    // Verify it's an overlay (not a page navigation)
    const urlAfterClick = page.url();
    const isStillOnLanding = urlAfterClick === URL || urlAfterClick === URL + '/';
    console.log('  URL after click: ' + urlAfterClick + ' (still landing: ' + isStillOnLanding + ')');

    // Verify overlay pane exists (Material CDK overlay)
    const overlayCount = await page.locator('.cdk-overlay-pane').count();
    console.log('  cdk-overlay-pane count: ' + overlayCount);

    results.B = (dropdownOpened && overlayCount > 0) ? 'PASS' : 'FAIL';

    // Screenshot: open dropdown
    await page.screenshot({
      path: SCREENSHOT_DIR + '/scenario-b-dropdown-open.png',
      fullPage: false,
    });
    console.log('  Screenshot: scenario-b-dropdown-open.png');
  } catch (e) {
    results.B = 'FAIL';
    console.log('  ERROR: ' + e.message.slice(0, 200));
  }

  // =========================================================
  // SCENARIO C: Dropdown contains Running + Recent sections
  // =========================================================
  console.log('\n--- Scenario C: Running + Recent sections ---');
  try {
    // Panel should already be open from Scenario B. Re-open if needed.
    const panelVisible = await page.locator('.job-queue-panel').isVisible().catch(() => false);
    if (!panelVisible) {
      await page.locator('button.queue-button').click();
      await page.locator('.job-queue-panel').waitFor({ state: 'visible', timeout: 8000 });
    }

    // Check for either sections OR empty-state
    const sectionTitles = await page.locator('.job-queue-panel .section-title').allTextContents();
    console.log('  Section titles found: ' + JSON.stringify(sectionTitles));
    const hasRunning = sectionTitles.some(t => /running/i.test(t));
    const hasRecent = sectionTitles.some(t => /recent/i.test(t));

    const emptyState = await page.locator('.job-queue-panel .empty-state').isVisible().catch(() => false);
    console.log('  Empty state visible: ' + emptyState);

    // Pass if both sections present OR empty state shown (valid alternative)
    if (hasRunning && hasRecent) {
      results.C = 'PASS';
    } else if (emptyState) {
      results.C = 'PASS'; // empty state is valid — sections appear when jobs exist
      console.log('  (empty state — no jobs, sections hidden by design)');
    } else {
      results.C = 'FAIL';
    }
  } catch (e) {
    results.C = 'FAIL';
    console.log('  ERROR: ' + e.message.slice(0, 200));
  }

  // Close dropdown before testing tooltip
  if (dropdownOpened) {
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(500);
  }

  // =========================================================
  // SCENARIO D: Hovering the X/Y button shows tooltip
  // =========================================================
  console.log('\n--- Scenario D: Hover shows tooltip ---');
  try {
    const btn = page.locator('button.queue-button');
    await btn.hover();
    // Material tooltip appears with a delay; wait for it
    const tooltip = page.locator('.mat-mdc-tooltip, .mat-tooltip');
    await tooltip.first().waitFor({ state: 'visible', timeout: 6000 });
    const tooltipText = (await tooltip.first().textContent()).trim();
    console.log('  Tooltip text: "' + tooltipText + '"');
    const hasRunningPending = /Running:\s*\d+\s*\/\s*Pending:\s*\d+/i.test(tooltipText);
    results.D = hasRunningPending ? 'PASS' : 'FAIL';

    // Screenshot: tooltip on hover
    await page.screenshot({
      path: SCREENSHOT_DIR + '/scenario-d-tooltip.png',
      fullPage: false,
    });
    console.log('  Screenshot: scenario-d-tooltip.png');
  } catch (e) {
    results.D = 'FAIL';
    console.log('  ERROR: ' + e.message.slice(0, 200));
  }

  // =========================================================
  // SCENARIO E: Clicking a job row navigates
  // =========================================================
  console.log('\n--- Scenario E: Job row click navigates ---');
  try {
    // Open dropdown
    await page.locator('button.queue-button').click();
    await page.locator('.job-queue-panel').waitFor({ state: 'visible', timeout: 8000 });

    const jobRows = page.locator('.job-queue-panel .job-row');
    const rowCount = await jobRows.count();
    console.log('  Job rows found: ' + rowCount);

    if (rowCount === 0) {
      results.E = 'SKIPPED';
      console.log('  No job rows — scenario skipped (valid: empty queue)');
    } else {
      const urlBefore = page.url();
      await jobRows.first().click();
      await page.waitForTimeout(2000);
      const urlAfter = page.url();
      console.log('  URL before: ' + urlBefore);
      console.log('  URL after:  ' + urlAfter);
      const navigated = urlAfter !== urlBefore && /\/projects\//.test(urlAfter);
      results.E = navigated ? 'PASS' : 'FAIL';
    }
  } catch (e) {
    results.E = 'FAIL';
    console.log('  ERROR: ' + e.message.slice(0, 200));
  }

  // --- Final report ---
  console.log('\n=== RESULTS ===');
  for (const [k, v] of Object.entries(results)) {
    console.log('Scenario ' + k + ': ' + v);
  }
  const failures = Object.values(results).filter(v => v === 'FAIL').length;
  console.log('\n=== Test Pack: job_queue_indicator_e2e ===');
  if (failures > 0) {
    console.log('RESULT: FAIL (' + (Object.keys(results).length - failures) + '/' + Object.keys(results).length + ' passed)');
    return 1;
  } else {
    console.log('RESULT: PASS');
    return 0;
  }
}

// Main with timeout guard
const runPromise = run();
const timeoutPromise = new Promise((_, reject) =>
  setTimeout(() => reject(new Error('INTERNAL_TIMEOUT')), INTERNAL_TIMEOUT_MS)
);

Promise.race([runPromise, timeoutPromise])
  .then((code) => {
    if (browser) browser.close().finally(() => process.exit(code));
    else process.exit(code);
  })
  .catch(async (err) => {
    console.log('\nERROR: ' + err.message);
    console.log('\n=== Test Pack: job_queue_indicator_e2e ===');
    console.log('RESULT: TIMEOUT');
    try { if (browser) await browser.close(); } catch (_) {}
    process.exit(124);
  });
