/**
 * PROD Phase 1 supplemental probe 2 — workspace internals.
 */
const { chromium } = require('playwright');

const PROD_URL =
  'http://localhost:9797/projects/83da04de-a410-4fb5-9e92-251a99d28a52/' +
  'instances/cba392f7-49c8-403c-852d-f7c260ae4606';
const SETTLE_MS = 4000;

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(PROD_URL, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(SETTLE_MS);

  const probe = await page.evaluate(() => {
    const ws = document.querySelector('app-workspace');
    if (!ws) return { error: 'no app-workspace' };

    // Workspace component's own children (the .workspace-container inside it)
    const wc = ws.querySelector('.workspace-container');
    const wcVisible = wc ? getComputedStyle(wc).display !== 'none' : false;
    const wcChildren = wc ? Array.from(wc.children).map((c) => ({
      tag: c.tagName.toLowerCase(),
      cls: c.className || '',
    })) : [];

    // File tree
    const fileTree = ws.querySelector('app-file-tree');
    const codeViewer = ws.querySelector('app-code-viewer');
    const vscode = ws.querySelector('app-vscode-editor-cache');
    const loadingOverlay = ws.querySelector('.loading-overlay, mat-spinner');

    // Project id bound to workspace (Angular binds via attribute or via component prop — try common hooks)
    const wsAttrs = Array.from(ws.attributes).map((a) => a.name + '=' + a.value);

    // Toolbar title (might show project context)
    const toolbarTitle = ws.querySelector('.toolbar-title');
    const toolbarText = toolbarTitle ? (toolbarTitle.textContent || '').trim().slice(0, 80) : null;

    // Hide button (workspace's internal hide)
    const wsHideBtn = ws.querySelector('.hide-button, [data-testid="workspace-hide"]');

    // Workspace component input value (Angular DevTools exposes ng.getComponent; check)
    const ngComp = (() => {
      // We can't safely expose ng devtools; just inspect DOM
      return null;
    })();

    return {
      workspaceElementDisplay: getComputedStyle(ws).display,
      workspaceElementStyleDisplay: ws.style.display,
      workspaceContainerPresent: !!wc,
      workspaceContainerVisible: wcVisible,
      workspaceContainerChildren: wcChildren,
      fileTreePresent: !!fileTree,
      codeViewerPresent: !!codeViewer,
      vscodeCachePresent: !!vscode,
      loadingOverlayPresent: !!loadingOverlay,
      workspaceAttrs: wsAttrs,
      toolbarTitleText: toolbarText,
      workspaceInternalHideBtnPresent: !!wsHideBtn,
    };
  });
  console.log(JSON.stringify(probe, null, 2));

  await browser.close();
})().catch((e) => { console.error('FATAL', e?.stack || e); process.exit(2); });