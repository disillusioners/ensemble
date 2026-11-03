// The METRICS BINDINGS pins (P5 task 5.5/5.8/5.9 · D1 pack
// `metrics-fe-bindings-pins`).
//
// Source-grep pins (the house pattern — the precedent is
// `pages/maintenance/maintenance.bindings.pins.spec.ts`).
//
// The load-bearing pins:
//
//   * P5-M1 — every polling tab body declares a NAMED `POLL_INTERVAL_MS`
//     (a bare literal would drift silently);
//   * P5-M2 — every polling tab body tears its interval down
//     (`clearInterval`) — a leaked interval keeps polling a tab the
//     operator has left;
//   * P5-M11 — the GATE runs BOTH build legs: the dev configuration is
//     the AOT leg, and the PRODUCTION configuration is the only one that
//     carries `budgets` + `outputHashing` (AM-23). A green dev build is
//     NOT evidence of budget compliance.

import { readFileSync } from 'fs';
import { join } from 'path';

const metricsDir = join(__dirname);
const repoRoot = join(metricsDir, '..', '..', '..', '..', '..');

const overviewSrc = readFileSync(join(metricsDir, 'overview', 'overview-tab.component.ts'), 'utf-8');
const chartsSrc = readFileSync(join(metricsDir, 'charts', 'charts-tab.component.ts'), 'utf-8');
const packScript = readFileSync(
  join(repoRoot, 'test', 'packs', 'metrics_fe_dual_side_test.sh'),
  'utf-8',
);

const POLLING_BODIES: readonly { name: string; source: string }[] = [
  { name: 'overview-tab.component.ts', source: overviewSrc },
  { name: 'charts-tab.component.ts', source: chartsSrc },
];

describe('metrics bindings — the polling discipline (P5-M1 / P5-M2)', () => {
  for (const body of POLLING_BODIES) {
    it(`${body.name} declares a named POLL_INTERVAL_MS`, () => {
      expect(body.source).toContain('readonly POLL_INTERVAL_MS');
    });

    it(`${body.name} tears the interval down with clearInterval`, () => {
      expect(body.source).toContain('clearInterval');
    });

    it(`${body.name} clears the handle on teardown (the handle is nulled)`, () => {
      expect(body.source).toMatch(/clearInterval\(this\.pollHandle\)/);
      expect(body.source).toMatch(/this\.pollHandle = null/);
    });

    it(`${body.name} carries the skip-if-in-flight guard and the supersede token`, () => {
      expect(body.source).toContain('inFlight');
      expect(body.source).toContain('requestSeq');
      expect(body.source).toMatch(/token !== this\.requestSeq/);
    });
  }

  it('the two polling bodies declare DIFFERENT cadences (10 s / 15 s)', () => {
    expect(overviewSrc).toContain('POLL_INTERVAL_MS = 10_000');
    expect(chartsSrc).toContain('POLL_INTERVAL_MS = 15_000');
  });

  it('neither polling body polls the listener surface (:9798)', () => {
    expect(overviewSrc).not.toContain('9798');
    expect(chartsSrc).not.toContain('9798');
  });
});

describe('metrics bindings — the D2 zero-dependency guard', () => {
  it('no charting or date library is imported by the lane FE sources', () => {
    const sources = [
      overviewSrc,
      chartsSrc,
      readFileSync(join(metricsDir, 'metrics.component.ts'), 'utf-8'),
      readFileSync(
        join(
          repoRoot,
          'frontend',
          'src',
          'app',
          'components',
          'charts',
          'svg-line-chart',
          'svg-line-chart.component.ts',
        ),
        'utf-8',
      ),
      readFileSync(
        join(repoRoot, 'frontend', 'src', 'app', 'services', 'metrics.service.ts'),
        'utf-8',
      ),
    ].join('\n');
    for (const banned of ['chart.js', 'd3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']) {
      expect(sources).not.toContain(banned);
    }
  });
});

describe('metrics bindings — the gate build legs (P5-M11 / AM-23)', () => {
  it('the gate runs the DEV build leg (the AOT leg)', () => {
    expect(packScript).toContain('./node_modules/.bin/ng build --configuration development');
  });

  it('the gate runs the PRODUCTION build leg (the only budgets configuration)', () => {
    expect(packScript).toContain('./node_modules/.bin/ng build --configuration production');
  });

  it('the gate runs the scoped type-check leg (the P5-M9 surface)', () => {
    expect(packScript).toContain('tsconfig.metrics-fe.json');
  });

  it('the gate runs the jest legs (the D1 + the D2 set)', () => {
    expect(packScript).toContain('node_modules/.bin/jest');
    expect(packScript).toContain('d2_declared');
    expect(packScript).toContain('d2_derived');
  });

  it('the gate asserts the package.json dependency diff is empty (D2)', () => {
    expect(packScript).toContain('package_json_dependency_diff');
    expect(packScript).toContain('frontend/package.json');
  });

  it('the gate records the budget warnings as findings (never an auto-fix)', () => {
    expect(packScript).toContain('BUDGET_WARNINGS');
  });
});
