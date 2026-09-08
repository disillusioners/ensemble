import * as path from 'path';
import * as fs from 'fs';

/**
 * Source-drift pins for NotificationBellComponent.
 *
 * Plain-TS spec mirroring the W4-style source pins used by the
 * job-queue fix (commit 3ee54883). No Angular TestBed — the bell is a
 * small presentational component whose runtime behavior depends
 * entirely on Material menu positioning and the notification service;
 * the bug class lives in the template/SCSS wiring and the GLOBAL
 * stylesheet (mat-menu SHELL sizing contract), so a source-text pin
 * is the most truthful assertion.
 *
 * Bug class (notification-bell menu overflow — fix 2026-09-08):
 *  1. Material 21 ships `.mat-mdc-menu-panel { max-width: 280px;
 *     overflow: auto }` globally. The embedded `.notification-panel`
 *     is `width: min(440px, calc(100vw - 32px))`, so the 280px shell
 *     capped the panel and surfaced as a 160px horizontal scrollbar
 *     (measured: scrollWidth 440 vs clientWidth 280 at 1440/768 vw;
 *     63px at 375 vw). Fixed by the GLOBAL rule in src/styles.scss
 *     keyed on `.mat-mdc-menu-panel.notification-dropdown`.
 *  2. The bell sits at header-right; Material's default
 *     ``xPosition='after'`` connects the overlay's LEFT edge to the
 *     trigger, so the panel can spill past the right viewport edge
 *     when the auto-flip fallback doesn't engage. Fixed by pinning
 *     ``xPosition="before"`` so the overlay's RIGHT edge aligns to
 *     the bell's right edge (mirrors the job-queue fix pattern).
 *
 * The mat-menu SHELL is body-mounted by the CDK overlay and so lives
 * OUTSIDE the component's view-encapsulation subtree — a
 * component-scoped `::ng-deep` selector cannot reach it. The widening
 * rule MUST live in the global stylesheet; this spec reads that
 * real stylesheet to prove the contract.
 */
describe('NotificationBellComponent source-drift pins', () => {
  let templateHtml: string;
  let componentScss: string;

  beforeAll(() => {
    const specDir = __dirname;
    templateHtml = fs.readFileSync(path.join(specDir, 'notification-bell.component.html'), 'utf-8');
    componentScss = fs.readFileSync(path.join(specDir, 'notification-bell.component.scss'), 'utf-8');
  });

  // W-top-right — bell at header-right → the menu must anchor its
  // right edge to the bell's right edge (opens leftward+downward).
  // Same rationale as the job-queue fix (commit 3ee54883):
  // Material's default ``xPosition='after'`` connects the overlay's
  // LEFT (start) edge to the trigger — with the bell near the right
  // viewport edge, the panel's right edge can spill past the viewport.
  // ``before`` → originX/overlayX 'end' → the panel's RIGHT edge
  // aligns with the bell's right edge and opens leftward+down
  // (yPosition stays the default 'below'). Assert against the
  // #notificationMenu tag specifically so any future sibling menu
  // can't satisfy this.
  describe('anchors the bell menu TOP-RIGHT: xPosition="before" on #notificationMenu', () => {
    it('declares xPosition="before" on the bell mat-menu (NOT "after")', () => {
      const menuTag = templateHtml.match(/<mat-menu[^>]*#notificationMenu[^>]*>/)?.[0] ?? '';
      expect(menuTag).toContain('#notificationMenu');
      expect(menuTag).toContain('xPosition="before"');
      expect(menuTag).not.toContain('xPosition="after"');
    });
  });

  // W-zero-h-scroll — the dropdown SHELL contract in the GLOBAL
  // stylesheet. Angular Material 21 ships
  // `.mat-mdc-menu-panel { max-width: 280px; overflow: auto }` as a
  // global rule (ViewEncapsulation.None), and MatMenu copies the
  // host `class="notification-dropdown"` ONTO the .mat-mdc-menu-panel
  // element (host class → _classList → [class] binding). The CDK
  // overlay mounts at <body>, so component ::ng-deep can never reach
  // it — the widening rule lives in src/styles.scss. This pin reads
  // the REAL stylesheet: dropping or renaming the rule re-creates the
  // user-reported horizontal scroll (shell 280px vs 440px panel) with
  // every behavioural test still green.
  describe('notification-bell dropdown shell sizing (styles.scss global pin)', () => {
    let stylesScss: string;

    beforeAll(() => {
      const stylesPath = path.join(__dirname, '..', '..', '..', 'styles.scss');
      stylesScss = fs.readFileSync(stylesPath, 'utf-8');
    });

    it('widens the mat-menu SHELL for .notification-dropdown (Material default cap is max-width: 280px)', () => {
      const rule = stylesScss.match(/\.mat-mdc-menu-panel\.notification-dropdown\s*\{[^}]*\}/)?.[0] ?? '';
      expect(rule).toContain('max-width: calc(100vw - 16px)');
      expect(rule).toContain('overflow-x: hidden');
    });
  });

  // W3-row-truncation — the panel-side SCSS contract. The user-
  // reported horizontal scroll lived on the mat-menu SHELL (Material
  // caps .mat-mdc-menu-panel at max-width 280px, pinned in the
  // styles.scss pin above), but these panel-side rules are the
  // second half of the contract: rows must TRUNCATE (ellipsis) so
  // the shell can stay scrollbar-free at every viewport down to
  // ~360px. A revert that re-opens an X-overflow channel (the
  // title losing its ellipsis, or a fixed-width panel re-introducing
  // an overflow) would re-create the defect class from the inside;
  // these pins flip loudly if the rules are dropped.
  describe('bell panel SCSS sizing + row-truncation contract (source-text pin)', () => {
    it('notification-panel width is responsive (NOT fixed 440px), so the shell hugs it at every viewport', () => {
      // The job-queue fix moved the panel from a fixed width to
      // min(<px>, calc(100vw - 32px)) to ensure the mat-menu SHELL
      // never under-cuts the panel. The bell already has this form
      // — pin it so a future revert that re-hardcodes 440px is
      // caught (the shell would sprout an X scrollbar below ~440vw).
      const block = componentScss.match(/\.notification-panel\s*\{[^}]*\}/)?.[0] ?? '';
      expect(block).toContain('width: min(440px, calc(100vw - 32px))');
    });

    it('notification-name still truncates with ellipsis (row title contract)', () => {
      // Multiple ``.notification-name`` blocks exist:
      //   (1) inside ``.notification-item.unread`` — font-weight
      //       override only (no truncation rules)
      //   (2) inside ``.notification-content > .notification-title``
      //       — owns the TRUNCATION contract (white-space / overflow
      //       / text-overflow)
      // The contract is satisfied iff AT LEAST ONE ``.notification-
      // name`` block has both ``white-space: nowrap`` AND
      // ``text-overflow: ellipsis``. Pin the truncation block by
      // matching for the truncation signature, so a revert that
      // drops the ellipsis (or replaces it with overflow-wrap:
      // anywhere) flips loudly.
      const blocks = componentScss.match(/\.notification-name\s*\{[^}]*\}/g) ?? [];
      const truncationBlock = blocks.find((b) => b.includes('text-overflow: ellipsis'));
      expect(truncationBlock).toBeDefined();
      expect(truncationBlock).toContain('white-space: nowrap');
      expect(truncationBlock).toContain('overflow: hidden');
      expect(truncationBlock).toContain('text-overflow: ellipsis');
    });

    it('does NOT contain the dead ::ng-deep mat-mdc-menu-content rule (proven never-matching by the job-queue fix)', () => {
      // The old ``.notification-dropdown { ::ng-deep { .mat-mdc-menu-content
      // { padding: 0 !important } } }`` block was unreachable —
      // MatMenu mounts the .mat-mdc-menu-panel at <body> via the CDK
      // overlay, so a component-scoped ::ng-deep can never match it.
      // The new global rule in styles.scss is the canonical seat. Pin
      // its removal so a future revert that re-introduces the dead
      // block fails loudly here.
      expect(componentScss).not.toContain('::ng-deep');
      expect(componentScss).not.toMatch(/\.notification-dropdown\s*\{[^}]*::ng-deep/);
    });
  });
});
