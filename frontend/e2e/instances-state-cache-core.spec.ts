/**
 * E2E tests for the Instances detail overlay caching contract (R1 + R5 + R6 partial).
 *
 * Validates the primary user journey:
 *   1. Open /instances → click first instance card → chat detail overlay opens.
 *   2. Capture a DOM-identity marker on a stable element inside the chat subtree,
 *      type a draft into the message textarea, scroll the messages list.
 *   3. Navigate to /plan → assert the chat overlay is hidden (display:none),
 *      ZERO console errors, and NO open EventSource handles (SSE closed).
 *   4. Click "Instances" nav → assert URL returns to the cached detail route,
 *      chat overlay visible again, the SAME DOM node still has the marker
 *      (proves no destroy/recreate), draft text preserved, scroll position
 *      preserved, ZERO console errors.
 *   5. localStorage key 'ensemble-instances-view-state' contains the instance id.
 *
 * Selector strategy:
 *   - Prefer roles / aria-labels / data-* markers over brittle class chains.
 *   - Tag a stable element with [data-e2e-marker="A"] via page.evaluate so we
 *     can prove "same DOM node" across hide/show cycles without depending on
 *     internal Angular template structure.
 *   - Use getComputedStyle() for visibility checks — the overlay uses
 *     [style.display]="visible ? 'flex' : 'none'" so :visible is unreliable.
 *
 * Assumptions:
 *   - BE daemon running on localhost:8079 (PID 96878 — do not kill).
 *   - FE dev server on localhost:4199, auto-started by playwright.config.ts
 *     webServer with reuseExistingServer:true (120s timeout).
 *   - "Plan" nav link is present (planeEnabled=true in dev). If absent,
 *     the Plan-related assertions are skipped with a clear reason.
 *   - At least one instance exists in any project so the first card click
 *     has a target. Tests bootstrap their own if necessary.
 */

import { test, expect, Page, ConsoleMessage } from '@playwright/test';
import { createTestProject, createTestInstance, listInstances } from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

// Single browser context for the whole file so localStorage + Angular signals
// survive across tests. test.describe.configure({ mode: 'serial' }) below
// guarantees ordering.
test.describe.configure({ mode: 'serial' });

/**
 * Install an EventSource monkey-patch via addInitScript BEFORE any app code
 * runs. The patch counts constructor invocations and .close() calls into
 * window.__esLog so the test can assert "no open SSE handles" without
 * relying on unreliable PerformanceResourceTiming filters.
 *
 * Why a wrapper function (not Object.assign):
 *   `window.EventSource = function(...) { ... }` is constructable as long as
 *   the returned object has the original prototype. We delegate to the real
 *   constructor so instanceof EventSource still works for Angular's SSE
 *   service. We monkey-patch only the .close() method on the returned
 *   instance to count closes — we never call the real close() ourselves.
 */
const SSE_MONKEY_PATCH = `
(function() {
  if (window.__esLog) return;
  window.__esLog = { opens: 0, closes: 0, openAtPeak: 0, events: [] };
  const Orig = window.EventSource;
  if (!Orig) return;
  function Patched(url, config) {
    const u = String(url);
    window.__esLog.opens++;
    window.__esLog.events.push({ url: u, type: 'open' });
    const es = new Orig(url, config);
    const origClose = es.close.bind(es);
    es.close = function() {
      window.__esLog.closes++;
      window.__esLog.events.push({ url: u, type: 'close' });
      try { origClose(); } catch (e) { /* swallow */ }
    };
    // Track running peak for diagnostics
    const cur = window.__esLog.opens - window.__esLog.closes;
    if (cur > window.__esLog.openAtPeak) window.__esLog.openAtPeak = cur;
    return es;
  }
  Patched.prototype = Orig.prototype;
  // static constants the spec depends on
  Patched.OPEN = Orig.OPEN;
  Patched.CLOSED = Orig.CLOSED;
  Patched.CONNECTING = Orig.CONNECTING;
  window.EventSource = Patched;
})();
`;

interface ESEvent { url: string; type: 'open' | 'close' }
interface ESLog { opens: number; closes: number; openAtPeak: number; events: ESEvent[] }

// The chat stream is uniquely identifiable among the app's 5 EventSource
// sites: sse.service builds /api/instances/{id}/events, while workspace
// (/api/workspace/...), migration (/api/migration/events), notifications
// (/api/notifications/stream) and job-sse (/api/jobs/...) are all distinct.
// NotificationService is root-provided and NEVER disconnects, so an
// app-wide net count has a permanent +1 — assertions must be chat-scoped.
const CHAT_STREAM_PATTERN = /\/api\/instances\/[^/]+\/events$/;

/** Net open count across EventSources whose URL is the chat/instance stream. */
function chatStreamNet(log: ESLog): number {
  let net = 0;
  for (const e of log.events || []) {
    if (CHAT_STREAM_PATTERN.test(e.url)) net += e.type === 'open' ? 1 : -1;
  }
  return net;
}

/** Diagnostic: per-URL open/close/net landscape of ALL EventSource traffic. */
function esLandscape(log: ESLog): Record<string, { opens: number; closes: number; net: number }> {
  const map: Record<string, { opens: number; closes: number; net: number }> = {};
  for (const e of log.events || []) {
    const m = (map[e.url] ??= { opens: 0, closes: 0, net: 0 });
    if (e.type === 'open') { m.opens++; m.net++; } else { m.closes++; m.net--; }
  }
  return map;
}

async function readESLog(page: Page): Promise<ESLog> {
  return page.evaluate(() => {
    const log = (window as unknown as { __esLog?: ESLog }).__esLog;
    return log || { opens: 0, closes: 0, openAtPeak: 0, events: [] };
  });
}

test.describe('Instances Detail Overlay - Core Caching', () => {
  let page: Page;
  let consoleErrors: string[] = [];
  let pageErrors: string[] = [];
  const timestamp = Date.now();
  const targetProject = { name: `e2e-core-${timestamp}`, project_id: '' };
  const createdInstanceIds: string[] = [];

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    // Install SSE monkey-patch BEFORE any app load.
    await page.addInitScript(SSE_MONKEY_PATCH);

    // Capture console errors and uncaught page errors for the whole file.
    page.on('console', (msg: ConsoleMessage) => {
      if (msg.type() === 'error') {
        consoleErrors.push(`[${msg.type()}] ${msg.text()}`);
      }
    });
    page.on('pageerror', (err: Error) => {
      pageErrors.push(err.message);
    });

    // Bootstrap a project + instance so the test is hermetic.
    // (The task allows reusing live BE; we add our own to avoid races.)
    const project = await createTestProject(targetProject.name);
    targetProject.project_id = project.project_id;
    trackProject(targetProject.project_id);

    // Create three instances so the sidebar / first-card click is reliable.
    for (let i = 0; i < 3; i++) {
      const inst = await createTestInstance('leader', targetProject.project_id);
      createdInstanceIds.push(inst.instance_id);
      trackInstance(inst.instance_id);
    }

    // Pre-flight: navigate to root so the SPA boots with our SSE patch active.
    // NOTE: 'networkidle' is unreachable here — the app keeps persistent SSE
    // connections open by design, so network activity never settles. Wait for
    // DOM readiness only; per-test gotos wait on real elements.
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
  });

  test.afterAll(async () => {
    await cleanupAll();
    await page?.close();
  });

  test.beforeEach(async () => {
    // Reset transient error counters per test so a single console warning in
    // test A doesn't fail an unrelated test B.
    consoleErrors = [];
    pageErrors = [];
  });

  // ==========================================================================
  // Test 1: Open detail overlay and capture identity/draft/scroll state.
  // ==========================================================================
  test('Open /instances → click first card → detail overlay opens', async () => {
    await page.goto('/instances');
    // 'networkidle' is unreachable (persistent SSE) — real readiness is
    // asserted by the instance-card visibility wait below.
    await page.waitForLoadState('domcontentloaded');

    // Wait for the instance list to render at least one card.
    const firstCard = page.locator('a.instance-item').first();
    await expect(firstCard).toBeVisible({ timeout: 15000 });

    // Capture the href BEFORE clicking so we can assert the URL transition.
    const targetHref = await firstCard.getAttribute('href');
    expect(targetHref).toBeTruthy();
    expect(targetHref).toMatch(/^\/projects\/[^/?]+\/instances\/[^/?]+$/);

    await firstCard.click();

    // Wait for the URL to match the detail route (Angular routing is async).
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Chat overlay must be visible (display !== 'none'). The root-mounted
    // <app-chat> is always in the DOM; visibility is a [style.display] binding.
    const chatHost = page.locator('app-chat');
    await expect(chatHost).toBeAttached();
    const chatDisplay = await chatHost.evaluate((el) => getComputedStyle(el).display);
    expect(chatDisplay).not.toBe('none');

    // Tag a stable element inside the chat subtree with our identity marker.
    // We use app-chat > .chat-container — the outermost stable element that
    // Angular never destroys during hide/show cycles. If the chat was
    // destroyed/recreated, the marker would be gone.
    const tagged = await page.evaluate(() => {
      const container = document.querySelector('app-chat .chat-container');
      if (!container) return false;
      container.setAttribute('data-e2e-marker', 'A');
      return true;
    });
    expect(tagged).toBe(true);

    // Wait for the message input to render so we know the chat subtree is
    // fully built (not just the skeleton).
    const textarea = page.locator('textarea.input-textarea');
    await expect(textarea).toBeVisible({ timeout: 15000 });

    // Type a draft string.
    await textarea.fill('e2e-draft-PERSIST');

    // Scroll the messages list. We use messages-scroll — the inner scrollable
    // pane — so the parent .messages-container's overflow:hidden doesn't
    // shadow our scrollTop assignment.
    const scrollOk = await page.evaluate(() => {
      const scroller = document.querySelector('app-chat .messages-scroll') as HTMLElement | null;
      if (!scroller) return false;
      scroller.scrollTop = 100;
      return true;
    });
    expect(scrollOk).toBe(true);

    // Snapshot the localStorage payload so subsequent tests can assert it
    // persists (it is set by InstancesViewStateService.openDetail on click).
    const stored = await page.evaluate(() =>
      localStorage.getItem('ensemble-instances-view-state'),
    );
    expect(stored).toBeTruthy();
    const parsed = JSON.parse(stored as string);
    expect(parsed.activeInstanceId).toBeTruthy();
    expect(typeof parsed.activeProjectId).toBe('string');
  });

  // ==========================================================================
  // Test 2: Navigate to Plan → chat hidden, no console errors, SSE closed.
  // ==========================================================================
  test('Click Plan nav → chat hidden, SSE closed, no console errors', async () => {
    // Plan nav is conditional on planeEnabled=true. Skip with a clear
    // reason if the dev env doesn't expose it.
    const planNav = page.locator('a.nav-link', { hasText: /^Plan$/ });
    const planCount = await planNav.count();
    if (planCount === 0) {
      test.skip(true, 'Plan nav link not present (plane disabled in this env)');
      return;
    }

    // SSE should be open now (we just opened detail). Capture the baseline
    // BEFORE clicking so we measure the *transition*, not the absolute count.
    // Chat-scoped: the notifications stream would make the app-wide count
    // non-zero even if the chat stream never opened at all.
    const esBefore = await readESLog(page);
    expect(chatStreamNet(esBefore)).toBeGreaterThan(0);

    await planNav.first().click();

    // URL transitions to /plan.
    await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });

    // Chat overlay must be display:none.
    const chatDisplay = await page.locator('app-chat').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(chatDisplay).toBe('none');

    // Wait briefly for SSE .close() to land (the chat's visibility effect
    // calls sseService.disconnect() on the next tick after display:none).
    // Poll for up to 5s — scoped to the CHAT stream only: the app has other
    // long-lived EventSources (notifications stream is never closed), so an
    // app-wide net count would have a permanent +1 that is NOT a leak.
    let esAfter: ESLog = esBefore;
    for (let i = 0; i < 25; i++) {
      esAfter = await readESLog(page);
      if (chatStreamNet(esAfter) === 0) break;
      await page.waitForTimeout(200);
    }
    // Diagnostic: full per-URL EventSource landscape at /plan (all streams).
    console.log(
      'ES landscape @ /plan:',
      JSON.stringify(esLandscape(esAfter), null, 2),
      `| chat-scoped net = ${chatStreamNet(esAfter)}`,
    );
    // Chat stream (sse.service /api/instances/{id}/events) must be closed.
    expect(chatStreamNet(esAfter), `ES landscape: ${JSON.stringify(esLandscape(esAfter))}`).toBe(0);

    // No console / page errors from the transition.
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });

  // ==========================================================================
  // Test 3 (moved before the draft/scroll test): localStorage payload still
  // contains the instance id. Runs BEFORE the known-bug test so serial-mode
  // abort cannot suppress this evidence — localStorage only needs the cache
  // written by test 1 and surviving the Plan switch of test 2.
  // ==========================================================================
  test('localStorage persists the cached instance id', async () => {
    const stored = await page.evaluate(() =>
      localStorage.getItem('ensemble-instances-view-state'),
    );
    expect(stored).toBeTruthy();
    const parsed = JSON.parse(stored as string);
    expect(typeof parsed.activeInstanceId).toBe('string');
    expect(parsed.activeInstanceId.length).toBeGreaterThan(0);
    expect(typeof parsed.activeProjectId).toBe('string');
    expect(parsed.activeProjectId.length).toBeGreaterThan(0);
  });

  // ==========================================================================
  // Test 4: Click "Instances" nav → cached detail restored, same node,
  //         draft + scroll preserved, no console errors.
  // ==========================================================================
  test('Click Instances nav → same node restored, draft + scroll preserved', async () => {
    // The Instances nav link is bound to lastDetailRoute() so it routes back
    // to /projects/{pid}/instances/{iid} when a cache exists.
    const instancesNav = page.locator('a.nav-link', { hasText: /^Instances$/ }).first();
    await expect(instancesNav).toBeVisible();

    // Sanity: the nav link's href must point at the detail route (R6).
    const instancesHref = await instancesNav.getAttribute('href');
    expect(instancesHref).toMatch(/^\/projects\/[^/?]+\/instances\/[^/?]+$/);

    // SSE baseline before reopen (should still be 0 from previous test).
    const esBefore = await readESLog(page);

    await instancesNav.click();

    // URL must return to the detail route — proves lastDetailRoute wired.
    await page.waitForURL(/\/projects\/[^/?]+\/instances\/[^/?]+$/, { timeout: 10000 });

    // Chat overlay visible again.
    await expect(async () => {
      const display = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(display).not.toBe('none');
    }).toPass({ timeout: 10000 });

    // CRITICAL: query for [data-e2e-marker="A"] — same DOM node? It exists?
    // If the chat subtree was destroyed and recreated, the marker would be
    // gone (setAttribute lives on the element, not persisted anywhere).
    const markerExists = await page.evaluate(() =>
      !!document.querySelector('[data-e2e-marker="A"]'),
    );
    expect(markerExists).toBe(true);

    // Scroll position preserved. We allow a small tolerance because the
    // re-mount tick can nudge scrollTop by a few pixels due to a re-layout.
    // (Ordered BEFORE the draft assert: draft loss is a KNOWN feature bug;
    // this ordering harvests scroll evidence before the failing assert.)
    const scrollTop = await page.evaluate(() => {
      const scroller = document.querySelector('app-chat .messages-scroll') as HTMLElement | null;
      return scroller ? scroller.scrollTop : -1;
    });
    expect(scrollTop).toBeGreaterThanOrEqual(90);

    // Draft text preserved in the textarea. (KNOWN BUG — intentionally kept
    // as the failing terminal assert of this test.)
    const textarea = page.locator('textarea.input-textarea');
    await expect(textarea).toBeVisible({ timeout: 10000 });
    const draftValue = await textarea.inputValue();
    expect(draftValue).toBe('e2e-draft-PERSIST');

    // SSE should be open again (chat reconnected on visibility=true).
    // Chat-scoped: app-wide net is inflated by the never-closed
    // notifications stream, which would pass this vacuously.
    await expect(async () => {
      const es = await readESLog(page);
      expect(chatStreamNet(es)).toBeGreaterThanOrEqual(1);
    }).toPass({ timeout: 10000 });

    // No console / page errors from the reopen.
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([]);
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
  });
});
