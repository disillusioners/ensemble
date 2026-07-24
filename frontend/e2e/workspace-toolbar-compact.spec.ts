import { test, expect, type Page } from '@playwright/test';

/**
 * E2E verification of the compact workspace toolbar.
 * Uses dev DB project ID: 39ed737e-f106-4b1a-beb4-667c1c887918 (agents-ensemble)
 */

const PROJECT_ID = '39ed737e-f106-4b1a-beb4-667c1c887918';
const WORKSPACE_URL = `http://localhost:4199/projects/${PROJECT_ID}/workspace`;

/** Wait for the mat-tree to have visible nodes. */
async function waitForFileTree(page: Page) {
  await page.waitForSelector('.file-tree-sidenav mat-tree-node .filename', { timeout: 15000 });
}

/** Click a file node by clicking its .filename span. Uses .first() for ambiguous names. */
async function clickFile(page: Page, filename: string): Promise<void> {
  await waitForFileTree(page);
  const fileSpan = page.locator(`.filename:text-is("${filename}")`).first();
  await fileSpan.click({ timeout: 10000 });
  await page.waitForTimeout(2000); // Wait for content + toolbar to update
}

/** Expand a directory by clicking its toggle button. */
async function expandDir(page: Page, dirname: string): Promise<void> {
  const dirNode = page.locator(`mat-tree-node:has(.dirname:text-is("${dirname}"))`).first();
  const isExpanded = await dirNode.getAttribute('aria-expanded');
  if (isExpanded === 'false') {
    await dirNode.locator('button[aria-label]').click();
    await page.waitForTimeout(800);
  }
}

test.describe('Compact Workspace Toolbar', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(WORKSPACE_URL);
    await page.waitForTimeout(3000);
  });

  test('1. Only ONE toolbar row visible', async ({ page }) => {
    const toolbars = page.locator('.content-toolbar');
    await expect(toolbars).toHaveCount(1);

    const allToolbars = page.locator('mat-toolbar');
    const count = await allToolbars.count();
    console.log(`[Check 1] mat-toolbar count: ${count}`);
    expect(count).toBe(1);
  });

  test('2. File path shown only once', async ({ page }) => {
    await clickFile(page, '.DS_Store');

    const titleEl = page.locator('.toolbar-title');
    await expect(titleEl).toBeVisible();

    const titleText = (await titleEl.textContent())?.trim() || '';
    console.log(`[Check 2] Toolbar title: "${titleText}"`);
    expect(titleText).toContain('.DS_Store');

    // Count how many times the path appears in the toolbar
    const occurrences = await page.locator('.content-toolbar').evaluate(
      (el, path) => (el.textContent || '').split(path).length - 1,
      '.DS_Store'
    );
    console.log(`[Check 2] Path occurrences in toolbar: ${occurrences}`);
    expect(occurrences).toBe(1);
  });

  test('3. Save icon button visible and clickable', async ({ page }) => {
    await clickFile(page, '.DS_Store');

    const saveBtn = page.locator('[data-testid="save-button"]');
    await expect(saveBtn).toBeVisible({ timeout: 10000 });
    console.log('[Check 3] Save button visible');
  });

  test('4. Save button disabled when not dirty / no file open', async ({ page }) => {
    // No file selected → save button hidden (template: @if (selectedPath()))
    const saveBtnInitial = page.locator('[data-testid="save-button"]');
    const initialVisible = await saveBtnInitial.isVisible().catch(() => false);
    console.log(`[Check 4] Save button visible before file select: ${initialVisible}`);
    expect(initialVisible).toBe(false);

    // Select a text file
    await waitForFileTree(page);
    await expandDir(page, '.agents');
    await expandDir(page, 'tester');
    await clickFile(page, 'README.md');

    const saveBtn = page.locator('[data-testid="save-button"]');
    await expect(saveBtn).toBeVisible({ timeout: 10000 });
    const isDisabled = await saveBtn.isDisabled();
    console.log(`[Check 4] Save disabled when not dirty: ${isDisabled}`);
    expect(isDisabled).toBe(true);
  });

  test('5. Dirty indicator (*) appears when editing', async ({ page }) => {
    await waitForFileTree(page);
    await expandDir(page, '.agents');
    await expandDir(page, 'tester');
    await clickFile(page, 'README.md');

    // CodeMirror 6 uses .cm-editor / .cm-content (contenteditable)
    const editor = page.locator('.cm-content').first();
    await expect(editor).toBeVisible({ timeout: 10000 });

    // Focus the editor, move to end, and type
    await editor.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.type(' // test edit');
    await page.waitForTimeout(500);

    const dirty = page.locator('[data-testid="dirty-indicator"]');
    await expect(dirty).toBeVisible({ timeout: 5000 });
    const dirtyText = await dirty.textContent();
    console.log(`[Check 5] Dirty indicator: "${dirtyText}"`);
    expect(dirtyText?.trim()).toBe('*');
  });

  test('6. Code/Diff toggle works', async ({ page }) => {
    await clickFile(page, '.DS_Store');

    const toggleGroup = page.locator('mat-button-toggle-group');
    await expect(toggleGroup).toBeVisible({ timeout: 10000 });

    const codeToggle = page.locator('mat-button-toggle[value="code"]');
    const diffToggle = page.locator('mat-button-toggle[value="diff"]');
    await expect(codeToggle).toBeVisible();
    await expect(diffToggle).toBeVisible();
    console.log('[Check 6] Code/Diff toggle group visible');

    // Switch to Diff
    await diffToggle.click();
    await page.waitForTimeout(2000);
    const diffViewer = page.locator('app-diff-viewer');
    await expect(diffViewer).toBeVisible({ timeout: 5000 });
    console.log('[Check 6] Diff viewer visible after click');

    // Switch back to Code
    await codeToggle.click();
    await page.waitForTimeout(1000);
    const codeViewer = page.locator('app-code-viewer');
    await expect(codeViewer).toBeVisible({ timeout: 5000 });
    console.log('[Check 6] Code viewer visible after switch back');
  });

  test('7. Metadata (lines · size) shown for selected file', async ({ page }) => {
    await waitForFileTree(page);
    await expandDir(page, '.agents');
    await expandDir(page, 'tester');
    await clickFile(page, 'README.md');

    const meta = page.locator('.file-meta');
    await expect(meta).toBeVisible({ timeout: 10000 });
    const metaText = (await meta.textContent())?.trim() || '';
    console.log(`[Check 7] File meta: "${metaText}"`);
    expect(metaText.toLowerCase()).toContain('lines');
    expect(metaText).toContain('·');
  });

  test('8. Binary badge appears for binary file', async ({ page }) => {
    await clickFile(page, '.DS_Store');

    const badge = page.locator('.badge-binary');
    await expect(badge).toBeVisible({ timeout: 10000 });
    const badgeText = await badge.textContent();
    console.log(`[Check 8] Binary badge: "${badgeText}"`);
    expect(badgeText?.trim()).toBe('BIN');
  });

  test('9. SSE indicator (Live/Disconnected) visible', async ({ page }) => {
    const sse = page.locator('.sse-indicator');
    await expect(sse).toBeVisible({ timeout: 10000 });

    const sseLabel = page.locator('.sse-indicator .sse-label');
    await expect(sseLabel).toBeVisible();
    const sseText = (await sseLabel.textContent())?.trim() || '';
    console.log(`[Check 9] SSE indicator: "${sseText}"`);
    expect(['Live', 'Disconnected']).toContain(sseText);
  });

  test('10. Hide button works', async ({ page }) => {
    const hideBtn = page.locator('[data-testid="workspace-hide"]');
    await expect(hideBtn).toBeVisible({ timeout: 10000 });

    const ariaLabel = await hideBtn.getAttribute('aria-label');
    console.log(`[Check 10] Hide button aria-label: "${ariaLabel}"`);
    expect(ariaLabel).toBe('Hide workspace');

    await hideBtn.click();
    await page.waitForTimeout(500);
    console.log('[Check 10] Hide button clicked successfully');
  });

  test('11. Ctrl/Cmd+S saves when dirty', async ({ page }) => {
    await waitForFileTree(page);
    await expandDir(page, '.agents');
    await expandDir(page, 'tester');
    await clickFile(page, 'README.md');

    // CodeMirror 6 editor
    const editor = page.locator('.cm-content').first();
    await expect(editor).toBeVisible({ timeout: 10000 });

    await editor.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.type(' // ctrl+s test');
    await page.waitForTimeout(500);

    const dirty = page.locator('[data-testid="dirty-indicator"]');
    await expect(dirty).toBeVisible({ timeout: 5000 });
    console.log('[Check 11] Content made dirty');

    const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${modifier}+s`);
    await page.waitForTimeout(3000);

    // Dirty indicator should be gone after save
    const dirtyAfter = page.locator('[data-testid="dirty-indicator"]');
    const dirtyStillVisible = await dirtyAfter.isVisible().catch(() => false);
    console.log(`[Check 11] Dirty after save: ${dirtyStillVisible ? 'still visible (FAIL)' : 'gone (PASS)'}`);
    expect(dirtyStillVisible).toBe(false);
  });
});
