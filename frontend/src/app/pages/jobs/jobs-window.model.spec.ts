// jobs-window.model spec — jobs-page-improvement arc, Phase 2.
//
// Pure-TS specs over the window policy (windowIsFull, renderGuard)
// plus the virtual-scroll item projection (toWindowItems). Pins:
// * windowIsFull truth table — exactly-at-100 IS visible (BOTH-WAYS
//   honesty edge pinned separately).
// * renderGuard fixtures AT and PAST MAX_RENDER_ROWS — silent slice
//   is structurally impossible; the tagged union enforces branching.
// * WINDOW_BANNER_COPY pinned (template source text — F-5).
// * toWindowItems preserves job order, sets track-by job_id, is a
//   pure map.

import {
  DEFAULT_WINDOW_LIMIT,
  MAX_RENDER_ROWS,
  WINDOW_BANNER_COPY,
  renderGuard,
  toWindowItems,
  windowIsFull,
} from './jobs-window.model';
import { createMockJob } from '../../testing/job-test-helpers';

describe('jobs-window — windowIsFull banner policy', () => {
  it('rowCount BELOW the limit hides the banner', () => {
    expect(windowIsFull(0)).toBe('hidden');
    expect(windowIsFull(1)).toBe('hidden');
    expect(windowIsFull(50)).toBe('hidden');
    expect(windowIsFull(99)).toBe('hidden');
  });

  it('rowCount AT the limit shows the banner (BOTH-WAYS honesty edge)', () => {
    // Plan task 1: "exactly-at-100 shows banner; 99 does not".
    // The 100-row case is the contested edge — at the cap we cannot
    // know whether the list is complete or truncated, so the banner
    // shows there with the BOTH-WAYS copy.
    expect(windowIsFull(100)).toBe('visible');
  });

  it('rowCount ABOVE the limit shows the banner', () => {
    expect(windowIsFull(101)).toBe('visible');
    expect(windowIsFull(500)).toBe('visible');
    expect(windowIsFull(1001)).toBe('visible');
  });

  it('honors a non-default limit when supplied', () => {
    expect(windowIsFull(49, 50)).toBe('hidden');
    expect(windowIsFull(50, 50)).toBe('visible');
    expect(windowIsFull(100, 50)).toBe('visible');
  });

  it('the constant DEFAULT_WINDOW_LIMIT equals 100 (the wire cap)', () => {
    expect(DEFAULT_WINDOW_LIMIT).toBe(100);
  });
});

describe('jobs-window — renderGuard (all-work hard guard)', () => {
  it('returns ok for rows UNDER the cap — silent passthrough', () => {
    const rows = Array.from({ length: 999 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows);
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      expect(out.rows).toBe(rows);
      expect(out.rows.length).toBe(999);
    }
  });

  it('returns ok AT exactly the cap (1000 rows fit)', () => {
    const rows = Array.from({ length: 1000 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows);
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      expect(out.rows.length).toBe(1000);
    }
  });

  it('PAST the cap (1001): guarded outcome, exactly cap rows kept, hidden count surfaced', () => {
    const rows = Array.from({ length: 1001 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows);
    expect(out.kind).toBe('guarded');
    if (out.kind === 'guarded') {
      expect(out.kept.length).toBe(1000);
      expect(out.hidden).toBe(1);
      expect(out.cap).toBe(1000);
    }
  });

  it('PAST the cap (5000): the hidden count is the excess, not the kept', () => {
    const rows = Array.from({ length: 5000 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows);
    expect(out.kind).toBe('guarded');
    if (out.kind === 'guarded') {
      expect(out.kept.length).toBe(1000);
      expect(out.hidden).toBe(4000);
    }
  });

  it('the truncation notice names the hidden count + the Queues-view affordance', () => {
    const rows = Array.from({ length: 1500 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows);
    expect(out.kind).toBe('guarded');
    if (out.kind === 'guarded') {
      expect(out.notice).toContain('500'); // hidden count
      expect(out.notice).toContain('1500'); // total
      expect(out.notice).toContain('Queues'); // the affordance
    }
  });

  it('MAX_RENDER_ROWS equals 1000 (the guard cap)', () => {
    expect(MAX_RENDER_ROWS).toBe(1000);
  });

  it('honors a non-default cap when supplied', () => {
    const rows = Array.from({ length: 11 }, (_, i) =>
      createMockJob({ job_id: `j-${i}` }),
    );
    const out = renderGuard(rows, 10);
    expect(out.kind).toBe('guarded');
    if (out.kind === 'guarded') {
      expect(out.kept.length).toBe(10);
      expect(out.hidden).toBe(1);
      expect(out.cap).toBe(10);
    }
  });
});

describe('jobs-window — WINDOW_BANNER_COPY (template F-5 pin)', () => {
  it('exposes the BOTH-WAYS visible copy at the exactly-100 edge', () => {
    // Plan task 1: copy is honest at exactly-100 — "may continue or
    // may be complete". A "Showing first 100" / "Showing 100 of N"
    // would imply a contract the wire does not provide.
    expect(WINDOW_BANNER_COPY.visible).toContain('Showing newest 100');
    expect(WINDOW_BANNER_COPY.visible).toContain('may continue');
    expect(WINDOW_BANNER_COPY.visible).toContain('may be complete');
    expect(WINDOW_BANNER_COPY.visible).toContain('refine filters');
  });

  it('declares the reload accessible label (no icon-only button)', () => {
    expect(WINDOW_BANNER_COPY.reloadAccessibleLabel).toBe('Reload jobs');
  });
});

describe('jobs-window — toWindowItems (virtual scroll source)', () => {
  // Phase 3 — the projection now takes groups (NOT raw jobs) so
  // the header arm can ship its full shape (chevron + title + meta
  // line + live flag). The component wires the page-level helpers
  // (``groupHeaderTitle`` / ``groupMetaLine``) — the model keeps
  // its pure, injectable interface so the spec can drive it with
  // simple stubs.

  // Stable stubs for the helper functions — kept inside the
  // describe so the spec reads as a self-contained fixture. The
  // production component injects ``groupHeaderTitle`` and
  // ``groupMetaLine`` from ``jobs-grouping.model``.
  const identityTitle = (g: { key: string }): string => g.key;
  const identityMeta = (): string => 'meta';

  it('maps every Job in EVERY group to a row WindowItem with key=job_id (expanded by default)', () => {
    const jobs = [
      createMockJob({ job_id: 'a', mission_id: 'm-1', instance_id: 'i-1' }),
      createMockJob({ job_id: 'b', mission_id: 'm-1', instance_id: 'i-1' }),
      createMockJob({ job_id: 'c', mission_id: 'm-2', instance_id: 'i-2' }),
    ];
    // groupJobs lives in the grouping model — replicate the simple
    // shape the projection expects (the grouping model spec is the
    // authoritative pin for groupJobs).
    const groups = [
      { key: 'm-1', missionId: 'm-1', instanceId: 'i-1', agentId: null, jobCount: 2, lastActivityAt: null, jobs: [jobs[0], jobs[1]], isLive: true },
      { key: 'm-2', missionId: 'm-2', instanceId: 'i-2', agentId: null, jobCount: 1, lastActivityAt: null, jobs: [jobs[2]], isLive: true },
    ];
    // Empty expanded set ⇒ ALL groups treated as collapsed ⇒ headers
    // only, NO rows.
    const items = toWindowItems(groups, new Set(), identityTitle, identityMeta);
    expect(items.length).toBe(2);
    expect(items.map((i) => i.kind)).toEqual(['header', 'header']);

    // ALL groups expanded ⇒ headers + every row in server order.
    const expanded = toWindowItems(groups, new Set(['m-1', 'm-2']), identityTitle, identityMeta);
    expect(expanded.length).toBe(5);
    expect(expanded.map((i) => i.kind)).toEqual(['header', 'row', 'row', 'header', 'row']);
    expect(expanded.filter((i) => i.kind === 'row').map((i) => (i as { job: { job_id: string } }).job.job_id)).toEqual(['a', 'b', 'c']);
  });

  it('preserves server order WITHIN each expanded group (no client-side sort)', () => {
    const groups = [
      {
        key: 'm-1',
        missionId: 'm-1',
        instanceId: 'i-1',
        agentId: null,
        jobCount: 3,
        lastActivityAt: null,
        jobs: [
          createMockJob({ job_id: 'first', priority: 9 }),
          createMockJob({ job_id: 'mid', priority: 1 }),
          createMockJob({ job_id: 'last', priority: 5 }),
        ],
        isLive: true,
      },
    ];
    const items = toWindowItems(groups, new Set(['m-1']), identityTitle, identityMeta);
    const rowKeys = items
      .filter((i) => i.kind === 'row')
      .map((i) => (i as { job: { job_id: string } }).job.job_id);
    expect(rowKeys).toEqual(['first', 'mid', 'last']);
  });

  it('collapsed groups OMIT rows from the flattened list (keyboard order == DOM order)', () => {
    const groups = [
      {
        key: 'm-1',
        missionId: 'm-1',
        instanceId: 'i-1',
        agentId: null,
        jobCount: 1,
        lastActivityAt: null,
        jobs: [createMockJob({ job_id: 'collapsed-row', mission_id: 'm-1' })],
        isLive: true,
      },
      {
        key: 'm-2',
        missionId: 'm-2',
        instanceId: 'i-2',
        agentId: null,
        jobCount: 1,
        lastActivityAt: null,
        jobs: [createMockJob({ job_id: 'expanded-row', mission_id: 'm-2' })],
        isLive: true,
      },
    ];
    const items = toWindowItems(groups, new Set(['m-2']), identityTitle, identityMeta);
    // Two headers + ONE row (the expanded group's only row). The
    // collapsed group's row is OMITTED — never silently sliced.
    expect(items.length).toBe(3);
    expect(items.map((i) => i.kind)).toEqual(['header', 'header', 'row']);
    const row = items.find((i) => i.kind === 'row') as { job: { job_id: string } };
    expect(row.job.job_id).toBe('expanded-row');
  });

  it('handles an empty input without erroring (returns [])', () => {
    expect(toWindowItems([], new Set(), identityTitle, identityMeta)).toEqual([]);
  });

  it('returns a NEW array on every call (immutable projection)', () => {
    const groups = [
      { key: 'm-1', missionId: 'm-1', instanceId: 'i-1', agentId: null, jobCount: 1, lastActivityAt: null, jobs: [createMockJob({ job_id: 'x' })], isLive: true },
    ];
    const a = toWindowItems(groups, new Set(['m-1']), identityTitle, identityMeta);
    const b = toWindowItems(groups, new Set(['m-1']), identityTitle, identityMeta);
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });

  it('header key === group key (track-by identity)', () => {
    const groups = [
      { key: 'm-1', missionId: 'm-1', instanceId: 'i-1', agentId: null, jobCount: 0, lastActivityAt: null, jobs: [], isLive: false },
    ];
    const items = toWindowItems(groups, new Set(), identityTitle, identityMeta);
    expect(items[0].kind).toBe('header');
    const header = items[0] as Extract<typeof items[0], { kind: 'header' }>;
    expect(header.key).toBe('m-1');
    expect(header.groupKey).toBe('m-1');
  });

  it('header carries the live flag (drives chevron styling + auto-expand seed)', () => {
    const groups = [
      { key: 'live-1', missionId: 'live-1', instanceId: 'live-1', agentId: null, jobCount: 1, lastActivityAt: null, jobs: [createMockJob({ job_id: 'j-1' })], isLive: true },
      { key: 'term-1', missionId: 'term-1', instanceId: 'term-1', agentId: null, jobCount: 1, lastActivityAt: null, jobs: [createMockJob({ job_id: 'j-2' })], isLive: false },
    ];
    const items = toWindowItems(groups, new Set(), identityTitle, identityMeta);
    const liveHeader = items.find((i) => i.kind === 'header' && (i as { key: string }).key === 'live-1') as Extract<typeof items[0], { kind: 'header' }>;
    const termHeader = items.find((i) => i.kind === 'header' && (i as { key: string }).key === 'term-1') as Extract<typeof items[0], { kind: 'header' }>;
    expect(liveHeader.isLive).toBe(true);
    expect(termHeader.isLive).toBe(false);
  });
});