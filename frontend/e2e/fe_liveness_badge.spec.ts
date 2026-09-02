/**
 * E2E — Job-queue badge liveness case matrix (web automation A).
 *
 * Gate: FE liveness branch `feature/job-queue-fe-liveness` @ de493472
 *       (worktree FE source identical to 064145ad).
 * Date: 2026-09-02
 *
 * Purpose: exercise the header `JobQueueIndicatorComponent` badge
 * (app.html global header → button[aria-label="View job queue"])
 * against the REAL dev stack (BE :8079 via dev.sh, FE :4199 proxied)
 * for every badge state the dev DB NATURALLY presents at run time:
 *
 *   STATE 1  jobs present            → "X/Y", no idle/missions-live class
 *   STATE 2  0 jobs + N live missions → "missions: N", .missions-live (blue)
 *                                      + .mission-pulse-dot child
 *   STATE 3  0 jobs + 0 missions     → "0/0", .idle (muted), no pulse dot
 *
 * Expected state is DERIVED from the same read-only GET endpoints the
 * component polls (GET /api/jobs?status=queued,active and
 * GET /api/jobs?status=completed,failed,cancelled,dead_letter&limit=10,
 * through the FE proxy) — mirroring liveMissionIds() from
 * frontend/src/app/models/job.model.ts. No DB writes, no job creation,
 * no state fabrication: a state that the dev DB does not naturally
 * present at run time is SKIPPED with the covering unit specs cited.
 *
 * Unit-spec cross-verification (job-queue-indicator.component.spec.ts):
 *   STATE 1 → 'should produce "2/3" with 2 processing and 1 pending',
 *             'should produce "0/3" with 0 processing and 3 pending',
 *             'CASE C — jobs present + live missions: X/Y display
 *              unchanged, tooltip explains both numbers'
 *   STATE 2 → 'CASE A — 0 jobs + live-mission receipts: badge shows
 *              "missions: N" instead of bare 0/0',
 *             'should de-duplicate multiple receipts from the same
 *              mission into one mission',
 *             'should count live missions found in the ACTIVE list too
 *              (defensive mirror scan)'
 *   STATE 3 → 'CASE B — 0 jobs + 0 missions: badge reads bare "0/0" idle',
 *             'should default to "0/0" and idle state',
 *             'should produce "Running: 0 / Pending: 0" when idle',
 *             'should be true even when recent (terminal) jobs exist'
 *
 * Screenshots: .agents/tester/RESULTS/2026-09-02-fe-liveness-web/
 */
import { test, expect, type APIRequestContext } from '@playwright/test';
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

/** Unit-spec citations per badge state (skip evidence for unreachable states). */
const UNIT_COVERAGE: Record<string, string[]> = {
  state1: [
    'should produce "2/3" with 2 processing and 1 pending',
    'should produce "0/3" with 0 processing and 3 pending',
    'CASE C — jobs present + live missions: X/Y display unchanged, tooltip explains both numbers',
  ],
  state2: [
    'CASE A — 0 jobs + live-mission receipts: badge shows "missions: N" instead of bare 0/0',
    'should de-duplicate multiple receipts from the same mission into one mission',
    'should count live missions found in the ACTIVE list too (defensive mirror scan)',
  ],
  state3: [
    'CASE B — 0 jobs + 0 missions: badge reads bare "0/0" idle',
    'should default to "0/0" and idle state',
    'should produce "Running: 0 / Pending: 0" when idle',
    'should be true even when recent (terminal) jobs exist',
  ],
};

interface JobRow {
  job_id: string;
  job_type?: string | null;
  status: string;
  mission_liveness?: string | null;
  instance_id?: string | null;
}

interface BadgeExpectation {
  state: 'state1' | 'state2' | 'state3';
  running: number;
  pending: number;
  total: number;
  missions: number;
  text: string;
  cssClass: 'idle' | 'missions-live' | null;
  tooltip: string;
}

/**
 * Mirror of the component contract (job-queue-indicator.component.ts)
 * and liveMissionIds() (models/job.model.ts):
 *   running  = FE statuses processing/active/paused (isRunningStatus,
 *              incl. 'active' defensive fallback — the backend's own
 *              lifecycle name for running jobs)
 *   pending  = FE statuses pending/queued (isPendingStatus, incl.
 *              'queued' defensive fallback)
 *   missions = distinct instance_ids of message (mirror) rows whose
 *              mission_liveness ∈ {pending, processing, paused} across
 *              the active + recent (terminal) windows
 */
function deriveBadgeExpectation(
  active: JobRow[],
  recent: JobRow[]
): BadgeExpectation {
  const RUNNING = new Set(['processing', 'active', 'paused']);
  const PENDING = new Set(['pending', 'queued']);
  const LIVE_MISSION = new Set(['pending', 'processing', 'paused']);

  const running = active.filter((j) => RUNNING.has(j.status)).length;
  const pending = active.filter((j) => PENDING.has(j.status)).length;
  const total = running + pending;

  const missionIds = new Set<string>();
  for (const j of [...active, ...recent]) {
    if (
      j.job_type === 'message' &&
      j.mission_liveness &&
      LIVE_MISSION.has(j.mission_liveness)
    ) {
      missionIds.add(j.instance_id ?? j.job_id);
    }
  }
  const missions = missionIds.size;

  const base = `Running: ${running} / Pending: ${pending}`;
  const tooltip =
    missions > 0
      ? `${base} · Live missions: ${missions} (${
          missions === 1 ? 'message' : 'messages'
        } handled; parent mission${missions === 1 ? '' : 's'} still working)`
      : base;

  if (total === 0 && missions > 0) {
    return {
      state: 'state2',
      running, pending, total, missions,
      text: `missions: ${missions}`,
      cssClass: 'missions-live',
      tooltip,
    };
  }
  if (total === 0) {
    return {
      state: 'state3',
      running, pending, total, missions,
      text: '0/0',
      cssClass: 'idle',
      tooltip,
    };
  }
  return {
    state: 'state1',
    running, pending, total, missions,
    text: `${running}/${total}`,
    cssClass: null,
    tooltip,
  };
}

async function fetchLiveSnapshot(request: APIRequestContext) {
  // Same two endpoints the component polls, via the FE proxy (GET only).
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

const STATE_LABELS: Record<BadgeExpectation['state'], string> = {
  state1: 'jobs present',
  state2: 'missions live',
  state3: 'idle',
};

async function dismissViteErrorOverlayIfPresent(page: import('@playwright/test').Page): Promise<void> {
  // The Angular dev server (vite) can flash a transient HMR/build error
  // overlay that intercepts pointer events. It is dev-tooling chrome, not
  // app functionality — remove it if present and keep a log line so the
  // run report can mention it. On a persistent overlay the removal is a
  // no-op for correctness: the badge assertions below still run.
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
      '[fe_liveness_badge] transient vite-error-overlay dismissed (dev-server HMR chrome)'
    );
  }
}

for (const target of ['state1', 'state2', 'state3'] as const) {
  test(`badge liveness — ${target} (${STATE_LABELS[target]}): rendered contract matches live-derived expectation`, async ({
    page,
    request,
  }) => {
    const { active, recent } = await fetchLiveSnapshot(request);
    const expected = deriveBadgeExpectation(active, recent);

    // Persist the API snapshot as evidence next to the screenshots.
    fs.mkdirSync(RESULTS_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(RESULTS_DIR, `api_snapshot_${expected.state}.json`),
      JSON.stringify(
        {
          fetchedAt: new Date().toISOString(),
          activeJobs: active,
          recentJobs: recent,
          derived: expected,
        },
        null,
        2
      )
    );

    test.skip(
      expected.state !== target,
      `Dev DB naturally presents STATE ${expected.state.slice(-1)} ` +
        `(${STATE_LABELS[expected.state]}; running=${expected.running}, ` +
        `pending=${expected.pending}, liveMissions=${expected.missions}) — ` +
        `${target} not reachable without state fabrication (forbidden: ` +
        `no DB writes, no job creation). Covered by unit specs: ` +
        UNIT_COVERAGE[target].map((n) => `it('${n}')`).join(', ')
    );

    // The badge lives in the ROOT app shell header → present on every
    // route; use /jobs as the on-theme page.
    await page.goto('/jobs');
    await dismissViteErrorOverlayIfPresent(page);
    const badge = page.locator('button[aria-label="View job queue"]');
    await expect(badge).toBeVisible();

    // Explicit wait: badge text settles to the live-derived value
    // (first fetch fires on component init; 8s poll keeps it fresh).
    await expect(badge.locator('.queue-count')).toHaveText(expected.text);

    const cls = (await badge.getAttribute('class')) ?? '';
    if (expected.cssClass) {
      expect(cls).toContain(expected.cssClass);
    } else {
      expect(cls).not.toContain('idle');
      expect(cls).not.toContain('missions-live');
    }

    // Pulse dot only exists in the missions-live state.
    await expect(badge.locator('.mission-pulse-dot')).toHaveCount(
      expected.state === 'state2' ? 1 : 0
    );

    // matTooltip materialises in an overlay only after hover.
    await badge.hover();
    const tooltip = page.locator('.mat-mdc-tooltip');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toHaveText(expected.tooltip);

    await page.screenshot({
      path: path.join(RESULTS_DIR, `badge_${expected.state}_${STATE_LABELS[target].replace(/\s+/g, '_')}.png`),
      fullPage: false,
    });
  });
}
