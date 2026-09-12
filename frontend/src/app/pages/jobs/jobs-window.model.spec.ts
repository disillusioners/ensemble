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
  it('maps every Job to a row WindowItem with key=job_id', () => {
    const jobs = [
      createMockJob({ job_id: 'a' }),
      createMockJob({ job_id: 'b' }),
      createMockJob({ job_id: 'c' }),
    ];
    const items = toWindowItems(jobs);
    expect(items.length).toBe(3);
    expect(items.map((i) => i.kind)).toEqual(['row', 'row', 'row']);
    expect(items.map((i) => i.key)).toEqual(['a', 'b', 'c']);
  });

  it('preserves server order (no client-side sort)', () => {
    const jobs = [
      createMockJob({ job_id: 'first', priority: 9 }),
      createMockJob({ job_id: 'mid', priority: 1 }),
      createMockJob({ job_id: 'last', priority: 5 }),
    ];
    const items = toWindowItems(jobs);
    expect(items.map((i) => i.key)).toEqual(['first', 'mid', 'last']);
  });

  it('handles an empty list without erroring', () => {
    expect(toWindowItems([])).toEqual([]);
  });

  it('returns a NEW array on every call (immutable projection)', () => {
    const jobs = [createMockJob({ job_id: 'x' })];
    const a = toWindowItems(jobs);
    const b = toWindowItems(jobs);
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });
});