/**
 * PROD Phase 1 evidence-collection — STRICT READ-ONLY.
 *
 * No clicks. No form input. No localStorage/sessionStorage writes.
 * No addInitScript that mutates. No network mocking. No process control.
 *
 * Targets PROD frontend on http://localhost:9797 (tag v0.10.5).
 */
const { chromium } = require('playwright');

const PROD_URL =
  'http://localhost:9797/projects/83da04de-a410-4fb5-9e92-251a99d28a52/' +
  'instances/cba392f7-49c8-403c-852d-f7c260ae4606';
const SETTLE_MS = 4000;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    // Do NOT seed any storage — we want a true fresh observe.
    storageState: undefined,
  });
  const page = await context.newPage();

  // Capture console + pageerror from context creation, with phase tag.
  const consoleByPhase = { initial: [], reload: [], unknown: [] };
  let activePhase = 'initial';
  const classify = (msg) => {
    // The console listener cannot see the reload event by itself.
    // We mark phase manually right after navigation/reload completes
    // (via setPhase()); earlier entries go to 'initial', after the
    // 'phase=reload' marker goes to 'reload'. Anything before either
    // explicit tag is 'initial'.
    return activePhase === 'initial'
      ? consoleByPhase.initial
      : activePhase === 'reload'
        ? consoleByPhase.reload
        : consoleByPhase.unknown;
  };
  const setPhase = (p) => {
    activePhase = p;
  };

  page.on('console', (msg) => {
    const type = msg.type();
    if (type === 'error' || type === 'warning') {
      const location = msg.location() || {};
      const bucket = classify();
      bucket.push({
        type,
        text: msg.text(),
        url: location.url || '',
        lineNumber: location.lineNumber,
        columnNumber: location.columnNumber,
      });
    }
  });
  page.on('pageerror', (err) => {
    const bucket = classify();
    bucket.push({
      type: 'pageerror',
      text: String(err && err.message ? err.message : err),
      stack: err && err.stack ? err.stack : '',
    });
  });

  // Navigation
  const navResp = await page.goto(PROD_URL, {
    waitUntil: 'load',
    timeout: 30000,
  });
  // Fixed settle — SSE keeps network busy; 'networkidle' never lands.
  await page.waitForTimeout(SETTLE_MS);

  // ── Capture function ──
  const capture = async (label) => {
    const data = await page.evaluate(() => {
      const q = (sel, root = document) => root.querySelector(sel);
      const qa = (sel, root = document) =>
        Array.from(root.querySelectorAll(sel));
      // Header hide button
      const btn = q('.overlay-hide-btn');
      const headerBtns = qa('header button');
      const headerIcons = qa('header mat-icon').map((el) => ({
        text: (el.textContent || '').trim(),
        aria: el.getAttribute('aria-label') || '',
      }));
      const visibilityLikeIcons = headerIcons.filter((i) =>
        /visibility/.test(i.text + ' ' + i.aria),
      );

      // Chat overlay
      const chat = q('app-chat');
      let chatDisplay = null;
      let chatStyleDisplay = null;
      let chatVisible = null;
      let messageSelectors = {};
      if (chat) {
        const cs = getComputedStyle(chat);
        chatDisplay = cs.display;
        chatStyleDisplay = chat.style.display;
        chatVisible = chat.offsetParent !== null;
        const trySelectors = [
          '.message',
          '.msg',
          '.message-row',
          '.message-content',
          '.chat-message',
          '.messages-container',
          '.messages',
          '.empty-state',
          'app-message',
          'app-chat-message',
        ];
        for (const sel of trySelectors) {
          const count = qa(sel, chat).length;
          if (count > 0) {
            messageSelectors[sel] = {
              count,
              firstText: (qa(sel, chat)[0].textContent || '').trim().slice(0, 200),
            };
          } else {
            messageSelectors[sel] = { count: 0, firstText: '' };
          }
        }
      }
      // Loading spinner inside chat?
      const chatLoading =
        chat && qa('mat-spinner, mat-progress-spinner, .loading-spinner', chat).length;

      // Workspace overlay
      const ws = q('app-workspace');
      const wsDisplay = ws ? getComputedStyle(ws).display : 'absent';
      const wsStyleDisplay = ws ? ws.style.display : 'absent';

      // Other overlays
      const planeOverlay = q('.plane-overlay');
      const planeDisplay = planeOverlay
        ? getComputedStyle(planeOverlay).display
        : 'absent';

      // Header health/version
      const headerVersionEl = q('.health-status .version, .header-version, app-health-indicator');
      const headerVersion = headerVersionEl
        ? (headerVersionEl.textContent || '').trim()
        : null;

      // Page URL/title
      const url = location.href;
      const title = document.title;
      const appRootExists = !!q('app-root');

      // Performance entries (resource URLs for build id hunt)
      const resources = (performance.getEntriesByType('resource') || [])
        .map((e) => e.name)
        .filter((u) => /\.js(\?|$)/.test(u) || /\/main-/.test(u))
        .slice(0, 30);

      // window globals
      const globals = {
        __version__: (window).__version__ || null,
        APP_VERSION: (window).APP_VERSION || null,
        ng: (window).ng ? 'present' : 'absent',
        ngVersion: (window).ng && (window).ng.version ? 'present' : 'absent',
      };

      // Bare DOM probe — anything else interesting in app-root?
      const appRoot = q('app-root');
      let appRootChildSummary = null;
      if (appRoot) {
        appRootChildSummary = Array.from(appRoot.children).map((c) => ({
          tag: c.tagName.toLowerCase(),
          cls: c.className || '',
          id: c.id || '',
        }));
      }

      return {
        url,
        title,
        appRootExists,
        navStatus: 'OK (captured at navigation)',
        header: {
          overlayHideBtnCount: btn ? 1 : 0,
          overlayHideBtnAria: btn ? btn.getAttribute('aria-label') : null,
          overlayHideBtnTitle: btn ? btn.getAttribute('title') : null,
          overlayHideBtnIconText: btn
            ? (q('mat-icon', btn)?.textContent || '').trim()
            : null,
          overlayHideBtnDisplay: btn ? getComputedStyle(btn).display : null,
          headerButtonCount: headerBtns.length,
          headerIcons,
          visibilityLikeIcons,
        },
        chat: {
          present: !!chat,
          display: chatDisplay,
          styleDisplay: chatStyleDisplay,
          visibleInLayout: chatVisible,
          messageSelectors,
          loadingSpinnerCount: chatLoading,
        },
        workspace: {
          present: !!ws,
          display: wsDisplay,
          styleDisplay: wsStyleDisplay,
        },
        plane: {
          present: !!planeOverlay,
          display: planeDisplay,
        },
        headerVersion,
        resources,
        globals,
        appRootChildSummary,
      };
    });
    return data;
  };

  const initialCapture = await capture('initial');
  await page.screenshot({
    path: 'test-results/prod-phase1-01-fresh-load.png',
    fullPage: true,
  });

  // ── Reload, same settle, same captures ──
  setPhase('reload');
  const reloadResp = await page.reload({ waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(SETTLE_MS);
  const reloadCapture = await capture('reload');
  await page.screenshot({
    path: 'test-results/prod-phase1-02-after-reload.png',
    fullPage: true,
  });

  // ── localStorage dump ──
  const localStorageDump = await page.evaluate(() => {
    const out = {};
    for (const [k, v] of Object.entries(localStorage)) {
      const full = String(v);
      const truncated = full.length > 500;
      out[k] = {
        fullLength: full.length,
        truncated,
        value: truncated ? full.slice(0, 500) + '…[truncated]' : full,
      };
    }
    return out;
  });

  // ── Build version hunt ──
  const buildInfo = await page.evaluate(() => {
    const meta = document.querySelector('meta[name="version"], meta[content*="v"]');
    const footerVersion =
      document.querySelector('.footer-version, .version, .app-version')?.textContent?.trim() ||
      null;
    const headerVersion =
      document.querySelector('.health-status .version, app-job-queue-indicator .version')?.textContent?.trim() ||
      null;
    // Try common global locations
    const globals = {
      __version__: (window).__version__ || null,
      APP_VERSION: (window).APP_VERSION || null,
      ng: !!(window).ng,
    };
    // Resource entries for the main bundle
    const resources = (performance.getEntriesByType('resource') || [])
      .map((e) => e.name)
      .filter((u) => /main-|chunk-|styles-|\.js$/.test(u));
    return { metaVersion: meta?.getAttribute('content') || null, footerVersion, headerVersion, globals, resources };
  });

  const responseStatus = {
    initial: navResp ? { status: () => -1 } : { status: () => -1 },
    reload: reloadResp ? { status: () => -1 } : { status: () => -1 },
  };
  // Helper: page.on('response') could track redirects; for now record via resp.status()
  const navStatus = {
    initial: navResp ? navResp.status() : null,
    reload: reloadResp ? reloadResp.status() : null,
  };
  void responseStatus; // not currently needed; navStatus below is the truth

  // ── DOM-only final state ──
  const finalUrl = page.url();

  const report = {
    prodUrl: PROD_URL,
    captureAt: new Date().toISOString(),
    navStatus,
    finalUrl,
    initialCapture,
    reloadCapture,
    localStorageDump,
    consoleByPhase,
    buildInfo,
    protocolCompliance: {
      zeroClicks: true,
      zeroWrites: true,
      noNetworkMocking: true,
      noInitScript: true,
      noProcessControl: true,
    },
  };

  console.log(JSON.stringify(report, null, 2));

  await browser.close();
})().catch((err) => {
  console.error('FATAL', err && err.stack ? err.stack : err);
  process.exit(2);
});