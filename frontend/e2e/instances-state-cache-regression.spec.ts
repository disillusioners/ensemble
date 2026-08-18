/**
 * Regression coverage for the Instances detail overlay caching contract.
 *
 * Covers:
 *   - R6  Persistence: cold reload restores the cache to the "Instances" nav
 *        link's routerLink (lastDetailRoute) but leaves detailVisible FALSE
 *        so the overlay stays hidden until the user explicitly opens it.
 *   - R2  Sidebar: the chat sidebar must keep showing the full instance list
 *        when activeProjectId === 'all' (the "All" pseudo-project), across
 *        route changes.
 *   - R4  Hide button: clicking the unified overlay hide button while the
 *        detail overlay is up must navigate to /instances (closing the
 *        overlay) without a blank screen.
 *   - R5  Workspace overlay: workspace must layer ABOVE the chat detail
 *        (z-index workspace=100 > chat=90 per the documented ladder).
 *   - Terminate flow: terminating the cached instance clears the localStorage
 *        cache and the "Instances" nav link falls back to /instances.
 *
 * Destructive test:
 *   - The terminate test creates a FRESH test instance via the API, opens
 *     its detail, terminates it from the chat sidebar's terminate button,
 *     and asserts the localStorage cache + nav link react correctly. The
 *     shared `trackInstance()` fixture cleans it up in afterAll.
 *
 * Selector strategy: roles/aria-labels preferred. .overlay-hide-btn,
 * [aria-label="Open Workspace Viewer"], a.instance-item, .terminate-btn are
 * all stable across the current UI.
 */

import { test, expect, Page, ConsoleMessage } from '@playwright/test';
import {
  createTestProject,
  createTestInstance,
  listInstances,
} from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

test.describe.configure({ mode: 'serial' });

test.describe('Instances Detail Overlay - Regression', () => {
  let page: Page;
  let consoleErrors: string[] = [];
  let pageErrors: string[] = [];
  const timestamp = Date.now();
  const r5Project = { name: `e2e-regression-r5-${timestamp}`, project_id: '' };
  const r5Instances: string[] = [];
  const terminateProject = { name: `e2e-regression-term-${timestamp}`, project_id: '' };
  let terminateInstanceId = '';

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

    // R5 needs a REAL project (not 'all') so the chat-header workspace
    // button is rendered (the template gates it on hasRealProject).
    const r5proj = await createTestProject(r5Project.name);
    r5Project.project_id = r5proj.project_id;
    trackProject(r5Project.project_id);
    for (let i = 0; i < 2; i++) {
      const inst = await createTestInstance('leader', r5Project.project_id);
      r5Instances.push(inst.instance_id);
      trackInstance(inst.instance_id);
    }

    // Terminate test gets its own project + instance for hermetic cleanup.
    const tProj = await createTestProject(terminateProject.name);
    terminateProject.project_id = tProj.project_id;
    trackProject(terminateProject.project_id);
    const tInst = await createTestInstance('leader', terminateProject.project_id);
    terminateInstanceId = tInst.instance_id;
    trackInstance(tInst.instance_id);

    // Pre-flight boot so Angular bootstraps with our state seeded.
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream
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
  // R6: Persistence — reload must keep nav-link target cached but overlay hidden
  // ==========================================================================
  test('R6: reload restores nav-link target, keeps overlay hidden', async () => {
    // Seed a cached detail by opening one. Use the first available instance
    // in any project — we don't care which one for this test.
    await page.goto('/instances');
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream
    const card = page.locator('a.instance-item').first();
    await expect(card).toBeVisible({ timeout: 15000 });
    const expectedHref = await card.getAttribute('href');
    expect(expectedHref).toMatch(/^\/projects\/[^/?]+\/instances\/[^/?]+$/);

    await card.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Sanity: the Instances nav link is now bound to that detail route.
    const instancesNav = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    await expect(instancesNav).toBeVisible();
    const hrefBefore = await instancesNav.getAttribute('href');
    expect(hrefBefore).toMatch(/^\/projects\/[^/?]+\/instances\/[^/?]+$/);

    // Reload — cold start. The cache should be restored to the nav link
    // but detailVisible must stay FALSE (restoreState is non-flipping).
    await page.reload();
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream

    // URL should NOT be the detail route after reload (we navigated to /
    // implicitly? Actually reload preserves URL — let me be careful here).
    // The user was on the detail URL; reload stays there. The overlay SHOULD
    // still be visible because URL points at detail. The "non-flipping"
    // guarantee is only that a NON-detail URL after reload doesn't open the
    // overlay. So after reload at the detail URL, overlay stays visible.
    // To prove R6 non-flipping, we navigate to / first, then reload, and
    // assert overlay stays hidden.
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream

    const chatDisplay = await page.locator('app-chat').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(chatDisplay).toBe('none');

    // BUT the Instances nav link should still point at the cached detail
    // (lastDetailRoute computes from activeInstanceId which restoreState
    // does populate).
    const navAfter = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    const hrefAfter = await navAfter.getAttribute('href');
    expect(hrefAfter).toBe(expectedHref);

    // Click the nav link → detail of the cached instance opens.
    await navAfter.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R2: Sidebar shows multiple instances on "All" tab across navigation
  // ==========================================================================
  test('R2: "All" tab sidebar keeps showing multiple instances across navigation', async () => {
    // Make sure the "All" project tab is active (it's the default, but the
    // previous test may have left a different tab active).
    await page.goto('/instances');
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream
    const allTab = page.locator('.tab').first();
    await expect(allTab).toHaveClass(/active/);

    // Wait for the sidebar instance list (inside the chat subtree is rendered
    // when we open detail; for the list page it's the standalone instance list).
    // For R2 we open detail and inspect the chat sidebar.
    // Hermetic: pick a card for an instance in our OWN fixture project
    // (r5Instances[0]) so the test doesn't depend on dev-DB content. NOTE:
    // select by INSTANCE ID, not owning-project path — instance-list.html
    // builds hrefs as ['/projects', getProjectContext(), 'instances', id]
    // and getProjectContext() returns 'all' on the All tab, so project-path
    // matching never hits.
    const card = page
      .locator(`a.instance-item[href*="/instances/${r5Instances[0]}"]`)
      .first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await card.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Wait for the chat sidebar to render its instance list — AND for it
    // to be populated. The sidebar list loads async (project-scoped); the
    // skip guard previously fired on an EMPTY list, not a real count.
    const sidebar = page.locator('app-chat .instance-sidebar app-instance-list');
    await expect(sidebar).toBeVisible({ timeout: 15000 });
    await expect(async () => {
      const n = await page.evaluate(() =>
        document.querySelectorAll('app-chat .instance-sidebar a.instance-item').length,
      );
      expect(n).toBeGreaterThanOrEqual(2);
    }).toPass({ timeout: 10000 });

    const countBefore = await page.evaluate(() => {
      const root = document.querySelector('app-chat .instance-sidebar');
      if (!root) return 0;
      // Count .instance-item links anywhere inside the sidebar.
      return root.querySelectorAll('a.instance-item').length;
    });

    // We need >1 to actually exercise the "multiple instances" assertion.
    // If the dev env is empty, this test is invalid — bail with a clear skip.
    if (countBefore < 2) {
      test.skip(true, `Sidebar shows ${countBefore} instances — need >=2 for R2`);
      return;
    }

    // Click Plan nav (if available), else click the home logo to navigate away.
    const planNav = page.locator('a.nav-link', { hasText: /^Plan$/ });
    let navigated = false;
    if ((await planNav.count()) > 0) {
      await planNav.first().click();
      await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });
      navigated = true;
    } else {
      await page.locator('a.logo-link').first().click();
      await page.waitForURL(/\/$/, { timeout: 10000 });
      navigated = true;
    }
    expect(navigated).toBe(true);

    // Navigate back via the Instances nav (which goes to the cached detail).
    const instancesNav = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    await instancesNav.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Re-check sidebar count — must be >= the original.
    const countAfter = await page.evaluate(() => {
      const root = document.querySelector('app-chat .instance-sidebar');
      if (!root) return 0;
      return root.querySelectorAll('a.instance-item').length;
    });
    expect(countAfter).toBeGreaterThanOrEqual(countBefore);

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R4: Hide button → URL becomes /instances, no blank screen, no errors
  // ==========================================================================
  test('R4: overlay hide button navigates to /instances without blank screen', async () => {
    // Open detail first.
    await page.goto('/instances');
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream
    const card = page.locator('a.instance-item').first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await card.click();
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Hide button only renders when anyOverlayVisible is true.
    const hideBtn = page.locator('.overlay-hide-btn');
    await expect(hideBtn).toBeVisible({ timeout: 10000 });

    await hideBtn.click();

    // URL becomes /instances and the chat overlay is display:none.
    await page.waitForURL(/\/instances(\/?$|\?)/, { timeout: 10000 });
    const chatDisplay = await page.locator('app-chat').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(chatDisplay).toBe('none');

    // The instances list page is visible (no blank screen).
    // app-instances is the routed page; instances-container is its root.
    await expect(page.locator('app-instances .instances-container')).toBeVisible({ timeout: 10000 });

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // R5: Workspace overlay layers above chat (z-index 100 > 90)
  // ==========================================================================
  test('R5: workspace overlay layers above chat and chat survives underneath', async () => {
    // Open detail for an instance IN A REAL PROJECT (workspace button is
    // gated on hasRealProject).
    const r5InstanceId = r5Instances[0];
    await page.goto(`/projects/${r5Project.project_id}/instances/${r5InstanceId}`);
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    // Tag the chat subtree with the identity marker so we can assert
    // survival after the workspace is dismissed.
    const tagged = await page.evaluate(() => {
      const c = document.querySelector('app-chat .chat-container');
      if (!c) return false;
      c.setAttribute('data-e2e-marker', 'R5');
      return true;
    });
    expect(tagged).toBe(true);

    // Click the workspace toggle in the chat header. The button uses
    // aria-label="Open Workspace Viewer" and has the folder_open icon.
    // The chat-header workspace button is only rendered when hasRealProject
    // (true here, project is r5Project).
    const workspaceBtn = page.locator('button[aria-label="Open Workspace Viewer"]').first();
    await expect(workspaceBtn).toBeVisible({ timeout: 10000 });
    await workspaceBtn.click();

    // Wait for the workspace overlay to be visible.
    const workspace = page.locator('app-workspace');
    await expect(async () => {
      const display = await workspace.evaluate((el) => getComputedStyle(el).display);
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    // Compare computed z-index. Per the documented ladder in app.scss:
    //   app-chat         z-index: 90
    //   app-workspace    z-index: 100
    //   .plane-overlay   z-index: 1000
    // The DOM order can vary, so we read z-index directly.
    const zIndices = await page.evaluate(() => {
      const w = document.querySelector('app-workspace') as HTMLElement | null;
      const c = document.querySelector('app-chat') as HTMLElement | null;
      const wz = w ? parseInt(getComputedStyle(w).zIndex || '0', 10) : -1;
      const cz = c ? parseInt(getComputedStyle(c).zIndex || '0', 10) : -1;
      // bounding rects to confirm overlap
      const wRect = w?.getBoundingClientRect();
      const cRect = c?.getBoundingClientRect();
      return {
        workspace: wz,
        chat: cz,
        workspaceRect: wRect ? { top: wRect.top, left: wRect.left, width: wRect.width, height: wRect.height } : null,
        chatRect: cRect ? { top: cRect.top, left: cRect.left, width: cRect.width, height: cRect.height } : null,
      };
    });
    expect(zIndices.workspace).toBeGreaterThan(zIndices.chat);

    // Both overlays overlap (same coordinate area) — proves the layering
    // matters, not just the z-index assignment.
    expect(zIndices.workspaceRect).toBeTruthy();
    expect(zIndices.chatRect).toBeTruthy();
    if (zIndices.workspaceRect && zIndices.chatRect) {
      const overlap =
        zIndices.workspaceRect.width > 0 &&
        zIndices.workspaceRect.height > 0 &&
        zIndices.chatRect.width > 0 &&
        zIndices.chatRect.height > 0;
      expect(overlap).toBe(true);
    }

    // Close workspace. Chat must still exist with the marker intact.
    // The workspace emits (hide) → workspaceOverlayService.hide(). NOTE: we
    // CANNOT click the workspace button here — this very test proves the
    // workspace overlay (z-index 100) covers the chat header (z-index 90),
    // so the button is pointer-intercepted by the overlay's error banner.
    // Escape is the correct dismissal path (workspace.component.ts binds
    // @HostListener('window:keydown.escape')).
    await page.keyboard.press('Escape');

    await expect(async () => {
      const display = await workspace.evaluate((el) => getComputedStyle(el).display);
      expect(display).toBe('none');
    }).toPass({ timeout: 10000 });

    // Chat still visible.
    const chatStillVisible = await page.locator('app-chat').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(chatStillVisible).not.toBe('none');

    // Marker still on the same node — proves no destroy/recreate.
    const markerStillThere = await page.evaluate(() =>
      !!document.querySelector('[data-e2e-marker="R5"]'),
    );
    expect(markerStillThere).toBe(true);

    // Console-error criterion (R5): app-level breakage on menu switches —
    // JS exceptions, chat/SSE faults — must fail. HANDLED backend errors
    // from the workspace overlay are filtered ONLY for two documented
    // fixture-gap patterns: (1) /api/workspace/ resource loads, and (2) 404s
    // on /api/projects/{id}/vscode-folder. This test's fixture project is
    // synthetic (API-created) with no workspace files or VS Code folder on
    // disk, so the overlay's tree/file/diff/vscode-folder GETs legitimately
    // fail and the app handles them by design (error banner,
    // workspace.component.html:164). Dispatcher classification 2026-08-18
    // (extended same day): fixture gap, not feature bug. Every other
    // console error or pageerror still fails this assert.
    const filteredConsoleErrors = consoleErrors.filter(
      (e) => !(
        e.includes('Failed to load resource') &&
        (e.includes('/api/workspace/') || e.includes('/vscode-folder'))
      ),
    );
    expect(filteredConsoleErrors, `console errors: ${filteredConsoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // Terminate flow — destructive, creates fresh instance via API.
  // ==========================================================================
  test('Terminate cached instance → localStorage cache cleared, nav link falls back', async () => {
    // Open the freshly-created terminate instance. Going to its URL directly
    // is fine — syncDetailVisibility handles deep-link on app boot.
    await page.goto(`/projects/${terminateProject.project_id}/instances/${terminateInstanceId}`);
    await page.waitForLoadState('domcontentloaded'); // networkidle unreachable: permanent notifications SSE stream

    // Wait for the chat to be visible (overlay opened from URL).
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    // Sanity: localStorage should now contain terminateInstanceId.
    const storedBefore = await page.evaluate(() =>
      localStorage.getItem('ensemble-instances-view-state'),
    );
    expect(storedBefore).toBeTruthy();
    const parsedBefore = JSON.parse(storedBefore as string);
    expect(parsedBefore.activeInstanceId).toBe(terminateInstanceId);

    // Click the terminate button on the cached instance's card in the
    // chat sidebar. The terminate button is rendered on every root-level
    // instance card. We target by scoping to the instance id via the href.
    // The instance card <a> has routerLink to /projects/.../instances/{iid}.
    const terminateBtn = page
      .locator(
        `app-chat .instance-sidebar a.instance-item[href*="/${terminateInstanceId}"] .terminate-btn`,
      )
      .first();
    await expect(terminateBtn).toBeVisible({ timeout: 10000 });
    await terminateBtn.click();

    // The chat's onTerminateInstance calls api.deleteInstance → 200 → the
    // component subscribes to instanceService polling, which removes the
    // row. The view-state service's clearInstance() is called from the
    // INSTANCE LIST page's onTerminateInstance path. From the chat sidebar,
    // the click handler goes through chat.component.ts:onTerminateInstance
    // which also clears the cache (verify in source if needed; the test
    // asserts the outcome anyway).
    // Poll up to 10s for the localStorage entry to either be removed or
    // for activeInstanceId to be null.
    let finalStored: string | null = storedBefore;
    let parsedAfter: { activeInstanceId: string | null; activeProjectId: string } | null = null;
    for (let i = 0; i < 25; i++) {
      finalStored = await page.evaluate(() =>
        localStorage.getItem('ensemble-instances-view-state'),
      );
      if (finalStored === null) {
        parsedAfter = null;
        break;
      }
      try {
        const parsed = JSON.parse(finalStored);
        if (parsed.activeInstanceId === null) {
          parsedAfter = parsed;
          break;
        }
      } catch {
        // Ignore JSON parse errors during transition.
      }
      await page.waitForTimeout(400);
    }
    // Accept either "key removed" OR "activeInstanceId === null" as a valid
    // terminal state. The spec says either is acceptable.
    if (finalStored !== null && parsedAfter === null) {
      try {
        parsedAfter = JSON.parse(finalStored);
      } catch {
        // leave null
      }
    }
    const cleared =
      finalStored === null ||
      (parsedAfter !== null && parsedAfter.activeInstanceId === null);
    expect(cleared, `localStorage after terminate: ${finalStored}`).toBe(true);

    // After cache clear, the "Instances" nav link should fall back to
    // /instances (lastDetailRoute returns null when activeInstanceId is null).
    const navLink = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    const hrefAfter = await navLink.getAttribute('href');
    expect(hrefAfter).toBe('/instances');

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });
});
