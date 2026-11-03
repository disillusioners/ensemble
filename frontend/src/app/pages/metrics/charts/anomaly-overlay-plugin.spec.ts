// The HOUSE ANOMALY OVERLAY PLUGIN's spec (T1 phase 4, task 4.7).
//
// The plugin is a HOUSE file — NOT a vendor package. The load-bearing
// pins:
//
//   * the severity colours are the house palette, VERBATIM (the same
//     values the retired `svg-timeline.component.ts:169-173` used);
//   * the plugin draws one marker per marker, and SKIPS a marker whose
//     series is not in the chart (a marker for a dataset that is not
//     there must never be drawn at a bogus position);
//   * the nearest-marker lookup honours the click tolerance and never
//     fabricates an id;
//   * THE LOCKFILE GAINS NO SECOND VENDOR PACKAGE — the overlay is the
//     house's own code, so the dependency set stays exactly the two
//     direct entries (plus their transitive closure).

import { readFileSync } from 'fs';
import { join } from 'path';

import { CHART_SEVERITY_COLORS } from '../../../components/charts/chart-theme';
import {
  HOUSE_ANOMALY_MARKER_RADIUS,
  HOUSE_ANOMALY_PLUGIN_ID,
  HouseAnomalyMarker,
  createHouseAnomalyOverlayPlugin,
  houseAnomalyColor,
  houseDatasetIndex,
  houseNearestAnomaly,
} from './anomaly-overlay-plugin';

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);

function fakeChart(datasets: { label?: string; seriesId?: string }[]) {
  const arcs: { x: number; y: number; r: number; fill: string }[] = [];
  const ctx = {
    fillStyle: '',
    save: () => undefined,
    restore: () => undefined,
    beginPath: () => undefined,
    arc: (x: number, y: number, r: number) => {
      arcs.push({ x, y, r, fill: ctx.fillStyle });
    },
    fill: () => undefined,
  };
  return {
    arcs,
    chart: {
      ctx: ctx as unknown as CanvasRenderingContext2D,
      chartArea: { top: 10, left: 0, right: 100, bottom: 50 },
      scales: { x: { getPixelForValue: (value: number) => value / 1000 } },
      data: { datasets },
    },
  };
}

describe('house anomaly overlay plugin', () => {
  it('registers under the house plugin id', () => {
    expect(createHouseAnomalyOverlayPlugin().id).toBe(HOUSE_ANOMALY_PLUGIN_ID);
  });

  it('draws one marker per marker, in the house severity colours VERBATIM', () => {
    const markers: HouseAnomalyMarker[] = [
      { anomalyId: 'a1', seriesId: 'mem', tsMs: 1000, severity: 'low', title: 'low' },
      { anomalyId: 'a2', seriesId: 'mem', tsMs: 2000, severity: 'medium', title: 'medium' },
      { anomalyId: 'a3', seriesId: 'mem', tsMs: 3000, severity: 'high', title: 'high' },
    ];
    const { arcs, chart } = fakeChart([{ label: 'memory', seriesId: 'mem' }]);
    createHouseAnomalyOverlayPlugin().beforeDatasetsDraw(
      chart as never,
      null,
      { markers },
    );

    expect(arcs.length).toBe(3);
    expect(arcs.map((arc) => arc.fill)).toEqual([
      CHART_SEVERITY_COLORS.low,
      CHART_SEVERITY_COLORS.medium,
      CHART_SEVERITY_COLORS.high,
    ]);
    // The house palette, verbatim (the retired timeline engine's values).
    expect(CHART_SEVERITY_COLORS).toEqual({
      low: '#38bdf8',
      medium: '#f59e0b',
      high: '#ef4444',
    });
    expect(arcs[0].r).toBe(HOUSE_ANOMALY_MARKER_RADIUS);
    // The marker lane sits at the top of the chart area.
    expect(arcs[0].y).toBe(10 + HOUSE_ANOMALY_MARKER_RADIUS);
  });

  it('SKIPS a marker whose series is not in the chart', () => {
    const { arcs, chart } = fakeChart([{ label: 'memory', seriesId: 'mem' }]);
    createHouseAnomalyOverlayPlugin().beforeDatasetsDraw(chart as never, null, {
      markers: [{ anomalyId: 'a1', seriesId: 'cpu', tsMs: 1000, severity: 'high', title: 'x' }],
    });
    expect(arcs.length).toBe(0);
  });

  it('draws nothing when there are no markers', () => {
    const { arcs, chart } = fakeChart([{ label: 'memory', seriesId: 'mem' }]);
    createHouseAnomalyOverlayPlugin().beforeDatasetsDraw(chart as never, null, {
      markers: [],
    });
    expect(arcs.length).toBe(0);
  });

  it('resolves the dataset by its seriesId', () => {
    const datasets = [{ label: 'memory', seriesId: 'mem' }, { label: 'cpu', seriesId: 'cpu' }];
    expect(houseDatasetIndex(datasets, 'cpu')).toBe(1);
    expect(houseDatasetIndex(datasets, 'absent')).toBe(-1);
  });

  it('finds the nearest marker within the tolerance', () => {
    const markers: HouseAnomalyMarker[] = [
      { anomalyId: 'a1', seriesId: 'mem', tsMs: 1000, severity: 'high', title: 'a1' },
      { anomalyId: 'a2', seriesId: 'mem', tsMs: 5000, severity: 'high', title: 'a2' },
    ];
    expect(houseNearestAnomaly(markers, 'mem', 1100, 500)?.anomalyId).toBe('a1');
    expect(houseNearestAnomaly(markers, 'mem', 4900, 500)?.anomalyId).toBe('a2');
    // Outside the tolerance — no marker, and never a fabricated one.
    expect(houseNearestAnomaly(markers, 'mem', 3000, 500)).toBeNull();
    // A different series — no marker.
    expect(houseNearestAnomaly(markers, 'cpu', 1000, 500)).toBeNull();
  });

  it('falls back to the medium severity for an unknown severity', () => {
    expect(houseAnomalyColor('low')).toBe(CHART_SEVERITY_COLORS.low);
    expect(
      houseAnomalyColor('nonsense' as unknown as 'low'),
    ).toBe(CHART_SEVERITY_COLORS.medium);
  });
});

describe('the dependency set — NO second vendor package', () => {
  it('carries exactly the two direct entries and their transitive closure', () => {
    const frontendRoot = join(__dirname, '..', '..', '..', '..', '..');
    const pkg = JSON.parse(
      readFileSync(join(frontendRoot, 'package.json'), 'utf8'),
    ) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
    };
    const direct = [
      ...Object.keys(pkg.dependencies),
      ...Object.keys(pkg.devDependencies),
    ];
    expect(direct).toContain('chart.js');
    expect(direct).toContain('jest-canvas-mock');
    // The anomaly overlay is a HOUSE plugin — no annotation package.
    expect(direct.filter((name) => name.includes('annotation'))).toEqual([]);
    expect(direct.filter((name) => name.includes('chartjs-plugin'))).toEqual([]);
  });

  it('the lockfile carries no second chart vendor', () => {
    const frontendRoot = join(__dirname, '..', '..', '..', '..', '..');
    const lock = readFileSync(join(frontendRoot, 'package-lock.json'), 'utf8');
    const chartPackages = [...lock.matchAll(/"node_modules\/([^"]*chart[^"]*)":/g)].map(
      (match) => match[1],
    );
    // `chart.js` is the ONLY chart vendor in the lockfile; the canvas
    // mock is a devDependency and carries no 'chart' in its name.
    expect(chartPackages.sort()).toEqual(['chart.js']);
    expect(lock).toContain('"node_modules/jest-canvas-mock"');
  });
});
