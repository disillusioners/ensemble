// The BUNDLE GUARD's own pins (T1 phase 1, task 1.9).
//
// The guard itself is a NODE tool in the lane's plan dir
// (`.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js`)
// — it runs against a production build, so it cannot be a jest leg.
//
// These pins bind the guard to its CONTRACT so the guard cannot rot:
//
//   * B8 — the guard reads the NAMED baseline artifact
//     (`bundle-baseline.json`); it hard-codes NO numeric baseline of its
//     own, so the artifact cannot drift away from the guard;
//   * the artifact carries the frozen numbers (the initial-total baseline,
//     the threshold, the warning SET — not a count);
//   * the guard declares the FOUR parts (B1/B2/B3/B6) and FAILS LOUDLY
//     (a non-zero exit) on a violation;
//   * the guard's FENCE — it reads a build's dist directory, a build log
//     and the source tree; it never opens a socket, never starts a daemon,
//     and never resolves a live install path.

import { existsSync, readFileSync } from 'fs';
import { join } from 'path';

const metricsDir = join(__dirname);
const repoRoot = join(metricsDir, '..', '..', '..', '..', '..');
const planDir = join(
  repoRoot,
  '.agents',
  'shared',
  'planning',
  'metrics-unification',
  'fe-t1-build',
);

const baselinePath = join(planDir, 'bundle-baseline.json');
const guardPath = join(planDir, 'bundle-guard.js');

describe('the bundle guard — the named baseline artifact (B8)', () => {
  it('the artifact EXISTS (the single machine-readable source)', () => {
    expect(existsSync(baselinePath)).toBe(true);
  });

  it('the artifact carries the frozen initial-total baseline + its threshold', () => {
    const baseline = JSON.parse(readFileSync(baselinePath, 'utf-8'));
    expect(baseline.guards.B1_initial_total_delta.baseline_bytes).toBe(5909018);
    expect(baseline.guards.B1_initial_total_delta.threshold).toContain('2048');
  });

  it('the artifact carries the B2 / B3 guard definitions', () => {
    const baseline = JSON.parse(readFileSync(baselinePath, 'utf-8'));
    expect(baseline.guards.B2_no_chartjs_marker_in_any_initial_chunk.threshold).toBe('0 hits');
    expect(baseline.guards.B3_import_shape_and_site.threshold).toContain('0 static');
    expect(baseline.guards.B3_import_shape_and_site.threshold).toContain(
      'src/app/pages/metrics/**',
    );
  });

  it('the artifact carries the warning SET — 9 paths, not a count', () => {
    const baseline = JSON.parse(readFileSync(baselinePath, 'utf-8'));
    expect(baseline.anyComponentStyle_baseline.count).toBe(9);
    expect(baseline.anyComponentStyle_baseline.entries.length).toBe(9);
    expect(baseline.anyComponentStyle_baseline.guard).toContain('SET DIFFERENCE');
    // the largest offender (blueprint, 18.32 kB) is IN the set — C-3
    expect(
      baseline.anyComponentStyle_baseline.entries.some((entry: string) =>
        entry.includes('blueprint.component.scss'),
      ),
    ).toBe(true);
  });
});

describe('the bundle guard — its contract', () => {
  const guardSrc = readFileSync(guardPath, 'utf-8');

  it('the guard EXISTS', () => {
    expect(existsSync(guardPath)).toBe(true);
  });

  it('the guard READS the named artifact (B8 — not a number copied into prose)', () => {
    expect(guardSrc).toContain('bundle-baseline.json');
    expect(guardSrc).toContain('BASELINE_PATH');
    expect(guardSrc).toContain('B1_initial_total_delta');
    expect(guardSrc).toContain('anyComponentStyle_baseline');
  });

  it('the guard hard-codes NO numeric baseline of its own', () => {
    expect(guardSrc).not.toContain('5909018');
  });

  it('the guard declares the FOUR parts (B1 + B2 + B3 + B6)', () => {
    expect(guardSrc).toContain('(a) B1');
    expect(guardSrc).toContain('(b) B2');
    expect(guardSrc).toContain('(c) B3');
    expect(guardSrc).toContain('(d) B6');
  });

  it('the guard FAILS LOUDLY on a violation (a non-zero exit)', () => {
    expect(guardSrc).toContain('process.exit(1)');
    expect(guardSrc).toContain('RESULT: RED');
  });

  it('the guard is NOT VACUOUS — it fails when it finds no import site', () => {
    expect(guardSrc).toContain('VACUOUS');
  });

  it('the guard FENCE — no socket, no daemon, no live install path', () => {
    expect(guardSrc).not.toContain('http://');
    expect(guardSrc).not.toContain('https://');
    expect(guardSrc).not.toContain('localhost');
    expect(guardSrc).not.toContain('9797');
    expect(guardSrc).not.toContain('.env');
  });
});
