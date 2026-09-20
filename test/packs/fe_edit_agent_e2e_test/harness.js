// Playwright-as-library harness for fe_edit_agent_e2e_test.
//
// Spawns the mock server in-process, drives a bundled headless Chromium
// against http://127.0.0.1:10180, executes the 5 acceptance scenarios,
// captures screenshots + payloads + console transcripts, prints RESULT line.
//
// Exit codes:
//   0  PASS
//   1  FAIL (one or more scenarios failed)
//   2  setup error (server boot / Playwright launch / dist missing)

'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require(
  path.resolve(__dirname, '../../../frontend/node_modules/playwright')
);

const mock = require('./mock_server.js');

const EVIDENCE = process.env.FE_SMOKE_EVIDENCE_DIR ||
  '/tmp/fe-edit-agent-smoke-20260916';
const BASE_URL = process.env.FE_SMOKE_BASE_URL || 'http://127.0.0.1:10180';

const HEADLESS = true;
const NAV_TIMEOUT_MS = 30000;
const ACTION_TIMEOUT_MS = 10000;

// ── scenario results accumulator ─────────────────────────────────────────
const results = [];
const consoleLog = []; // page-level console messages
const pageErrors = []; // uncaught page errors

function recordScenario(name, pass, summary) {
  results.push({ name, pass, summary });
  console.log(`  [${pass ? 'PASS' : 'FAIL'}] ${name} — ${summary}`);
}

async function screenshot(page, name) {
  const file = path.join(EVIDENCE, `screenshot-${name}.png`);
  try {
    await page.screenshot({ path: file, fullPage: false });
    return file;
  } catch (e) {
    console.warn(`[harness] screenshot failed (${name}):`, e.message);
    return null;
  }
}

// Open the agent SearchableSelect input by field label. Slack: "Default Agent";
// we use a stable selector that scopes to the active modal.
async function getAgentInput(page) {
  // The input lives inside <app-searchable-select> rendered for a 'select'
  // Simple field. Placeholder is "Select an agent...". There's only ever one
  // such field per modal at a time (telegram/slack default_agent OR discord
  // agent, not both simultaneously).
  return page.locator(
    'app-searchable-select input[placeholder="Select an agent..."]'
  );
}

// Wait for the dropdown panel to be open and contain at least one mat-option.
// Material renders the panel as a <div class="mat-mdc-autocomplete-panel ...">
// appended to .cdk-overlay-container; the `mat-mdc-autocomplete-visible` class
// is the open-state signal.
async function waitForPanelOpen(page, timeout = ACTION_TIMEOUT_MS) {
  const panel = page.locator('.mat-mdc-autocomplete-panel.mat-mdc-autocomplete-visible');
  await panel.first().waitFor({ state: 'visible', timeout });
  await page.locator('mat-option').first().waitFor({ state: 'visible', timeout });
}

// Read the most recent captured request matching (method,path). We don't
// filter by X-Smoke-Scenario header because per-scenario boundaries are
// already enforced via clearCaptures() → writeScenarioPayloadFile(label, rec).
// Header-tagging turned out flaky (in-flight Angular HttpClient XHRs appear to
// race the context-level header update); the capture-file truncation is the
// reliable scenario fence.
function lastCaptureFor(method, pathPrefix) {
  if (!fs.existsSync(mock.CAPTURE_FILE)) return null;
  const lines = fs.readFileSync(mock.CAPTURE_FILE, 'utf8').trim().split('\n').filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const rec = JSON.parse(lines[i]);
      if (rec.method !== method) continue;
      if (!rec.path.startsWith(pathPrefix)) continue;
      return rec;
    } catch (_) {}
  }
  return null;
}

function captureCount() {
  if (!fs.existsSync(mock.CAPTURE_FILE)) return 0;
  return fs.readFileSync(mock.CAPTURE_FILE, 'utf8').trim().split('\n').filter(Boolean).length;
}

// Reset capture file at scenario boundary (each scenario gets a clean slate).
async function clearCaptures() {
  fs.writeFileSync(mock.CAPTURE_FILE, '');
}

// NOTE: per-scenario header tagging via Playwright extraHTTPHeaders was tried
// first but caused CORS preflight failures on cross-origin font fetches
// (fonts.gstatic.com Material Icons) without strengthening the scenario fence.
// clearCaptures() truncation is the reliable scenario boundary.

// Write a single-scenario payload file (numbered, for the per-scenario report).
function writeScenarioPayloadFile(scenarioName, record) {
  if (!record) {
    console.log(`  [payload-write] ${scenarioName}: skipped (no record)`);
    return;
  }
  const safe = scenarioName.replace(/[^a-zA-Z0-9_-]/g, '-');
  const method = record.method.toLowerCase();
  const pathSeg = record.path.replace(/[^a-zA-Z0-9_-]/g, '-');
  const file = path.join(EVIDENCE, `payload-${safe}-${method}${pathSeg}.json`);
  try {
    fs.writeFileSync(file, JSON.stringify(record.body, null, 2));
    console.log(`  [payload-write] ${scenarioName}: wrote ${path.basename(file)}`);
  } catch (e) {
    console.log(`  [payload-write] ${scenarioName}: FAILED ${e.message}`);
  }
}

// ── scenario helpers ─────────────────────────────────────────────────────
async function navigateToSources(page) {
  await page.goto(`${BASE_URL}/sources`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('app-source-list', { timeout: NAV_TIMEOUT_MS });
  // Wait for at least one source card to render (proves listSources + render).
  await page.waitForSelector('.source-card', { timeout: NAV_TIMEOUT_MS });
}

async function openEditModal(page, sourceName) {
  // Find the card by source name (h3.source-name) and click its edit-btn.
  const card = page.locator('.source-card', { has: page.locator(`h3.source-name:has-text("${sourceName}")`) });
  await card.first().waitFor({ state: 'visible' });
  await card.first().locator('button.edit-btn').click();
  // Wait for edit modal title
  await page.waitForSelector('h2:has-text("Edit Source")', { timeout: ACTION_TIMEOUT_MS });
  // Wait for agents to load + select to render (placeholder appears)
  await page.waitForSelector(
    'app-searchable-select input[placeholder="Select an agent..."]',
    { state: 'visible', timeout: ACTION_TIMEOUT_MS }
  );
  // Give the search effect one frame to settle before we interact.
  await page.waitForTimeout(200);
}

async function closeModal(page) {
  // Cancel button or close (X) — Cancel is the safer option since it doesn't
  // fire the PUT/POST.
  const cancel = page.locator('button:has-text("Cancel")');
  if (await cancel.first().isVisible().catch(() => false)) {
    await cancel.first().click();
  } else {
    // Fall back to X close button
    await page.locator('.close-btn').first().click().catch(() => {});
  }
  // Wait for modal to disappear (source-list re-renders)
  await page.waitForSelector('h2:has-text("Edit Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS }).catch(() => {});
}

async function selectAgentBySearch(page, partial, exactLabel, scenarioLabel) {
  // Focus + clear the input
  const input = await getAgentInput(page);
  await input.click();
  await input.fill('');                  // clear any preselected label
  await input.fill(partial);              // type partial (fires input event → panel opens)
  // Wait for filtered options
  await waitForPanelOpen(page);
  // Verify the input STILL shows the typed text (THE pre-fix regression check)
  const valueAfterType = await input.inputValue();
  await screenshot(page, `${scenarioLabel}-typed-${partial}`);
  if (valueAfterType !== partial) {
    return { pass: false, valueAfterType, valueAfterCD: null, payload: null, reason: `typed text wiped immediately: expected "${partial}", got "${valueAfterType}"` };
  }
  // Trigger additional change-detection cycles WITHOUT closing the panel:
  //   - typing more characters then deleting them fires input events
  //   - moving the mouse over the panel re-fires hover/CD
  //   - waiting 2s lets Angular's async tick + Material overlay CD run many cycles
  // The pre-fix bug re-fires the options-effect on each cycle, overwriting
  // displayText with the preselected label. With the WeakMap memo the effect
  // does not re-fire (options identity stable), so typed text persists.
  // NOTE: do NOT press Escape here — onPanelClosed intentionally restores the
  // selected label; that's a separate behavior, not the bug under test.
  await input.press('End');
  await page.keyboard.type(' ');                     // append a space
  await page.keyboard.press('Backspace');            // remove the space
  await page.mouse.move(200, 200);
  await page.mouse.move(400, 400);
  await page.waitForTimeout(1600);
  const valueAfterCD = await input.inputValue();
  if (valueAfterCD !== partial) {
    return { pass: false, valueAfterType, valueAfterCD, payload: null, reason: `typed text wiped after CD trigger + 1.6s wait: expected "${partial}", got "${valueAfterCD}"` };
  }
  // Click the option matching exactLabel.
  const opt = page.locator('mat-option', { hasText: new RegExp(`^\\s*${exactLabel}\\s*$`) });
  await opt.first().click();
  // Verify the control now displays the new agent.
  await page.waitForTimeout(250);
  const valueAfterSelect = await input.inputValue();
  if (valueAfterSelect.trim() !== exactLabel) {
    return { pass: false, valueAfterType, valueAfterCD: valueAfterSelect, payload: null, reason: `selection did not stick: expected "${exactLabel}", got "${valueAfterSelect}"` };
  }
  await screenshot(page, `${scenarioLabel}-selected-${exactLabel}`);
  return { pass: true, valueAfterType, valueAfterCD, payload: null, reason: null };
}

// ── scenarios ────────────────────────────────────────────────────────────
async function scenarioS1(page) {
  await clearCaptures();
  await navigateToSources(page);
  await screenshot(page, 'S1-01-sources-list');
  await openEditModal(page, 'S1 Slack Production');
  await screenshot(page, 'S1-02-modal-open-preselected-leader');
  const sel = await selectAgentBySearch(page, 'cod', 'coder', 'S1');
  if (!sel.pass) return { pass: false, summary: sel.reason };
  // Verify dropdown is NOT showing only the preselected — assert the panel
  // previously contained at least 2 filtered options (coder + code-reviewer).
  // We don't re-open here (panel closed on select) — re-typing would re-trigger
  // the bug if present. Instead, assert post-condition via input value + save.
  // Click Save Changes
  await page.locator('button:has-text("Save Changes")').click();
  await page.waitForSelector('h2:has-text("Edit Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS });
  // Wait for the PUT to land (UI snackbar appears, source-list updates)
  await page.waitForTimeout(400);
  const cap = lastCaptureFor('PUT', '/api/sources/');
  writeScenarioPayloadFile('S1', cap);
  if (!cap) return { pass: false, summary: 'no PUT captured' };
  await screenshot(page, 'S1-03-after-save');
  const agent = cap.body?.config?.default_agent;
  if (agent !== 'agent-coder') {
    return { pass: false, summary: `PUT config.default_agent = ${JSON.stringify(agent)}, expected "agent-coder"`, payload: cap.body };
  }
  return {
    pass: true,
    summary: `typed="${sel.valueAfterType}"→wait=ok→PUT /api/sources/src-slack-s1 config.default_agent=agent-coder`,
    payload: cap.body,
  };
}

async function scenarioS2(page) {
  await clearCaptures();
  await navigateToSources(page);
  // Click the global Add Source button (header right-side)
  await page.locator('button.add-btn:has-text("Add Source")').first().click();
  await page.waitForSelector('h2:has-text("Add New Source")', { timeout: ACTION_TIMEOUT_MS });
  // Fill required fields. Default source_type is 'telegram'. Required:
  //   source_id, name, bot_token (telegram).
  await page.locator('input.form-input').first().fill('src-add-s2');  // source_id
  await page.locator('input.form-input').nth(1).fill('S2 Add Test');  // name
  // Telegram bot_token is required: find input with placeholder containing "123456:ABC"
  await page.locator('input[placeholder^="123456:ABC"]').fill('123456:ABC-MOCK-TOKEN');
  // Wait for agent select to be present (agents load on ngOnInit, may take a tick)
  await page.waitForSelector(
    'app-searchable-select input[placeholder="Select an agent..."]',
    { state: 'visible', timeout: ACTION_TIMEOUT_MS }
  );
  await page.waitForTimeout(200);
  const sel = await selectAgentBySearch(page, 'test', 'tester', 'S2');
  if (!sel.pass) return { pass: false, summary: `ADD agent select failed: ${sel.reason}` };
  // Submit — AddSourceModal doesn't have "Save Changes"; uses default submit
  await page.locator('button[type="submit"]').first().click();
  await page.waitForSelector('h2:has-text("Add New Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS });
  await page.waitForTimeout(400);
  const cap = lastCaptureFor('POST', '/api/sources');
  writeScenarioPayloadFile('S2', cap);
  if (!cap) return { pass: false, summary: 'no POST /api/sources captured' };
  await screenshot(page, 'S2-after-save');
  const agent = cap.body?.config?.default_agent;
  if (agent !== 'agent-tester') {
    return { pass: false, summary: `POST config.default_agent = ${JSON.stringify(agent)}, expected "agent-tester"`, payload: cap.body };
  }
  return {
    pass: true,
    summary: `ADD typed="test"→PUT (POST) config.default_agent=agent-tester, source_type=telegram`,
    payload: cap.body,
  };
}

async function scenarioS3(page) {
  await clearCaptures();
  await navigateToSources(page);
  // S2 (telegram) has default_agent=null in our fixture
  await openEditModal(page, 'S2 Telegram Bot');
  // Verify the input is empty / blank (null preselected → labelFor returns '')
  const initialValue = await (await getAgentInput(page)).inputValue();
  await screenshot(page, 'S3-01-modal-null-preselect');
  // Now do the select
  const sel = await selectAgentBySearch(page, 'wan', 'wanderer', 'S3');
  if (!sel.pass) return { pass: false, summary: `S3 select failed (initial="${initialValue}"): ${sel.reason}` };
  await page.locator('button:has-text("Save Changes")').click();
  await page.waitForSelector('h2:has-text("Edit Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS });
  await page.waitForTimeout(400);
  const cap = lastCaptureFor('PUT', '/api/sources/');
  writeScenarioPayloadFile('S3', cap);
  if (!cap) return { pass: false, summary: 'no PUT captured' };
  const agent = cap.body?.config?.default_agent;
  if (agent !== 'agent-wanderer') {
    return { pass: false, summary: `PUT config.default_agent = ${JSON.stringify(agent)}, expected "agent-wanderer"`, payload: cap.body };
  }
  return {
    pass: true,
    summary: `null→typed="wan"→PUT config.default_agent=agent-wanderer`,
    payload: cap.body,
  };
}

async function scenarioS4(page) {
  await clearCaptures();
  await navigateToSources(page);
  await openEditModal(page, 'S1 Slack Production');
  // Verify the input shows "leader" (preselected)
  const initial = await (await getAgentInput(page)).inputValue();
  if (initial.trim() !== 'leader') {
    return { pass: false, summary: `expected preselect "leader", got "${initial}"` };
  }
  // Open the dropdown without typing, re-select "leader"
  await (await getAgentInput(page)).click();
  await waitForPanelOpen(page);
  await screenshot(page, 'S4-01-panel-open');
  const opt = page.locator('mat-option', { hasText: /^\s*leader\s*$/ });
  await opt.first().click();
  await page.waitForTimeout(200);
  const after = await (await getAgentInput(page)).inputValue();
  if (after.trim() !== 'leader') {
    return { pass: false, summary: `re-select didn't stick: "${after}"` };
  }
  await screenshot(page, 'S4-02-reselected');
  await page.locator('button:has-text("Save Changes")').click();
  await page.waitForSelector('h2:has-text("Edit Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS });
  await page.waitForTimeout(400);
  const cap = lastCaptureFor('PUT', '/api/sources/');
  writeScenarioPayloadFile('S4', cap);
  if (!cap) return { pass: false, summary: 'no PUT captured' };
  const agent = cap.body?.config?.default_agent;
  if (agent !== 'agent-leader') {
    return { pass: false, summary: `PUT config.default_agent = ${JSON.stringify(agent)}, expected "agent-leader"`, payload: cap.body };
  }
  return {
    pass: true,
    summary: `re-select "leader"→PUT config.default_agent=agent-leader`,
    payload: cap.body,
  };
}

async function scenarioS5(page) {
  await clearCaptures();
  await navigateToSources(page);
  // Open S1 (leader preselected), Cancel WITHOUT saving.
  await openEditModal(page, 'S1 Slack Production');
  await closeModal(page);
  // Now open S3 (wanderer preselected)
  await openEditModal(page, 'S3 Slack Staging');
  // Verify the input shows "wanderer" (no stale state from S1's "leader")
  const initial = await (await getAgentInput(page)).inputValue();
  if (initial.trim() !== 'wanderer') {
    return { pass: false, summary: `S3 preselect stale from S1: got "${initial}", expected "wanderer"` };
  }
  const sel = await selectAgentBySearch(page, 'rev', 'reviewer', 'S5');
  if (!sel.pass) return { pass: false, summary: `S5 select failed: ${sel.reason}` };
  await page.locator('button:has-text("Save Changes")').click();
  await page.waitForSelector('h2:has-text("Edit Source")', { state: 'detached', timeout: ACTION_TIMEOUT_MS });
  await page.waitForTimeout(400);
  const cap = lastCaptureFor('PUT', '/api/sources/');
  writeScenarioPayloadFile('S5', cap);
  if (!cap) return { pass: false, summary: 'no PUT captured' };
  await screenshot(page, 'S5-after-save');
  const wrongId = cap.source_id !== 'src-slack-s3';
  const agent = cap.body?.config?.default_agent;
  if (wrongId) {
    return { pass: false, summary: `PUT routed to wrong source: ${cap.source_id}, expected src-slack-s3`, payload: cap.body };
  }
  if (agent !== 'agent-reviewer') {
    return { pass: false, summary: `PUT config.default_agent = ${JSON.stringify(agent)}, expected "agent-reviewer"`, payload: cap.body };
  }
  return {
    pass: true,
    summary: `S3 switch→typed="rev"→PUT /api/sources/src-slack-s3 config.default_agent=agent-reviewer (no S1 staleness)`,
    payload: cap.body,
  };
}

// ── main ────────────────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(path.join(mock.DIST_DIR || path.resolve(__dirname, '../../../frontend/dist/frontend/browser'), 'index.html'))) {
    console.error('[harness] FAIL: frontend/dist/frontend/browser/index.html not found. Run `npm run build` first.');
    process.exit(2);
  }
  fs.mkdirSync(EVIDENCE, { recursive: true });

  console.log('[harness] starting mock server on 127.0.0.1:10180 ...');
  const addr = await mock.start(10180);

  let browser;
  try {
    console.log('[harness] launching headless chromium ...');
    browser = await chromium.launch({ headless: HEADLESS });
    const context = await browser.newContext({
      viewport: { width: 1400, height: 900 },
      // Notifications often stream forever — silence them in the page log
      // via a per-page console sink (we filter at print time, not source).
    });
    const page = await context.newPage();
    page.setDefaultTimeout(ACTION_TIMEOUT_MS);
    page.setDefaultNavigationTimeout(NAV_TIMEOUT_MS);

    page.on('console', (msg) => {
      const text = `[${msg.type()}] ${msg.text()}`;
      consoleLog.push(text);
    });
    page.on('pageerror', (err) => {
      pageErrors.push(err.message);
    });

    // ── run scenarios ──
    const scenarios = [
      ['S1-edit-different-agent', scenarioS1],
      ['S2-add-flow-regression', scenarioS2],
      ['S3-null-default-agent', scenarioS3],
      ['S4-reselect-same-agent', scenarioS4],
      ['S5-switch-sources-no-stale-state', scenarioS5],
    ];
    for (const [name, fn] of scenarios) {
      console.log(`\n[harness] === ${name} ===`);
      try {
        const r = await fn(page);
        recordScenario(name, !!r.pass, r.summary + (r.payload ? ` | payload=${JSON.stringify(r.payload)}` : ''));
      } catch (err) {
        await screenshot(page, `${name}-exception`).catch(() => {});
        recordScenario(name, false, `EXCEPTION: ${err.message}`);
      }
      // Always dismiss any leftover dialog between scenarios
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(150);
    }

    // ── finalize ──
    fs.writeFileSync(
      path.join(EVIDENCE, 'console.log'),
      consoleLog.join('\n') + '\n'
    );
    fs.writeFileSync(
      path.join(EVIDENCE, 'page-errors.log'),
      pageErrors.join('\n') + '\n'
    );

    const pass = results.filter((r) => r.pass).length;
    const total = results.length;
    console.log(`\n=== Test Pack: fe_edit_agent_e2e_test ===`);
    for (const r of results) {
      console.log(`  ${r.pass ? 'PASS' : 'FAIL'}: ${r.name}`);
    }
    console.log(`Console messages captured: ${consoleLog.length}`);
    console.log(`Page errors captured:    ${pageErrors.length}`);
    console.log(`Captured PUT/POST bodies: ${captureCount()}`);
    const verdict = pass === total ? 'PASS' : 'FAIL';
    console.log(`RESULT: ${verdict} (${pass}/${total} scenarios)`);

    await context.close();
    await browser.close();

    process.exit(verdict === 'PASS' ? 0 : 1);
  } catch (e) {
    console.error('[harness] fatal:', e);
    if (browser) await browser.close().catch(() => {});
    process.exit(2);
  } finally {
    await mock.stop();
  }
}

main();

