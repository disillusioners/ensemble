/**
 * R3 DEV symptom mirror — mirror the prod Phase-1 hide-button symptom on DEV.
 *
 * Compares to prod-phase1-observe.cjs structure (strict read-only browser
 * probe + console capture + screenshot evidence). This script also drives
 * ONE API mutation (fresh instance creation) and ONE cleanup (DELETE) — both
 * inside the 240s budget. No chat messages are sent, no instances are
 * terminated otherwise, no project create/delete.
 *
 * Procedure mirrors prod Phase-1:
 *   1. PREP (API): pick project = 83da04de if present on dev, else FIRST.
 *      Create ONE fresh instance via POST /api/instances (project_id in body).
 *   2. FRESH LOAD: page.goto(detailUrl) → capture button + chat + workspace.
 *      EXPECT (fix): chat visible; .overlay-hide-btn count 0.
 *   3. RELOAD: page.reload() → same captures + screenshot.
 *      EXPECT (fix): identical state.
 *   4. WORKSPACE TOGGLE CYCLE (dev-only addition):
 *      a. Open workspace via tab-bar .workspace-btn. Record banner+button.
 *      b. EXPECT: button count 1, "Hide editor", visibility_off.
 *      c. Click header button → workspace hidden, "Show editor".
 *      d. Click again → workspace re-shown with same projectId.
 *   5. CLEANUP: DELETE the instance only if I created it.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const DEV_BASE = 'http://localhost:4199';
const DEV_API = 'http://localhost:8079';
const PREFERRED_PROJECT_ID = '83da04de-a410-4fb5-9e92-251a99d28a52';
const SETTLE_MS = 5000;
const RELOAD_SETTLE_MS = 8000;
const POLL_TIMEOUT_MS = 8000;
const SCRIPT_BUDGET_MS = 240000;

const PROJECT_SCREENSHOT_DIR = 'frontend/test-results';
const SCRIPT_DIR = 'frontend/scripts';

// ── Helpers ──
const log = (...args) => console.log('[r3]', ...args);
const err = (...args) => console.error('[r3]', ...args);

const knownNoiseFilter = (text) => {
  if (typeof text !== 'string') return false;
  // CSP warnings from plane.ensem.dev iframe
  if (text.includes('plane.ensem.dev') && text.includes('CSP')) return true;
  if (text.includes('Content Security Policy') && text.includes('plane.ensem.dev')) return true;
  // Angular NG0100 stale-signal writes (common in dev mode)
  if (text.includes('NG0100') || text.includes('ExpressionChangedAfterItHasBeenCheckedError')) return true;
  // /api/workspace and /vscode-folder 404s (known prod bug for empty project)
  if (text.includes('/api/workspace') && text.includes('404')) return true;
  if (text.includes('/vscode-folder') && text.includes('404')) return true;
  // Plane iframe cascading errors (React #418 / #423)
  if (text.includes('Minified React error #418') || text.includes('Minified React error #423')) return true;
  return false;
};

const timeout = (ms) => new Promise((res) => setTimeout(res, ms));

async function pickProject(request) {
  const resp = await request.get(`${DEV_API}/api/projects?exclude_system=true`);
  if (!resp.ok()) throw new Error(`GET /api/projects failed: ${resp.status()}`);
  const body = await resp.json();
  const projects = body.projects || [];
  if (projects.length === 0) throw new Error('No non-system projects available on dev');
  const preferred = projects.find((p) => p.project_id === PREFERRED_PROJECT_ID);
  const chosen = preferred || projects[0];
  return {
    projectId: chosen.project_id,
    projectName: chosen.name,
    wasPreferred: !!preferred,
    totalProjects: projects.length,
  };
}

async function createInstance(request, projectId, agentId = 'leader') {
  const resp = await request.post(`${DEV_API}/api/instances`, {
    data: { agent_id: agentId, project_id: projectId },
  });
  if (!resp.ok()) {
    const text = await resp.text();
    throw new Error(`POST /api/instances failed: ${resp.status()} ${text.slice(0, 200)}`);
  }
  const body = await resp.json();
  return body.instance_id;
}

async function deleteInstance(request, instanceId) {
  const resp = await request.delete(`${DEV_API}/api/instances/${instanceId}?hard_delete=true`);
  if (!resp.ok()) {
    const text = await resp.text();
    return { ok: false, status: resp.status(), text: text.slice(0, 200) };
  }
  return { ok: true, status: resp.status() };
}

// ── Main capture (state extracts) ──
async function captureState(page) {
  return await page.evaluate(() => {
    const q = (sel, root = document) => root.querySelector(sel);
    const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    const btn = q('.overlay-hide-btn');
    const headerBtns = qa('header button');
    const headerIcons = qa('header mat-icon').map((el) => ({
      text: (el.textContent || '').trim(),
      aria: el.getAttribute('aria-label') || '',
    }));
    const visibilityLikeIcons = headerIcons.filter((i) =>
      /visibility/.test(i.text + ' ' + i.aria),
    );

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
        '.message-row',
        '.message-content',
        '.message-bubble',
        'app-message',
        '.empty-state',
        '.welcome-description',
        '.welcome-title',
      ];
      for (const sel of trySelectors) {
        const count = qa(sel, chat).length;
        let firstText = '';
        if (count > 0) {
          firstText = (qa(sel, chat)[0].textContent || '').trim().slice(0, 200);
        }
        messageSelectors[sel] = { count, firstText };
      }
    }
    const chatLoading =
      chat && qa('mat-spinner, mat-progress-spinner, .loading-spinner', chat).length;

    const ws = q('app-workspace');
    const wsStyleDisplay = ws ? ws.style.display : 'absent';
    const wsComputedDisplay = ws ? getComputedStyle(ws).display : 'absent';

    const wsInner = ws ? q('.workspace-container, .workspace-content, app-workspace > *', ws) : null;
    const wsInnerSummary = ws
      ? ws.innerHTML.length
      : 0;
    const wsErrorBanner = ws
      ? qa('.error-banner, .error-state, [class*="error"]', ws).map((el) => ({
          cls: (el.className || '').toString().slice(0, 100),
          text: (el.textContent || '').trim().slice(0, 200),
        }))
      : [];

    const tabBar = q('app-project-tab-bar');
    const tabBarWorkspaceBtns = qa('app-project-tab-bar .workspace-btn');
    const activeTab = tabBar
      ? q('app-project-tab-bar .tab.active', tabBar) || q('app-project-tab-bar .tab.active-tab', tabBar)
      : null;
    const activeTabName = activeTab ? (activeTab.textContent || '').trim().slice(0, 80) : null;

    const planeOverlay = q('.plane-overlay');
    const planeDisplay = planeOverlay
      ? getComputedStyle(planeOverlay).display
      : 'absent';

    const isPlanRoute = location.pathname.includes('/plan');

    const localStorageKeys = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k) localStorageKeys[k] = (localStorage.getItem(k) || '').length;
    }

    const ensembleTabsRaw = localStorage.getItem('ensemble-project-tabs');
    let ensembleTabsSummary = null;
    if (ensembleTabsRaw) {
      try {
        const parsed = JSON.parse(ensembleTabsRaw);
        ensembleTabsSummary = {
          activeTabId: parsed.activeTabId,
          openTabCount: Array.isArray(parsed.openTabs) ? parsed.openTabs.length : null,
          openTabIds: Array.isArray(parsed.openTabs) ? parsed.openTabs.map((t) => t.id) : null,
        };
      } catch (e) {
        ensembleTabsSummary = { error: String(e) };
      }
    }

    return {
      url: location.href,
      title: document.title,
      header: {
        overlayHideBtnCount: btn ? 1 : 0,
        overlayHideBtnAria: btn ? btn.getAttribute('aria-label') : null,
        overlayHideBtnIconText: btn ? (q('mat-icon', btn)?.textContent || '').trim() : null,
        overlayHideBtnDisplay: btn ? getComputedStyle(btn).display : null,
        overlayHideBtnRect: btn
          ? (() => {
              const r = btn.getBoundingClientRect();
              return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
            })()
          : null,
        headerButtonCount: headerBtns.length,
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
        styleDisplay: wsStyleDisplay,
        computedDisplay: wsComputedDisplay,
        innerHtmlLength: wsInnerSummary,
        childCount: ws ? ws.children.length : 0,
        errorBannerCount: wsErrorBanner.length,
        errorBannerSummaries: wsErrorBanner.slice(0, 3),
      },
      tabBar: {
        present: !!tabBar,
        workspaceBtnCount: tabBarWorkspaceBtns.length,
        activeTabName,
      },
      plane: {
        present: !!planeOverlay,
        display: planeDisplay,
      },
      isPlanRoute,
      localStorageKeys,
      ensembleTabsSummary,
    };
  });
}

// ── Polling helper for affordance reads ──
async function pollUntil(predicate, timeoutMs = POLL_TIMEOUT_MS, interval = 200) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const v = await predicate();
      if (v) return v;
    } catch (e) {
      // ignore transient errors
    }
    await timeout(interval);
  }
  return null;
}

// ── Run ──
(async () => {
  const budgetStart = Date.now();
  const budgetPromise = (async () => {
    await timeout(SCRIPT_BUDGET_MS);
    throw new Error(`Script exceeded internal budget of ${SCRIPT_BUDGET_MS}ms`);
  })();

  const mainPromise = (async () => {
    if (!fs.existsSync(PROJECT_SCREENSHOT_DIR)) {
      fs.mkdirSync(PROJECT_SCREENSHOT_DIR, { recursive: true });
    }

    const browser = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });

    // Two contexts: one for API (no UI), one for browser (no storage state)
    const apiContext = await browser.newContext();
    const api = apiContext.request;
    const browserContext = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      storageState: undefined,
    });
    const page = await browserContext.newPage();

    const consoleByPhase = {
      prep: [],
      freshLoad: [],
      reload: [],
      wsOpen: [],
      wsHide: [],
      wsReshow: [],
      unknown: [],
    };
    let activePhase = 'prep';
    const setPhase = (p) => { activePhase = p; };
    const classified = () => consoleByPhase[activePhase] || consoleByPhase.unknown;

    page.on('console', (msg) => {
      const type = msg.type();
      if (type === 'error' || type === 'warning') {
        const loc = msg.location() || {};
        classified().push({
          type,
          text: msg.text(),
          url: loc.url || '',
          lineNumber: loc.lineNumber,
          columnNumber: loc.columnNumber,
          phase: activePhase,
        });
      }
    });
    page.on('pageerror', (err) => {
      classified().push({
        type: 'pageerror',
        text: String(err && err.message ? err.message : err),
        stack: err && err.stack ? err.stack.slice(0, 500) : '',
        phase: activePhase,
      });
    });

    const probe = {
      prep: {},
      freshLoad: {},
      reload: {},
      workspaceOpen: {},
      workspaceHidden: {},
      workspaceReshown: {},
      cleanup: {},
    };

    let createdInstanceId = null;
    let detailUrl = null;
    let projectId = null;
    let projectName = null;
    let urlInitial = null;
    let urlReload = null;

    try {
      // ── PREP (API) ──
      const chosen = await pickProject(api);
      projectId = chosen.projectId;
      projectName = chosen.projectName;
      log('PREP: chose project', projectId, '(', projectName, ')', 'preferred=', chosen.wasPreferred);
      setPhase('prep');

      // Capture a few existing instances for the report (NOT used in flow)
      const existingResp = await api.get(
        `${DEV_API}/api/instances?project_id=${projectId}&limit=5`,
      );
      const existingJson = existingResp.ok() ? await existingResp.json() : { total: 0, instances: [] };
      probe.prep.project = chosen;
      probe.prep.existingInstances = {
        total: existingJson.total,
        sample: (existingJson.instances || []).map((i) => ({
          instance_id: i.instance_id,
          status: i.status,
          agent_id: i.agent_id,
          title: i.title,
        })),
      };

      // Decide: existing-with-messages or fresh?
      // Task: "prefer existing-with-messages to avoid side effects; record which you used and why"
      // The first project's existing instance is terminated with no messages. Fresh instance is
      // the cleanest path. Send NO messages — the chat will render its welcome state.
      let useInstanceId = null;
      let createdFresh = false;
      let useExistingWhy = null;

      const viable = (existingJson.instances || []).find((i) => {
        if (i.status === 'terminated' || i.status === 'failed' || i.status === 'error') return false;
        return true;
      });

      if (viable) {
        // Check messages
        const msgResp = await api.get(`${DEV_API}/api/instances/${viable.instance_id}/messages?limit=3`);
        const msgJson = msgResp.ok() ? await msgResp.json() : [];
        if (Array.isArray(msgJson) && msgJson.length > 0) {
          useInstanceId = viable.instance_id;
          useExistingWhy = `existing instance ${viable.instance_id} has ${msgJson.length} messages (status=${viable.status})`;
        } else {
          useExistingWhy = `viable existing instance ${viable.instance_id} has zero messages, falling back to fresh`;
        }
      } else {
        useExistingWhy = `no viable existing instance on project ${projectId} (all ${existingJson.instances.length} are terminated or absent); creating fresh`;
      }

      if (!useInstanceId) {
        createdInstanceId = await createInstance(api, projectId, 'leader');
        useInstanceId = createdInstanceId;
        createdFresh = true;
        log('PREP: created fresh instance', createdInstanceId);
      } else {
        log('PREP: using existing instance', useInstanceId, '—', useExistingWhy);
      }

      probe.prep.decision = {
        instanceId: useInstanceId,
        createdFresh,
        rationale: useExistingWhy,
      };

      detailUrl = `${DEV_BASE}/projects/${projectId}/instances/${useInstanceId}`;
      urlInitial = detailUrl;
      probe.prep.detailUrl = detailUrl;

      // ── STEP 2: FRESH LOAD ──
      try {
        await page.goto(detailUrl, { waitUntil: 'load', timeout: 30000 });
      } catch (e) {
        log('FRESH LOAD goto error:', e.message);
      }
      // Wait for chat to render + either messages or welcome state
      setPhase('freshLoad');
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const chat = document.querySelector('app-chat');
            if (!chat) return false;
            const cs = getComputedStyle(chat);
            if (cs.display === 'none') return false;
            return !!(
              document.querySelector('app-chat .message-row, app-chat .welcome-title, app-chat .message-bubble')
            );
          });
        },
        POLL_TIMEOUT_MS,
      );
      await timeout(SETTLE_MS);
      probe.freshLoad = await captureState(page);
      await page.screenshot({
        path: path.join(PROJECT_SCREENSHOT_DIR, 'r3-mirror-01-fresh-load.png'),
        fullPage: true,
      });

      // ── STEP 3: RELOAD ──
      setPhase('reload');
      try {
        await page.reload({ waitUntil: 'load', timeout: 30000 });
      } catch (e) {
        log('RELOAD goto error:', e.message);
      }
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const chat = document.querySelector('app-chat');
            if (!chat) return false;
            const cs = getComputedStyle(chat);
            if (cs.display === 'none') return false;
            return !!(
              document.querySelector('app-chat .message-row, app-chat .welcome-title, app-chat .message-bubble')
            );
          });
        },
        POLL_TIMEOUT_MS,
      );
      await timeout(RELOAD_SETTLE_MS);
      probe.reload = await captureState(page);
      urlReload = page.url();
      await page.screenshot({
        path: path.join(PROJECT_SCREENSHOT_DIR, 'r3-mirror-02-after-reload.png'),
        fullPage: true,
      });

      // ── STEP 4: WORKSPACE TOGGLE CYCLE ──
      // 4a. Open workspace via tab-bar .workspace-btn
      setPhase('wsOpen');
      const wsBtnClicked = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('app-project-tab-bar .workspace-btn'));
        if (btns.length === 0) return { clicked: false, reason: 'no workspace-btn' };
        // Pick the active tab's button (or any) and click
        const activeTab = document.querySelector('app-project-tab-bar .tab.active') ||
          document.querySelector('app-project-tab-bar .tab[class*="active"]');
        let target = btns[0];
        if (activeTab) {
          const inner = activeTab.querySelector('.workspace-btn');
          if (inner) target = inner;
        }
        target.click();
        return { clicked: true, btnCount: btns.length, targetIsActiveTab: !!activeTab };
      });
      log('4a ws-open click:', wsBtnClicked);

      // Wait for workspace to become visible
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const ws = document.querySelector('app-workspace');
            if (!ws) return false;
            return getComputedStyle(ws).display !== 'none';
          });
        },
        POLL_TIMEOUT_MS,
      );
      // Wait for header button to appear (toPass-style)
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const btn = document.querySelector('.overlay-hide-btn');
            if (!btn) return null;
            return {
              aria: btn.getAttribute('aria-label'),
              icon: (btn.querySelector('mat-icon')?.textContent || '').trim(),
            };
          });
        },
        POLL_TIMEOUT_MS,
      );
      await timeout(SETTLE_MS);
      probe.workspaceOpen = await captureState(page);
      await page.screenshot({
        path: path.join(PROJECT_SCREENSHOT_DIR, 'r3-mirror-03-workspace-open.png'),
        fullPage: true,
      });

      // 4b. Already captured as part of workspaceOpen. Skip - state is in workspaceOpen.

      // 4c. Click header button to hide workspace
      setPhase('wsHide');
      const headerBtnClicked = await page.evaluate(() => {
        const btn = document.querySelector('.overlay-hide-btn');
        if (!btn) return { clicked: false, reason: 'no overlay-hide-btn' };
        btn.click();
        return { clicked: true, aria: btn.getAttribute('aria-label') };
      });
      log('4c header click:', headerBtnClicked);

      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const ws = document.querySelector('app-workspace');
            if (!ws) return false;
            return getComputedStyle(ws).display === 'none';
          });
        },
        POLL_TIMEOUT_MS,
      );
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const btn = document.querySelector('.overlay-hide-btn');
            if (!btn) return null;
            const aria = btn.getAttribute('aria-label') || '';
            const icon = (btn.querySelector('mat-icon')?.textContent || '').trim();
            // After hide, expect 'Show editor'
            if (aria === 'Show editor') return { aria, icon };
            return null;
          });
        },
        POLL_TIMEOUT_MS,
      );
      await timeout(SETTLE_MS);
      probe.workspaceHidden = await captureState(page);
      await page.screenshot({
        path: path.join(PROJECT_SCREENSHOT_DIR, 'r3-mirror-04-hidden.png'),
        fullPage: true,
      });

      // 4d. Click again to re-show workspace
      setPhase('wsReshow');
      const reshownBtnClicked = await page.evaluate(() => {
        const btn = document.querySelector('.overlay-hide-btn');
        if (!btn) return { clicked: false, reason: 'no overlay-hide-btn' };
        btn.click();
        return { clicked: true, aria: btn.getAttribute('aria-label') };
      });
      log('4d reshown click:', reshownBtnClicked);

      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const ws = document.querySelector('app-workspace');
            if (!ws) return false;
            return getComputedStyle(ws).display !== 'none';
          });
        },
        POLL_TIMEOUT_MS,
      );
      await pollUntil(
        async () => {
          const v = await page.evaluate(() => {
            const btn = document.querySelector('.overlay-hide-btn');
            if (!btn) return null;
            const aria = btn.getAttribute('aria-label') || '';
            const icon = (btn.querySelector('mat-icon')?.textContent || '').trim();
            if (aria === 'Hide editor') return { aria, icon };
            return null;
          });
        },
        POLL_TIMEOUT_MS,
      );
      await timeout(SETTLE_MS);
      probe.workspaceReshown = await captureState(page);
      await page.screenshot({
        path: path.join(PROJECT_SCREENSHOT_DIR, 'r3-mirror-05-reshown.png'),
        fullPage: true,
      });

      // ── STEP 5: CLEANUP ──
      if (createdInstanceId) {
        const cleanup = await deleteInstance(api, createdInstanceId);
        probe.cleanup = {
          instanceId: createdInstanceId,
          hardDeleted: true,
          ...cleanup,
        };
        log('CLEANUP: deleted instance', createdInstanceId, '→', cleanup);
      } else {
        probe.cleanup = { skipped: 'no fresh instance created', instanceId: useInstanceId };
      }
    } catch (e) {
      err('FATAL in main:', e && e.stack ? e.stack : e);
      probe.fatal = { error: String(e && e.message ? e.message : e) };
    } finally {
      // Filter console noise
      const filtered = {};
      const counts = {};
      for (const [phase, entries] of Object.entries(consoleByPhase)) {
        counts[phase] = { total: entries.length, filtered: 0, kept: [] };
        for (const e of entries) {
          const text = String(e.text || '');
          if (knownNoiseFilter(text)) {
            counts[phase].filtered++;
          } else {
            counts[phase].kept.push(e);
          }
        }
        filtered[phase] = counts[phase].kept;
      }

      const report = {
        capturedAt: new Date().toISOString(),
        scriptBudgetMs: SCRIPT_BUDGET_MS,
        runtimeMs: Date.now() - budgetStart,
        devBase: DEV_BASE,
        devApi: DEV_API,
        instanceId: probe.prep.decision ? probe.prep.decision.instanceId : null,
        instanceCreatedFresh: probe.prep.decision ? probe.prep.decision.createdFresh : null,
        instanceCleanup: probe.cleanup,
        prep: probe.prep,
        freshLoad: probe.freshLoad,
        reload: probe.reload,
        workspaceOpen: probe.workspaceOpen,
        workspaceHidden: probe.workspaceHidden,
        workspaceReshown: probe.workspaceReshown,
        consoleNoise: {
          filterFn: 'plane.ensem.dev CSP, NG0100, /api/workspace 404, /vscode-folder 404, React #418/#423',
          countsByPhase: counts,
          filteredNetByPhase: filtered,
        },
        fatal: probe.fatal || null,
      };
      console.log('===R3_REPORT_JSON===');
      console.log(JSON.stringify(report, null, 2));
      console.log('===END_R3_REPORT===');

      await browser.close();
    }
  })();

  await Promise.race([mainPromise, budgetPromise]);
})().catch((err) => {
  console.error('===R3_FATAL===', err && err.stack ? err.stack : err);
  process.exit(2);
});
