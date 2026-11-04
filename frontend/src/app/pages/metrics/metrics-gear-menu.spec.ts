// The METRICS route + gear-menu pins (P5 task 5.6/5.8 · D1 pack
// `metrics-fe-page`).
//
// House pattern (mirrors `pages/mcps-gateway/mcps-gateway.pins.spec.ts`):
// source-grep pins over the production sources.
//
// The load-bearing pin is P5-M7: the gear-menu entry's EXPECTED
// POSITION. The Metrics entry is appended AFTER MCP Gateway and BEFORE
// the conditional Database/Maintenance appends — those appends must
// stay at the menu tail (the gateway ordering pin depends on it).
//
// ── THE PHASE-7 RE-POINTING (T1 phase 7, task 7.5) ─────────────────────
//
// The standalone `Resources` gear entry is GONE (D-3): the resource
// module is now tab 1 of this page and the `/resources` route is
// retired. Two assertions are therefore DROPPED, each with its reason:
//
//   | the dropped assertion                                  | reason |
//   |--------------------------------------------------------|--------|
//   | `resourcesIdx` (the Resources entry EXISTS)             | the entry no longer exists — the surface is tab 1 of Metrics |
//   | "Resources stays before the appends" (the ordering pin) | the entry is gone, so the pin has no subject |
//
// Both are REPLACED, not merely dropped: the "no Resources entry" and
// "the Metrics entry remains" assertions below are STRONGER — they pin
// the ABSENCE of the retired entry (so it cannot come back silently)
// and the presence of the surviving one.
//
// The `Metrics` entry's own ordering assertion is UNCHANGED and still
// pins `MCP Gateway → Metrics → (conditional appends)`.

import { readFileSync } from 'fs';
import { join } from 'path';

const appDir = join(__dirname, '..', '..');
const appTs = readFileSync(join(appDir, 'app.ts'), 'utf-8');
const appHtml = readFileSync(join(appDir, 'app.html'), 'utf-8');
const routesSrc = readFileSync(join(appDir, 'app.routes.ts'), 'utf-8');

describe('Metrics — the gear menu entry', () => {
  it('seeds the Metrics item in settingsMenuItems with the frozen shape', () => {
    const itemMatch = appTs.match(
      /\{\s*label:\s*'Metrics',\s*icon:\s*'query_stats',\s*route:\s*'\/metrics'\s*\}/,
    );
    expect(itemMatch).not.toBeNull();
  });

  it('places Metrics AFTER MCP Gateway and BEFORE the conditional appends (P5-M7)', () => {
    const gatewayIdx = appTs.indexOf("{ label: 'MCP Gateway'");
    const metricsIdx = appTs.indexOf("{ label: 'Metrics'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');

    expect(gatewayIdx).toBeGreaterThan(-1);
    expect(metricsIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);

    // MCP Gateway → Metrics → (conditional appends).
    expect(gatewayIdx).toBeLessThan(metricsIdx);
    expect(metricsIdx).toBeLessThan(dbUpdateIdx);
  });

  it('has NO Resources gear entry (the surface is tab 1 of Metrics — D-3)', () => {
    // The REPLACEMENT for the dropped `resourcesIdx` assertion: the
    // retired entry must not come back, and the menu must not carry a
    // second route to the same surfaces.
    expect(appTs).not.toContain("{ label: 'Resources'");
    expect(appTs).not.toContain("route: '/resources'");
  });

  it('does NOT add a top-level header link for /metrics', () => {
    expect(appHtml.includes("'/metrics'")).toBe(false);
    expect(appHtml.includes('"/metrics"')).toBe(false);
  });
});

describe('Metrics — the lazy route', () => {
  it('declares a lazy /metrics route via loadComponent', () => {
    const routeMatch = routesSrc.match(
      /\{\s*path:\s*'metrics',\s*loadComponent:\s*\(\)\s*=>\s*import\(['"]\.\/pages\/metrics\/metrics\.component['"]\)\.then\(m\s*=>\s*m\.MetricsComponent\)/,
    );
    expect(routeMatch).not.toBeNull();
  });

  it('the /metrics route is not eagerly declared (`component:` absent on that route)', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    expect(routeIdx).toBeGreaterThan(-1);
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain('loadComponent');
    expect(routeSlice).not.toMatch(/\bcomponent:\s/);
  });

  it('titles the route "Metrics"', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain("title: 'Metrics'");
  });

  it('the /metrics route is declared BEFORE the catch-all wildcard', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const wildcardIdx = routesSrc.indexOf("path: '**'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(wildcardIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(wildcardIdx);
  });

  it('the /metrics route is declared BEFORE the :id route family (first-match-wins)', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const idRouteIdx = routesSrc.indexOf("path: 'skills/:id'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(idRouteIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(idRouteIdx);
  });
});
