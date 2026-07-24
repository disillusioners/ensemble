import { test, expect, type Page, request, type APIRequestContext } from '@playwright/test';
import { createTestInstance } from './fixtures/test-helpers';
import { trackInstance, cleanupAll } from './fixtures/cleanup';
import { execSync } from 'child_process';

/**
 * E2E: Workspace State Preservation — EXACT original bug scenario (steps a-g)
 *
 * Branch: feature/workspace-state-preserve (commit 2fd787aa)
 *
 * Two fixes verified:
 *   1. File content is REFETCHED when restoring a previously-viewed file
 *      after tab switch (was being nulled with no refetch → empty viewer).
 *   2. Directory tree expansion state is PRESERVED across tab switches
 *      (expanded dirs stay expanded when you switch back).
 *
 * This spec tests BOTH fixes via the exact user scenario (steps a-g):
 *   a. Open Project A workspace, expand directories, open a file
 *   b. Switch to Project B tab → different tree, no file open
 *   c. Switch back to A → file content must be restored (refetched)
 *   d. Switch back to A → expanded directories must be preserved
 *   e. Repeat A → B → A multiple times → consistent each time
 *   f. Expand different dirs in B → switch to A → A's expansion unaffected
 *   g. Open different file in B → switch to A → A's file still correct
 */

const SCREENSHOT_DIR = './e2e/screenshots';
const BACKEND_URL = 'http://localhost:8079';

interface TestProject {
  project_id: string;
  name: string;
  main_directory: string;
  markerFile: string;
  markerContent: string;
  allFiles: string[];
  /** A directory name that exists in this project (for expansion testing) */
  expandDir: string;
  /** A file inside expandDir (for file-content testing) */
  fileInDir: string;
  fileInDirContent: string;
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

async function clickWorkspaceIconOnTab(page: Page, projectName: string): Promise<void> {
  const tab = page.locator('.tab', { hasText: projectName }).first();
  const wsBtn = tab.locator('.workspace-btn');
  await wsBtn.click();
  await page.waitForTimeout(800);
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

/**
 * Expand a directory node in the file tree by clicking its toggle button.
 * The directory node is identified by its dirname text. Returns true if
 * the expansion succeeded (icon shows 'expand_more' = expanded state).
 */
async function expandDirectory(page: Page, dirName: string, timeout = 10000): Promise<boolean> {
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    // Find the directory node — it's a mat-tree-node with hasChild that
    // contains a .dirname span matching dirName
    const dirNode = page.locator('app-workspace mat-tree-node', { hasText: dirName }).first();

    if (await dirNode.isVisible().catch(() => false)) {
      // Check if already expanded
      const icon = dirNode.locator('mat-icon').first();
      const iconText = (await icon.textContent())?.trim();

      if (iconText === 'expand_more') {
        return true; // Already expanded
      }

      // Click the toggle button (or the dirname) to expand
      await dirNode.locator('button[aria-label]').first().click().catch(async () => {
        // Fallback: click dirname span
        await dirNode.locator('.dirname').first().click();
      });
      await page.waitForTimeout(1500); // Wait for lazy-load children

      // Verify expansion
      const iconAfter = dirNode.locator('mat-icon').first();
      const iconTextAfter = (await iconAfter.textContent())?.trim();
      if (iconTextAfter === 'expand_more') {
        return true;
      }
    }
    await page.waitForTimeout(300);
  }

  console.log(`[expandDirectory] TIMEOUT expanding "${dirName}"`);
  return false;
}

/**
 * Check if a directory node is currently expanded.
 * Returns true if the directory's toggle icon shows 'expand_more'.
 */
async function isDirectoryExpanded(page: Page, dirName: string): Promise<boolean> {
  const dirNode = page.locator('app-workspace mat-tree-node', { hasText: dirName }).first();
  if (!(await dirNode.isVisible().catch(() => false))) {
    return false;
  }
  const icon = dirNode.locator('mat-icon').first();
  const iconText = (await icon.textContent())?.trim();
  return iconText === 'expand_more';
}

/**
 * Click a file in the file tree to open it in the code viewer.
 */
async function openFile(page: Page, fileName: string, timeout = 10000): Promise<boolean> {
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    const fileNode = page.locator('app-workspace .filename', { hasText: fileName }).first();
    if (await fileNode.isVisible().catch(() => false)) {
      await fileNode.click();
      await page.waitForTimeout(1500); // Wait for content fetch
      return true;
    }
    await page.waitForTimeout(300);
  }

  console.log(`[openFile] TIMEOUT finding file "${fileName}"`);
  return false;
}

/**
 * Check if file content is currently displayed in the code viewer.
 * Returns the text content of the code viewer, or null if no content.
 */
async function getFileViewerContent(page: Page): Promise<string | null> {
  // The code viewer shows content in a .code-content div inside CodeMirror
  // The CodeMirror lines are in .cm-line elements
  const codeContent = page.locator('app-workspace .code-content').first();
  if (!(await codeContent.isVisible().catch(() => false))) {
    return null;
  }

  // Try to get CodeMirror line content
  const lines = await page.locator('app-workspace .cm-line').allTextContents().catch(() => []);
  if (lines.length > 0) {
    return lines.join('\n');
  }

  // Fallback: get raw text from code-content
  const text = await codeContent.textContent().catch(() => null);
  return text?.trim() || null;
}

/**
 * Get the currently displayed selected file path from the toolbar.
 */
async function getSelectedFilePath(page: Page): Promise<string | null> {
  const toolbar = page.locator('app-workspace .toolbar-title').first();
  const text = (await toolbar.textContent().catch(() => ''))?.trim();
  if (!text || text === 'Select a file') return null;
  return text;
}

/**
 * Check if file content matches expected content (with polling for async fetch).
 */
async function fileContentMatches(
  page: Page,
  expectedSubstring: string,
  timeout = 10000
): Promise<boolean> {
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    const content = await getFileViewerContent(page);
    if (content && content.includes(expectedSubstring)) {
      return true;
    }
    await page.waitForTimeout(300);
  }

  const finalContent = await getFileViewerContent(page);
  console.log(`[fileContentMatches] TIMEOUT. Expected substring "${expectedSubstring}". Got: "${finalContent?.substring(0, 200)}"`);
  return false;
}

async function screenshot(page: Page, label: string): Promise<string> {
  const path = `${SCREENSHOT_DIR}/preserve-${label}.png`;
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

test.describe('Workspace State Preservation — Steps a-g', () => {
  let page: Page;
  const timestamp = Date.now();

  const stepResults: Array<{ step: string; result: string; details: string }> = [];

  const projects: TestProject[] = [];
  let baseInstanceId = '';

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    attachConsoleCapture(page);

    // Create 2 temp directories with UNIQUE marker files and directory structures
    // that support expansion testing
    const dirs: string[] = [];
    const configs = [
      {
        letter: 'Alpha',
        marker: { file: 'MARKER_ALPHA.txt', content: 'ALPHA-STATE-PRESERVE-MARKER' },
        readme: 'README_ALPHA.md',
        appFile: 'alpha_app.ts',
        appContent: "export const ALPHA = 'state-preserve';",
        expandDir: 'src',
        fileInDir: 'file1.ts',
        fileInDirContent: "export function alphaFn() { return 'alpha'; }",
        subDir: 'sub',
        deepFile: 'deep.ts',
        deepContent: "export const DEEP_ALPHA = 42;",
      },
      {
        letter: 'Beta',
        marker: { file: 'MARKER_BETA.txt', content: 'BETA-STATE-PRESERVE-MARKER' },
        readme: 'README_BETA.md',
        appFile: 'beta_app.ts',
        appContent: "export const BETA = 'state-preserve';",
        expandDir: 'lib',
        fileInDir: 'utils.ts',
        fileInDirContent: "export function betaFn() { return 'beta'; }",
        subDir: 'helpers',
        deepFile: 'helper.ts',
        deepContent: "export const DEEP_BETA = 99;",
      },
    ];

    for (const cfg of configs) {
      const dir = `/tmp/e2e-preserve-${cfg.letter}-${timestamp}`;
      execSync(`mkdir -p ${dir}`);
      // Marker file (unique per project)
      execSync(`echo "${cfg.marker.content}" > ${dir}/${cfg.marker.file}`);
      // README
      execSync(`echo "# ${cfg.letter}" > ${dir}/${cfg.readme}`);
      // App file at root
      execSync(`echo "${cfg.appContent}" > ${dir}/${cfg.appFile}`);
      // Expandable directory with a file
      execSync(`mkdir -p ${dir}/${cfg.expandDir}`);
      execSync(`echo "${cfg.fileInDirContent}" > ${dir}/${cfg.expandDir}/${cfg.fileInDir}`);
      // Nested subdirectory for deeper expansion testing
      execSync(`mkdir -p ${dir}/${cfg.expandDir}/${cfg.subDir}`);
      execSync(`echo "${cfg.deepContent}" > ${dir}/${cfg.expandDir}/${cfg.subDir}/${cfg.deepFile}`);
      // Extra directory for step f testing
      execSync(`mkdir -p ${dir}/docs`);
      execSync(`echo "# Docs ${cfg.letter}" > ${dir}/docs/guide.md`);
      dirs.push(dir);
    }

    // Create 2 test projects WITH main_directory
    const ctx = await request.newContext({ baseURL: BACKEND_URL });
    async function createProjectWithDir(name: string, mainDir: string, idx: number): Promise<TestProject> {
      const resp = await ctx.post('/api/projects', {
        data: { name, main_directory: mainDir, project_type: 'software' },
        headers: { 'Content-Type': 'application/json' },
      });
      if (!resp.ok()) throw new Error(`Failed to create project ${name}: ${resp.status()} ${await resp.text()}`);
      const data = await resp.json();
      const cfg = configs[idx];
      return {
        ...data,
        main_directory: mainDir,
        markerFile: cfg.marker.file,
        markerContent: cfg.marker.content,
        allFiles: [cfg.marker.file, cfg.readme, cfg.appFile, cfg.expandDir, 'docs'],
        expandDir: cfg.expandDir,
        fileInDir: cfg.fileInDir,
        fileInDirContent: cfg.fileInDirContent,
      };
    }

    const projA = await createProjectWithDir(`E2E-PRESERVE-Alpha-${timestamp}`, dirs[0], 0);
    const projB = await createProjectWithDir(`E2E-PRESERVE-Beta-${timestamp}`, dirs[1], 1);
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
    console.log('WORKSPACE STATE PRESERVE — E2E RESULTS');
    console.log('========================================');
    let passed = 0;
    for (const r of stepResults) {
      console.log(`${r.result === 'PASS' ? '✅' : '❌'} Step ${r.step}: ${r.result} — ${r.details}`);
      if (r.result === 'PASS') passed++;
    }
    console.log('----------------------------------------');
    console.log(`OVERALL: ${passed}/${stepResults.length} steps passed`);
    console.log(`\nConsole Errors (${consoleErrors.length}):`);
    for (const e of consoleErrors.slice(0, 20)) console.log(`  ❌ ${e}`);
    console.log(`\nConsole Warnings (${consoleWarnings.length}):`);
    for (const w of consoleWarnings.slice(0, 10)) console.log(`  ⚠️ ${w}`);
    console.log('========================================\n');
  });

  // ════════════════════════════════════════════════════════════════════════
  // SINGLE TEST: Full scenario a-g (all steps, all assertions)
  // ════════════════════════════════════════════════════════════════════════
  test('Full scenario: steps a through g — file content refetch + expansion preservation', async () => {
    test.setTimeout(300000);
    const projA = projects[0];
    const projB = projects[1];

    // Setup: navigate fresh
    await resetTabState(page, baseInstanceId);

    // Open both project tabs
    await openProjectTab(page, projA.name);
    await openProjectTab(page, projB.name);
    await page.waitForTimeout(500);

    // ─── Step a: Open workspace for Project A, expand dirs, open file ────
    await clickWorkspaceIconOnTab(page, projA.name);
    await page.waitForTimeout(2000);

    const a_visible = await isWorkspaceVisible(page);
    const a_showsA = await workspaceShowsProject(page, projA, projB);

    // Expand src/ directory
    const a_expanded = await expandDirectory(page, projA.expandDir);

    // Open file inside expanded directory
    const a_fileOpened = await openFile(page, projA.fileInDir);

    // Verify file content is showing
    const a_contentOk = await fileContentMatches(page, projA.fileInDirContent);

    await screenshot(page, 'a-workspace-A-open-expanded-file');

    const a_pass = a_visible && a_showsA && a_expanded && a_fileOpened && a_contentOk;
    stepResults.push({
      step: 'a',
      result: a_pass ? 'PASS' : 'FAIL',
      details: a_pass
        ? `Workspace A open, ${projA.expandDir}/ expanded, ${projA.fileInDir} loaded with content`
        : `visible=${a_visible}, showsA=${a_showsA}, expanded=${a_expanded}, fileOpened=${a_fileOpened}, contentOk=${a_contentOk}`,
    });
    expect(a_visible).toBe(true);
    expect(a_showsA).toBe(true);
    expect(a_expanded).toBe(true);
    expect(a_fileOpened).toBe(true);
    expect(a_contentOk).toBe(true);

    // ─── Step b: Switch to Project B → different tree, no file open ──────
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(2000);

    const b_visible = await isWorkspaceVisible(page);
    const b_showsB = await workspaceShowsProject(page, projB, projA);

    // B should have NO file open (different project, fresh state)
    const b_selectedPath = await getSelectedFilePath(page);

    await screenshot(page, 'b-switched-to-B');

    const b_pass = b_visible && b_showsB && !b_selectedPath;
    stepResults.push({
      step: 'b',
      result: b_pass ? 'PASS' : 'FAIL',
      details: b_pass
        ? `Workspace B showing Beta files, no file open (fresh state)`
        : `visible=${b_visible}, showsB=${b_showsB}, selectedPath="${b_selectedPath}" (expected null)`,
    });
    expect(b_visible).toBe(true);
    expect(b_showsB).toBe(true);
    expect(b_selectedPath).toBeNull();

    // ─── Step c: Switch back to A → file content MUST be restored ────────
    // ★★★ CRITICAL FIX #1: file content refetch on restore
    await clickProjectTab(page, projA.name);
    await page.waitForTimeout(2000);

    const c_visible = await isWorkspaceVisible(page);
    const c_showsA = await workspaceShowsProject(page, projA, projB);

    // File content must be restored (refetched)
    const c_contentRestored = await fileContentMatches(page, projA.fileInDirContent, 12000);
    const c_selectedPath = await getSelectedFilePath(page);

    await screenshot(page, 'c-back-to-A-content-restored');

    const c_pass = c_visible && c_showsA && c_contentRestored;
    stepResults.push({
      step: 'c (CONTENT REFETCH)',
      result: c_pass ? 'PASS' : 'FAIL',
      details: c_pass
        ? `✅ File content restored: ${projA.fileInDir} shows "${projA.fileInDirContent.substring(0, 40)}..."`
        : `❌ CONTENT NOT RESTORED: visible=${c_visible}, showsA=${c_showsA}, contentRestored=${c_contentRestored}, selectedPath="${c_selectedPath}"`,
    });
    expect(c_visible).toBe(true);
    expect(c_showsA).toBe(true);
    expect(c_contentRestored).toBe(true);

    // ─── Step d: Expanded directories must be preserved in A ─────────────
    // ★★★ CRITICAL FIX #2: expansion state preservation
    // src/ was expanded in step a — verify it's STILL expanded after A→B→A
    const d_srcExpanded = await isDirectoryExpanded(page, projA.expandDir);

    await screenshot(page, 'd-A-expansion-preserved');

    const d_pass = d_srcExpanded;
    stepResults.push({
      step: 'd (EXPANSION PRESERVED)',
      result: d_pass ? 'PASS' : 'FAIL',
      details: d_pass
        ? `✅ Directory "${projA.expandDir}/" still expanded after A→B→A roundtrip`
        : `❌ Directory "${projA.expandDir}/" NOT expanded after roundtrip — expansion state lost`,
    });
    expect(d_srcExpanded).toBe(true);

    // ─── Step e: Repeat A → B → A multiple times → state consistent ──────
    let e_allConsistent = true;
    const e_details: string[] = [];

    for (let round = 1; round <= 3; round++) {
      // Switch to B
      await clickProjectTab(page, projB.name);
      await page.waitForTimeout(1500);
      const e_bShowsB = await workspaceShowsProject(page, projB, projA, 8000);

      // Switch back to A
      await clickProjectTab(page, projA.name);
      await page.waitForTimeout(1500);
      const e_aShowsA = await workspaceShowsProject(page, projA, projB, 8000);

      // File content must still be there
      const e_contentOk = await fileContentMatches(page, projA.fileInDirContent, 8000);

      // Expansion must still be there
      const e_expandedOk = await isDirectoryExpanded(page, projA.expandDir);

      const roundPass = e_bShowsB && e_aShowsA && e_contentOk && e_expandedOk;
      if (!roundPass) {
        e_allConsistent = false;
      }
      e_details.push(`round${round}: ${roundPass ? '✅' : '❌'} (B=${e_bShowsB}, A=${e_aShowsA}, content=${e_contentOk}, expanded=${e_expandedOk})`);

      if (!roundPass) {
        await screenshot(page, `e-FAIL-round-${round}`);
      }
    }

    await screenshot(page, 'e-after-3-rounds');

    stepResults.push({
      step: 'e (3× ROUNDTRIP)',
      result: e_allConsistent ? 'PASS' : 'FAIL',
      details: e_allConsistent
        ? `✅ 3 A→B→A roundtrips all consistent: ${e_details.join('; ')}`
        : `❌ Inconsistent: ${e_details.join('; ')}`,
    });
    expect(e_allConsistent).toBe(true);

    // ─── Step f: Expand different dirs in B → A's expansion unaffected ───
    // First, expand B's lib/ directory
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(1500);
    const f_bExpanded = await expandDirectory(page, projB.expandDir);
    // Also expand B's docs/ directory (a different one)
    const f_bDocsExpanded = await expandDirectory(page, 'docs');

    await screenshot(page, 'f-B-expanded-lib-and-docs');

    // Now switch back to A
    await clickProjectTab(page, projA.name);
    await page.waitForTimeout(2000);

    // A's src/ must still be expanded — B's expansion must NOT bleed into A
    const f_aSrcExpanded = await isDirectoryExpanded(page, projA.expandDir);
    const f_aShowsA = await workspaceShowsProject(page, projA, projB, 8000);

    // Check that B's lib directory is NOT showing in A's tree
    const f_aFiles = await getWorkspaceFileNames(page);
    const f_hasBleed = f_aFiles.some(f => f === projB.expandDir);

    await screenshot(page, 'f-A-expansion-unaffected-by-B');

    const f_pass = f_bExpanded && f_aSrcExpanded && f_aShowsA && !f_hasBleed;
    stepResults.push({
      step: 'f (B ISOLATION)',
      result: f_pass ? 'PASS' : 'FAIL',
      details: f_pass
        ? `✅ B's expansion of "${projB.expandDir}/" did NOT affect A — A's "${projA.expandDir}/" still expanded, no B files bleeding`
        : `❌ ISOLATION FAILURE: bExpanded=${f_bExpanded}, aSrcExpanded=${f_aSrcExpanded}, showsA=${f_aShowsA}, hasBleed=${f_hasBleed}, files=[${f_aFiles.join(', ')}]`,
    });
    expect(f_bExpanded).toBe(true);
    expect(f_aSrcExpanded).toBe(true);
    expect(f_aShowsA).toBe(true);
    expect(f_hasBleed).toBe(false);

    // ─── Step g: Open different file in B → A's file still correct ───────
    // Switch to B and open a DIFFERENT file (the marker file)
    await clickProjectTab(page, projB.name);
    await page.waitForTimeout(1500);

    // B might have lib/ collapsed now (from cache-miss), re-expand and open a file
    await expandDirectory(page, projB.expandDir);
    const g_bFileOpened = await openFile(page, projB.fileInDir);
    const g_bContentOk = await fileContentMatches(page, projB.fileInDirContent);

    await screenshot(page, 'g-B-opened-different-file');

    // Now switch back to A — A's file must still be correct
    await clickProjectTab(page, projA.name);
    await page.waitForTimeout(2000);

    // A's original file (file1.ts) content must be correct
    const g_aContentOk = await fileContentMatches(page, projA.fileInDirContent, 12000);
    const g_aSelectedPath = await getSelectedFilePath(page);
    const g_aShowsA = await workspaceShowsProject(page, projA, projB, 8000);

    await screenshot(page, 'g-A-file-still-correct');

    const g_pass = g_bFileOpened && g_bContentOk && g_aContentOk && g_aShowsA;
    stepResults.push({
      step: 'g (FILE ISOLATION)',
      result: g_pass ? 'PASS' : 'FAIL',
      details: g_pass
        ? `✅ Opened ${projB.fileInDir} in B, switched to A — A's ${projA.fileInDir} content still correct`
        : `❌ FILE ISOLATION FAIL: bFileOpened=${g_bFileOpened}, bContentOk=${g_bContentOk}, aContentOk=${g_aContentOk}, showsA=${g_aShowsA}, aSelectedPath="${g_aSelectedPath}"`,
    });
    expect(g_bFileOpened).toBe(true);
    expect(g_bContentOk).toBe(true);
    expect(g_aContentOk).toBe(true);
    expect(g_aShowsA).toBe(true);
  });
});
