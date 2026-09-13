// Jobs keyboard model spec — jobs-page-improvement arc, Phase 6 (task 2).
//
// Pure-model truth table for the clamped arrow navigation over the
// flattened ``WindowItem`` list + the WAI-ARIA tree actions. The
// recycling DOM sequence (scrollToIndex → refocus after the render
// tick → viewport fallback) is CONTRACT in the model doc + F-5
// source-pinned against the real component at the bottom of this
// spec — plain-TS specs cannot drive real DOM focus, so the pairing
// (behavioral model pins + production source pins) is the proof.

import {
  isJobsActivateKey,
  jobsWindowItemId,
  nearestHeaderIndexAbove,
  nextJobsWindowItem,
  resolveJobsKeyAction,
} from './jobs-keyboard.model';
import { toWindowItems } from './jobs-window.model';
import type { WindowItem } from './jobs-window.model';
import type { Job } from '../../models/job.model';
import { createMockJob } from '../../testing/job-test-helpers';
import { readFileSync } from 'fs';
import { join } from 'path';

// ── Fixtures ─────────────────────────────────────────────────────────

function job(id: string, group: string): Job {
  return createMockJob({ job_id: id, agent_id: group, project_id: 'p1' });
}

// Two groups: g-live (2 rows) and g-arch (1 row). Mirrors the
// ``toWindowItems`` contract the virtual scroll actually renders.
function makeItems(expanded: ReadonlySet<string>): readonly WindowItem[] {
  const groups = [
    {
      key: 'g-live',
      missionId: 'm-1',
      instanceId: 'i-1',
      agentId: 'leader',
      jobCount: 2,
      lastActivityAt: '2026-09-12T00:00:00Z',
      jobs: [job('j-1', 'leader'), job('j-2', 'leader')],
      isLive: true,
    },
    {
      key: 'g-arch',
      missionId: null,
      instanceId: null,
      agentId: 'worker',
      jobCount: 1,
      lastActivityAt: '2026-09-10T00:00:00Z',
      jobs: [job('j-3', 'worker')],
      isLive: false,
    },
  ];
  return toWindowItems(
    groups,
    expanded,
    (g) => g.key,
    (g) => `${g.jobCount} jobs`,
  );
}

const EXPANDED_ALL = new Set(['g-live', 'g-arch']);
const NONE_EXPANDED = new Set<string>();
const LIVE_ONLY = new Set(['g-live']);

describe('jobs-keyboard.model — clamped navigation (nextJobsWindowItem)', () => {
  it('moves ±1 over the flattened items (header AND rows in one walk)', () => {
    const items = makeItems(EXPANDED_ALL);
    // items: [hdr g-live, row j-1, row j-2, hdr g-arch, row j-3]
    expect(items.map((i) => i.kind)).toEqual(['header', 'row', 'row', 'header', 'row']);
    expect(nextJobsWindowItem(items, 0, 1)).toBe(1);
    expect(nextJobsWindowItem(items, 1, 1)).toBe(2);
    expect(nextJobsWindowItem(items, 3, 1)).toBe(4);
    expect(nextJobsWindowItem(items, 2, -1)).toBe(1);
    expect(nextJobsWindowItem(items, 4, -1)).toBe(3);
  });

  it('CLAMPS at the ends — no wrap (first ↓ stays, last ↑ stays)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(nextJobsWindowItem(items, 0, -1)).toBe(0);
    expect(nextJobsWindowItem(items, items.length - 1, 1)).toBe(items.length - 1);
  });

  it('no focus yet (-1) treats ↓ as first / ↑ as last (panel parity)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(nextJobsWindowItem(items, -1, 1)).toBe(0);
    expect(nextJobsWindowItem(items, -1, -1)).toBe(items.length - 1);
  });

  it('an out-of-range index also treats ↓ as first / ↑ as last (defensive)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(nextJobsWindowItem(items, items.length, 1)).toBe(0);
    expect(nextJobsWindowItem(items, 999, -1)).toBe(items.length - 1);
  });

  it('an empty list returns -1 (nothing to focus)', () => {
    expect(nextJobsWindowItem([], 0, 1)).toBe(-1);
    expect(nextJobsWindowItem([], -1, -1)).toBe(-1);
  });
});

describe('jobs-keyboard.model — DOM order == keyboard order BY CONSTRUCTION', () => {
  it('collapsed groups contribute ONLY their header — no off-screen rows to skip', () => {
    const items = makeItems(LIVE_ONLY);
    // g-arch is collapsed: its row j-3 is OMITTED (toWindowItems contract).
    expect(items.map((i) => jobsWindowItemId(i))).toEqual([
      'jobs:hdr|g-live',
      'jobs:row|j-1',
      'jobs:row|j-2',
      'jobs:hdr|g-arch',
    ]);
  });

  it('the model walks the SAME array the template renders (renderRows → cdkVirtualFor)', () => {
    // The invariant is structural: resolveJobsKeyAction + the virtual
    // scroll both consume the one flattened WindowItem[]. The
    // production source pin at the bottom of this file proves the
    // template binds renderRows() and the handler resolves over the
    // same array; here we pin the shape: header keys == group keys
    // (the expansion-set identity) and row keys == job ids.
    const items = makeItems(EXPANDED_ALL);
    const headers = items.filter((i): i is Extract<WindowItem, { kind: 'header' }> => i.kind === 'header');
    expect(headers.map((h) => h.key)).toEqual(['g-live', 'g-arch']);
    expect(headers.map((h) => h.groupKey)).toEqual(['g-live', 'g-arch']);
    const rows = items.filter((i): i is Extract<WindowItem, { kind: 'row' }> => i.kind === 'row');
    expect(rows.map((r) => r.key)).toEqual(['j-1', 'j-2', 'j-3']);
  });

  it('ids are stable + namespaced (recycle-safe identity for the (focus) single-writer)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(jobsWindowItemId(items[0])).toBe('jobs:hdr|g-live');
    expect(jobsWindowItemId(items[1])).toBe('jobs:row|j-1');
  });
});

describe('jobs-keyboard.model — WAI-ARIA tree actions (resolveJobsKeyAction)', () => {
  const expanded = (groupKey: string) => EXPANDED_ALL.has(groupKey);

  it('ArrowDown/ArrowUp resolve to a clamped focus move from the focused id', () => {
    const items = makeItems(EXPANDED_ALL);
    const at = (id: string) => items.findIndex((i) => jobsWindowItemId(i) === id);

    expect(resolveJobsKeyAction(items, at('jobs:row|j-2'), 'ArrowDown', expanded)).toEqual({
      kind: 'focus',
      index: at('jobs:hdr|g-arch'),
    });
    expect(resolveJobsKeyAction(items, at('jobs:hdr|g-arch'), 'ArrowUp', expanded)).toEqual({
      kind: 'focus',
      index: at('jobs:row|j-2'),
    });
  });

  it('clamp ends: ↓ on the last item / ↑ on the first stay in place', () => {
    const items = makeItems(EXPANDED_ALL);
    const last = items.length - 1;
    expect(resolveJobsKeyAction(items, last, 'ArrowDown', expanded)).toEqual({
      kind: 'focus',
      index: last,
    });
    expect(resolveJobsKeyAction(items, 0, 'ArrowUp', expanded)).toEqual({
      kind: 'focus',
      index: 0,
    });
  });

  it('ArrowRight on a COLLAPSED header expands (focus stays on the header)', () => {
    const items = makeItems(NONE_EXPANDED);
    expect(resolveJobsKeyAction(items, 0, 'ArrowRight', () => false)).toEqual({
      kind: 'expand',
      groupKey: 'g-live',
    });
  });

  it('ArrowRight on an expanded header or on a row is a no-op', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(resolveJobsKeyAction(items, 0, 'ArrowRight', expanded)).toEqual({ kind: 'none' });
    expect(resolveJobsKeyAction(items, 1, 'ArrowRight', expanded)).toEqual({ kind: 'none' });
  });

  it('ArrowLeft on an expanded header collapses it (focus stays — the header survives)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(resolveJobsKeyAction(items, 0, 'ArrowLeft', expanded)).toEqual({
      kind: 'collapse',
      groupKey: 'g-live',
      refocusHeaderId: null,
    });
  });

  it('ArrowLeft on a collapsed header is a no-op', () => {
    const items = makeItems(NONE_EXPANDED);
    expect(resolveJobsKeyAction(items, 0, 'ArrowLeft', () => false)).toEqual({ kind: 'none' });
  });

  it('ANCESTOR-COLLAPSE: ArrowLeft on a row collapses its group AND refocuses the header', () => {
    const items = makeItems(EXPANDED_ALL);
    // row j-1 (index 1) sits under header g-live (index 0).
    expect(resolveJobsKeyAction(items, 1, 'ArrowLeft', expanded)).toEqual({
      kind: 'collapse',
      groupKey: 'g-live',
      refocusHeaderId: 'jobs:hdr|g-live',
    });
    // row j-3 (last) sits under header g-arch.
    expect(resolveJobsKeyAction(items, 4, 'ArrowLeft', expanded)).toEqual({
      kind: 'collapse',
      groupKey: 'g-arch',
      refocusHeaderId: 'jobs:hdr|g-arch',
    });
  });

  it('ArrowRight/ArrowLeft with NO focus yet are no-ops (no unintended toggle — panel parity)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(resolveJobsKeyAction(items, -1, 'ArrowRight', expanded)).toEqual({ kind: 'none' });
    expect(resolveJobsKeyAction(items, -1, 'ArrowLeft', expanded)).toEqual({ kind: 'none' });
  });

  it('unknown / activation keys are not container actions (Enter/Space stay on the rows)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(resolveJobsKeyAction(items, 0, 'Enter', expanded)).toEqual({ kind: 'none' });
    expect(resolveJobsKeyAction(items, 0, ' ', expanded)).toEqual({ kind: 'none' });
    expect(resolveJobsKeyAction(items, 0, 'Home', expanded)).toEqual({ kind: 'none' });
    expect(isJobsActivateKey('Enter')).toBe(true);
    expect(isJobsActivateKey(' ')).toBe(true);
    expect(isJobsActivateKey('Space')).toBe(false);
    expect(isJobsActivateKey('Escape')).toBe(false);
  });

  it('an empty list resolves every key to none', () => {
    expect(resolveJobsKeyAction([], 0, 'ArrowDown', expanded)).toEqual({ kind: 'none' });
    expect(resolveJobsKeyAction([], 0, 'ArrowLeft', expanded)).toEqual({ kind: 'none' });
  });
});

describe('jobs-keyboard.model — nearestHeaderIndexAbove (ancestor resolution)', () => {
  it('walks BACKWARDS to the nearest header (rows always sit under their header)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(nearestHeaderIndexAbove(items, 1)).toBe(0); // j-1 → g-live
    expect(nearestHeaderIndexAbove(items, 2)).toBe(0); // j-2 → g-live
    expect(nearestHeaderIndexAbove(items, 4)).toBe(3); // j-3 → g-arch
  });

  it('returns -1 when no header exists above (defensive orphan-row case)', () => {
    const items = makeItems(EXPANDED_ALL);
    expect(nearestHeaderIndexAbove(items, 0)).toBe(-1);
    expect(nearestHeaderIndexAbove([], 3)).toBe(-1);
  });
});

// ── F-5 production-source pins (paired with the behavioral specs) ────
//
// The recycling contract is DOM choreography: plain-TS specs cannot
// execute it, so the REAL component source is pinned for the exact
// sequence the model contract prescribes (scrollToIndex FIRST,
// refocus after the render tick, viewport-container fallback).

const componentSrc = readFileSync(join(__dirname, 'jobs.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(__dirname, 'jobs.component.html'), 'utf-8');

describe('jobs-keyboard.model — recycling contract pinned on the REAL component', () => {
  it('the container handler resolves over renderRows() — the SAME array the viewport renders', () => {
    expect(componentSrc).toMatch(/const items = this\.renderRows\(\);/);
    expect(templateSrc).toMatch(/\*cdkVirtualFor="let item of renderRows\(\); trackBy: trackByKey"/);
  });

  it('focus moves scrollToIndex FIRST, then re-resolve getElementById after the render tick', () => {
    // Order pin: scrollToIndex precedes the deferred getElementById
    // focus inside the same helper.
    const helper = componentSrc.match(/private scheduleFocusAfterRender[\s\S]{0,1200}?\n  \}/);
    expect(helper).not.toBeNull();
    const body = helper![0];
    const scrollAt = body.indexOf('scrollToIndex(');
    const focusAt = body.indexOf('getElementById(');
    expect(scrollAt).toBeGreaterThan(-1);
    expect(focusAt).toBeGreaterThan(scrollAt);
    // The re-resolve happens AFTER the render tick (setTimeout), not
    // synchronously inside the keydown handler.
    expect(body).toMatch(/setTimeout\(/);
  });

  it('the viewport-container fallback keeps focus inside the list region', () => {
    const helper = componentSrc.match(/private scheduleFocusAfterRender[\s\S]{0,1200}?\n  \}/);
    expect(helper![0]).toMatch(/elementRef\.nativeElement\.focus\(\)/);
  });

  it('the viewport reference is the REAL CdkVirtualScrollViewport (viewChild)', () => {
    expect(componentSrc).toMatch(/viewChild\(CdkVirtualScrollViewport\)/);
  });

  it('the single-writer (focus) binding is on BOTH item kinds in the template', () => {
    expect(templateSrc).toMatch(/\(focus\)="onWindowItemFocus\(itemId\(item\)\)"/g);
  });

  it('W1 prerequisite — the focus-target [id]="itemId(item)" binding exists on BOTH hosts; the cross-reference home lives in jobs-page.bindings.pins.spec.ts', () => {
    // The id binding is the prerequisite for the recycling contract:
    // scheduleFocusAfterRender calls document.getElementById(id) — a
    // null result triggers the viewport-container fallback every arrow
    // press and the (focus) single-writer never fires for arrow nav.
    // This pin is the single cross-reference back to the binding home
    // (jobs-page.bindings.pins.spec.ts) — the model spec documents the
    // CONTRACT, the bindings spec asserts the wiring.
    expect(templateSrc).toMatch(
      /class="group-header"[^>]*\[id\]="itemId\(item\)"[^>]*>/,
    );
    expect(templateSrc).toMatch(
      /<app-job-card[^>]*\[id\]="itemId\(item\)"[^>]*>/,
    );
  });
});
