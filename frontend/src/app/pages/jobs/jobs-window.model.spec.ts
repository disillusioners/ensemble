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
  toBoundedWindowItems,
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

// P3 review — boundary-aware render guard. The legacy
// ``renderGuard(this.windowItems())`` silently slices the FLAT
// items list, which produces the orphan-header case (header
// rendered, zero visible rows) for an expanded group that
// crosses the cap. ``toBoundedWindowItems`` walks groups in
// order and includes a group only if its FULL cost (1 header +
// N rows if expanded) fits in the remaining cap; otherwise the
// whole group is omitted and its expanded rows count toward
// ``hidden``. Collapsed groups contribute a 1-item cost
// (header only — the spec's "collapsed group is header-only by
// design" path). The hidden count is ROWS dropped by the cap
// (NOT collapsed-by-user rows; collapsed = by user choice, not
// by truncation). The template's "N hidden rows" copy stays
// truthful.
describe('jobs-window — toBoundedWindowItems (P3 review: boundary slicing)', () => {
  // Fixture shape: an array of groups with the fields the
  // projection reads. Kept inline (mirrors the toWindowItems
  // spec convention) so the test reads as a self-contained
  // unit.
  const identityTitle = (g: { key: string }): string => g.key;
  const identityMeta = (): string => 'meta';

  function buildGroup(key: string, rowCount: number) {
    return {
      key,
      missionId: key,
      instanceId: key,
      agentId: null,
      jobCount: rowCount,
      lastActivityAt: null,
      jobs: Array.from({ length: rowCount }, (_, i) =>
        createMockJob({ job_id: `${key}-j-${i}`, mission_id: key }),
      ),
      isLive: true,
    };
  }

  it('120-row grouped fixture (mixed expanded/collapsed): hidden count + zero orphan expanded headers + never-hide', () => {
    // The flagship P3-review pin: a 120-row dataset split into 3
    // groups with the cap set so the third expanded group is
    // cut. Asserts:
    //
    // * hidden = rows dropped by cap (NOT collapsed-by-user
    //   rows).
    // * zero orphan expanded headers — the third group is
    //   omitted ENTIRELY (no header rendered with no rows).
    // * never-hide — every input row is either in ``kept``
    //   (rendered) or in ``hidden`` (dropped by cap).
    const groups = [
      buildGroup('g-1', 30), // expanded: 30 rows + 1 header = 31 items
      buildGroup('g-2', 40), // COLLAPSED: 1 header = 1 item, rows user-collapsed
      buildGroup('g-3', 50), // expanded: 50 rows + 1 header = 51 items
    ];
    const expanded = new Set(['g-1', 'g-3']);
    // Cap = 80 items: fits g-1 (31) + g-2 header (1) = 32 items,
    // g-3 (51) doesn't fit → omitted entirely, hidden += 50.
    const out = toBoundedWindowItems(
      groups,
      expanded,
      identityTitle,
      identityMeta,
      80,
    );
    expect(out.kind).toBe('guarded');
    if (out.kind !== 'guarded') return;

    // Hidden count = 50 (rows dropped by cap from g-3). NOT
    // g-3's full cost (51) — headers don't count.
    expect(out.hidden).toBe(50);
    expect(out.cap).toBe(80);

    // ZERO orphan expanded headers. g-3's header is NOT in
    // ``kept`` (the group was omitted whole).
    const headerKeysInKept = out.kept
      .filter((i) => i.kind === 'header')
      .map((i) => (i as { groupKey: string }).groupKey);
    expect(headerKeysInKept).toEqual(['g-1', 'g-2']);

    // Never-hide: every input row is either rendered (in
    // ``kept``) or counted in ``hidden``.
    const renderedRowKeys = out.kept
      .filter((i) => i.kind === 'row')
      .map((i) => (i as { job: { job_id: string } }).job.job_id);
    // g-1's 30 rows are rendered.
    expect(renderedRowKeys.length).toBe(30);
    // g-2's 40 rows are user-collapsed → NOT rendered, NOT in
    // hidden (collapsed-by-user, not cap-dropped).
    // g-3's 50 rows are cap-dropped → counted in hidden.
    // 30 rendered + 50 hidden = 80 (rows the cap actually
    // dealt with). The other 40 (g-2 collapsed) are part of
    // the dataset but neither rendered nor cap-hidden — that's
    // correct (collapsed-by-user is a deliberate choice).
    expect(renderedRowKeys.length + out.hidden).toBe(80);
  });

  it('all groups fit: returns ``ok`` (no truncation)', () => {
    const groups = [
      buildGroup('g-1', 3),
      buildGroup('g-2', 4),
      buildGroup('g-3', 5),
    ];
    const expanded = new Set(['g-1', 'g-2', 'g-3']);
    // Cap = 100: easily fits 1+3 + 1+4 + 1+5 = 15 items.
    const out = toBoundedWindowItems(
      groups,
      expanded,
      identityTitle,
      identityMeta,
      100,
    );
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      // Every header rendered, every row rendered.
      const rendered = out.rows.length;
      const expected = (1 + 3) + (1 + 4) + (1 + 5);
      expect(rendered).toBe(expected);
    }
  });

  it('empty input returns ok branch with empty rows', () => {
    const out = toBoundedWindowItems([], new Set(), identityTitle, identityMeta);
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      expect(out.rows).toEqual([]);
    }
  });

  it('a group that fits EXACTLY at the cap boundary is included (no off-by-one)', () => {
    // 5 collapsed groups, cap = 5 → exactly fits (5 headers,
    // zero rows).
    const groups = [
      buildGroup('g-1', 0),
      buildGroup('g-2', 0),
      buildGroup('g-3', 0),
      buildGroup('g-4', 0),
      buildGroup('g-5', 0),
    ];
    const out = toBoundedWindowItems(
      groups,
      new Set(),
      identityTitle,
      identityMeta,
      5,
    );
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      expect(out.rows.length).toBe(5);
    }
  });

  it('orphan-header pin: an expanded group that does not fit is omitted ENTIRELY (header + rows, not header alone)', () => {
    // Cap = 5: g-1 (1 header + 1 row = 2 items) fits; g-2
    // (1 header + 4 rows = 5 items) does not fit at 2+5=7 >
    // cap → omitted entirely.
    const groups = [
      buildGroup('g-1', 1),
      buildGroup('g-2', 4),
    ];
    const expanded = new Set(['g-1', 'g-2']);
    const out = toBoundedWindowItems(
      groups,
      expanded,
      identityTitle,
      identityMeta,
      5,
    );
    expect(out.kind).toBe('guarded');
    if (out.kind !== 'guarded') return;
    // g-2's header is NOT in kept — no orphan header.
    const headerKeys = out.kept
      .filter((i) => i.kind === 'header')
      .map((i) => (i as { groupKey: string }).groupKey);
    expect(headerKeys).toEqual(['g-1']);
    // g-2's 4 rows are counted in hidden.
    expect(out.hidden).toBe(4);
  });

  it('collapsed-only overflow: a collapsed group that does not fit contributes ZERO to hidden', () => {
    // Cap = 1: g-1 header fits (1 item); g-2 header doesn't
    // (1+1=2 > cap). g-2's rows (collapsed = not rendered
    // anyway) do NOT count toward hidden.
    const groups = [
      buildGroup('g-1', 0),
      buildGroup('g-2', 5),
    ];
    const out = toBoundedWindowItems(
      groups,
      new Set(),
      identityTitle,
      identityMeta,
      1,
    );
    expect(out.kind).toBe('guarded');
    if (out.kind !== 'guarded') return;
    // g-2 was COLLAPSED — its 5 rows weren't going to render
    // anyway. Hidden = 0 (no cap-induced row drops).
    expect(out.hidden).toBe(0);
  });

  it('hidden count excludes collapsed-by-user rows (only cap-induced drops count)', () => {
    // The exact spec wording: "Hidden count = hidden ROWS
    // (headers excluded from the count)" — collapsed groups
    // are NOT counted. Their rows are not rendered, but the
    // user chose to collapse them (deliberate view choice), so
    // they're not "hidden by the cap".
    const groups = [
      buildGroup('g-1', 30), // expanded
      buildGroup('g-2', 40), // collapsed (user choice)
    ];
    const expanded = new Set(['g-1']);
    // Cap = 35: g-1 (1+30 = 31) fits; g-2's collapsed header
    // (1 item, 31+1 = 32 ≤ 35) fits. All groups fit → ok
    // branch. (Different test path: when ALL fit, no
    // truncation. The hidden-counts-collapsed-rows-not
    // assertion lives in the 120-row fixture above.)
    const out = toBoundedWindowItems(
      groups,
      expanded,
      identityTitle,
      identityMeta,
      35,
    );
    expect(out.kind).toBe('ok');
    if (out.kind === 'ok') {
      // g-2's header is rendered (user collapsed the rows but
      // not the header — the chevron is the only expand
      // toggle).
      const headerKeys = out.rows
        .filter((i) => i.kind === 'header')
        .map((i) => (i as { groupKey: string }).groupKey);
      expect(headerKeys).toEqual(['g-1', 'g-2']);
    }
  });
});