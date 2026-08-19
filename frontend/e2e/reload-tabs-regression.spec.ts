/**
 * E2E regression tests for the reload-tabs fix (hydrate-before-NavigationEnd).
 *
 * Original symptom: "Opened tabs/projects are not remembered when I reload
 * the page. Before it was remembered." Root cause: F3's
 * ``syncDetailVisibility`` ``addTab()`` on NavigationEnd fired BEFORE
 * ``TabStateService.restoreState()`` hydrated persisted tabs (restore only
 * ran in ``InstancesComponent.ngOnInit`` after the async ``listProjects``
 * resolved), so ``saveState()`` clobbered ``ensemble-project-tabs``
 * ``[All, A, B, C]`` → ``[All, singleProject]`` on a detail-URL reload.
 *
 * Fix under test: ``App`` constructor now calls
 * ``tabStateService.restoreState()`` BEFORE any NavigationEnd wiring (see
 * app.ts "hydrate-before-NavigationEnd invariant").
 *
 * Scenarios:
 *   R-TAB-1  Multi-tab reload restores ALL tabs (the original symptom).
 *   R-TAB-2  Detail-URL reload keeps tabs intact (the clobber path).
 *   R-TAB-3  Cold deep-link adds a tab without dropping persisted ones (F3).
 *   R-TAB-4  Fresh browser context: clean default state, stable on reload.
 *   R-TAB-5  Feature interaction: detail open → Plan → back → same
 *            instance restored (constructor restoreState must not break
 *            the instances view-state cache).
 *
 * Selector strategy (mirrors instances-state-cache-*.spec.ts):
 *   - ``app-project-tab-bar`` appears TWICE in the DOM once a detail has
 *     been opened: once inside the routed ``app-instances`` page and once
 *     inside the root-mounted, lazily-created ``app-chat .chat-container``
 *     (which is kept alive forever after first mount). All tab-bar reads
 *     are therefore scoped with ``:visible`` and/or explicit containers.
 *   - Tab chips are ``button.tab`` inside ``.tab-bar``; the "All" tab is
 *     the ``type === 'all'`` chip and has no close button. Project tabs
 *     render an ``[aria-label^="Close "]`` affordance, which is the most
 *     stable discriminator between All and project tabs.
 *   - localStorage key: ``ensemble-project-tabs`` with shape
 *     ``{ openTabs: Array<{id, name, type}>, activeTabId: string }`` (see
 *     tab-state.service.ts). The default in-memory state is ``[ALL_TAB]``
 *     and is NOT persisted until the first ``saveState()`` call — an
 *     empty localStorage means "default [All]".
 *
 * Assumptions:
 *   - BE daemon running on localhost:8079 (do not kill).
 *   - FE dev server on localhost:4199, auto-started by playwright.config.ts
 *     webServer with reuseExistingServer:true (120s timeout).
 *   - Project fixtures are API-created with unique timestamped names
 *     (projects cannot be deleted via API — same convention as the
 *     existing specs). Instances created for the detail tests are tracked
 *     and cleaned in afterAll.
 */

import { test, expect, Page, ConsoleMessage, BrowserContext } from '@playwright/test';
import {
  createTestProject,
  createTestInstance,
} from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

const TABS_STORAGE_KEY = 'ensemble-project-tabs';
const VIEW_STATE_STORAGE_KEY = 'ensemble-instances-view-state';

test.describe.configure({ mode: 'serial' });

// ─────────────────────────────────────────────────────────────────────────────
// localStorage helpers — shape mirrors TabStateService's StoredTabState.
// ─────────────────────────────────────────────────────────────────────────────

interface StoredTab { id: string; name: string; type: 'all' | 'project' }
interface StoredTabState { openTabs: StoredTab[]; activeTabId: string }

/** Read and parse ensemble-project-tabs; null when absent. */
async function readStoredTabs(page: Page): Promise<StoredTabState | null> {
  const raw = await page.evaluate(
    (key) => localStorage.getItem(key),
    TABS_STORAGE_KEY,
  );
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredTabState;
  } catch {
    return null;
  }
}

/**
 * Seed ensemble-project-tabs the same way the app writes it (saveState
 * JSON-serializes the full openTabs array of {id,name,type} objects). Only
 * project-type tabs are restored by restoreState, so seeded state uses
 * real fixture project ids.
 */
async function seedStoredTabs(page: Page, state: StoredTabState): Promise<void> {
  await page.evaluate(
    ({ key, value }) => localStorage.setItem(key, JSON.stringify(value)),
    { key: TABS_STORAGE_KEY, value: state },
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Tab-bar readers. All scoped: app-project-tab-bar can appear twice (routed
// instances page + always-alive chat subtree). We read the first VISIBLE
// bar; when two bars are visible simultaneously they render the same
// service state, so the read is deterministic either way.
// ─────────────────────────────────────────────────────────────────────────────

interface TabBarSnapshot {
  /** texts of every visible tab chip, in bar order */
  tabNames: string[];
  /** ids of the project-type tabs (derived from localStorage — the DOM has
   *  no id attribute; we pair DOM order with stored openTabs) */
  projectTabIds: string[];
  activeTabName: string;
  activeTabIndex: number;
}

/** Read the first VISIBLE project tab bar's chips. */
async function readTabBar(page: Page): Promise<TabBarSnapshot> {
  const bars = page.locator('app-project-tab-bar');
  const count = await bars.count();
  for (let i = 0; i < count; i++) {
    const bar = bars.nth(i);
    const chips = bar.locator('button.tab');
    const n = await chips.count();
    if (n === 0) continue;
    const visible = await chips.first().isVisible().catch(() => false);
    if (!visible) continue;
    const names: string[] = [];
    let activeName = '';
    let activeIndex = -1;
    for (let c = 0; c < n; c++) {
      const chip = chips.nth(c);
      const name = (await chip.locator('.tab-name').innerText()).trim();
      names.push(name);
      const cls = await chip.getAttribute('class');
      if (cls && /\bactive\b/.test(cls)) {
        activeName = name;
        activeIndex = c;
      }
    }
    // Pair DOM order with the persisted openTabs ids (same service signal).
    const stored = await readStoredTabs(page);
    const projectTabIds = stored
      ? stored.openTabs.filter((t) => t.type === 'project').map((t) => t.id)
      : [];
    return { tabNames: names, projectTabIds, activeTabName: activeName, activeTabIndex: activeIndex };
  }
  throw new Error('No visible app-project-tab-bar found');
}

/** Wait until the visible tab bar contains all expected chip names. */
async function expectTabBarTabs(
  page: Page,
  expected: string[],
  timeout = 15000,
): Promise<void> {
  await expect(async () => {
    const snap = await readTabBar(page);
    for (const name of expected) {
      expect(
        snap.tabNames,
        `tab bar chips: ${JSON.stringify(snap.tabNames)}`,
      ).toContain(name);
    }
  }).toPass({ timeout });
}

/** Assert localStorage ensemble-project-tabs ids equal expected set. */
async function expectStoredTabIds(
  page: Page,
  expectedIds: string[],
): Promise<void> {
  const stored = await readStoredTabs(page);
  expect(stored, `${TABS_STORAGE_KEY} should be present`).toBeTruthy();
  const ids = stored!.openTabs.filter((t) => t.type === 'project').map((t) => t.id);
  expect(
    [...ids].sort().join(','),
    `${TABS_STORAGE_KEY} project ids: ${JSON.stringify(stored)}`,
  ).toBe([...expectedIds].sort().join(','));
}

/** Filter known-noise console errors (mirrors regression spec policy). */
function filterConsoleErrors(consoleErrors: string[]): string[] {
  return consoleErrors.filter(
    (e) =>
      !(
        e.includes('Failed to load resource') &&
        (e.includes('/api/workspace/') || e.includes('/vscode-folder'))
      ) &&
      !(
        e.includes('[SSE] Connection error') ||
        e.includes('[SSE] EventSource connection error')
      ),
  );
}

/** Wait for the app shell to settle post-navigation (no networkidle: SSE). */
async function bootAndWait(page: Page, url: string): Promise<void> {
  await page.goto(url);
  // networkidle unreachable: permanent notifications SSE stream
  await page.waitForLoadState('domcontentloaded');
}

test.describe('Reload Tabs Regression (hydrate-before-NavigationEnd)', () => {
  let page: Page;
  let consoleErrors: string[] = [];
  let pageErrors: string[] = [];
  const timestamp = Date.now();

  // R-TAB-1/2/3 fixtures — three real projects so seeded tabs validate
  // against listProjects in restoreState(projectIds).
  const projects: Array<{ name: string; project_id: string }> = [];

  // R-TAB-2 fixture: instance in projects[0] for the detail URL.
  let detailInstanceId = '';

  // R-TAB-3 fixtures — a FOURTH project deep-linked cold (NOT seeded into
  // the persisted tabs). Describe-scoped because its tab persists into
  // R-TAB-5's assertions.
  const deepProject = { name: '', project_id: '' };
  let deepInstanceId = '';

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    page.on('console', (msg: ConsoleMessage) => {
      if (msg.type() === 'error') {
        // Include the originating resource URL when available so tests can
        // distinguish fixture-induced backend noise from app breakage.
        const loc = msg.location();
        consoleErrors.push(`[${msg.type()}] ${msg.text()}${loc.url ? ` (${loc.url})` : ''}`);
      }
    });
    page.on('pageerror', (err: Error) => pageErrors.push(err.message));

    for (let i = 0; i < 3; i++) {
      const p = await createTestProject(`e2e-rtabs-${timestamp}-p${i}`);
      projects.push({ name: p.name, project_id: p.project_id });
      trackProject(p.project_id);
    }

    const inst = await createTestInstance('leader', projects[0].project_id);
    detailInstanceId = inst.instance_id;
    trackInstance(inst.instance_id);

    // Pre-flight boot so Angular bootstraps.
    await bootAndWait(page, '/');
  });

  test.afterAll(async () => {
    await cleanupAll();
    await page?.close();
  });

  test.beforeEach(async () => {
    consoleErrors = [];
    pageErrors = [];
  });

  // ==========================================================================
  // R-TAB-1: multi-tab reload restores ALL tabs (the original symptom)
  // ==========================================================================
  test('R-TAB-1: reload with multiple open tabs restores ALL tabs', async () => {
    // Open three project tabs through the UI: the "+" menu lists
    // unopened projects; clicking one calls addTab (the genuine user
    // path). Selectors mirror instances-project-tabs.spec.ts — the add
    // button is scoped to the routed instances page, and the menu items
    // are NOT scoped because mat-menu panels render in the CDK overlay.
    await bootAndWait(page, '/instances');

    const tabBarAdd = page.locator('app-instances .tab-add');
    await expect(tabBarAdd).toBeVisible({ timeout: 15000 });

    for (const proj of projects) {
      await tabBarAdd.click();
      const menuItem = page
        .locator('.project-menu button[mat-menu-item]', { hasText: proj.name })
        .first();
      await expect(menuItem).toBeVisible({ timeout: 10000 });
      await menuItem.click();
      await expectTabBarTabs(page, [proj.name], 15000);
    }

    // Tab bar shows All + all three projects.
    await expectTabBarTabs(page, ['All', ...projects.map((p) => p.name)]);
    await expectStoredTabIds(page, projects.map((p) => p.project_id));

    // RELOAD — the original symptom: all-but-All tabs dropped.
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    await expectTabBarTabs(page, ['All', ...projects.map((p) => p.name)], 20000);
    await expectStoredTabIds(page, projects.map((p) => p.project_id));

    const filtered = filterConsoleErrors(consoleErrors);
    expect(filtered, `console errors: ${filtered.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R-TAB-2: detail-URL reload keeps tabs intact (the clobber path)
  // ==========================================================================
  test('R-TAB-2: detail-URL reload does not clobber persisted tabs', async () => {
    // Preconditions from R-TAB-1: three project tabs persisted and active
    // state is the last-added project. Open an instance detail whose
    // project (projects[0]) already has a tab open.
    const detailUrl = `/projects/${projects[0].project_id}/instances/${detailInstanceId}`;
    await bootAndWait(page, detailUrl);

    // Wait for the chat overlay to open (lazy mount on first detail).
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 15000 });

    // Three tabs must be open BEFORE the reload.
    await expectTabBarTabs(page, ['All', ...projects.map((p) => p.name)], 20000);
    await expectStoredTabIds(page, projects.map((p) => p.project_id));

    // RELOAD on the DETAIL URL — the clobber path: F3's NavigationEnd
    // addTab used to fire before restoreState and saveState() overwrote
    // [All, A, B, C] with [All, A].
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // Chat overlay re-opens from the URL (deep-link path).
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 15000 });

    // Tabs NOT clobbered to [All, single]: all three still present in the
    // bar AND in localStorage.
    await expectTabBarTabs(page, ['All', ...projects.map((p) => p.name)], 20000);
    await expectStoredTabIds(page, projects.map((p) => p.project_id));

    const stored = await readStoredTabs(page);
    expect(
      stored!.openTabs.filter((t) => t.type === 'project').length,
      `persisted project tab count must stay 3: ${JSON.stringify(stored)}`,
    ).toBe(3);

    const filtered2 = filterConsoleErrors(consoleErrors);
    expect(filtered2, `console errors: ${filtered2.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R-TAB-3: cold deep-link adds a tab without dropping persisted ones (F3)
  // ==========================================================================
  test('R-TAB-3: cold deep-link to unopened project ADDS tab, keeps others', async () => {
    // Fourth project with an instance — NOT in the persisted tabs.
    const projD = await createTestProject(`e2e-rtabs-${timestamp}-p3`);
    deepProject.name = projD.name;
    deepProject.project_id = projD.project_id;
    trackProject(projD.project_id);
    const instD = await createTestInstance('leader', projD.project_id);
    deepInstanceId = instD.instance_id;
    trackInstance(instD.instance_id);

    // Seed exactly like the app does: [All, A, B, C] with A active, on a
    // NON-detail URL so seeding is not disturbed by F3 wiring.
    await bootAndWait(page, '/instances');
    await seedStoredTabs(page, {
      openTabs: [
        { id: 'all', name: 'All', type: 'all' },
        ...projects.map((p) => ({ id: p.project_id, name: p.name, type: 'project' as const })),
      ],
      activeTabId: projects[0].project_id,
    });

    // COLD deep-link (page.goto, full browser-style navigation) to the
    // detail URL of the project NOT in the persisted tabs. On boot the
    // constructor restoreState() hydrates [All, A, B, C] FIRST; the F3
    // branch then finds projD missing and addTab(projD) APPENDS it — the
    // persisted tabs must survive the write-back.
    await page.goto(`/projects/${deepProject.project_id}/instances/${deepInstanceId}`);
    await page.waitForLoadState('domcontentloaded');

    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 15000 });

    // New tab ADDED and all existing tabs still present. NOTE: the F3
    // deep-link branch calls addTab({ project_id, name: projectId }) — the
    // chip label is the project ID until a later listProjects-driven
    // restore refreshes the name, so we assert on the ID, not the name.
    await expectTabBarTabs(page, ['All', deepProject.project_id, ...projects.map((p) => p.name)], 20000);
    await expectStoredTabIds(page, [deepProject.project_id, ...projects.map((p) => p.project_id)]);
    const stored = await readStoredTabs(page);
    expect(stored!.activeTabId).toBe(deepProject.project_id);

    const filtered3 = filterConsoleErrors(consoleErrors);
    expect(filtered3, `console errors: ${filtered3.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R-TAB-4: fresh browser clean default
  // ==========================================================================
  test('R-TAB-4: fresh context shows clean default tab state, stable on reload', async ({ browser }) => {
    // Fresh context: EMPTY localStorage — no seeded tabs from earlier tests.
    const context: BrowserContext = await browser.newContext();
    const freshPage = await context.newPage();
    const freshConsoleErrors: string[] = [];
    const freshPageErrors: string[] = [];
    freshPage.on('console', (msg: ConsoleMessage) => {
      if (msg.type() === 'error') {
        const loc = msg.location();
        freshConsoleErrors.push(`[${msg.type()}] ${msg.text()}${loc.url ? ` (${loc.url})` : ''}`);
      }
    });
    freshPage.on('pageerror', (err: Error) => freshPageErrors.push(err.message));

    try {
      // NOTE: browser.newContext() does NOT inherit the fixture context's
      // baseURL (use.baseURL only applies to the built-in page fixture) —
      // use absolute URLs here.
      await freshPage.goto('http://localhost:4199/');
      await freshPage.waitForLoadState('domcontentloaded');

      // Default state: the All tab only. The default in-memory state is
      // [ALL_TAB] and is not persisted until the first saveState, so
      // localStorage may be absent — absent means default.
      await freshPage.goto('http://localhost:4199/instances');
      await freshPage.waitForLoadState('domcontentloaded');
      const chips = freshPage.locator('app-project-tab-bar:visible button.tab');
      await expect(chips).toHaveCount(1, { timeout: 15000 });
      await expect(chips.first()).toContainText('All');

      const stored = await freshPage.evaluate(
        (key) => localStorage.getItem(key),
        TABS_STORAGE_KEY,
      );
      // Either absent (never saved) or the clean default [All].
      if (stored !== null) {
        const parsed = JSON.parse(stored) as StoredTabState;
        expect(parsed.openTabs).toHaveLength(1);
        expect(parsed.openTabs[0].type).toBe('all');
        expect(parsed.openTabs[0].id).toBe('all');
      }

      // Reload → still default, no errors.
      await freshPage.reload();
      await freshPage.waitForLoadState('domcontentloaded');
      const chipsAfter = freshPage.locator('app-project-tab-bar:visible button.tab');
      await expect(chipsAfter).toHaveCount(1, { timeout: 15000 });
      await expect(chipsAfter.first()).toContainText('All');

      const filtered4 = filterConsoleErrors(freshConsoleErrors);
      expect(filtered4, `console errors: ${filtered4.join('\n')}`).toEqual([]);
      expect(freshPageErrors, `page errors: ${freshPageErrors.join('\n')}`).toEqual([]);
    } finally {
      await context.close();
    }
  });

  // ==========================================================================
  // R-TAB-5: feature interaction — detail open → Plan → back → same instance
  // ==========================================================================
  test('R-TAB-5: detail open → Plan → back restores SAME instance (view-state cache intact)', async () => {
    // Reuse the R-TAB-2 detail instance. Cold deep-link opens it.
    const detailUrl = `/projects/${projects[0].project_id}/instances/${detailInstanceId}`;
    await bootAndWait(page, detailUrl);

    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 15000 });

    // View-state cache must hold this instance (deep-link openDetail).
    const viewState = await page.evaluate(
      (key) => localStorage.getItem(key),
      VIEW_STATE_STORAGE_KEY,
    );
    expect(viewState, 'ensemble-instances-view-state should be set').toBeTruthy();
    const parsedView = JSON.parse(viewState as string);
    expect(parsedView.activeInstanceId).toBe(detailInstanceId);

    // Plan nav is conditional on planeEnabled — skip with a clear reason
    // if this env doesn't expose it (same policy as the core spec).
    const planNav = page.locator('a.nav-link', { hasText: /^Plan$/ });
    if ((await planNav.count()) === 0) {
      test.skip(true, 'Plan nav link not present (plane disabled in this env)');
      return;
    }

    // Tag the chat subtree with an identity marker so we can prove the
    // SAME DOM node is restored (no destroy/recreate).
    const tagged = await page.evaluate(() => {
      const c = document.querySelector('app-chat .chat-container');
      if (!c) return false;
      c.setAttribute('data-e2e-marker', 'RTAB5');
      return true;
    });
    expect(tagged).toBe(true);

    // In-project navigation: Plan.
    await planNav.first().click();
    await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });
    // Chat overlay hidden while on /plan.
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).toBe('none');
    }).toPass({ timeout: 10000 });

    // Back to Instances — the nav link is bound to lastDetailRoute so the
    // SAME cached detail is restored.
    const instancesNav = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    await instancesNav.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    // SAME instance restored: URL + view-state cache + identity marker.
    expect(page.url()).toContain(detailInstanceId);
    const viewAfter = await page.evaluate(
      (key) => localStorage.getItem(key),
      VIEW_STATE_STORAGE_KEY,
    );
    expect(viewAfter).toBeTruthy();
    expect(JSON.parse(viewAfter as string).activeInstanceId).toBe(detailInstanceId);

    const markerStillThere = await page.evaluate(() =>
      !!document.querySelector('[data-e2e-marker="RTAB5"]'),
    );
    expect(markerStillThere, 'chat subtree must be the same DOM node').toBe(true);

    // Tab state must ALSO survive the Plan round-trip (constructor restore
    // + F3 setActiveTab write-back keep the full set intact). R-TAB-3
    // deep-linked a fourth project, so all FOUR project tabs must persist.
    await expectStoredTabIds(page, [
      projects[0].project_id,
      ...projects.slice(1).map((p) => p.project_id),
      deepProject.project_id,
    ]);

    const filtered5 = filterConsoleErrors(consoleErrors);
    expect(filtered5, `console errors: ${filtered5.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });
});
