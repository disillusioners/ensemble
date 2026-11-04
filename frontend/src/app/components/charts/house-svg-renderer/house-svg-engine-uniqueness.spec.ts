// The SINGLE-ENGINE gate (T1 phase 4, task 4.12).
//
// The consolidation's promise: exactly ONE house SVG engine remains in
// the tree. The assertion is a MIGRATION gate, not an engine property —
// it can only pass once the two superseded engines are deleted, so it
// lives in its own spec.

import { existsSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

const SRC_ROOT = join(__dirname, '..', '..', '..', '..');

describe('the house SVG engine — the single-engine gate (task 4.12)', () => {
  it('is the ONLY SVG chart engine in the tree', () => {
    // ── Council fold (agentic-1): the offender pattern is BASENAME-anchored
    // over the WHOLE `src/app` tree, not gated on a `charts` path segment.
    // The path-scoped form could only see a resurrection that also landed
    // under a directory named `charts` — yet one of the two retired engines
    // lived at `pages/resources/svg-timeline/`, so a resurrected engine in
    // a pages-shaped path would have escaped while this spec's title
    // claimed "the ONLY SVG chart engine in the tree".
    //
    // The retired engine FILES are the identity, wherever they sit:
    //   * `svg-line-chart.{ts,html,scss,spec.ts}`  (the newer engine)
    //   * `svg-timeline.{ts,html,scss,spec.ts}`    (the older engine)
    // Any extension, any directory under `src/app` — the module stem is
    // what must never come back. (The gate is deliberately extension-
    // agnostic: a resurrection could ship as an inline template plus a
    // companion stylesheet, so `.ts`-only scanning is too narrow.)
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (statSync(full).isFile()) {
          const file = entry.name;
          if (
            /^svg-line-chart(\.|$)/.test(file) ||
            /^svg-timeline(\.|$)/.test(file)
          ) {
            offenders.push(full.replace(SRC_ROOT, 'frontend/src'));
          }
        }
      }
    };
    walk(join(SRC_ROOT, 'app'));
    expect(offenders).toEqual([]);
  });

  it('the retired out-of-tree wrapper path holds ZERO orphan files', () => {
    const retired = join(SRC_ROOT, 'app', 'components', 'charts', 'chart');
    // The path was NEVER created — the sweep is a no-op that still
    // asserts the count (a missing path is zero orphan files).
    const files = existsSync(retired)
      ? readdirSync(retired, { recursive: true, withFileTypes: true }).filter((entry) =>
          entry.isFile(),
        ).length
      : 0;
    expect(files).toBe(0);
  });
});
