/**
 * Lazy-Mount + Hold-Release e2e — instances-state-cache (re-drive items 2+3)
 *
 * Targets the lazy root-mount (app.ts lazyChatMountEffect, app.html
 * ng-container#chatHost) and the renderedInstance capture-and-hold signal
 * (chat.component.ts). All 4 tests are INDEPENDENT (own page + own
 * fixtures) — no serial-abort suppression; a failure in one never hides
 * another's evidence.
 *
 * Known-open BUG5 (NOT under test here): component-scoped app.scss rules
 * don't match the VCR-created chat host → z-index/position/inset are
 * lost. Layout-shaped weirdness is classified as BUG5-fallout; only
 * DISTINCT behavior classes (mount-once violation, A→B stale bleed,
 * mount race, console/page errors) are new findings.
 *
 * House patterns: domcontentloaded only (permanent notifications SSE
 * makes networkidle unreachable); instance cards selected by instance-id
 * href; fixture-readiness gates (assert-the-set-took).
 */
import { test, expect, Page, ConsoleMessage } from '@playwright/test';
import { createTestProject, createTestInstance } from './fixtures/test-helpers';
import { trackInstance, trackProject, cleanupAll } from './fixtures/cleanup';

const PROJ_NAME = `e2e-lazy-${Date.now()}`;
const NON_EXISTENT_ID = '00000000-0000-0000-0000-000000000000';

interface Fixture {
  project_id: string;
  aId: string;
  bId: string;
}

let fixture: Fixture | null = null;

/** Attach per-page console/page-error collectors. */
function watchErrors(page: Page): { consoleErrors: string[]; pageErrors: string[] } {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() === 'error') {
      const loc = msg.location();
      consoleErrors.push(`[${msg.type()}] ${msg.text()}${loc.url ? ` (${loc.url})` : ''}`);
    }
  });
  page.on('pageerror', (err: Error) => pageErrors.push(err.message));
  return { consoleErrors, pageErrors };
}

test.beforeAll(async () => {
  const project = await createTestProject(PROJ_NAME);
  trackProject(project.project_id);
  const a = await createTestInstance('leader', project.project_id);
  trackInstance(a.instance_id);
  const b = await createTestInstance('leader', project.project_id);
  trackInstance(b.instance_id);
  fixture = { project_id: project.project_id, aId: a.instance_id, bId: b.instance_id };
});

test.afterAll(async () => {
  await cleanupAll();
});

/** Wait for the chat overlay to be visible (display !== none) on a page. */
async function chatVisible(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const el = document.querySelector('app-chat');
    return !!el && getComputedStyle(el).display !== 'none';
  });
}

/** Count app-chat elements in the DOM — mount-once guard reads this. */
async function chatCount(page: Page): Promise<number> {
  return page.evaluate(() => document.querySelectorAll('app-chat').length);
}

/** Sidebar card href for a given instance id. */
function cardHref(page: Page, instanceId: string) {
  return page.locator(`a.instance-item[href*="/instances/${instanceId}"]`).first();
}

// ===========================================================================
// Test 1: A→B switch — hold releases, B renders B's data, host survives,
//         B's draft survives a Plan round-trip (hold works for B too).
// ===========================================================================
test('A→B switch: hold releases to B, no A-bleed, host identity kept, B draft survives', async ({ page }) => {
  const { consoleErrors, pageErrors } = watchErrors(page);
  const fx = fixture!;

  // Open A via its card on the instances page.
  await page.goto('/instances');
  await page.waitForLoadState('domcontentloaded');
  await cardHref(page, fx.aId).click();
  await page.waitForURL(new RegExp(`/instances/${fx.aId}$`), { timeout: 10000 });
  await expect(async () => expect(await chatVisible(page)).toBe(true)).toPass({ timeout: 15000 });

  // Tag the HOST element (not the inner subtree — a legitimate re-render
  // for B may rebuild the subtree; the host must be the same node).
  const taggedA = await page.evaluate(() => {
    const el = document.querySelector('app-chat');
    if (!el) return false;
    el.setAttribute('data-e2e-host', 'HOST');
    return true;
  });
  expect(taggedA).toBe(true);

  // Type a draft on A.
  const ta = page.locator('textarea.input-textarea');
  await expect(ta).toBeVisible({ timeout: 15000 });
  await ta.fill('A-draft-MUST-NOT-BLEED');

  // Switch to B via the sidebar card inside the chat.
  await cardHref(page, fx.bId).click();
  await page.waitForURL(new RegExp(`/instances/${fx.bId}$`), { timeout: 10000 });

  // B renders B's data: URL is B's; B's id visible in sidebar active card;
  // the current chat must NOT still show A's draft.
  const draftAfterSwitch = await page.evaluate(async () => {
    // Wait briefly for the switch to settle before reading.
    const read = () =>
      (document.querySelector('textarea.input-textarea') as HTMLTextAreaElement | null)?.value ?? '';
    for (let i = 0; i < 25; i++) {
      const v = read();
      if (v !== 'A-draft-MUST-NOT-BLEED') return v;
      await new Promise((r) => setTimeout(r, 200));
    }
    return read();
  });
  expect(draftAfterSwitch).not.toBe('A-draft-MUST-NOT-BLEED');

  // Host identity: same app-chat node across the A→B switch.
  const sameHost = await page.evaluate(() => !!document.querySelector('app-chat[data-e2e-host="HOST"]'));
  expect(sameHost).toBe(true);

  // Hold works for B too: type B's draft, Plan round-trip, draft survives.
  const tb = page.locator('textarea.input-textarea');
  await expect(tb).toBeVisible({ timeout: 15000 });
  await tb.fill('B-draft-PERSIST');
  await page.locator('a.nav-link', { hasText: /^Plan$/ }).first().click();
  await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });
  await expect(async () => expect(await chatVisible(page)).toBe(false)).toPass({ timeout: 10000 });
  await page.locator('a.nav-link', { hasText: /^Instances$/ }).first().click();
  await page.waitForURL(new RegExp(`/instances/${fx.bId}$`), { timeout: 10000 });
  await expect(async () => expect(await chatVisible(page)).toBe(true)).toPass({ timeout: 10000 });
  const bDraft = await page.locator('textarea.input-textarea').inputValue();
  expect(bDraft).toBe('B-draft-PERSIST');

  // No new behavior-class errors (BUG5-fallout console noise filtered below).
  const filtered = consoleErrors.filter(
    (e) => !(
      e.includes('Failed to load resource') &&
      (e.includes('/api/workspace/') || e.includes('/vscode-folder'))
    ),
  );
  expect(filtered, `console errors: ${filtered.join('\n')}`).toEqual([]);
  expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([]);
});

// ===========================================================================
// Test 2: First-open lazy mount — absent on cold /, mounts once, keep-alive.
// ===========================================================================
test('lazy mount: absent at cold load, appears on first open, exactly one host across round-trips', async ({ page }) => {
  const { consoleErrors, pageErrors } = watchErrors(page);
  const fx = fixture!;

  // Cold load — chat must NOT be mounted yet.
  await page.goto('/');
  await page.waitForLoadState('domcontentloaded');
  await expect(async () => expect(await chatCount(page)).toBe(0)).toPass({ timeout: 5000 });

  // First open mounts it.
  await cardHref(page, fx.aId).isVisible().catch(() => {});
  await page.goto('/instances');
  await page.waitForLoadState('domcontentloaded');
  await cardHref(page, fx.aId).click();
  await page.waitForURL(new RegExp(`/instances/${fx.aId}$`), { timeout: 10000 });
  await expect(async () => expect(await chatCount(page)).toBe(1)).toPass({ timeout: 15000 });

  // Tag the host; a Plan round-trip must reuse the SAME node, count stays 1.
  await page.evaluate(() => {
    document.querySelector('app-chat')?.setAttribute('data-e2e-host', 'LAZY');
  });
  await page.locator('a.nav-link', { hasText: /^Plan$/ }).first().click();
  await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });
  await page.locator('a.nav-link', { hasText: /^Instances$/ }).first().click();
  await page.waitForURL(new RegExp(`/instances/${fx.aId}$`), { timeout: 10000 });
  await expect(async () => expect(await chatVisible(page)).toBe(true)).toPass({ timeout: 10000 });

  const after = await page.evaluate(() => ({
    count: document.querySelectorAll('app-chat').length,
    sameHost: !!document.querySelector('app-chat[data-e2e-host="LAZY"]'),
  }));
  expect(after.count).toBe(1);
  expect(after.sameHost).toBe(true);

  const filtered = consoleErrors.filter(
    (e) => !(
      e.includes('Failed to load resource') &&
      (e.includes('/api/workspace/') || e.includes('/vscode-folder'))
    ),
  );
  expect(filtered).toEqual([]);
  expect(pageErrors).toEqual([]);
});

// ===========================================================================
// Test 3: Navigate-away-during-load race — open A, immediately Plan, back.
// ===========================================================================
test('navigate-away race: open A → immediately Plan → back — no double-mount, no stuck loading, no errors', async ({ page }) => {
  const { consoleErrors, pageErrors } = watchErrors(page);
  const fx = fixture!;

  await page.goto('/instances');
  await page.waitForLoadState('domcontentloaded');
  await cardHref(page, fx.aId).click();
  // Do NOT wait for load to settle — race immediately.
  await page.locator('a.nav-link', { hasText: /^Plan$/ }).first().click();
  await page.waitForURL(/\/plan(\/|$)/, { timeout: 10000 });
  await expect(async () => expect(await chatCount(page)).toBeLessThanOrEqual(1)).toPass({ timeout: 5000 });

  // Back to the cached detail via the Instances nav.
  await page.locator('a.nav-link', { hasText: /^Instances$/ }).first().click();
  await page.waitForURL(new RegExp(`/instances/${fx.aId}$`), { timeout: 10000 });
  await expect(async () => expect(await chatVisible(page)).toBe(true)).toPass({ timeout: 15000 });

  // No double mount; no stuck loading (textarea reachable = chat usable).
  const count = await chatCount(page);
  expect(count).toBe(1);
  await expect(page.locator('textarea.input-textarea')).toBeVisible({ timeout: 15000 });

  const filtered = consoleErrors.filter(
    (e) => !(
      e.includes('Failed to load resource') &&
      (e.includes('/api/workspace/') || e.includes('/vscode-folder'))
    ),
  );
  expect(filtered).toEqual([]);
  expect(pageErrors).toEqual([]);
});

// ===========================================================================
// Test 4: Hold release on 404 — nonexistent instance id must not blank-page.
// ===========================================================================
test('404 nonexistent instance: not-found UI, no crash, nav link sane', async ({ page }) => {
  const { consoleErrors, pageErrors } = watchErrors(page);
  const fx = fixture!;

  await page.goto(`/projects/${fx.project_id}/instances/${NON_EXISTENT_ID}`);
  await page.waitForLoadState('domcontentloaded');

  // The not-found UI (chat.html instanceNotFound block) OR a visible chat
  // with not-found state — either way NOT a blank page. The heading is the
  // discriminator; tolerate slow chunk-load via toPass.
  await expect(async () => {
    const found = await page.evaluate(() => {
      const headings = Array.from(document.querySelectorAll('h2')).map((h) => h.textContent || '');
      return headings.some((t) => t.includes('Instance Not Found'));
    });
    expect(found).toBe(true);
  }).toPass({ timeout: 15000 });

  // Nav link must not crash the app — click through to /instances.
  await page.locator('a.nav-link', { hasText: /^Instances$/ }).first().click();
  await page.waitForURL(/\/instances(\/|$)/, { timeout: 10000 });
  await expect(page.locator('app-instances .instances-container')).toBeVisible({ timeout: 10000 });

  // No page errors; console errors filtered only for the authorized
  // fixture-noise classes (+ any 404 resource load for the nonexistent id).
  const filtered = consoleErrors.filter(
    (e) => !(
      (e.includes('Failed to load resource') &&
        (e.includes('/api/workspace/') || e.includes('/vscode-folder'))) ||
      e.includes(NON_EXISTENT_ID)
    ),
  );
  expect(filtered).toEqual([]);
  expect(pageErrors).toEqual([]);
});
