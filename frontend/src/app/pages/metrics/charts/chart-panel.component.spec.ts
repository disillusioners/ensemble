// The Chart.js PANEL WRAPPER's contract spec (T1 phase 1, task 1.7).
//
// The house pattern — a SOURCE-GREP pin over the wrapper's own source
// (the `metrics.bindings.pins.spec.ts` precedent). The wrapper is a
// CONTRACT in this phase: Phase 4 implements it, so the clauses are
// pinned against the source, and the source must carry them.
//
// The load-bearing pins:
//
//   * the FOUR CONTRACT CLAUSES are present in the source — the per-series
//     DOM proxy (`data-series` / `data-render-mode`), the visually-hidden
//     data table, `chart.destroy()` in `ngOnDestroy`, and the zoneless
//     rule;
//   * the IMPORT SHAPE — the vendor arrives through the DYNAMIC
//     `await import('chart.js')` form;
//   * the STATIC form is absent — and the assertion is
//     STATEMENT-ANCHORED, so the wrapper's own doc comment (which NAMES
//     the forbidden form) cannot redden it;
//   * the numeric W-1b assertion — ZERO files may exist under
//     `frontend/src/app/components/charts/chart/` (the retired out-of-tree
//     home must not exist at all).

import { readFileSync, readdirSync, existsSync } from 'fs';
import { join } from 'path';
import { TestBed } from '@angular/core/testing';

import { ChartPanelComponent } from './chart-panel.component';

const wrapperDir = __dirname;
const repoRoot = join(wrapperDir, '..', '..', '..', '..', '..');
const wrapperSrc = readFileSync(join(wrapperDir, 'chart-panel.component.ts'), 'utf-8');

/** A STATIC import statement of the vendor — the forbidden shape. */
const STATIC_CHART_IMPORT = /^\s*import\s[^\n]*\bfrom\s+['"]chart\.js['"]/m;

/** The DYNAMIC import — the sanctioned shape. */
const DYNAMIC_CHART_IMPORT = /\bimport\s*\(\s*['"]chart\.js['"]\s*\)/;

describe('chart-panel — the wrapper CONTRACT (T1 phase 1, task 1.7)', () => {
  it('carries the per-series DOM proxy clause (data-series / data-render-mode)', () => {
    expect(wrapperSrc).toContain('data-series');
    expect(wrapperSrc).toContain('data-render-mode');
    expect(wrapperSrc).toContain('chart-panel-proxy');
  });

  it('carries the visually-hidden data table clause (a11y + the DOM-assertion gate)', () => {
    expect(wrapperSrc).toContain('chart-panel-data');
    expect(wrapperSrc).toContain('visually-hidden');
    expect(wrapperSrc).toContain('<table');
  });

  it('carries the destroy clause (chart.destroy() in ngOnDestroy)', () => {
    expect(wrapperSrc).toMatch(/ngOnDestroy\(\)[^{]*\{[^}]*destroy\(\)/s);
  });

  it('carries the zoneless rule (no Angular-rendered overlay may depend on a redraw)', () => {
    expect(wrapperSrc).toContain('ZONELESS');
    expect(wrapperSrc).toMatch(/overlay may depend[\s\S]{0,40}?on a Chart\.js redraw/);
  });

  it('uses the DYNAMIC import shape for the vendor', () => {
    expect(wrapperSrc).toMatch(DYNAMIC_CHART_IMPORT);
    expect(wrapperSrc).toMatch(/await\s+import\s*\(\s*['"]chart\.js['"]\s*\)/);
  });

  it('carries NO static import statement of the vendor (the statement-anchored shape)', () => {
    // The assertion is STATEMENT-ANCHORED on purpose: the wrapper's own
    // doc comment NAMES the forbidden static form, so a naive
    // `not.toContain('chart.js')` would redden the freeze.
    expect(wrapperSrc).not.toMatch(STATIC_CHART_IMPORT);
  });

  it('lives at the canonical path (under src/app/pages/metrics/**)', () => {
    expect(wrapperSrc).toContain("from '@angular/core'");
    expect(existsSync(join(wrapperDir, 'chart-panel.component.ts'))).toBe(true);
  });
});

describe('chart-panel — the retired out-of-tree home (W-1b)', () => {
  it('has ZERO files under frontend/src/app/components/charts/chart/', () => {
    const retired = join(repoRoot, 'frontend', 'src', 'app', 'components', 'charts', 'chart');
    const files = existsSync(retired)
      ? readdirSync(retired, { recursive: true, withFileTypes: true }).filter((entry) =>
          entry.isFile(),
        ).length
      : 0;
    expect(files).toBe(0);
  });
});

describe('chart-panel — the scaffold renders the contract\'s DOM', () => {
  it('renders the per-series proxy and the visually-hidden table', async () => {
    const fixture = TestBed.createComponent(ChartPanelComponent);
    fixture.componentRef.setInput('series', [
      { id: 'probe', label: 'probe', mode: 'step', points: [{ t: 1, v: 2 }] },
    ]);
    fixture.componentRef.setInput('label', 'probe panel');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host: HTMLElement = fixture.nativeElement;
    const proxy = host.querySelector('[data-test="chart-panel-proxy"]');
    expect(proxy).not.toBeNull();
    expect(proxy?.getAttribute('data-series')).toBe('probe');
    expect(proxy?.getAttribute('data-render-mode')).toBe('step');

    const table = host.querySelector('[data-test="chart-panel-data"]');
    expect(table).not.toBeNull();
    expect(table?.classList.contains('visually-hidden')).toBe(true);
    expect(table?.querySelectorAll('tbody tr').length).toBe(1);
  });
});
