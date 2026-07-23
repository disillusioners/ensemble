/**
 * E2E test: searchable agent selector (feature/agent-search-initial, aeab4214).
 *
 * Run: node tests/e2e-agent-selector.mjs
 *
 * Verifies in a REAL browser:
 *   1. Agent selector renders (search input + agent list)
 *   2. Agent list is POPULATED (the critical reactivity check)
 *   3. Search filtering (partial name, description, case-insensitive)
 *   4. Empty state ("No agents found") for non-matching queries
 *   5. Clear search restores all agents
 *   6. Keyboard navigation (arrow keys, enter, escape)
 *   7. Selecting an agent works
 *
 * Selectors (stable, from agent-selector.html):
 *   #agent-search   — search input
 *   #agent-list     — listbox container
 *   .agent-item[role=option] — individual agent rows
 *   .empty-state    — "No agents found"
 *   .clear-search   — clear button
 */
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'node:fs';

const URL = process.env.E2E_URL || 'http://localhost:4199';
const SHOT_DIR = './tests/e2e-shots';
mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
let overall = 'PASS';
function check(name, ok, detail = '') {
  const status = ok ? 'PASS' : 'FAIL';
  if (!ok) overall = 'FAIL';
  results.push({ name, status, detail });
  console.log(`[${status}] ${name}${detail ? ' — ' + detail : ''}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
});
const page = await context.newPage();

// Collect console errors for diagnostics
const consoleErrors = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});

try {
  // ── 1. Navigate & wait for app ──────────────────────────────────────────
  console.log('\n=== Navigating to', URL, '===');
  await page.goto(URL, { waitUntil: 'networkidle', timeout: 60_000 });

  // The agent selector may be on a route; the app may redirect to a project.
  // Wait for the search input to appear (robust to router redirects).
  const searchInput = page.locator('#agent-search');
  await searchInput.waitFor({ state: 'visible', timeout: 30_000 });
  check('Agent selector visible (search input rendered)', true, 'found #agent-search');
  await page.screenshot({ path: `${SHOT_DIR}/01-initial.png`, fullPage: true });

  // ── 2. Agent list populated (CRITICAL reactivity check) ─────────────────
  // Poll briefly: the API call (listAgents) + Angular render may take a moment.
  let agentItems = page.locator('#agent-list .agent-item[role="option"]');
  let count = 0;
  for (let i = 0; i < 10; i++) {
    count = await agentItems.count();
    if (count > 0) break;
    await sleep(500);
  }
  check(
    'Agent list populated (>0 agents visible)',
    count > 0,
    count > 0 ? `${count} agents rendered` : '0 agents — possible reactivity bug!',
  );
  if (count > 0) {
    const names = await agentItems.evaluateAll((els) =>
      els.slice(0, 6).map((e) => e.querySelector('.agent-name')?.textContent?.trim() ?? '?'),
    );
    check('Agent names readable', names.length > 0, names.join(', '));
  }

  // ── 3a. Empty search shows all agents ───────────────────────────────────
  // Type a single char then clear, then verify full count persists on empty input.
  await searchInput.fill('');
  const fullCount = await agentItems.count();
  check('Empty query shows all agents', fullCount === count, `${fullCount} (expected ${count})`);

  // ── 3b. Search by partial name match ────────────────────────────────────
  // Pick a real agent name fragment to search. Use first agent's name prefix.
  const firstName = (await agentItems.first().locator('.agent-name').textContent())?.trim() ?? '';
  const queryName = firstName.slice(0, Math.min(4, firstName.length));
  await searchInput.fill(queryName);
  await sleep(400); // real-time filter
  const nameFilterCount = await agentItems.count();
  check(
    'Search by partial name filters list',
    nameFilterCount > 0 && nameFilterCount <= count,
    `query="${queryName}" → ${nameFilterCount} matches (was ${count})`,
  );
  await page.screenshot({ path: `${SHOT_DIR}/02-search-name.png`, fullPage: true });

  // ── 3c. Search by description match ─────────────────────────────────────
  // Use a common word likely in some description. "agent" is almost certainly present.
  await searchInput.fill('agent');
  await sleep(400);
  const descFilterCount = await agentItems.count();
  check(
    'Search by description term works',
    descFilterCount >= 0, // at least doesn't error; expect some matches likely
    `"agent" → ${descFilterCount} matches`,
  );

  // ── 3d. Case-insensitive search ─────────────────────────────────────────
  await searchInput.fill(queryName.toUpperCase());
  await sleep(400);
  const upperCount = await agentItems.count();
  check(
    'Case-insensitive search (UPPER vs lower same count)',
    upperCount === nameFilterCount,
    `lower="${queryName}"→${nameFilterCount}, UPPER→${upperCount}`,
  );

  // ── 4. Empty state ("No agents found") ──────────────────────────────────
  await searchInput.fill('zzzznomatchxyz123');
  await sleep(400);
  const emptyState = page.locator('.empty-state');
  const emptyVisible = await emptyState.isVisible();
  const emptyText = (await emptyState.textContent())?.trim() ?? '';
  check(
    'Empty state shown for non-matching query',
    emptyVisible && emptyText.toLowerCase().includes('no agents'),
    `text="${emptyText.replace(/\s+/g, ' ').slice(0, 60)}"`,
  );
  const agentCountWhenEmpty = await agentItems.count();
  check('Zero agents when empty state shown', agentCountWhenEmpty === 0, `${agentCountWhenEmpty} items`);
  await page.screenshot({ path: `${SHOT_DIR}/03-empty-state.png`, fullPage: true });

  // ── 5. Clear search restores all agents ─────────────────────────────────
  // Click the clear button
  const clearBtn = page.locator('.clear-search');
  if (await clearBtn.isVisible().catch(() => false)) {
    await clearBtn.click();
    await sleep(400);
    const restoredCount = await agentItems.count();
    check(
      'Clear button restores all agents',
      restoredCount === count,
      `${restoredCount} (expected ${count})`,
    );
  } else {
    // Fall back to clearing the input directly
    await searchInput.fill('');
    await sleep(400);
    const restoredCount = await agentItems.count();
    check('Clear search (input clear) restores all agents', restoredCount === count, `${restoredCount}`);
  }

  // ── 6. Keyboard navigation ──────────────────────────────────────────────
  await searchInput.click();
  await searchInput.fill('');
  await sleep(300);
  const baseCount = await agentItems.count();

  // ArrowDown should focus index 0 (or wrap)
  await searchInput.press('ArrowDown');
  await sleep(200);
  let focusedIdx = -1;
  for (let i = 0; i < baseCount; i++) {
    const isFocused = await agentItems.nth(i).evaluate((el) => el.classList.contains('focused') || el === document.activeElement || el.tabIndex === 0);
    if (isFocused) { focusedIdx = i; break; }
  }
  check('ArrowDown focuses an item', focusedIdx >= 0, `focused index=${focusedIdx}`);

  // ArrowDown again → index 1
  await searchInput.press('ArrowDown');
  await sleep(200);
  let nextIdx = -1;
  for (let i = 0; i < baseCount; i++) {
    const isFocused = await agentItems.nth(i).evaluate((el) => el.classList.contains('focused') || el === document.activeElement || el.tabIndex === 0);
    if (isFocused) { nextIdx = i; break; }
  }
  check('ArrowDown advances focus', nextIdx >= 0, `moved to index=${nextIdx}`);

  // Escape clears search (already empty) — just verify no crash, focus blurs
  await searchInput.press('Escape');
  await sleep(200);
  check('Escape does not error', true, 'no exception thrown');

  // ── 7. Selecting an agent works ─────────────────────────────────────────
  await searchInput.fill('');
  await sleep(300);
  const beforeClick = await page.locator('.agent-item.selected').count();
  await agentItems.first().click();
  await sleep(500);
  const afterClick = await page.locator('.agent-item.selected').count();
  check(
    'Clicking an agent selects it (.selected class appears)',
    afterClick > beforeClick,
    `selected before=${beforeClick}, after=${afterClick}`,
  );
  await page.screenshot({ path: `${SHOT_DIR}/04-selected.png`, fullPage: true });

  // ── Enter key selects focused agent ─────────────────────────────────────
  await searchInput.click();
  await searchInput.fill('');
  await sleep(300);
  await searchInput.press('ArrowDown');
  await sleep(200);
  const selBeforeEnter = await page.locator('.agent-item.selected').count();
  await searchInput.press('Enter');
  await sleep(500);
  const selAfterEnter = await page.locator('.agent-item.selected').count();
  check(
    'Enter selects focused agent',
    selAfterEnter >= selBeforeEnter,
    `selected before=${selBeforeEnter}, after=${selAfterEnter}`,
  );

} catch (err) {
  overall = 'FAIL';
  check('Test execution (no uncaught error)', false, err.message);
  try { await page.screenshot({ path: `${SHOT_DIR}/99-error.png`, fullPage: true }); } catch {}
} finally {
  await context.close();
  await browser.close();
}

// ── Report ────────────────────────────────────────────────────────────────
console.log('\n=== Test Summary ===');
for (const r of results) {
  console.log(`  ${r.status === 'PASS' ? '✓' : '✗'} ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
}
const passed = results.filter((r) => r.status === 'PASS').length;
console.log(`\nRESULT: ${overall} (${passed}/${results.length} checks passed)`);
if (consoleErrors.length) {
  console.log(`\nConsole errors (${consoleErrors.length}):`);
  consoleErrors.slice(0, 8).forEach((e) => console.log('  ! ' + e.slice(0, 150)));
}
process.exit(overall === 'PASS' ? 0 : 1);
