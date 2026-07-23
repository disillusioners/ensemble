import { test, expect, Page } from '@playwright/test';

/**
 * E2E test: Chat Auto-Scroll-to-Bottom on Instance Entry
 *
 * Verifies commit 1a2e657d fix on branch feature/instance-auto-scroll:
 *  - scrollToBottom() now targets `.messages-scroll` container directly
 *  - Uses scrollTop = scrollHeight for absolute bottom positioning
 *  - behavior: 'auto' (instant) instead of 'smooth'
 *  - Re-scrolls at 50ms / 150ms for async markdown rendering
 *
 * Test instance: a 'leader' instance (e1c467e6) in __system_default__ project
 * that has a 20K-char assistant message — guaranteed to overflow the viewport
 * and require scrolling. The instance is visible in the instances list so the
 * app's currentInstance() signal resolves correctly on direct navigation.
 */

const FRONTEND_URL = 'http://localhost:4199';
const SCREENSHOTS_DIR = 'test-results/auto-scroll';

const PROJECT_ID = '71931ae0-0f25-5fbf-853b-2a78cc978d7e';
const INSTANCE_ID = 'e1c467e6-e02a-4ea5-87b8-e0e2b44e3926';

interface ScrollGeometry {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  distanceFromBottom: number;
  isScrollable: boolean;
}

async function readScrollGeometry(page: Page): Promise<ScrollGeometry> {
  return page.locator('.messages-scroll').evaluate((el: HTMLElement) => {
    const scrollTop = el.scrollTop;
    const scrollHeight = el.scrollHeight;
    const clientHeight = el.clientHeight;
    return {
      scrollTop,
      scrollHeight,
      clientHeight,
      distanceFromBottom: Math.round(scrollHeight - scrollTop - clientHeight),
      isScrollable: scrollHeight > clientHeight + 10,
    };
  });
}

/** Navigate to the instance and wait for messages + markdown to render. */
async function enterInstance(page: Page): Promise<void> {
  await page.goto(
    `${FRONTEND_URL}/projects/${PROJECT_ID}/instances/${INSTANCE_ID}`,
    { waitUntil: 'domcontentloaded' }
  );
  await page.waitForSelector('.messages-scroll', { timeout: 15000 });
  await page.waitForSelector('.message-row', { timeout: 15000 });
}

test.describe('Chat Auto-Scroll-to-Bottom on Instance Entry', () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    page.setDefaultTimeout(20000);
  });

  test.afterAll(async () => {
    await page?.close();
  });

  // ==========================================================================
  // Test 1: Instance entry scrolls chat to bottom (scrollTop ≈ scrollHeight)
  // ==========================================================================
  test('Instance entry scrolls chat to bottom (scrollTop ≈ scrollHeight)', async () => {
    await enterInstance(page);

    // Allow async markdown rendering + the 150ms delayed re-scroll to settle.
    await page.waitForTimeout(800);

    const geo = await readScrollGeometry(page);
    console.log('[Test 1] scroll geometry:', JSON.stringify(geo, null, 2));

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/01-on-entry-scroll-position.png`,
      fullPage: false,
    });

    // 1. Content must actually be scrollable (test is non-vacuous)
    expect(
      geo.isScrollable,
      `Expected scrollable content but scrollHeight (${geo.scrollHeight}) ` +
        `<= clientHeight (${geo.clientHeight}) + 10`
    ).toBe(true);

    // 2. View should be at (or very near) the bottom (5px tolerance)
    expect(
      geo.distanceFromBottom,
      `Expected near-bottom scroll but distanceFromBottom=${geo.distanceFromBottom}px ` +
        `(scrollTop=${geo.scrollTop}, scrollHeight=${geo.scrollHeight}, clientHeight=${geo.clientHeight})`
    ).toBeLessThanOrEqual(5);
  });

  // ==========================================================================
  // Test 2: Scroll stays at bottom after markdown fully renders
  // ==========================================================================
  test('Scroll remains at bottom after async markdown rendering settles', async () => {
    await enterInstance(page);

    const geoImmediate = await readScrollGeometry(page);

    // Wait well beyond the 150ms delayed re-scroll
    await page.waitForTimeout(1500);

    const geoSettled = await readScrollGeometry(page);
    console.log('[Test 2] immediate:', JSON.stringify({ d: geoImmediate.distanceFromBottom }));
    console.log('[Test 2] settled:  ', JSON.stringify({ d: geoSettled.distanceFromBottom }));

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/02-after-markdown-settled.png`,
      fullPage: false,
    });

    expect(geoSettled.isScrollable).toBe(true);
    expect(
      geoSettled.distanceFromBottom,
      `Scroll drifted from bottom after markdown render: distanceFromBottom=${geoSettled.distanceFromBottom}px`
    ).toBeLessThanOrEqual(5);
  });

  // ==========================================================================
  // Test 3: Re-entering an instance after scrolling up re-pins to bottom
  // ==========================================================================
  test('Re-entering after manual scroll-up re-pins to bottom', async () => {
    // Enter, verify at bottom
    await enterInstance(page);
    await page.waitForTimeout(800);
    const geoFirst = await readScrollGeometry(page);
    expect(geoFirst.distanceFromBottom).toBeLessThanOrEqual(5);

    // Manually scroll to TOP
    await page
      .locator('.messages-scroll')
      .evaluate((el: HTMLElement) => (el.scrollTop = 0));
    const geoTop = await readScrollGeometry(page);
    expect(geoTop.scrollTop, 'manual scroll to top failed').toBe(0);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/03-manually-scrolled-to-top.png`,
      fullPage: false,
    });

    // Re-enter the instance (fresh navigation)
    await enterInstance(page);
    await page.waitForTimeout(800);

    const geoReEntry = await readScrollGeometry(page);
    console.log('[Test 3] re-entry:', JSON.stringify(geoReEntry, null, 2));

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/04-re-entry-pinned-to-bottom.png`,
      fullPage: false,
    });

    expect(geoReEntry.isScrollable).toBe(true);
    expect(
      geoReEntry.distanceFromBottom,
      `Re-entry did not pin to bottom: distanceFromBottom=${geoReEntry.distanceFromBottom}px`
    ).toBeLessThanOrEqual(5);
  });
});
