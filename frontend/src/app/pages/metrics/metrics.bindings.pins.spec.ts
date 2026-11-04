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
//     NOT evidence of budget compliance;
//   * the D-1 zero-dependency posture — the banned-list guard now carries
//     FIVE bans (the `chart.js` entry is WITHDRAWN, the five others are
//     NOT), and the POSITIVE import-shape pin replaces the withdrawn
//     entry with a STRONGER rule (see below).

import { readFileSync, readdirSync } from 'fs';
import { join, relative, sep } from 'path';

const metricsDir = join(__dirname);
const repoRoot = join(metricsDir, '..', '..', '..', '..', '..');
const frontendDir = join(repoRoot, 'frontend');

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
    // The banned list — FIVE entries. The `chart.js` entry is WITHDRAWN
    // (D-1: the wrapper's import is sanctioned, and it is governed by the
    // POSITIVE import-shape pin below, which is strictly stronger than
    // the withdrawn substring ban).
    //
    // The scope change is PER-BAN, NOT per-file (W-6): every remaining
    // ban keeps scanning ALL FIVE sources — the same five the spec read
    // before the amendment.
    //
    // AM-10 / W-1: the fourth slot is RE-POINTED at the wrapper stub
    // (`pages/metrics/charts/chart-panel.component.ts`). The house SVG
    // engine that occupied this slot is CONSOLIDATED AWAY in Phase 4,
    // so a read of it would crash the spec at merge; the stub is created
    // in Phase 1 and is not deleted by Phase 4.
    const sources = [
      overviewSrc,
      chartsSrc,
      readFileSync(join(metricsDir, 'metrics.component.ts'), 'utf-8'),
      readFileSync(join(metricsDir, 'charts', 'chart-panel.component.ts'), 'utf-8'),
      readFileSync(
        join(repoRoot, 'frontend', 'src', 'app', 'services', 'metrics.service.ts'),
        'utf-8',
      ),
    ].join('\n');
    for (const banned of ['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']) {
      expect(sources).not.toContain(banned);
    }
  });
});

// ── The D-1 import-shape pin (C-1 / AM-15 / W-1) ───────────────────────
//
// The withdrawn `chart.js` substring ban is replaced by a POSITIVE pin
// that gates the import SHAPE and the import SITE.
//
// WHY NOT A SUBSTRING SCAN: the wrapper's own doc comment NAMES the
// forbidden static form (a reader must be able to see WHY the static
// form is banned), so a naive `expect(sources).not.toContain('chart.js')`
// would go RED at the very freeze this pin requires to be GREEN. The pin
// therefore matches import STATEMENTS — the comments and the doc strings
// are stripped before the match, so prose can never redden it and a real
// static import can never escape it.

/** A STATIC import statement of the vendor — FORBIDDEN (C-1). */
const STATIC_CHART_JS_IMPORTS: readonly RegExp[] = [
  /^\s*import\s[^\n]*\bfrom\s+['"]chart\.js['"]/m,
  /^\s*import\s+['"]chart\.js['"]/m,
];

/** The DYNAMIC import of the vendor — the ONLY sanctioned shape. */
const DYNAMIC_CHART_JS_IMPORT = /\bimport\s*\(\s*['"]chart\.js['"]\s*\)/g;

/**
 * Strip the comments so the pin matches STATEMENTS, never prose.
 *
 * The block-comment pass keeps the line count (the line anchors stay
 * meaningful); the line-comment pass leaves a `//` inside a URL alone.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

/** Every `.ts` file under `frontend/` (the tree the rule covers). */
function collectSourceFiles(root: string): string[] {
  const skip = new Set(['node_modules', 'dist', '.angular', 'coverage']);
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) {
        continue;
      }
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.name.endsWith('.ts')) {
        out.push(full);
      }
    }
  };
  walk(root);
  return out;
}

interface ImportSite {
  /** The path relative to `frontend/`. */
  readonly path: string;
  /** The static import statements found (the forbidden shape). */
  readonly static: readonly string[];
  /** The dynamic import calls found (the sanctioned shape). */
  readonly dynamic: readonly string[];
}

const CHART_JS_SITES: readonly ImportSite[] = collectSourceFiles(frontendDir).map((file) => {
  const code = stripComments(readFileSync(file, 'utf-8'));
  const staticMatches = STATIC_CHART_JS_IMPORTS.flatMap((pattern) =>
    code.match(pattern) ?? [],
  );
  const dynamicMatches = code.match(DYNAMIC_CHART_JS_IMPORT) ?? [];
  return {
    path: relative(frontendDir, file).split(sep).join('/'),
    static: staticMatches,
    dynamic: dynamicMatches,
  };
}).filter((site) => site.static.length > 0 || site.dynamic.length > 0);

describe('metrics bindings — the D-1 import-shape pin (C-1 / AM-15)', () => {
  it('has NO static chart.js import statement anywhere in the FE tree', () => {
    const offenders = CHART_JS_SITES.filter((site) => site.static.length > 0);
    expect(offenders.map((site) => site.path)).toEqual([]);
  });

  it('reaches the vendor only through the DYNAMIC import form', () => {
    // Every site that mentions the vendor as an import is dynamic; a
    // static site would have been caught above, so the two assertions
    // together are the "every site is the dynamic form" rule.
    for (const site of CHART_JS_SITES) {
      expect(site.dynamic.length).toBeGreaterThan(0);
    }
  });

  it('keeps EVERY chart.js import site inside src/app/pages/metrics/**', () => {
    const outside = CHART_JS_SITES.filter(
      (site) => !site.path.startsWith('src/app/pages/metrics/'),
    );
    expect(outside.map((site) => site.path)).toEqual([]);
  });

  it('is NOT vacuous — the wrapper stub IS one of the sites', () => {
    const paths = CHART_JS_SITES.map((site) => site.path);
    expect(paths).toContain('src/app/pages/metrics/charts/chart-panel.component.ts');
  });

  it('the wrapper stub uses the awaited dynamic form', () => {
    const stub = CHART_JS_SITES.find(
      (site) => site.path === 'src/app/pages/metrics/charts/chart-panel.component.ts',
    );
    expect(stub?.dynamic.length ?? 0).toBeGreaterThan(0);
    expect(stub?.static.length ?? 1).toBe(0);
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
