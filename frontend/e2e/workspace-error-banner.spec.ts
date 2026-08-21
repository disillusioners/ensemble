/**
 * Workspace Error-Banner Layout + Structured Tree API Errors — E2E (aca8aa2b)
 * ==========================================================================
 *
 * Live post-merge UI verification pack for merge 3b4da6a6 (fix aca8aa2b).
 *
 * ORIGINAL BUG (found by hide_button_symptom_e2e round-3, scenario S5):
 *   On a tree-API error inside the workspace overlay, the blanket
 *   `.viewer-content > * { height: 100% }` rule forced `.error-banner` to
 *   the FULL container height (1280x664 @ y=56) — the entire viewer column
 *   was the banner; the actual editor pane was pushed below the fold.
 *
 * FIX UNDER TEST (aca8aa2b):
 *   - workspace.component.scss: `.viewer-content` is a flex column;
 *     `.error-banner` is `flex: 0 0 auto; height: auto` (content-height
 *     strip); viewer panes get scoped `flex: 1 1 0`.
 *   - workspace.service.ts `extractErrorMessage`: unwraps FastAPI's
 *     structured `detail.error` so the banner shows the backend reason
 *     ("Project has no main_directory configured (400)") instead of
 *     Angular's generic "Http failure response for ...".
 *
 * ARCHITECTURAL GROUND TRUTH (verified live, diag 2026-08-21, and encoded
 * in app.scss:180-210): the workspace overlay (`app-workspace`, z=100,
 * opaque, inset:0) ALWAYS covers the chat overlay (z=90) — which hosts the
 * project tab bar — whenever the workspace is visible. The tab-bar
 * `.workspace-btn` is therefore NOT hit-testable while the workspace is
 * open, regardless of the banner (pre-fix AND post-fix, any mode). The
 * banner fix's user-visible contract is: (1) the banner is a slim top
 * strip, (2) the viewer pane below it is visible/interactive, (3) the
 * banner shows the structured reason, (4) the sanctioned close path (the
 * header `.overlay-hide-btn`, which sits ABOVE the overlay at y≈8) works,
 * after which the tab bar responds immediately.
 *
 * EDITOR MODE: the workspace has two render modes. `builtin` shows the
 * file-tree sidenav + toolbar + code viewer; `vscode` shows the VS Code
 * iframe cache container (no sidenav/toolbar — the banner strip spans the
 * full width). The dev backend's GLOBAL editor preference determines the
 * mode. T3 (success path with a real file tree) REQUIRES builtin mode, so
 * this spec flips the preference to `builtin` at start and RESTORES the
 * original value in afterAll. This is a global dev-settings mutation —
 * disclosed in the pack report. (Side effect already known: PUT
 * editor=builtin stops a running managed code-server; PUT editor=vscode
 * attempts an auto-restart that may be skipped by the manager's
 * user_stopped guard — a pre-existing daemon behavior, not this spec's
 * concern.)
 *
 * SCENARIOS (serial):
 *
 *   T1  BANNER GEOMETRY + STRUCTURED TEXT (synthetic project, tree API 400)
 *       Measured in BOTH modes: (i) the ORIGINAL preference mode as found,
 *       then (ii) builtin mode. Asserts (both modes): height < 120px,
 *       top-band position, text contains "main_directory" and NOT "Http
 *       failure". Asserts (builtin): NO geometric overlap with the tab-bar
 *       ws-btn box. In vscode mode the overlap is recorded (not asserted)
 *       — there the full-width strip sits over the (already covered) tab
 *       bar row; that is the overlay architecture, not the banner bug.
 *   T2  INTERCEPTION PROBE (S5 lineage):
 *       (a) LITERAL probe — click ws-btn while the workspace+banner are
 *           visible, SHORT timeout, EXPECT a caught timeout. Recorded
 *           verbatim + elementFromPoint attribution (hit target must be
 *           inside app-workspace — proving the cover is the overlay
 *           architecture, not the banner).
 *       (b) RECOVERY probe — header `.overlay-hide-btn` click (above the
 *           overlay) hides the workspace; ws-btn then responds (no
 *           timeout) and re-opens the workspace.
 *       (c) BELOW-BANNER interactivity — elementFromPoint at the
 *           workspace center hits the viewer pane (empty-state / code
 *           viewer / vscode container), NOT the banner. Pre-fix this
 *           point was inside the stretched banner.
 *   T3  SUCCESS PATH (scratch fixture AUTHORIZED): scratch project via
 *       API with a real seeded main_directory: file tree renders entries,
 *       NO `.error-banner`, click a file → viewer pane fills with the
 *       fixture content. Screenshot. CLEANUP mandatory in afterAll
 *       (DELETE project via API + rm -rf the /tmp fixture dir).
 *   T4  DISMISS: the banner exposes `aria-label="Dismiss error"` (DOM
 *       verified). Click → banner gone; recovery path (header hide →
 *       ws-btn re-open) still responsive.
 *
 * NAVIGATION CONTRACT: `page.goto` nulls singleton service state — used
 * ONLY for sanctioned per-phase initial loads (same pattern as the r3
 * hide-button spec's S5→S6 transition); in-app project switching in T3
 * uses SPA router-link navigation (Instances → instance card).
 *
 * Console hygiene: workspace-API 400/404s are the stimulus under test and
 * are filtered; plane.ensem.dev CSP noise is environmental and filtered
 * per repo convention.
 */

import { test, expect, Page, ConsoleMessage } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

test.describe.configure({ mode: 'serial' });

const API_BASE = 'http://localhost:8079';
const SYNTHETIC_PROJECT_ID = '4ad9f91b-2b69-4880-a3fa-464cd52b9ba0';
const SCRATCH_DIR = `/tmp/ens-banner-fixture-${process.pid}`;
const EVIDENCE_DIR = path.join(__dirname, '..', 'test-results');
const EVIDENCE_FILE = path.join(EVIDENCE_DIR, 'workspace-banner-evidence.json');

/** Structured evidence log — every bounding box / text recorded verbatim. */
const evidence: Record<string, unknown> = {
  pack: 'workspace_banner_e2e',
  fix: 'aca8aa2b (merged 3b4da6a6)',
  startedAt: new Date().toISOString(),
  baselineBeforeFix: {
    banner: '1280x664 @ y=56 (full workspace height, vscode-mode geometry)',
    wsBtn: '18x18 @ y=66',
    source: 'RESULTS/2026-08-21-hide-button-editor-only-r3.md (S5)',
  },
};
evidence.t1 = { originalMode: {}, builtinMode: {} };
evidence.t2 = {};
evidence.t4 = {};
evidence.environment = {};

/** Workspace-API failures are the stimulus under test — filter them out. */
function isExpectedWorkspaceNoise(text: string): boolean {
  if (text.includes('/api/workspace/')) return true;
  if (text.includes('/vscode-folder')) return true;
  if (text.includes('/vscode/')) return true;
  if (text.includes('plane.ensem.dev')) return true;
  if (text.includes('Content Security Policy')) return true;
  // The workspace SSE stream (/api/workspace/{id}/events) 404s on the
  // synthetic project (no main_directory → no watchable tree). The
  // WorkspaceService's own EventSource retry loop logs these as
  // "[SSE] Connection error" / "[SSE] EventSource connection error"
  // without a URL — they are part of the error-stimulus environment,
  // same family as the r3 spec's filtered /api/workspace/ resource
  // errors (see hide-button-symptom.spec.ts:124-127).
  if (text.includes('[SSE]')) return true;
  return false;
}

function recordConsoleErrorHandlers(page: Page): string[] {
  const consoleErrors: string[] = [];
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() === 'error') {
      const location = msg.location();
      const loc = location && location.url ? ` @ ${location.url}` : '';
      const line = `${msg.text()}${loc}`;
      if (!isExpectedWorkspaceNoise(line)) consoleErrors.push(line);
    }
  });
  page.on('pageerror', (err: Error) => {
    if (!isExpectedWorkspaceNoise(err.message)) consoleErrors.push(err.message);
  });
  return consoleErrors;
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

function boxesOverlap(a: Box, b: Box): boolean {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  );
}

/** Tab-bar workspace button for the ACTIVE project tab. */
function wsBtnForActiveTab(page: Page) {
  return page.locator('app-project-tab-bar .tab.active .workspace-btn').first();
}

async function getEditorPreference(): Promise<string> {
  const r = await fetch(`${API_BASE}/api/settings/editor`);
  const body = (await r.json()) as { editor: string };
  return body.editor;
}

async function putEditorPreference(editor: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/settings/editor`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ editor }),
  });
  if (!r.ok) throw new Error(`PUT editor=${editor} failed: ${r.status}`);
}

/** Open the workspace from a detail URL and return key locators/boxes. */
async function openWorkspaceOnSynthetic(page: Page, instanceId: string) {
  await page.goto(
    `/projects/${SYNTHETIC_PROJECT_ID}/instances/${instanceId}`,
    { waitUntil: 'domcontentloaded' },
  );
  await expect(async () => {
    const d = await page.locator('app-chat').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(d).not.toBe('none');
  }).toPass({ timeout: 15000 });

  const wsBtn = wsBtnForActiveTab(page);
  await expect(wsBtn).toBeVisible({ timeout: 10000 });
  await wsBtn.click({ timeout: 10000 });
  await expect(async () => {
    const d = await page.locator('app-workspace').evaluate(
      (el) => getComputedStyle(el).display,
    );
    expect(d).toBe('flex');
  }).toPass({ timeout: 8000 });
  return wsBtn;
}

test.describe('Workspace Error-Banner — aca8aa2b verification', () => {
  let scratchProjectId = '';
  let scratchInstanceId = '';
  let scratchDirCreated = false;
  let syntheticInstanceId = '';
  let syntheticInstanceCreated = false;
  let originalEditorPref = '';

  test.beforeAll(async () => {
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
    originalEditorPref = await getEditorPreference();
    (evidence.environment as Record<string, unknown>).originalEditorPref =
      originalEditorPref;
  });

  test.afterAll(async () => {
    // ── MANDATORY CLEANUP (runs even if tests failed) ──
    const cleanupReport: Record<string, unknown> = {};
    try {
      if (scratchProjectId) {
        const res = await fetch(`${API_BASE}/api/projects/${scratchProjectId}`, {
          method: 'DELETE',
        });
        cleanupReport.scratchProjectDeleted = res.ok;
        cleanupReport.scratchProjectStatus = res.status;
      } else {
        cleanupReport.scratchProjectDeleted = 'never-created';
      }
    } catch (err) {
      cleanupReport.scratchProjectDeleted = `error: ${(err as Error).message}`;
    }
    try {
      if (scratchDirCreated) {
        fs.rmSync(SCRATCH_DIR, { recursive: true, force: true });
        cleanupReport.tmpDirRemoved = !fs.existsSync(SCRATCH_DIR);
      } else {
        cleanupReport.tmpDirRemoved = 'never-created';
      }
    } catch (err) {
      cleanupReport.tmpDirRemoved = `error: ${(err as Error).message}`;
    }
    if (scratchInstanceId) {
      await fetch(`${API_BASE}/api/instances/${scratchInstanceId}`, {
        method: 'DELETE',
      }).then(
        () => {
          cleanupReport.scratchInstanceDeleted = true;
        },
        (err: Error) => {
          cleanupReport.scratchInstanceDeleted = `error: ${err.message}`;
        },
      );
    }
    if (syntheticInstanceId && syntheticInstanceCreated) {
      await fetch(`${API_BASE}/api/instances/${syntheticInstanceId}`, {
        method: 'DELETE',
      }).then(
        () => {
          cleanupReport.syntheticInstanceDeleted = true;
        },
        (err: Error) => {
          cleanupReport.syntheticInstanceDeleted = `error: ${err.message}`;
        },
      );
    }
    // Restore the GLOBAL editor preference we flipped for T3.
    try {
      if (originalEditorPref) {
        await putEditorPreference(originalEditorPref);
        cleanupReport.editorPrefRestoredTo = await getEditorPreference();
      }
    } catch (err) {
      cleanupReport.editorPrefRestore = `error: ${(err as Error).message}`;
    }
    evidence.cleanup = cleanupReport;
    evidence.finishedAt = new Date().toISOString();
    fs.writeFileSync(EVIDENCE_FILE, JSON.stringify(evidence, null, 2));
    // eslint-disable-next-line no-console
    console.log(`[evidence] written → ${EVIDENCE_FILE}`);
    // eslint-disable-next-line no-console
    console.log(`[cleanup] ${JSON.stringify(cleanupReport)}`);
  });

  test('T1+T2+T4: banner slim strip, structured text, interception probes, dismiss', async ({
    page,
  }) => {
    const consoleErrors = recordConsoleErrorHandlers(page);

    // ── Instance on the synthetic project (reuse first, else create) ──
    let instanceId = '';
    try {
      const list = await fetch(
        `${API_BASE}/api/projects/${SYNTHETIC_PROJECT_ID}/instances`,
      )
        .then((r) => (r.ok ? r.json() : { instances: [] }))
        .catch(() => ({ instances: [] }));
      const instances: Array<{ instance_id: string }> =
        (list as { instances?: Array<{ instance_id: string }> }).instances ?? [];
      instanceId = instances[0]?.instance_id ?? '';
    } catch {
      instanceId = '';
    }
    if (!instanceId) {
      const created = await fetch(`${API_BASE}/api/instances`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: 'leader',
          project_id: SYNTHETIC_PROJECT_ID,
        }),
      }).then((r) => r.json() as Promise<{ instance_id: string }>);
      instanceId = created.instance_id;
      syntheticInstanceId = instanceId;
      syntheticInstanceCreated = true;
    }
    (evidence.t1 as Record<string, unknown>).instanceId = instanceId;

    // ══ T1 (i): ORIGINAL preference mode (as found on the dev BE) ══
    const wsBtn1 = await openWorkspaceOnSynthetic(page, instanceId);
    const banner = page.locator('app-workspace .error-banner').first();
    await expect(banner).toBeVisible({ timeout: 10000 });

    const mode1 = `original:${originalEditorPref}`;
    const rec1 = (evidence.t1 as Record<string, any>).originalMode as Record<string, any>;
    rec1.mode = mode1;

    const bannerBox1 = (await banner.boundingBox()) as Box;
    const wsBtnBox1 = (await wsBtn1.boundingBox()) as Box;
    const overlayBox1 = (await page
      .locator('app-workspace')
      .boundingBox()) as Box;
    rec1.bannerBox = bannerBox1;
    rec1.wsBtnBox = wsBtnBox1;
    rec1.workspaceOverlayBox = overlayBox1;
    rec1.bannerFractionOfWorkspace =
      Math.round((bannerBox1.height / overlayBox1.height) * 1000) / 1000;

    // Height fix assert (mode-independent).
    expect(
      bannerBox1.height,
      `[${mode1}] banner height ${bannerBox1.height}px must be < 120px (pre-fix: full ${overlayBox1.height}px height)`,
    ).toBeLessThan(120);
    // Top-band assert (mode-independent).
    const topFraction1 = (bannerBox1.y - overlayBox1.y) / overlayBox1.height;
    rec1.bannerTopFractionOfOverlay = Math.round(topFraction1 * 1000) / 1000;
    expect(
      topFraction1,
      `[${mode1}] banner must sit in the top band (fraction ${topFraction1} >= 0.5 would mean mid/bottom)`,
    ).toBeLessThan(0.5);

    const bannerText1 = (await banner.textContent()) ?? '';
    rec1.bannerText = bannerText1.trim();
    expect(
      bannerText1,
      `[${mode1}] banner text must mention main_directory (got: ${bannerText1})`,
    ).toContain('main_directory');
    expect(
      bannerText1,
      `[${mode1}] banner must not show Angular generic Http failure text`,
    ).not.toContain('Http failure');

    // Overlap: recorded, asserted only in builtin mode below.
    rec1.overlapsWsBtnBox = boxesOverlap(bannerBox1, wsBtnBox1);

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, `banner-t1-${originalEditorPref}-mode.png`),
    });

    // ══ T1 (ii): builtin mode (file-tree layout — the r3 fix target) ══
    await putEditorPreference('builtin');
    // Fresh load so the WorkspaceService singleton re-reads the preference
    // (sanctioned per-phase initial load; see header comment).
    const wsBtn2 = await openWorkspaceOnSynthetic(page, instanceId);
    await expect(banner).toBeVisible({ timeout: 10000 });

    const rec2 = (evidence.t1 as Record<string, any>).builtinMode as Record<string, any>;
    rec2.mode = 'builtin';

    const bannerBox2 = (await banner.boundingBox()) as Box;
    const wsBtnBox2 = (await wsBtn2.boundingBox()) as Box;
    const overlayBox2 = (await page
      .locator('app-workspace')
      .boundingBox()) as Box;
    rec2.bannerBox = bannerBox2;
    rec2.wsBtnBox = wsBtnBox2;
    rec2.workspaceOverlayBox = overlayBox2;
    rec2.bannerFractionOfWorkspace =
      Math.round((bannerBox2.height / overlayBox2.height) * 1000) / 1000;
    rec2.sidenavPresent = (await page
      .locator('app-workspace mat-sidenav')
      .count()) > 0;
    rec2.toolbarPresent = (await page
      .locator('app-workspace .content-toolbar')
      .count()) > 0;

    expect(
      bannerBox2.height,
      `[builtin] banner height ${bannerBox2.height}px must be < 120px`,
    ).toBeLessThan(120);
    const topFraction2 = (bannerBox2.y - overlayBox2.y) / overlayBox2.height;
    rec2.bannerTopFractionOfOverlay = Math.round(topFraction2 * 1000) / 1000;
    expect(topFraction2, '[builtin] banner must sit in the top band').toBeLessThan(0.5);

    // The acceptance-grade no-overlap assertion: in builtin mode the
    // banner sits BELOW the toolbar (inside the viewer column, right of
    // the sidenav) — it must not geometrically overlap the tab-bar row.
    const overlap2 = boxesOverlap(bannerBox2, wsBtnBox2);
    rec2.overlapsWsBtnBox = overlap2;
    expect(
      overlap2,
      `[builtin] banner box ${JSON.stringify(bannerBox2)} must NOT overlap ws-btn box ${JSON.stringify(wsBtnBox2)}`,
    ).toBe(false);

    const bannerText2 = (await banner.textContent()) ?? '';
    rec2.bannerText = bannerText2.trim();
    expect(bannerText2).toContain('main_directory');
    expect(bannerText2).not.toContain('Http failure');

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, 'banner-t1-builtin-mode.png'),
    });

    // ══ T2 (a): LITERAL S5 probe — ws-btn click while workspace+banner visible ══
    // Architectural ground truth: the opaque z-100 overlay always covers
    // the z-90 chat (which hosts the tab bar), so this click is EXPECTED
    // to time out both pre- and post-fix. We catch it, record it verbatim,
    // and assert the hit-test target is inside app-workspace — attributing
    // the block to the overlay architecture, NOT the banner.
    const t2 = evidence.t2 as Record<string, any>;
    const center2 = {
      x: wsBtnBox2.x + wsBtnBox2.width / 2,
      y: wsBtnBox2.y + wsBtnBox2.height / 2,
    };
    const hitInfo = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y);
      const describe = (node: Element | null): string => {
        if (!node) return 'null';
        const cls = node.getAttribute('class') ?? '';
        return `${node.tagName.toLowerCase()}${cls ? '.' + cls.split(/\s+/).slice(0, 3).join('.') : ''}`;
      };
      return {
        hit: describe(el),
        insideAppWorkspace: !!el?.closest('app-workspace'),
        insideErrorBanner: !!el?.closest('.error-banner'),
        insideWorkspaceBtn: !!el?.closest('.workspace-btn'),
      };
    }, center2);
    t2.literalProbe = { clickCenter: center2, hitTest: hitInfo };
    // Attribution: the cover is the workspace overlay itself (pre-existing
    // architecture), not the banner (in builtin mode the banner sits below
    // the toolbar; the hit target is the sidenav/tree-header area).
    expect(
      hitInfo.insideAppWorkspace,
      `hit at ws-btn center must be owned by the workspace overlay (got ${hitInfo.hit}) — proving the block is the overlay architecture`,
    ).toBe(true);
    expect(
      hitInfo.insideErrorBanner,
      'in builtin mode the banner must NOT be the element covering the ws-btn row',
    ).toBe(false);

    const t0 = Date.now();
    let literalTimeoutCaught = false;
    try {
      await wsBtn2.click({ timeout: 4000 });
      t2.literalProbe.clickResult = 'CLICKED (no timeout)';
    } catch (err) {
      literalTimeoutCaught = true;
      t2.literalProbe.clickResult = `TIMEOUT after ${Date.now() - t0}ms: ${(err as Error).message.split('\n')[0]}`;
    }
    t2.literalProbe.expectedTimeoutCaught = literalTimeoutCaught;
    // Deterministic cover (not flake): the click MUST be blocked by the
    // overlay. If this ever PASSES, the overlay architecture changed and
    // this spec's ground-truth comments need revisiting.
    expect(literalTimeoutCaught).toBe(true);

    // ══ T2 (b): RECOVERY probe — sanctioned close path then tab bar ══
    const hideBtn = page.locator('.overlay-hide-btn').first();
    await expect(hideBtn).toBeVisible({ timeout: 5000 });
    const t1 = Date.now();
    await hideBtn.click({ timeout: 8000 });
    await expect(async () => {
      const d = await page.locator('app-workspace').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(d).toBe('none');
    }).toPass({ timeout: 5000 });
    t2.recoveryProbe = { headerHideLatencyMs: Date.now() - t1 };

    // With the workspace hidden the tab-bar button MUST respond.
    const t2start = Date.now();
    await wsBtn2.click({ timeout: 8000 }); // no timeout => responsive
    await expect(async () => {
      const d = await page.locator('app-workspace').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(d).toBe('flex');
    }).toPass({ timeout: 5000 });
    t2.recoveryProbe.wsBtnReopenLatencyMs = Date.now() - t2start;
    t2.recoveryProbe.wsBtnResponsiveAfterHide = true;

    // ══ T2 (c): BELOW-BANNER interactivity ══
    // Pre-fix, the workspace center was INSIDE the stretched banner.
    // Post-fix it must belong to the viewer pane.
    const overlayBoxC = (await page
      .locator('app-workspace')
      .boundingBox()) as Box;
    const mid = {
      x: overlayBoxC.x + overlayBoxC.width / 2,
      y: overlayBoxC.y + overlayBoxC.height * 0.75,
    };
    const belowHit = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y);
      return {
        hit: el
          ? `${el.tagName.toLowerCase()}.${(el.getAttribute('class') ?? '').split(/\s+/).slice(0, 3).join('.')}`
          : 'null',
        insideErrorBanner: !!el?.closest('.error-banner'),
        insideViewerContent: !!el?.closest('.viewer-content'),
      };
    }, mid);
    t2.belowBanner = { point: mid, hitTest: belowHit };
    expect(
      belowHit.insideErrorBanner,
      `point at 75% workspace height must NOT be banner (pre-fix it was); got ${belowHit.hit}`,
    ).toBe(false);
    expect(
      belowHit.insideViewerContent,
      `point at 75% workspace height must be inside the viewer pane; got ${belowHit.hit}`,
    ).toBe(true);

    // ══ T4: dismiss control ══
    const t4 = evidence.t4 as Record<string, any>;
    const dismissBtn = banner.locator('button[aria-label="Dismiss error"]');
    const dismissCount = await dismissBtn.count();
    t4.dismissControlFound = dismissCount > 0;
    expect(
      dismissCount,
      'banner must expose a dismiss control (aria-label="Dismiss error")',
    ).toBeGreaterThan(0);

    // Banner may have persisted through the toggles; if it was cleared by
    // a re-render, re-open the workspace path already has it visible.
    await expect(banner).toBeVisible({ timeout: 5000 });
    await dismissBtn.first().click({ timeout: 8000 });
    await expect(banner).not.toBeVisible({ timeout: 5000 });
    t4.bannerGoneAfterDismiss = true;

    // Tab bar still reachable via the sanctioned path: hide via header,
    // then ws-btn responds (re-open).
    await hideBtn.click({ timeout: 8000 });
    await expect(async () => {
      const d = await page.locator('app-workspace').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(d).toBe('none');
    }).toPass({ timeout: 5000 });
    const t4start = Date.now();
    await wsBtn2.click({ timeout: 8000 });
    t4.tabBarResponsiveAfterDismiss = { wsBtnLatencyMs: Date.now() - t4start };

    // Console hygiene (post-filter): no UNEXPECTED console errors.
    (evidence.t1 as Record<string, unknown>).unexpectedConsoleErrors =
      consoleErrors;
    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(' | ')}`,
    ).toEqual([]);
  });

  test('T3: success path — scratch project with real main_directory', async ({
    page,
  }) => {
    const consoleErrors = recordConsoleErrorHandlers(page);

    // ── Scratch fixture (AUTHORIZED): project + seeded dir ──
    const ts = Date.now();
    const scratchName = `tester-scratch-banner-${ts}`;
    fs.mkdirSync(path.join(SCRATCH_DIR, 'src'), { recursive: true });
    fs.mkdirSync(path.join(SCRATCH_DIR, 'docs'), { recursive: true });
    fs.writeFileSync(
      path.join(SCRATCH_DIR, 'src', 'main.ts'),
      'export const hello = "banner-t3";\n',
    );
    fs.writeFileSync(
      path.join(SCRATCH_DIR, 'src', 'util.ts'),
      'export const id = () => 42;\n',
    );
    fs.writeFileSync(
      path.join(SCRATCH_DIR, 'docs', 'readme.md'),
      '# scratch fixture\nseeded by workspace_banner_e2e\n',
    );
    scratchDirCreated = true;

    const createRes = await fetch(`${API_BASE}/api/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: scratchName, main_directory: SCRATCH_DIR }),
    });
    expect(createRes.ok, `scratch project create failed: ${createRes.status}`).toBe(true);
    const project = (await createRes.json()) as { project_id: string };
    scratchProjectId = project.project_id;

    const instRes = await fetch(`${API_BASE}/api/instances`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: 'leader',
        project_id: scratchProjectId,
      }),
    });
    expect(instRes.ok, `scratch instance create failed: ${instRes.status}`).toBe(true);
    const inst = (await instRes.json()) as { instance_id: string };
    scratchInstanceId = inst.instance_id;

    const rec = {} as Record<string, any>;
    evidence.t3 = rec;
    rec.scratchName = scratchName;
    rec.scratchProjectId = scratchProjectId;
    rec.scratchDir = SCRATCH_DIR;
    rec.instanceId = scratchInstanceId;

    // Serial mode gives each test its OWN fresh page. Direct initial load
    // to the scratch project's detail route (sanctioned per-test initial
    // load, same pattern as T1 and the r3 spec's S5). The project tab bar
    // opens on this route with the scratch project's tab + workspace
    // button. (Going via the shared /instances list lands on the
    // /projects/all/... context whose tab is the All-tab — no
    // workspace-btn.)
    await page.goto(
      `/projects/${scratchProjectId}/instances/${scratchInstanceId}`,
      { waitUntil: 'domcontentloaded' },
    );
    await expect(async () => {
      const d = await page.locator('app-chat').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(d).not.toBe('none');
    }).toPass({ timeout: 15000 });

    const wsBtn = wsBtnForActiveTab(page);
    await expect(wsBtn).toBeVisible({ timeout: 10000 });
    await wsBtn.click({ timeout: 8000 });
    await expect(async () => {
      const d = await page.locator('app-workspace').evaluate(
        (el) => getComputedStyle(el).display,
      );
      expect(d).toBe('flex');
    }).toPass({ timeout: 8000 });

    // File tree renders entries (src/ and docs/ dirs at minimum).
    const treeNodes = page.locator(
      'app-workspace app-file-tree .filename, app-workspace app-file-tree .dirname',
    );
    await expect(treeNodes.first()).toBeVisible({ timeout: 15000 });
    const treeCount = await treeNodes.count();
    rec.treeEntryCount = treeCount;
    expect(
      treeCount,
      'file tree must render entries (src/, docs/ seeded)',
    ).toBeGreaterThanOrEqual(2);

    // No error banner on the success path.
    const bannerCount = await page
      .locator('app-workspace .error-banner')
      .count();
    rec.errorBannerCount = bannerCount;
    expect(
      bannerCount,
      'no .error-banner may be present on the success path',
    ).toBe(0);

    // Expand src/ and click a file → viewer pane fills.
    const srcDir = page
      .locator('app-workspace app-file-tree .dirname', { hasText: 'src' })
      .first();
    if ((await srcDir.count()) > 0) {
      await srcDir.click({ timeout: 8000 }).catch(() => undefined);
    }
    const mainFile = page
      .locator('app-workspace app-file-tree .filename', { hasText: 'main.ts' })
      .first();
    await expect(mainFile).toBeVisible({ timeout: 10000 });
    await mainFile.click({ timeout: 8000 });

    const codeViewer = page.locator('app-workspace app-code-viewer').first();
    await expect(codeViewer).toBeVisible({ timeout: 15000 });
    const viewerBox = (await codeViewer.boundingBox()) as Box;
    const overlayBox = (await page
      .locator('app-workspace')
      .boundingBox()) as Box;
    rec.codeViewerBox = viewerBox;
    rec.workspaceOverlayBox = overlayBox;
    rec.viewerFillsOverlay = {
      widthRatio: Math.round((viewerBox.width / overlayBox.width) * 1000) / 1000,
      heightRatio:
        Math.round((viewerBox.height / overlayBox.height) * 1000) / 1000,
    };
    expect(viewerBox.width, 'viewer width must be non-zero').toBeGreaterThan(0);
    expect(viewerBox.height, 'viewer height must be substantial').toBeGreaterThan(100);
    expect(viewerBox.x).toBeGreaterThanOrEqual(overlayBox.x);
    expect(viewerBox.x + viewerBox.width).toBeLessThanOrEqual(
      overlayBox.x + overlayBox.width + 1,
    );
    expect(
      viewerBox.height / overlayBox.height,
      'viewer must fill a large fraction of the workspace',
    ).toBeGreaterThan(0.3);

    // Viewer shows the fixture file content.
    const viewerText = await codeViewer.textContent();
    rec.viewerContainsFixtureContent = viewerText.includes('banner-t3');
    expect(
      viewerText,
      'code viewer must render the fixture file content',
    ).toContain('banner-t3');

    await page.screenshot({
      path: path.join(EVIDENCE_DIR, 'banner-t3-success.png'),
    });

    // Console hygiene: on the SUCCESS path workspace-API errors are still
    // filtered (SSE stream 404s are environmental on fresh projects) but
    // nothing else may leak.
    rec.unexpectedConsoleErrors = consoleErrors;
    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(' | ')}`,
    ).toEqual([]);
  });
});
