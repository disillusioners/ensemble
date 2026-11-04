// The SCOPED TYPE-CHECK's include list (T1 phase 4, task 4.9 — AM-10).
//
// The scoped type-check (`frontend/tsconfig.metrics-fe.json`) is a
// NAMED list of files. tsc SILENTLY IGNORES a stale include — a path
// that no longer exists is simply dropped from the program — so a
// relocated spec that is not re-pointed falls OUT of the scoped check
// with NO signal at all.
//
// The load-bearing pins:
//
//   * NO include entry points at a file that does not exist (the stale
//     `svg-line-chart.component.spec.ts` entry is gone);
//   * the CONSOLIDATED renderer's spec IS inside the scoped check;
//   * the registry's spec IS inside the scoped check;
//   * the old spec path is NOT listed anywhere.

import { existsSync, readFileSync } from 'fs';
import { join } from 'path';

const FRONTEND_ROOT = join(__dirname, '..', '..', '..', '..');
const TSCONFIG = join(FRONTEND_ROOT, 'tsconfig.metrics-fe.json');

interface MetricsFeTsconfig {
  readonly include: readonly string[];
}

function tsconfig(): MetricsFeTsconfig {
  return JSON.parse(readFileSync(TSCONFIG, 'utf8')) as MetricsFeTsconfig;
}

describe('the scoped type-check (tsconfig.metrics-fe.json)', () => {
  it('has NO stale include entry (every listed file exists)', () => {
    const stale = tsconfig()
      .include.filter((entry) => !entry.includes('*'))
      .filter((entry) => !existsSync(join(FRONTEND_ROOT, entry)));
    expect(stale).toEqual([]);
  });

  it('carries the CONSOLIDATED renderer\'s spec inside the scoped check', () => {
    expect(tsconfig().include).toContain(
      'src/app/components/charts/house-svg-renderer/house-svg-renderer.spec.ts',
    );
  });

  it('carries the renderer REGISTRY\'s spec inside the scoped check', () => {
    expect(tsconfig().include).toContain(
      'src/app/components/charts/renderer-registry.spec.ts',
    );
  });

  it('does NOT carry the retired engine\'s spec path', () => {
    expect(tsconfig().include).not.toContain(
      'src/app/components/charts/svg-line-chart/svg-line-chart.component.spec.ts',
    );
    expect(
      tsconfig().include.some((entry) => entry.includes('svg-line-chart')),
    ).toBe(false);
  });
});
