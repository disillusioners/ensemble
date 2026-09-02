/**
 * E2E — Mission/receipt chip rendering matrix + panel click/Enter
 * stopPropagation smoke (web automation B).
 *
 * Gate: FE liveness branch `feature/job-queue-fe-liveness` @ 72bc4914
 * Date: 2026-09-02
 *
 * Purpose: exercise the §8.2 chip surfaces against the REAL dev stack
 * (BE :8079 via dev.sh, FE :4199 proxied):
 *
 *   R1 — receipt+mission chip: a job-card row with job_type='message'
 *        AND non-null mission_liveness renders BOTH the receipt chip
 *        (.receipt-chip, label "message") AND the mission-liveness
 *        chip (.mission-chip, label "mission: <value>")
 *   R2 — paused-mission AMBER: a paused mission renders AMBER (the
 *        drawer hard-coded-blue regression class). Test gates on a
 *        live paused mission; if absent in the dev DB, the unit-spec
 *        GAP is recorded and the case is skipped.
 *   R3 — no-chip row: a mission-kind row (job_type='task') renders NO
 *        receipt chip; a row with mission_liveness=null renders NO
 *        mission chip. Test gates on a live mission-kind OR null-
 *        liveness row; if absent in the dev DB, the case is skipped.
 *   P1 — panel click smoke: clicking a mission chip inside a panel
 *        row must NOT bubble to the row's click handler (stopPropagation
 *        restored at 985f86d2). Asserted via menu-still-open +
 *        URL-unchanged (row selection would close the menu + nav).
 *   P2 — panel Enter-key smoke: keyboard Enter on a focused chip
 *        must NOT bubble to the row's keydown.enter handler.
 *        Same assertions as P1.
 *   S1 — (bonus) SSE settle: an in-flight work settling must patch
 *        mission_liveness on the chip WITHOUT page reload (works-view
 *        path). Only exercised if a live non-terminal job exists in
 *        the dev DB at run time; otherwise skipped.
 *
 * Read-only contract (mirrors wave A):
 *   - GET /api/jobs?status=queued,active   (badge-spec active path)
 *   - GET /api/jobs?status=completed,failed,cancelled,dead_letter&limit=10
 *     (badge-spec recent path; the panel reads the same data)
 *
 * No DB writes, no job creation, no message-sending/LLM cost, no
 * state fabrication. Cases the dev DB does not naturally present
 * at run time are SKIPPED with the covering unit specs.
 *
 * Unit-spec citations for the (potentially skipped) cases:
 *   R1 → job-card.component.spec.ts CASE 1/CASE 2/CASE 2b,
 *        job.model.spec.ts CASE 1/CASE 2/CASE 2b
 *   R2 → job-card.component.spec.ts CASE 1 (paused is live cluster),
 *        job.model.spec.ts "should treat paused as live",
 *        getMissionLivenessColor('paused') = '#F59E0B' (amber-500).
 *        NOTE: job-detail-drawer.component.spec.ts has NO it() items
 *        for mission/paused/amber/chip → the recent AMBER-fix has no
 *        unit coverage either → GAP (honest report).
 *   R3 → job-card.component.spec.ts CASE 3/CASE 4,
 *        job.model.spec.ts CASE 3/CASE 4/CASE 4b
 *   P1/P2 → (no dedicated unit spec for chip stopPropagation; the
 *        contract lives in the template binding
 *        job-queue-panel.component.html:91-96 only)
 *   S1 → jobs.component.spec.ts 'jobs[] path: settled mission_liveness
 *        in the payload overwrites the live row',
 *        'present-as-null: explicit null CLEARS, absent key KEEPS
 *        previous value'
 *
 * Screenshots: .agents/tester/RESULTS/2026-09-02-fe-liveness-web/
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/** Evidence dir (repo-root relative; Playwright runs from frontend/). */
const RESULTS_DIR = path.resolve(
  process.cwd(),
  '..',
  '.agents',
  'tester',
  'RESULTS',
  '2026-09-02-fe-liveness-web'
);

interface JobRow {
  job_id: string;
  status: string;
  job_type?: string | null;
  mission_liveness?: string | null;
  instance_id?: string | null;
}

/** Reused from fe_liveness_badge.spec.ts — same endpoints, GET only. */
async function fetchLiveSnapshot(request: APIRequestContext) {
  const activeRes = await request.get('/api/jobs?status=queued,active');
  const recentRes = await request.get(
    '/api/jobs?status=completed,failed,cancelled,dead_letter&limit=10'
  );
  expect(activeRes.ok(), `active jobs endpoint OK: ${activeRes.status()}`).toBeTruthy();
  expect(recentRes.ok(), `recent jobs endpoint OK: ${recentRes.status()}`).toBeTruthy();
  const active = ((await activeRes.json()) as { jobs: JobRow[] }).jobs;
  const recent = ((await recentRes.json()) as { jobs: JobRow[] }).jobs;
  return { active, recent };
}

/**
 * Dismiss the dev-tooling vite-error-overlay (HMR chrome) that can
 * intercept pointer events on /jobs during ng serve. Reused from
 * fe_liveness_badge.spec.ts — same gotcha, same guard.
 */
async function dismissViteErrorOverlayIfPresent(page: Page): Promise<void> {
  const removed = await page.evaluate(() => {
    const ov = document.querySelector('vite-error-overlay');
    if (ov) {
      ov.remove();
      return true;
    }
    return false;
  });
  if (removed) {
    console.warn(
      '[fe_liveness_chips] transient vite-error-overlay dismissed (dev-server HMR chrome)'
    );
  }
}

/** Open the panel via the header badge button (aria-label contract). */
async function openQueuePanel(page: Page): Promise<void> {
  const trigger = page.locator('button[aria-label="View job queue"]');
  await expect(trigger).toBeVisible();
  await trigger.click();
  // MatMenu renders the panel inside .cdk-overlay-container; wait for it.
  const panel = page.locator('.cdk-overlay-container app-job-queue-panel');
  await expect(panel).toBeVisible({ timeout: 10000 });
}

/** Close the panel by pressing Escape (defensive; closing not asserted). */
async function closeQueuePanel(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
}

// ────────────────────────────────────────────────────────────────────────
// R1 — receipt + mission chip co-render on a message row
// ────────────────────────────────────────────────────────────────────────
test('R1 — receipt + mission chip co-render on /jobs (message + non-null mission_liveness)', async ({
  page,
  request,
}) => {
  const { recent } = await fetchLiveSnapshot(request);
  const candidates = recent.filter(
    (j) => j.job_type === 'message' && !!j.mission_liveness
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 'r1_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        totalRecent: recent.length,
        messagePlusLiveness: candidates.length,
        sample: candidates[0] ?? null,
      },
      null,
      2
    )
  );

  test.skip(
    candidates.length === 0,
    `Dev DB naturally presents ${recent.length} recent jobs, ${candidates.length} of which are mirror rows with non-null mission_liveness — ` +
      `R1 (receipt+mission chip co-render) not reachable without state fabrication. ` +
      `Covered by unit specs: ` +
      `it('CASE 1 — mirror + live mission: receipt chip ON, mission chip ON and live'), ` +
      `it('CASE 2 — mirror + settled mission: receipt chip ON, mission chip ON but settled'), ` +
      `it('CASE 2b — mirror row + every settled value stays settled'), ` +
      `it('CASE 1 — mirror row + live mission: chip renders live with canonical value verbatim'), ` +
      `it('CASE 2 — mirror row + settled mission: chip renders settled (distinct from live)').`
  );

  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);
  // The status filter defaults to "all" — wait for cards to render.
  const firstCard = page.locator('app-job-card').first();
  await expect(firstCard).toBeVisible({ timeout: 15000 });

  // Receipt chip — class .receipt-chip, label "message" inside.
  const receiptChip = firstCard.locator('.receipt-chip');
  await expect(receiptChip).toBeVisible();
  await expect(receiptChip).toContainText('message');

  // Mission chip — <app-mission-liveness-chip> renders a .mission-chip span.
  const missionChipSpan = firstCard.locator('app-mission-liveness-chip .mission-chip');
  await expect(missionChipSpan).toBeVisible();

  // The chip's label is the canonical "mission: <value>" verbatim —
  // for the current DB that's "mission: completed" (settled cluster).
  await expect(missionChipSpan).toContainText(
    `mission: ${candidates[0].mission_liveness}`
  );
  // Settled cluster adds .mission-settled; live cluster adds .mission-live.
  const klass = (await missionChipSpan.getAttribute('class')) ?? '';
  expect(klass).toMatch(/mission-(live|settled)/);

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_R1_receipt_mission.png'),
    fullPage: false,
  });
});

// ────────────────────────────────────────────────────────────────────────
// R2 — paused mission renders AMBER (drawer hard-coded-blue regression)
// ────────────────────────────────────────────────────────────────────────
test('R2 — paused mission chip renders AMBER (the drawer hard-coded-blue fix)', async ({
  page,
  request,
}) => {
  const { active, recent } = await fetchLiveSnapshot(request);
  const LIVE = new Set(['pending', 'processing', 'paused']);
  const pausedRows = [...active, ...recent].filter(
    (j) =>
      j.job_type === 'message' &&
      LIVE.has(j.mission_liveness ?? '') &&
      j.mission_liveness === 'paused'
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 'r2_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        pausedMessageRows: pausedRows.length,
        sample: pausedRows[0] ?? null,
      },
      null,
      2
    )
  );

  test.skip(
    pausedRows.length === 0,
    `Dev DB naturally presents 0 paused missions (active=${active.length}, recent=${recent.length}) — ` +
      `R2 (paused → AMBER) not reachable without state fabrication. ` +
      `Covered by unit specs: ` +
      `it('CASE 1 — mirror + live mission: receipt chip ON, mission chip ON and live'), ` +
      `it('should treat paused as live (non-terminal, resumable)'). ` +
      `AMBER colour comes from getMissionLivenessColor('paused') === '#F59E0B' (amber-500). ` +
      `GAP: job-detail-drawer.component.spec.ts has NO it() items for mission/paused/amber/chip — ` +
      `the AMBER fix has no unit coverage either. Documented honestly in the test report.`
  );

  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);
  const firstCard = page.locator('app-job-card').first();
  await expect(firstCard).toBeVisible({ timeout: 15000 });

  const missionChipSpan = firstCard.locator('app-mission-liveness-chip .mission-chip');
  await expect(missionChipSpan).toBeVisible();
  await expect(missionChipSpan).toContainText('mission: paused');

  // Paused is a LIVE cluster → .mission-live class + amber-500 colour.
  const klass = (await missionChipSpan.getAttribute('class')) ?? '';
  expect(klass).toContain('mission-live');
  const inlineColor = await missionChipSpan.evaluate(
    (el) => window.getComputedStyle(el).color
  );
  // #F59E0B → rgb(245, 158, 11)
  expect(inlineColor).toBe('rgb(245, 158, 11)');

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_R2_paused_amber.png'),
    fullPage: false,
  });
});

// ────────────────────────────────────────────────────────────────────────
// R3 — mission-kind rows / null-liveness rows render NO extra chip
// ────────────────────────────────────────────────────────────────────────
test('R3 — mission-kind row renders NO receipt chip; null-liveness row renders NO mission chip', async ({
  page,
  request,
}) => {
  const { active, recent } = await fetchLiveSnapshot(request);
  const all = [...active, ...recent];

  const missionKind = all.filter((j) => j.job_type === 'task');
  const nullLiveness = all.filter(
    (j) => j.job_type === 'message' && j.mission_liveness === null
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 'r3_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        missionKindRows: missionKind.length,
        nullLivenessRows: nullLiveness.length,
        sample: missionKind[0] ?? nullLiveness[0] ?? null,
      },
      null,
      2
    )
  );

  test.skip(
    missionKind.length === 0 && nullLiveness.length === 0,
    `Dev DB naturally presents ${missionKind.length} mission-kind rows and ${nullLiveness.length} null-liveness rows — ` +
      `R3 (no-chip row) not reachable without state fabrication. ` +
      `Covered by unit specs: ` +
      `it('CASE 3 — mission row: NO receipt chip, NO mission chip (its own status IS the liveness)'), ` +
      `it('CASE 4 — degraded None: NO extra rendering, no invented state'), ` +
      `it('legacy rows (no job_type at all) render nothing extra — pre-Fix-C payloads unchanged'), ` +
      `it('CASE 3 — mission row (job_type task): renders NOTHING extra'), ` +
      `it('CASE 4 — degraded None (mirror row, null liveness): renders NOTHING extra, no invented state'), ` +
      `it('CASE 4b — Task-backed record (no job_type, no liveness): renders NOTHING extra').`
  );

  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);

  if (missionKind.length > 0) {
    // Find a card whose job is a mission-kind row. We can't query by
    // job_type directly via DOM, but the .receipt-chip absence inside
    // any non-message card is the contract. Use the first card whose
    // root has no .receipt-chip descendant.
    const noReceiptCard = page
      .locator('app-job-card')
      .filter({ hasNot: page.locator('.receipt-chip') })
      .first();
    await expect(noReceiptCard).toBeVisible({ timeout: 15000 });
    await expect(noReceiptCard.locator('app-mission-liveness-chip .mission-chip'))
      .toHaveCount(0);
  } else if (nullLiveness.length > 0) {
    // A null-liveness mirror row IS a receipt row (job_type=message)
    // but renders NO mission chip. Find the first card with a receipt
    // chip but no mission-chip span.
    const receiptOnlyCard = page
      .locator('app-job-card')
      .filter({ has: page.locator('.receipt-chip') })
      .filter({ hasNot: page.locator('app-mission-liveness-chip .mission-chip') })
      .first();
    await expect(receiptOnlyCard).toBeVisible({ timeout: 15000 });
    await expect(receiptOnlyCard.locator('app-mission-liveness-chip .mission-chip'))
      .toHaveCount(0);
  }

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_R3_no_chip.png'),
    fullPage: false,
  });
});

// ────────────────────────────────────────────────────────────────────────
// P1 — clicking a mission chip inside a panel row does NOT navigate
// ────────────────────────────────────────────────────────────────────────
test('P1 — click on mission chip inside panel row must NOT navigate (stopPropagation)', async ({
  page,
  request,
}) => {
  const { recent } = await fetchLiveSnapshot(request);
  const candidates = recent.filter(
    (j) => j.job_type === 'message' && !!j.mission_liveness
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 'p1_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        messagePlusLiveness: candidates.length,
      },
      null,
      2
    )
  );

  test.skip(
    candidates.length === 0,
    `Dev DB naturally presents 0 mirror rows with mission chip in the Recent section — ` +
      `P1 (panel click stopPropagation smoke) not reachable without state fabrication. ` +
      `stopPropagation is bound in job-queue-panel.component.html:91-96 (click + keydown.enter).`
  );

  // The panel hosts the Recent section and is opened from the header.
  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);
  await openQueuePanel(page);

  // Wait for at least one panel row that carries a mission chip.
  const panelChip = page
    .locator('.cdk-overlay-container app-job-queue-panel app-mission-liveness-chip .mission-chip')
    .first();
  await expect(panelChip).toBeVisible({ timeout: 10000 });

  const urlBefore = page.url();
  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_P1_click_before.png'),
    fullPage: false,
  });

  // Click the chip — must NOT bubble to the row's (click) handler.
  await panelChip.click();

  // Give Angular a tick to handle any (incorrectly) bubbled click.
  await page.waitForTimeout(300);

  const urlAfter = page.url();
  expect(urlAfter, 'URL must not change when clicking a chip in the panel').toBe(
    urlBefore
  );

  // Menu must still be open (a normal row click calls menuTrigger.closeMenu()).
  const panelStillOpen = await page
    .locator('.cdk-overlay-container app-job-queue-panel')
    .isVisible();
  expect(panelStillOpen, 'menu must remain open — stopPropagation should mute row click').toBe(
    true
  );

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_P1_click_after.png'),
    fullPage: false,
  });

  await closeQueuePanel(page);
});

// ────────────────────────────────────────────────────────────────────────
// P2 — keyboard Enter on a focused mission chip must NOT navigate
// ────────────────────────────────────────────────────────────────────────
test('P2 — keyboard Enter on mission chip inside panel row must NOT navigate', async ({
  page,
  request,
}) => {
  const { recent } = await fetchLiveSnapshot(request);
  const candidates = recent.filter(
    (j) => j.job_type === 'message' && !!j.mission_liveness
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 'p2_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        messagePlusLiveness: candidates.length,
      },
      null,
      2
    )
  );

  test.skip(
    candidates.length === 0,
    `Dev DB naturally presents 0 mirror rows with mission chip in the Recent section — ` +
      `P2 (panel Enter stopPropagation smoke) not reachable without state fabrication. ` +
      `stopPropagation is bound in job-queue-panel.component.html:91-96 (click + keydown.enter).`
  );

  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);
  await openQueuePanel(page);

  // The chip itself isn't naturally keyboard-focusable (no tabindex on
  // <app-mission-liveness-chip>), and Playwright's focus() against an
  // Angular component host can be silently ignored — the MatMenu
  // trigger button retained focus in the first run, so keyboard.press
  // ('Enter') was intercepted by MatMenu's overlay keyboard handler
  // (close menu) before the chip's listener could run. To exercise
  // the chip's `(keydown.enter)="$event.stopPropagation()"` binding
  // contract precisely we fire a bubbling keydown directly on the
  // chip host via the standard DOM API; this matches what the
  // component is required to handle.
  const chipHost = page
    .locator(
      '.cdk-overlay-container app-job-queue-panel app-mission-liveness-chip'
    )
    .first();
  await expect(chipHost).toBeVisible({ timeout: 10000 });

  const urlBefore = page.url();
  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_P2_enter_before.png'),
    fullPage: false,
  });

  // Dispatch a real, bubbling, cancelable keydown Enter on the chip
  // host — fires the same handler Angular's (keydown.enter) bound.
  await chipHost.dispatchEvent('keydown', {
    key: 'Enter',
    code: 'Enter',
    bubbles: true,
    cancelable: true,
  });
  await page.waitForTimeout(300);

  const urlAfter = page.url();
  expect(urlAfter, 'URL must not change when pressing Enter on a chip').toBe(
    urlBefore
  );

  const panelStillOpen = await page
    .locator('.cdk-overlay-container app-job-queue-panel')
    .isVisible();
  expect(panelStillOpen, 'menu must remain open — stopPropagation should mute row Enter').toBe(
    true
  );

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_P2_enter_after.png'),
    fullPage: false,
  });

  await closeQueuePanel(page);
});

// ────────────────────────────────────────────────────────────────────────
// S1 — (bonus) an in-flight work settling patches mission_liveness live
// ────────────────────────────────────────────────────────────────────────
test('S1 — (bonus) an in-flight work settling patches mission_liveness without refetch', async ({
  page,
  request,
}) => {
  const { active } = await fetchLiveSnapshot(request);
  // The live-mission derivation runs across active + recent — but to
  // exercise the works-view SSE patch path we need a row that is
  // currently processing/pending in the active window. With 0 active
  // jobs there is nothing to settle and we cannot fabricate one
  // (forbidden: no DB writes, no job creation).
  const inFlight = active.filter(
    (j) => j.status === 'processing' || j.status === 'pending' || j.status === 'paused'
  );

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(RESULTS_DIR, 's1_inventory.json'),
    JSON.stringify(
      {
        fetchedAt: new Date().toISOString(),
        inFlightActiveJobs: inFlight.length,
      },
      null,
      2
    )
  );

  test.skip(
    inFlight.length === 0,
    `Dev DB naturally presents ${active.length} active jobs — ` +
      `S1 (SSE settle without refetch) not observable within the spec budget. ` +
      `Covered by unit specs: ` +
      `it('jobs[] path: settled mission_liveness in the payload overwrites the live row'), ` +
      `it('present-as-null: explicit null CLEARS, absent key KEEPS previous value').`
  );

  // (Path not reachable in current DB state — defensive assertion if
  //  in-flight jobs ever appear naturally in this run.)
  await page.goto('/jobs');
  await dismissViteErrorOverlayIfPresent(page);

  // Locate a card whose status is processing/pending/paused — wait up
  // to 30s for one of those jobs' chip to flip to a settled value
  // (or vanish for completed/null updates).
  const statusChip = page.locator('app-job-card .status-chip').first();
  await expect(statusChip).toBeVisible({ timeout: 10000 });
  const before = await statusChip.innerText();
  // Passive observation — explicit wait, never fixed sleeps.
  await expect(statusChip).not.toHaveText(before, { timeout: 30000 });

  await page.screenshot({
    path: path.join(RESULTS_DIR, 'chips_S1_settled.png'),
    fullPage: false,
  });
});