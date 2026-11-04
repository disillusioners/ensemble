/**
 * @jest-environment node
 *
 * The REAL browser needs a NODE environment: playwright's bundle
 * expects Node's own classes, and under jest's jsdom environment it
 * fails to load ("Class extends value undefined"). This spec needs no
 * DOM — the layout happens inside Chromium.
 */
// The BANNER GEOMETRY spec (T1 phase 8, task 8.6).
//
// ── WHY A REAL BROWSER ──────────────────────────────────────────────────
//
// The audit captured the defect at 900×1000 (screenshot `01b`): the
// banner's text collided with its own Retry button. A CSS collision is
// a LAYOUT fact, and jsdom has NO layout engine — every
// `getBoundingClientRect()` there returns zeros, so a jsdom assertion
// would be VACUOUS.
//
// So this spec drives the REAL Chromium (already a devDependency — the
// lane adds NO package) against a harness built from the PRODUCTION
// sources:
//
//   * the CSS is EXTRACTED from the door-state component's own styles,
//     so a regression in the stylesheet reaches this spec;
//   * the copy is IMPORTED from the copy SSOT, so the harness measures
//     the words an operator actually reads;
//   * the harness reproduces the page's geometry (the host padding, the
//     material tab body padding) so the measured container is the
//     measured page.
//
// The assertion is a GEOMETRY assertion — the two rectangles are
// compared — not a screenshot and not a CSS string match.

import { readFileSync } from 'fs';
import { join } from 'path';

import { METRICS_DOOR_STATE_COPY } from '../../models/metrics.model';

const componentSrc = readFileSync(
  join(__dirname, 'metrics-door-state.component.ts'),
  'utf-8',
);

/** The viewport the audit captured the collision at. */
const VIEWPORT_WIDTH = 900;
const VIEWPORT_HEIGHT = 1000;

/**
 * The panel's REAL CSS — extracted from the component's own styles.
 *
 * The styles live in ONE template literal in the `styles:` array; the
 * extraction is exact, so the harness always measures the shipped
 * stylesheet.
 */
function panelCss(): string {
  // The styles live in ONE template literal in the `styles:` array. The
  // literal ENDS at the first backtick followed by a comma — a backtick
  // inside the CSS (a quoted word in a comment) must not truncate it.
  const match = /styles:\s*\[\s*`([\s\S]*?)`\s*,/.exec(componentSrc);
  expect(match).not.toBeNull();
  return match?.[1] ?? '';
}

/**
 * The harness — the panel's markup inside the page's own geometry.
 *
 * The markup mirrors the component's template: the banner, the body
 * that holds the copy, and the Retry button.
 */
function harness(copy: {
  cause: string;
  meaning: string;
  action: string;
  pointer: string | null;
}): string {
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; }
  /* The page host's own padding (metrics.component.scss). */
  .host { padding: 1rem 1.25rem; }
  /* The material tab body's own padding. */
  .tabbody { padding: 16px 24px; }
  ${panelCss()}
  /* The material button's own minimum width — the harder case. */
  button { font: 14px system-ui, sans-serif; min-width: 64px; height: 36px; padding: 0 16px; }
</style></head>
<body><div class="host"><div class="tabbody">
  <div class="metrics-banner">
    <div class="metrics-banner-body">
      <span class="metrics-banner-text">${copy.cause}</span>
      <span class="metrics-banner-meaning">${copy.meaning}</span>
      <span class="metrics-banner-action">${copy.action}</span>
      ${copy.pointer ? `<span class="metrics-banner-pointer">${copy.pointer}</span>` : ''}
    </div>
    <button type="button">Retry</button>
  </div>
</div></div></body></html>`;
}

/** Do two rectangles intersect? (A shared EDGE is not an intersection.) */
function intersects(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  return !(
    b.x >= a.x + a.width ||
    a.x >= b.x + b.width ||
    b.y >= a.y + a.height ||
    a.y >= b.y + b.height
  );
}

describe('the banner GEOMETRY at 900 px (T1 phase 8, task 8.6)', () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  let chromium: typeof import('playwright').chromium;
  let browser: import('playwright').Browser;

  beforeAll(async () => {
    chromium = (await import('playwright')).chromium;
    browser = await chromium.launch({ args: ['--no-sandbox'] });
  }, 120000);

  afterAll(async () => {
    await browser?.close();
  });

  /**
   * Measure the banner at the audit's viewport.
   *
   * The measurement is REAL: Chromium lays the harness out and the
   * rectangles come back from the engine.
   */
  async function measure(
    code: keyof typeof METRICS_DOOR_STATE_COPY,
  ): Promise<{
    body: { x: number; y: number; width: number; height: number };
    text: { x: number; y: number; width: number; height: number };
    button: { x: number; y: number; width: number; height: number };
    textContent: string;
  }> {
    const page = await browser.newPage({
      viewport: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT },
    });
    await page.setContent(harness(METRICS_DOOR_STATE_COPY[code]));
    const measured: {
      body: { x: number; y: number; width: number; height: number } | null;
      text: { x: number; y: number; width: number; height: number } | null;
      button: { x: number; y: number; width: number; height: number } | null;
      textContent: string;
    } = await page.evaluate(() => {
      const rect = (selector: string) => {
        const node = document.querySelector(selector);
        const box = node?.getBoundingClientRect();
        return box
          ? { x: box.x, y: box.y, width: box.width, height: box.height }
          : null;
      };
      return {
        // The COPY BLOCK — every line of the panel's words.
        body: rect('.metrics-banner-body'),
        // The headline span alone.
        text: rect('.metrics-banner-text'),
        button: rect('.metrics-banner button'),
        textContent: document.querySelector('.metrics-banner-text')?.textContent ?? '',
      };
    });
    await page.close();
    if (!measured.text || !measured.button || !measured.body) {
      throw new Error('the harness rendered no banner');
    }
    return {
      body: measured.body,
      text: measured.text,
      button: measured.button,
      textContent: measured.textContent,
    };
  }

  it('the banner text and the Retry button do NOT overlap at 900 px', async () => {
    const measured = await measure('auth_not_configured');
    expect(intersects(measured.text, measured.button)).toBe(false);
    // And the WHOLE copy block is clear of the button.
    expect(intersects(measured.body, measured.button)).toBe(false);
  }, 120000);

  it('the Retry button REFLOWS BELOW the copy at 900 px (the collision fix)', async () => {
    // THE LOAD-BEARING LEG. At the audit's viewport the button must
    // STACK under the copy — not sit beside it. The pre-fix stylesheet
    // kept them on ONE line (no narrow-width stacking), which is the
    // collision the audit captured.
    const measured = await measure('auth_not_configured');
    // The button's TOP is at or below the copy block's BOTTOM.
    expect(measured.button.y).toBeGreaterThanOrEqual(measured.body.y + measured.body.height);
    // And it does NOT share the copy block's vertical band — it is not
    // BESIDE the copy (a row layout would put it there, centred).
    const inTheSameBand = !(
      measured.button.y >= measured.body.y + measured.body.height ||
      measured.body.y >= measured.button.y + measured.button.height
    );
    expect(inTheSameBand).toBe(false);
  }, 120000);

  it('the LONGEST copy also reflows (the audit said "any longer message")', async () => {
    const measured = await measure('scope_insufficient');
    expect(intersects(measured.text, measured.button)).toBe(false);
  }, 120000);

  it('the SHIPPED stylesheet carries the reflow rules (the unit-level pin)', () => {
    const css = panelCss();
    // The banner WRAPS.
    expect(css).toContain('flex-wrap: wrap');
    // The text can shrink and wrap its own words.
    expect(css).toContain('min-width: 0');
    // The button never shrinks.
    expect(css).toContain('flex: 0 0 auto');
    // And the narrow widths STACK.
    expect(css).toContain('@media (max-width: 900px)');
    expect(css).toContain('flex-direction: column');
  });
});
