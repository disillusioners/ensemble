import { test, expect, type Page, request, type APIRequestContext } from '@playwright/test';
import { createTestInstance } from './fixtures/test-helpers';
import { trackInstance, cleanupAll } from './fixtures/cleanup';
import { execSync } from 'child_process';

/**
 * E2E: Tab ↔ Workspace Sync — FINAL VERIFICATION (both bugfixes)
 *
 * Branch: feature/tab-workspace-sync (commit 8d7e5c29)
 * Two bugfixes verified together:
 *   1. e65d686d — Angular effect dep-tracking fix (effect keeps firing after All-tab visit)
 *   2. 8d7e5c29 — WorkspaceService cache stale data fix (_treeProjectId prevents wrong-project tree)
 *
 * Previous run: effect fix worked, but file tree showed STALE DATA on steps c, d, h,
 * and even-numbered rapid switches. This run MUST verify file tree content is CORRECT
 * on EVERY step — no exceptions.
 *
 * KEY DIFFERENCE: file tree content is now a HARD assertion on steps c, d, h, and
 * every rapid switch in step i. No soft-fails.
 */

const SCREENSHOT_DIR = './e2e/screenshots';
const BACKEND_URL = 'http://localhost:8079';

interface TestProject {
  project_id: string;
  name: string;
  main_directory: string;
  markerFile: string;
  allFiles: string[];
}

// ─── Helpers ──────────────────────────────────────────────────────────────

async function navigateToChat(page: Page, instanceId: string): Promise<void> {
  await page.goto(`/instances/${instanceId}`);
  await page.waitForSelector('.tab-bar', { timeout: 20000 });
  await page.waitForTimeout(2000);
}

async function resetTabState(page: Page, instanceId: string): Promise<void> {
  await page.evaluate(() => {
    localStorage.removeItem('ensemble-project-tabs');
    for (const key of Object.keys(localStorage)) {
      if (key.includes('workspace')) localStorage.removeItem(key);
    }
  });
  await page.goto(`/instances/${instanceId}`);
  await page.waitForSelector('.tab-bar', { timeout: 20000 });
  await page.waitForTimeout(1500);
}

async function openProjectTab(page: Page, projectName: string): Promise<void> {
  await page.locator('.tab-add').click();
  await page.waitForSelector('.project-menu button[mat-menu-item]', { timeout: 8000 });
  const items = page.locator('.project-menu button[mat-menu-item]');
  const count = await items.count();
  let clicked = false;
  for (let i = 0; i < count; i++) {
    const text = (await items.nth(i).textContent())?.trim();
    if (text === projectName) {
      await items.nth(i).click();
      clicked = true;
      break;
    }
  }
  if (!clicked) throw new Error(`Project "${projectName}" not found in tab menu (checked ${count} items)`);
  await page.waitForTimeout(600);
}

async function clickProjectTab(page: Page, projectName: string): Promise<void> {
  const tab = page.locator('.tab', { hasText: projectName }).first();
  await tab.locator('.tab-name').click();
  await page.waitForTimeout(600);
}

async function clickAllTab(page: Page): Promise<void> {
  const allTab = page.locator('.tab:not(:has(.workspace-btn))').first();
  await allTab.click();
  await page.waitForTimeout(600);
}

async function clickWorkspaceIconOnTab(page: Page, projectName: string): Promise<void> {
  const tab = page.locator('.tab', { hasText: projectName }).first();
  const wsBtn = tab.locator('.workspace-btn');
  await wsBtn.click();
  await page.waitForTimeout(600);
}

async function isWorkspaceVisible(page: Page): Promise<boolean> {
  return await page.locator('app-workspace').isVisible().catch(() => false);
}

/**
 * Get ALL filenames/dirnames currently shown in the workspace file tree.
 */
async function getWorkspaceFileNames(page: Page): Promise<string[]> {
  return await page.locator('app-workspace .filename, app-workspace .dirname')
    .allTextContents()
    .catch(() => []);
}

/**
 * Verify the workspace is showing a specific project's files by checking
 * for the project's unique marker file in the file tree AND ensuring the
 * wrong project's marker is absent. Uses polling with retry.
 */
async function workspaceShowsProject(
  page: Page,
  project: TestProject,
  otherProject: TestProject | null,
  timeout = 15000
): Promise<boolean> {
  const deadline = Date.now() + timeout;
  let lastFiles: string[] = [];

  while (Date.now() < deadline) {
    try {
      const files = await page
        .locator('app-workspace .filename, app-workspace .dirname')
        .allTextContents()
        .catch(() => []);
      lastFiles = files.map(f => f.trim());

      const hasCorrectMarker = lastFiles.some(f => f === project.markerFile);
      const hasWrongMarker = otherProject
        ? lastFiles.some(f => f === otherProject.markerFile)
        : false;

      if (hasCorrectMarker && !hasWrongMarker) {
        return true;
      }
    } catch {
      // ignore — retry
    }
    await page.waitForTimeout(300);
  }

  console.log(`[workspaceShowsProject] TIMEOUT after ${timeout}ms. Last files: [${lastFiles.join(', ')}]`);
  return false;
}

async function screenshot(page: Page, label: string): Promise<string> {
  const path = `${SCREENSHOT_DIR}/final-${label}.png`;
  await page.screenshot({ path, fullPage: false });
  console.log(`[Screenshot] ${path}`);
  return path;
}

// ─── Console Error Capture ────────────────────────────────────────────────

const consoleErrors: string[] = [];
const consoleWarnings: string[] = [];

function attachConsoleCapture(page: Page): void {
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    } else if (msg.type() === 'warning') {
      consoleWarnings.push(msg.text());
    }
  });
  page.on('pageerror', (err) => {
    consoleErrors.push(`PAGE ERROR: ${err.message}`);
  });
}

// ─── Test Suite ───────────────────────────────────────────────────────────

test.describe.configure({ mode: 'serial' });

test.describe('Tab-Workspace Sync — FINAL Verification (Both Bugfixes)', () => {
  let page: Page;
  const timestamp = Date.now();

  const stepResults: Array<{ step: string; result: string; details: string }> = [];
  const switchResults: Array<{ switch: number; result: string; details: string }> = [];

  const projects: TestProject[] = [];
  let baseInstanceId = '';

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    attachConsoleCapture(page);

    // Create 2 temp directories with UNIQUE marker files
    const dirs: string[] = [];
    const markers = [
      { file: 'MARKER_ALPHA.txt', content: 'ALPHA-MARKER-FINAL' },
      { file: 'MARKER_BETA.txt', content: 'BETA-MARKER-FINAL' },
    ];

    for (let i = 0; i < 2; i++) {
      const letter = i === 0 ? 'Alpha' : 'Beta';
      const dir = `/tmp/e2e-final-${letter}-${timestamp}`;
      execSync(`mkdir -p ${dir}`);
      execSync(`echo "${markers[i].content}" > ${dir}/${markers[i].file}`);
      if (i === 0) {
        execSync(`echo "# Alpha" > ${dir}/README_ALPHA.md`);
        execSync(`echo "print('alpha')" > ${dir}/alpha_app.py`);
        execSync(`mkdir -p ${dir}/src`);
      } else {
        execSync(`echo "# Beta" > ${dir}/README_BETA.md`);
        execSync(`echo "print('beta')" > ${dir}/beta_app.py`);
        execSync(`mkdir -p ${dir}/lib`);
      }
      dirs.push(dir);
    }

    // Create 2 test projects WITH main_directory
    const ctx = await request.newContext({ baseURL: BACKEND_URL });
    async function createProjectWithDir(name: string, mainDir: string): Promise<TestProject> {
      const resp = await ctx.post('/api/projects', {
        data: { name, main_directory: mainDir, project_type: 'software' },
        headers: { 'Content-Type': 'application/json' },
      });
      if (!resp.ok()) throw new Error(`Failed to create project ${name}: ${resp.status()} ${await resp.text()}`);
      const data = await resp.json();
      const idx = dirs.indexOf(mainDir);
      return {
        ...data,
        main_directory: mainDir,
        markerFile: markers[idx].file,
        allFiles: idx === 0
          ? ['MARKER_ALPHA.txt', 'README_ALPHA.md', 'alpha_app.py', 'src']
          : ['MARKER_BETA.txt', 'README_BETA.md', 'beta_app.py', 'lib'],
      };
    }

    const projA = await createProjectWithDir(`E2E-FINAL-Alpha-${timestamp}`, dirs[0]);
    const projB = await createProjectWithDir(`E2E-FINAL-Beta-${timestamp}`, dirs[1]);
    projects.push(projA, projB);

    // Create a base instance to navigate to the chat page
    const baseInst = await createTestInstance('leader');
    trackInstance(baseInst.instance_id);
    baseInstanceId = baseInst.instance_id;

    await navigateToChat(page, baseInstanceId);
    await page.reload();
    await page.waitForSelector('.tab-bar', { timeout: 20000 });
    await page.waitForTimeout(2000);
  });

  test.afterAll(async () => {
    await cleanupAll();
    await page?.close();

    console.log('\n\n========================================');
    console.log('FINAL E2E — Tab-Workspace Sync — RESULTS');
    console.log('========================================');
    let passed = 0;
    for (const r of stepResults) {
      console.log(`${r.result === 'PASS' ? '✅' : '❌'} Step ${r.step}: ${r.result} — ${r.details}`);
      if (r.result === 'PASS') passed++;
    }
    console.log('----------------------------------------');
    for (const s of switchResults) {
      console.log(`  ${s.result === 'PASS' ? '✅' : '❌'} Switch ${s.switch}: ${s.result} — ${s.details}`);
    }
    console.log('----------------------------------------');
    console.log(`OVERALL: ${passed}/${stepResults.length} steps passed`);
    console.log(`Step i: ${switchResults.filter(s => s.result === 'PASS').length}/${switchResults.length} switches passed`);
    console.log(`\nConsole Errors (${consoleErrors.length}):`);
    for (const e of consoleErrors.slice(0, 20)) console.log(`  ❌ ${e}`);
    console.log(`\nConsole Warnings (${consoleWarnings.length}):`);
    for (const w of consoleWarnings.slice(0, 10)) console.log(`  ⚠️ ${w}`);
    console.log('========================================\n');
  });

  // ════════════════════════════════════════════════════════════════════════
  // SINGLE TEST: Full scenario a-i (all steps, all strict assertions)
  // ════════════════════════════════════════════════════════════════════════
  test('Full scenario: steps a through i with strict file tree verification', async () => {
    test.setTimeout(300000);
    const projA = projects[0];
    const projB = projects[1];

    // Setup: navigate fresh
    await resetTabState(page, baseInstanceId);

    // Open both project tabs
    await openProjectTab(page, projA.name);
    await openProjectTab(page, projB.name);
    await page.waitForTimeout(500);

    // ─── Step a: Open workspace for Project A ──────────────────────────
    await clickWorkspaceIconOnTab(page, projA.name);
    await page.waitForTimeout(2000);

    const a_visible = await isWorkspaceVisible(page);
    const a_showsA = await workspaceShowsProject(page, projA, projB);
    await screenshot(page, 'a-workspace-A-open');
    stepResults.push({
      step: 'a',
      result: a_visible && a_showsA ? 'PASS' : 'FAIL',
      details: a_visible && a_showsA
        ? `Workspace open showing Alpha files (marker: ${projA.markerFile})`
        : `visible=${a_visible}, showsAlpha=${a_showsA}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(a_visible).toBe(true);
    expect(a_showsA).toBe(true);

    // ─── Step b: Switch to Project Beta → workspace should sync ────────
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(2000);

    const b_visible = await isWorkspaceVisible(page);
    const b_showsB = await workspaceShowsProject(page, projB, projA);
    await screenshot(page, 'b-workspace-synced-to-B');
    stepResults.push({
      step: 'b',
      result: b_visible && b_showsB ? 'PASS' : 'FAIL',
      details: b_visible && b_showsB
        ? `Workspace synced to Beta (marker: ${projB.markerFile})`
        : `visible=${b_visible}, showsBeta=${b_showsB}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(b_visible).toBe(true);
    expect(b_showsB).toBe(true);

    // ─── Step c: Switch back to Project Alpha ──────────────────────────
    // ★ CRITICAL — was stale before the cache fix
    await clickProjectTab(page, projA.name);
    await page.waitForTimeout(2000);

    const c_visible = await isWorkspaceVisible(page);
    const c_showsA = await workspaceShowsProject(page, projA, projB);
    await screenshot(page, 'c-CRITICAL-back-to-A');
    stepResults.push({
      step: 'c (CRITICAL)',
      result: c_visible && c_showsA ? 'PASS' : 'FAIL',
      details: c_visible && c_showsA
        ? `✅ File tree correct: Alpha's files (marker: ${projA.markerFile})`
        : `❌ STALE DATA: visible=${c_visible}, showsAlpha=${c_showsA}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(c_visible).toBe(true);
    expect(c_showsA).toBe(true);

    // ─── Step d: Switch to Project Beta again ──────────────────────────
    // ★ CRITICAL — was stale before the cache fix
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(2000);

    const d_visible = await isWorkspaceVisible(page);
    const d_showsB = await workspaceShowsProject(page, projB, projA);
    await screenshot(page, 'd-CRITICAL-back-to-B');
    stepResults.push({
      step: 'd (CRITICAL)',
      result: d_visible && d_showsB ? 'PASS' : 'FAIL',
      details: d_visible && d_showsB
        ? `✅ File tree correct: Beta's files (marker: ${projB.markerFile})`
        : `❌ STALE DATA: visible=${d_visible}, showsBeta=${d_showsB}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(d_visible).toBe(true);
    expect(d_showsB).toBe(true);

    // ─── Step e: Switch to "All" tab → workspace hides ────────────────
    await clickAllTab(page);
    await page.waitForTimeout(1000);

    const e_hidden = !(await isWorkspaceVisible(page));
    await screenshot(page, 'e-all-tab-workspace-hidden');
    stepResults.push({
      step: 'e',
      result: e_hidden ? 'PASS' : 'FAIL',
      details: e_hidden
        ? 'Workspace hidden after visiting "All" tab'
        : 'Workspace still visible on "All" tab (should be hidden)',
    });
    expect(e_hidden).toBe(true);

    // ─── Step f: Switch back to Project Alpha → should NOT auto-open ───
    await clickProjectTab(page, projA.name);
    await page.waitForTimeout(1000);

    const f_notVisible = !(await isWorkspaceVisible(page));
    await screenshot(page, 'f-back-to-A-no-auto-open');
    stepResults.push({
      step: 'f',
      result: f_notVisible ? 'PASS' : 'FAIL',
      details: f_notVisible
        ? 'Workspace correctly NOT auto-opened after All-tab roundtrip'
        : 'Workspace auto-opened (unexpected)',
    });
    expect(f_notVisible).toBe(true);

    // ─── Step g: Click workspace icon on Project Alpha → opens ────────
    await clickWorkspaceIconOnTab(page, projA.name);
    await page.waitForTimeout(2000);

    const g_visible = await isWorkspaceVisible(page);
    const g_showsA = await workspaceShowsProject(page, projA, projB);
    await screenshot(page, 'g-workspace-reopened-A');
    stepResults.push({
      step: 'g',
      result: g_visible && g_showsA ? 'PASS' : 'FAIL',
      details: g_visible && g_showsA
        ? `Workspace manually reopened showing Alpha files`
        : `visible=${g_visible}, showsAlpha=${g_showsA}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(g_visible).toBe(true);
    expect(g_showsA).toBe(true);

    // ─── Step h: ★★★ CRITICAL — Switch to Project Beta ★★★ ────────────
    // Was stale before cache fix. Both bugs combined here.
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(2000);

    const h_visible = await isWorkspaceVisible(page);
    const h_showsB = await workspaceShowsProject(page, projB, projA);
    await screenshot(page, 'h-CRITICAL-switch-to-B-after-all');
    stepResults.push({
      step: 'h (CRITICAL)',
      result: h_visible && h_showsB ? 'PASS' : 'FAIL',
      details: h_visible && h_showsB
        ? `✅ CRITICAL PASS: File tree shows Beta's files after All-tab roundtrip — cache fix WORKS`
        : `❌ CRITICAL FAIL: File tree STALE. visible=${h_visible}, showsBeta=${h_showsB}, files=[${(await getWorkspaceFileNames(page)).join(', ')}]`,
    });
    expect(h_visible).toBe(true);
    expect(h_showsB).toBe(true);

    // ─── Step i: Switch back and forth RAPIDLY 6+ times ────────────────
    // ★★★ THE MOST CRITICAL STEP — both bugs combined to break everything here
    const SWITCH_COUNT = 8; // 8 switches = 4 to each project
    let allSwitchesPassed = true;

    for (let i = 0; i < SWITCH_COUNT; i++) {
      const targetProj = i % 2 === 0 ? projA : projB;
      const otherProj = i % 2 === 0 ? projB : projA;
      const direction = i % 2 === 0 ? 'Beta→Alpha' : 'Alpha→Beta';

      await clickProjectTab(page, targetProj.name);
      // Slightly shorter wait for "rapid" simulation but enough for Angular
      await page.waitForTimeout(800);

      const visible = await isWorkspaceVisible(page);
      const showsCorrect = await workspaceShowsProject(page, targetProj, otherProj, 8000);
      const filesShown = await getWorkspaceFileNames(page);

      const passed = visible && showsCorrect;
      if (!passed) {
        allSwitchesPassed = false;
        await screenshot(page, `i-FAIL-switch-${i + 1}-${targetProj.name}`);
      }

      switchResults.push({
        switch: i + 1,
        result: passed ? 'PASS' : 'FAIL',
        details: passed
          ? `${direction}: shows ${targetProj.markerFile} ✅ (files: [${filesShown.join(', ')}])`
          : `${direction}: WRONG. visible=${visible}, showsCorrect=${showsCorrect}, files=[${filesShown.join(', ')}]`,
      });

      expect(visible).toBe(true);
      expect(showsCorrect).toBe(true);
    }

    await screenshot(page, 'i-final-state');
    stepResults.push({
      step: 'i (RAPID)',
      result: allSwitchesPassed ? 'PASS' : 'FAIL',
      details: allSwitchesPassed
        ? `✅ ALL ${SWITCH_COUNT} rapid switches correct — file tree showed correct project every time`
        : `❌ Some rapid switches showed stale data`,
    });
    expect(allSwitchesPassed).toBe(true);
  });
});
