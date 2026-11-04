/**
 * THE `/resources` RETIREMENT (D-3 · T1 phase 7, tasks 7.1–7.8).
 *
 * The standalone `/resources` page is retired in the SAME release that
 * mounts the resource module as tab 1 of `/metrics`. This spec is the
 * phase's acceptance:
 *
 *   | task | what this file pins                                        |
 *   |------|-------------------------------------------------------------|
 *   | 7.1  | the `canMatch → UrlTree` redirect, END-TO-END, both legs    |
 *   | 7.1  | the null-fallback (no current navigation) path               |
 *   | 7.2  | the redirect's tab id agrees with the manifest's tab-1 id     |
 *   | 7.3  | the gear menu has NO `Resources` entry (the Metrics one lives) |
 *   | 7.4  | the retired page directory is GONE (and its subtitle with it) |
 *   | 7.6  | the WIDENED repo sweep — ZERO stale-route references          |
 *   | 7.7  | NO Playwright config targets the retired route               |
 *   | 7.7  | NO Playwright spec drives the retired route AS A PAGE         |
 *   | 7.8  | BOTH operator runbooks name the NEW route                     |
 *
 * ── THE 7.1 ACCEPTANCE IS END-TO-END (W-2) ────────────────────────────
 *
 * The two legs navigate the PRODUCTION route table with a real `Router`
 * and assert the LANDED URL's **PARSED** `queryParams` — never a raw
 * URL string (order-brittle) and never a hand-built `UrlTree` (a
 * unit assertion on a tree the test built itself proves nothing about
 * the guard).
 */

// ── The DOM-less router harness ────────────────────────────────────────
import { readdirSync, readFileSync, existsSync, statSync } from 'fs';
import { join, relative } from 'path';

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { NavigationEnd, provideRouter, Router, UrlTree } from '@angular/router';

import {
  RESOURCES_REDIRECT_TARGET,
  RESOURCES_REDIRECT_TAB_ID,
  resourcesRedirectGuard,
  routes,
} from './app.routes';
import { METRICS_TABS, metricsTabById } from './pages/metrics/tabs.manifest';

const REPO_ROOT = join(__dirname, '..', '..', '..');
const FRONTEND_ROOT = join(REPO_ROOT, 'frontend');
const DOCS_ROOT = join(REPO_ROOT, 'docs');
const E2E_ROOT = join(FRONTEND_ROOT, 'e2e');

/**
 * The retired ROUTE literal — a QUOTE-DELIMITED `/resources`.
 *
 * The quote-delimiter is the point of the pattern: it matches the
 * route as a PATH SEGMENT (`'/resources'`, `"/resources"`,
 * `` `/resources` ``) and it does NOT match:
 *   * the API SURFACE — `'/api/resources/live'` (it stays — class (b));
 *   * a MODULE path — `pages/resources/resources.component` (the module
 *     moved in phase 5);
 *   * an unrelated path segment — `'/tmp/resources-gate-evidence'`.
 */
const RETIRED_ROUTE_RE = /['"`]\/resources(?![\w/])/;

/** The retired shell's per-instance-attribution subtitle (task 7.4 / W8). */
const RETIRED_SUBTITLE =
  'Daemon memory, CPU and per-instance attribution — live snapshot and history';

/** The source with its COMMENTS stripped (the house idiom). */
function codeOnly(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

/** Every file under `dir` with one of the swept extensions. */
function sweptFiles(dir: string, skip: readonly string[] = []): string[] {
  const out: string[] = [];
  const walk = (current: string): void => {
    for (const name of readdirSync(current)) {
      if (skip.includes(name)) {
        continue;
      }
      const full = join(current, name);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (/\.(ts|html|scss|json|mjs|md)$/.test(name)) {
        out.push(full);
      }
    }
  };
  walk(dir);
  return out.sort();
}

// ── 7.1 — the redirect ─────────────────────────────────────────────────

describe('the /resources → /metrics?tab=resource redirect (task 7.1)', () => {
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
    router = TestBed.inject(Router);
  });

  it('LEG (i) — lands on /metrics with EVERY incoming param preserved (plus tab=resource)', async () => {
    const settled: NavigationEnd[] = [];
    const sub = router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) {
        settled.push(event);
      }
    });

    await router.navigateByUrl('/resources?window=24h&x=1');
    sub.unsubscribe();

    // The LANDED URL — the params PARSED, never a raw-string comparison.
    expect(router.url.split('?')[0]).toBe('/metrics');
    const landed = router.parseUrl(router.url);
    expect(landed.queryParams).toEqual({ window: '24h', x: '1', tab: 'resource' });

    // A SINGLE navigation settles — the redirect does not loop (the
    // target path differs, so the second navigation is a different URL).
    expect(settled).toHaveLength(1);
    expect(settled[0].urlAfterRedirects).toBe(router.url);
  });

  it('LEG (ii) — the redirect\'s OWN tab=resource OVERRIDES an incoming tab', async () => {
    await router.navigateByUrl('/resources?tab=charts');

    expect(router.url.split('?')[0]).toBe('/metrics');
    const landed = router.parseUrl(router.url);
    expect(landed.queryParams['tab']).toBe('resource');
  });

  it('carries a param-LESS retired URL to the resource tab', async () => {
    await router.navigateByUrl('/resources');

    expect(router.url.split('?')[0]).toBe('/metrics');
    const landed = router.parseUrl(router.url);
    expect(landed.queryParams).toEqual({ tab: 'resource' });
  });

  it('NULL-FALLBACK — with no current navigation the guard LOGS and degrades to a param-less redirect', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined);

    // Called OUTSIDE a navigation: `router.getCurrentNavigation()` is
    // null, which is exactly the null-fallback condition.
    let tree: UrlTree | undefined;
    try {
      TestBed.runInInjectionContext(() => {
        const result: unknown = resourcesRedirectGuard({ path: 'resources' } as never, []);
        tree = result instanceof UrlTree ? result : undefined;
      });

      // The guard LOGGED — the degradation is never silent.
      expect(warn).toHaveBeenCalled();
      expect(String(warn.mock.calls[0]?.[0])).toMatch(/no current navigation/i);
    } finally {
      warn.mockRestore();
    }

    expect(tree).toBeInstanceOf(UrlTree);
    expect(tree?.queryParams).toEqual({ tab: 'resource' });
  });

  it('the retired route is a canMatch guard whose fallback is the REDIRECT\'S OWN TARGET', () => {
    const retired = routes.find((route) => route.path === 'resources');
    expect(retired).toBeDefined();
    expect(retired?.canMatch).toEqual([resourcesRedirectGuard]);
    // Angular REQUIRES a route to name something to activate, and it
    // forbids `redirectTo` + `canMatch` — so the fallback is the
    // redirect's own target (the metrics page), never the deleted
    // resources shell.
    expect(String(retired?.loadComponent)).toContain('pages/metrics/metrics.component');
    expect(String(retired?.loadComponent)).not.toContain('pages/resources/');
    expect(retired?.redirectTo).toBeUndefined();
  });

  it('the redirect target is a LIVE route (the redirect cannot point at a dead path)', () => {
    expect(RESOURCES_REDIRECT_TARGET).toBe('/metrics');
    expect(routes.some((route) => route.path === 'metrics')).toBe(true);
  });
});

// ── 7.2 — the redirect's tab id vs the manifest ────────────────────────

describe('the redirect target id agrees with the manifest (task 7.2)', () => {
  it('the redirect writes the manifest\'s tab-1 id', () => {
    expect(RESOURCES_REDIRECT_TAB_ID).toBe(METRICS_TABS[0].id);
  });

  it('the redirect id is a REAL tab in the manifest', () => {
    expect(metricsTabById(RESOURCES_REDIRECT_TAB_ID)).not.toBeNull();
  });

  it('the redirect id is a LITERAL, NOT derived from the manifest (the assertion above stays meaningful)', () => {
    const src = readFileSync(join(__dirname, 'app.routes.ts'), 'utf-8');
    // If the redirect imported the manifest, the agreement assertion
    // would be vacuously true — a manifest rename would silently follow
    // the redirect instead of failing this spec.
    expect(src).not.toMatch(/from '\.\/pages\/metrics\/tabs\.manifest'/);
    expect(src).not.toContain('METRICS_DEFAULT_TAB_ID');
    expect(src).toContain("export const RESOURCES_REDIRECT_TAB_ID = 'resource';");
  });
});

// ── 7.3 — the gear menu ────────────────────────────────────────────────

describe('the gear menu after the retirement (task 7.3)', () => {
  const appTs = readFileSync(join(__dirname, 'app.ts'), 'utf-8');

  it('has NO Resources entry and keeps the Metrics entry', () => {
    expect(appTs).not.toContain("{ label: 'Resources'");
    expect(appTs).not.toContain("route: '/resources'");
    expect(appTs).toContain("{ label: 'Metrics', icon: 'query_stats', route: '/metrics' }");
  });
});

// ── 7.4 — the retired page's assets ────────────────────────────────────

describe('the retired page assets are GONE (task 7.4)', () => {
  it('the resources page directory no longer exists', () => {
    expect(existsSync(join(__dirname, 'pages', 'resources'))).toBe(false);
  });

  it('no source file imports the deleted page module', () => {
    const offenders = sweptFiles(join(__dirname, 'pages'))
      .filter((file) => /pages\/resources\/|resources\.component/.test(codeOnly(readFileSync(file, 'utf-8'))));
    expect(offenders.map((file) => relative(REPO_ROOT, file))).toEqual([]);
  });

  it('the retired shell\'s per-instance-attribution subtitle is gone with it', () => {
    const offenders = sweptFiles(FRONTEND_ROOT, ['node_modules', 'dist'])
      .filter((file) => file !== __filename) // this file carries the NEEDLE
      .filter((file) => readFileSync(file, 'utf-8').includes(RETIRED_SUBTITLE));
    expect(offenders.map((file) => relative(REPO_ROOT, file))).toEqual([]);
  });

  it('the RELOCATED live card still carries the honest attribution label', () => {
    const liveCard = readFileSync(
      join(__dirname, 'pages', 'metrics', 'resource', 'live', 'live-tab.component.html'),
      'utf-8',
    );
    expect(liveCard).toContain('per-instance attribution, which it does not deliver');
  });
});

// ── 7.6 — the widened repo sweep ───────────────────────────────────────

describe('the widened repo sweep (task 7.6)', () => {
  /**
   * The files that legitimately NAME the retired route — and why.
   *
   * A stale reference is a reference to the retired route AS A PAGE.
   * These two are not:
   *   * the e2e REDIRECT PROBE — it names the route as the redirect's
   *     INPUT and asserts the `/metrics` landing;
   *   * this ACCEPTANCE — it drives the redirect (the load-bearing
   *     probe) and carries the negative pins.
   */
  const ALLOWED_CARRIERS = [
    join('frontend', 'e2e', 'metrics-gate.spec.ts'),
    join('frontend', 'src', 'app', 'app.resources-retirement.spec.ts'),
  ];

  /**
   * A NEGATIVE assertion is a GUARD, not a reference (the house rule:
   * a negative-guard file's strings ARE the absence assertions — they
   * must never be "fixed" by removing the string).
   */
  const isNegativeGuard = (line: string): boolean =>
    /\.not\.(toContain|toMatch|toBe|toEqual)\(/.test(line);

  it('has ZERO stale references to the retired route', () => {
    const offenders: string[] = [];

    for (const file of sweptFiles(FRONTEND_ROOT, ['node_modules', 'dist'])) {
      const raw = readFileSync(file, 'utf-8');
      const lines = (file.endsWith('.ts') ? codeOnly(raw) : raw).split('\n');
      const rel = relative(REPO_ROOT, file);
      lines.forEach((line, index) => {
        if (!RETIRED_ROUTE_RE.test(line)) {
          return;
        }
        if (isNegativeGuard(line)) {
          return; // a guard, not a reference
        }
        if (ALLOWED_CARRIERS.includes(rel)) {
          return; // the redirect probe / the acceptance itself
        }
        offenders.push(`${rel}:${index + 1}: ${line.trim()}`);
      });
    }

    expect(offenders).toEqual([]);
  });

  it('every operator-doc mention of the retired route is inside a RETIREMENT notice', () => {
    const offenders: string[] = [];

    for (const file of sweptFiles(DOCS_ROOT)) {
      const lines = readFileSync(file, 'utf-8').split('\n');
      lines.forEach((line, index) => {
        if (!RETIRED_ROUTE_RE.test(line)) {
          return;
        }
        // The notice is a BLOCK: the mention may sit a few lines below
        // the word that announces it.
        const window = lines.slice(Math.max(0, index - 5), index + 1).join('\n');
        if (/RETIRED|redirect/i.test(window)) {
          return;
        }
        offenders.push(`${relative(REPO_ROOT, file)}:${index + 1}: ${line.trim()}`);
      });
    }

    expect(offenders).toEqual([]);
  });
});

// ── 7.7 — the Playwright gate ──────────────────────────────────────────

describe('the Playwright gate after the retirement (task 7.7)', () => {
  const playwrightConfigs = readdirSync(FRONTEND_ROOT).filter(
    (name) => name.startsWith('playwright') && name.endsWith('.config.ts'),
  );

  it('NO Playwright config targets the retired route', () => {
    expect(playwrightConfigs.length).toBeGreaterThan(0);
    const offenders = playwrightConfigs.filter((name) =>
      RETIRED_ROUTE_RE.test(codeOnly(readFileSync(join(FRONTEND_ROOT, name), 'utf-8'))),
    );
    expect(offenders).toEqual([]);
  });

  it('the ONLY e2e spec naming the retired route is the redirect probe', () => {
    const carriers = readdirSync(E2E_ROOT)
      .filter((name) => name.endsWith('.spec.ts'))
      .filter((name) =>
        RETIRED_ROUTE_RE.test(codeOnly(readFileSync(join(E2E_ROOT, name), 'utf-8'))),
      );
    expect(carriers).toEqual(['metrics-gate.spec.ts']);
  });

  it('the redirect probe asserts the /metrics LANDING (it never drives the retired route as a page)', () => {
    const src = codeOnly(readFileSync(join(E2E_ROOT, 'metrics-gate.spec.ts'), 'utf-8'));
    expect(src).toContain("page.goto('/resources?window=24h&x=1')");
    expect(src).toContain("expect(landed.pathname).toBe('/metrics')");
    expect(src).toContain("expect(landed.searchParams.get('tab')).toBe('resource')");
  });

  it('NO e2e spec asserts the retired route as a LANDING url', () => {
    const offenders = readdirSync(E2E_ROOT)
      .filter((name) => name.endsWith('.spec.ts'))
      .filter((name) =>
        /toHaveURL\([^)]*\/resources/.test(codeOnly(readFileSync(join(E2E_ROOT, name), 'utf-8'))),
      );
    expect(offenders).toEqual([]);
  });
});

// ── 7.8 — the operator runbooks ────────────────────────────────────────

describe('the operator runbooks point at the NEW route (task 7.8)', () => {
  const RUNBOOKS = [
    join(DOCS_ROOT, 'runbooks', 'resource-cost-control.md'),
    join(DOCS_ROOT, 'runbooks', 'resource-hygiene-operator-checklist.md'),
  ];

  it('BOTH runbooks name /metrics?tab=resource', () => {
    for (const file of RUNBOOKS) {
      expect(readFileSync(file, 'utf-8')).toContain('/metrics?tab=resource');
    }
  });

  it('BOTH runbooks keep the /api/resources surface documented', () => {
    for (const file of RUNBOOKS) {
      expect(readFileSync(file, 'utf-8')).toContain('/api/resources');
    }
  });
});
