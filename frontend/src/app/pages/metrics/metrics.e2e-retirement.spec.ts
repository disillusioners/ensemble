// The CANVAS-FATAL E2E LEGS' RETIREMENT (T1 phase 4, task 4.10 — AM-11 + W-3).
//
// The e2e carried FOUR canvas-fatal assertions. Under a Chart.js canvas
// renderer there is no SVG `d` attribute and no `chart-tick` element, so
// all four were re-specified in THREE leg-classes.
//
// The load-bearing pins:
//
//   * the `d`-ATTRIBUTE assertions are **RETIRED, NOT PRESERVED** — the
//     source carries no `getAttribute('d')` and no `d`-geometry
//     assertion at all;
//   * the retired `[data-test="svg-line-chart"]` count and the retired
//     `[data-test="chart-tick"]` locator are GONE;
//   * the REPLACEMENTS are present — the per-series proxy count, the
//     proxy's step/line render modes, and the wrapper's UTC tick text;
//   * the step-vs-linear GEOMETRY moved to the renderer spec (the one
//     assertion that now carries it).

import { readFileSync } from 'fs';
import { join } from 'path';

const FRONTEND_ROOT = join(__dirname, '..', '..', '..', '..');
const E2E = join(FRONTEND_ROOT, 'e2e', 'metrics-gate.spec.ts');

/** The e2e source with its COMMENTS stripped (the record names the retired forms). */
function e2eCode(): string {
  return readFileSync(E2E, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

describe('the canvas-fatal e2e legs (task 4.10)', () => {
  it('has RETIRED the `d`-attribute assertions (not preserved)', () => {
    const code = e2eCode();
    expect(code).not.toContain("getAttribute('d')");
    expect(code).not.toContain('getAttribute("d")');
  });

  it('has RETIRED the `[data-test="svg-line-chart"]` count', () => {
    expect(e2eCode()).not.toContain('[data-test="svg-line-chart"]');
  });

  it('has RETIRED the `[data-test="chart-tick"]` locator', () => {
    expect(e2eCode()).not.toContain('[data-test="chart-tick"]');
  });

  it('carries the per-series PROXY count (leg-class 1)', () => {
    expect(e2eCode()).toContain('[data-test="chart-panel-proxy"]');
  });

  it('carries the proxy\'s step/line render modes (leg-class 2)', () => {
    const code = e2eCode();
    expect(code).toContain('[data-test="chart-panel-proxy"][data-render-mode="step"]');
    expect(code).toContain('[data-test="chart-panel-proxy"][data-render-mode="line"]');
  });

  it('carries the wrapper\'s UTC tick text (leg-class 3)', () => {
    expect(e2eCode()).toContain('[data-test="chart-panel-tick"]');
  });

  it('states the retirement in the file\'s own header', () => {
    const raw = readFileSync(E2E, 'utf8');
    expect(raw).toContain('RETIRED');
    expect(raw).toContain('NOT PRESERVED');
  });

  it('the step-vs-linear GEOMETRY lives in the renderer spec now', () => {
    const rendererSpec = readFileSync(
      join(
        FRONTEND_ROOT,
        'src',
        'app',
        'components',
        'charts',
        'house-svg-renderer',
        'house-svg-renderer.spec.ts',
      ),
      'utf8',
    );
    expect(rendererSpec).toContain(
      'renders a COUNTER as STEP and a GAUGE as LINEAR (the geometric truth the e2e retired)',
    );
  });
});
